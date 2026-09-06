"""即时基线评估器（Day 2）。

调 LLM 现场为指定领域生成 3-5 题双坐标系评估题。
不预制任何题库——这是 LearnBuddy 通用引擎的核心差异点。

使用方式：

    from core.evaluator import BaselineEvaluator

    evaluator = BaselineEvaluator()
    questions = evaluator.generate_questions(
        domain="日语 N3 备考",
        baseline_hint="N5 水平，能识平假/片假",
        num_questions=4,
    )
    for q in questions:
        print(f"[{q.id}] ({q.type}) {q.question}")

异常时返回 fallback 通用题集，保证主流程不因 LLM 异常中断。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from core.llm_client import LLMClient, LLMError, get_default_client
from core.schemas import EvalQuestion, Message


PROMPTS_DIR = Path(__file__).parent / "prompts"
EVALUATOR_PROMPT_PATH = PROMPTS_DIR / "evaluator.md"


def _load_evaluator_prompt() -> str:
    if not EVALUATOR_PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"找不到 evaluator prompt: {EVALUATOR_PROMPT_PATH}"
        )
    return EVALUATOR_PROMPT_PATH.read_text(encoding="utf-8")


# ============================================================================
# Fallback：LLM 异常时回退到一组通用题（保证主流程不中断）
# ============================================================================


def _fallback_questions(domain: str, num: int = 3) -> list[EvalQuestion]:
    """LLM 出错时的兜底题集。

    兜底也必须能区分“知识底座 / AI 协作 / 验收纠错”，否则模型失败时
    会退回一组空泛自评题，误导后续路径生成。
    """
    base = [
        EvalQuestion(
            id="q1",
            question=(
                f"在「{domain}」里，系统给你一个新任务：目标很宽、边界不清、"
                "但用户希望尽快看到学习成果。你第一步最该先判断什么？"
            ),
            type="choice",
            options=[
                "先找一份最长的资料开始学",
                "先确认目标产出、约束和验收标准",
                "先让 AI 直接生成完整学习计划",
                "先挑最热门的工具或教材",
            ],
            expected_dimension="独立概念底座",
            answer_hint="评估用户是否能先定义目标、边界和验收，而不是堆资料或迷信 AI。正确倾向是先确认目标产出、约束和验收标准。",
            phase="independent",
            capability="independent_foundation",
            difficulty=2,
            grading_method="deterministic",
            correct_answer="先确认目标产出、约束和验收标准",
        ),
        EvalQuestion(
            id="q2",
            question=(
                f"你要用 AI 辅助完成一个「{domain}」真实任务。"
                "你手上只有目标描述、一些零散资料和一个不确定的截止时间。你会先让 AI 做什么？"
            ),
            type="choice",
            options=[
                "直接给最终答案，省时间",
                "先整理任务拆解、缺失信息和风险清单",
                "先生成一版漂亮文案",
                "先推荐最强模型或工具",
            ],
            expected_dimension="AI 协作成熟度",
            answer_hint="评估用户能否把 AI 用来拆任务和暴露缺口，而不是直接代做。正确倾向是任务拆解、缺失信息和风险清单。",
            phase="ai_collaboration",
            capability="ai_collaboration",
            difficulty=2,
            grading_method="deterministic",
            correct_answer="先整理任务拆解、缺失信息和风险清单",
        ),
        EvalQuestion(
            id="q3",
            question=(
                f"AI 给了你一份「{domain}」相关方案，结构完整、说法自信，"
                "但没有引用来源，也没有说明适用边界。你最该怎么验收？"
            ),
            type="choice",
            options=[
                "看起来完整就先采用",
                "让同一个 AI 再自检一遍",
                "核对来源、边界条件、失败样例和结果指标",
                "换一个模型重新生成，选更顺眼的一版",
            ],
            expected_dimension="AI 产物验收与纠错",
            answer_hint="评估用户是否能用来源、边界、失败样例和指标验收 AI 产物，而不是相信自信表达或单模型自检。",
            phase="ai_collaboration",
            capability="verification_correction",
            difficulty=3,
            grading_method="deterministic",
            correct_answer="核对来源、边界条件、失败样例和结果指标",
        ),
        EvalQuestion(
            id="q4",
            question=f"请用 3-5 句话说明：你认为掌握「{domain}」的核心标志是什么？你会拿什么产物或结果来验证？",
            type="open",
            expected_dimension="目标与验收标准",
            answer_hint="看用户能否把学习目标转成可观察产出和验证标准。",
            phase="independent",
            capability="independent_transfer",
            difficulty=3,
            grading_method="rubric",
            rubric=[
                {"id": "observable_output", "label": "可观察产出", "weight": 0.5},
                {"id": "verification", "label": "可执行验收方法", "weight": 0.5},
            ],
        ),
        EvalQuestion(
            id="q5",
            question=f"如果未来 2 周只能练「{domain}」里的一个最关键小闭环，你会选什么？为什么？",
            type="open",
            expected_dimension="任务拆解与学习策略",
            answer_hint="评估用户能否把大目标拆成可练、可复盘的小闭环。",
            phase="independent",
            capability="learning_strategy",
            difficulty=3,
            grading_method="rubric",
            rubric=[
                {"id": "scope", "label": "闭环范围可执行", "weight": 0.5},
                {"id": "feedback", "label": "包含产出和反馈", "weight": 0.5},
            ],
        ),
        EvalQuestion(
            id="q6",
            question=(
                f"请先向站内 AI 询问如何完成一个「{domain}」小任务，再提交你的最终方案。"
                "最终答案必须说明：你补了什么上下文、改了 AI 的哪一处，以及如何验证结果。"
            ),
            type="open",
            expected_dimension="AI 协作与结果验收",
            answer_hint="高质量回答应包含任务拆解、必要上下文、对 AI 输出的主动修改和独立校验；直接复制 AI 输出不算掌握。",
            phase="ai_collaboration",
            capability="ai_collaboration",
            difficulty=3,
            grading_method="rubric",
            rubric=[
                {"id": "decomposition", "label": "任务拆解", "weight": 0.25},
                {"id": "context", "label": "上下文质量", "weight": 0.25},
                {"id": "revision", "label": "主动修订", "weight": 0.25},
                {"id": "verification", "label": "独立验收", "weight": 0.25},
            ],
        ),
    ]
    num = max(3, min(num, len(base)))
    return base[:num]


# ============================================================================
# JSON 提取（容忍 LLM 偶尔不严格按指令输出）
# ============================================================================


_JSON_ARRAY_PATTERN = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)
_JSON_CODE_BLOCK_PATTERN = re.compile(
    r"```(?:json)?\s*(\[.*?\])\s*```",
    re.DOTALL,
)


def _extract_json_array(text: str) -> Optional[str]:
    """从 LLM 输出中尽力提取出 JSON 数组字符串。

    顺序尝试：
    1. 整段 strip 后是不是直接就是 JSON 数组
    2. 是不是被 ```json ... ``` 代码块包裹
    3. 兜底：正则找第一个 [...]
    """
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped

    m = _JSON_CODE_BLOCK_PATTERN.search(text)
    if m:
        return m.group(1).strip()

    m = _JSON_ARRAY_PATTERN.search(text)
    if m:
        return m.group(0)

    return None


# ============================================================================
# BaselineEvaluator
# ============================================================================


class BaselineEvaluator:
    """调 LLM 现场为指定领域生成评估题。

    构造：
        evaluator = BaselineEvaluator()  # 默认走 get_default_client()
        evaluator = BaselineEvaluator(client=my_client)
    """

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        prompt_template: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 2000,
    ):
        self.client = client or get_default_client()
        self.prompt_template = prompt_template or _load_evaluator_prompt()
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate_questions(
        self,
        domain: str,
        baseline_hint: str = "",
        num_questions: int = 4,
        *,
        use_fallback_on_error: bool = True,
    ) -> list[EvalQuestion]:
        """为指定领域生成 num_questions 道评估题。

        Args:
            domain: 学习领域（中文自然语言）
            baseline_hint: 用户给的水平提示（可空）
            num_questions: 题量，3-5 之间（自动 clamp）
            use_fallback_on_error: LLM 失败时是否回退到通用题集

        Returns:
            list[EvalQuestion]，长度通常等于 num_questions
            （LLM 出题数量不一致时按返回数量给）

        Raises:
            LLMError: 当 use_fallback_on_error=False 且 LLM 调用失败时
        """
        num_questions = max(3, min(num_questions, 10))

        rendered_prompt = (
            self.prompt_template
            .replace("{domain}", domain or "（未指定）")
            .replace("{baseline_hint}", baseline_hint or "（未给出）")
            .replace("{num_questions}", str(num_questions))
        )

        try:
            text = self.client.chat(
                [Message(role="user", content=rendered_prompt)],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except LLMError:
            if use_fallback_on_error:
                return _fallback_questions(domain, num_questions)
            raise

        return self._parse_or_fallback(text, domain, num_questions, use_fallback_on_error)

    def _parse_or_fallback(
        self,
        text: str,
        domain: str,
        num_questions: int,
        use_fallback_on_error: bool,
    ) -> list[EvalQuestion]:
        """解析 LLM 输出。失败时按 use_fallback 决定回退或抛异常。"""
        json_str = _extract_json_array(text)
        if json_str is None:
            if use_fallback_on_error:
                return _fallback_questions(domain, num_questions)
            raise LLMError(f"evaluator 输出中找不到 JSON 数组：{text[:200]}")

        try:
            raw = json.loads(json_str)
        except json.JSONDecodeError as e:
            if use_fallback_on_error:
                return _fallback_questions(domain, num_questions)
            raise LLMError(f"evaluator JSON 解析失败：{e}") from e

        if not isinstance(raw, list):
            if use_fallback_on_error:
                return _fallback_questions(domain, num_questions)
            raise LLMError(f"evaluator 输出不是 JSON 数组：{type(raw)}")

        questions: list[EvalQuestion] = []
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            item.setdefault("id", f"q{idx}")
            try:
                questions.append(EvalQuestion.model_validate(item))
            except ValidationError:
                continue

        if not questions:
            if use_fallback_on_error:
                return _fallback_questions(domain, num_questions)
            raise LLMError("evaluator 输出无任何有效题目")

        return questions


__all__ = [
    "BaselineEvaluator",
    "EVALUATOR_PROMPT_PATH",
]
