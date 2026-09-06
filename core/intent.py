"""用户意图语义锚定。

这一层不负责生成学习路径，只做对话入口的"起疑"：
- 明显多义的新概念先澄清，避免 LLM 自信猜错。
- 首轮学习目标先收集“目标产出”，避免一条回复里堆多个开放问题。
- 用户纠偏后注入短上下文，让后续 LLM 覆盖旧假设。
- 普通学习目标不打断，让原对话引擎继续工作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Iterable, Optional

from core.schemas import Message


_HARNESS_LEGACY_OPTIONS = [
    "AI 领域的完整 harness 工程体系（模型、Agent、工具、数据流、评测、上线观测）",
    "AI 领域的完整 harness 工程（模型 + Agent + 工具调用 + 数据流 + 上线观测）",
    "偏 Agent/工具调用编排的 harness（任务规划、工具路由、权限、状态管理）",
    "偏模型评测/实验的 harness（benchmark、自动评测、回归测试）",
    "AI 产品工程里的测试/上线 harness",
    "AI 应用的评测/实验 harness",
    "Agent 或工具调用的编排 harness",
]

_HARNESS_AI_CONTEXT_MARKERS = (
    "ai",
    "ai领域",
    "ai 领域",
    "ai方面",
    "ai 方面",
    "llm",
    "agent",
    "智能体",
    "模型",
    "人工智能",
    "工具调用",
    "函数调用",
    "数据流",
    "rag",
    "评测",
    "评估",
    "回归",
    "上线",
    "观测",
    "落地",
    "工程",
)

_HARNESS_AI_EXPLICIT_MARKERS = tuple(
    marker for marker in _HARNESS_AI_CONTEXT_MARKERS if marker not in ("落地", "工程")
)


@dataclass(frozen=True)
class IntentDecision:
    """对用户本轮输入的前置决策。"""

    action: str
    user_phrase: str
    concept_type: str
    question: str = ""
    options: list[str] = field(default_factory=list)
    rejected_meanings: list[str] = field(default_factory=list)
    confirmed_meaning: str = ""
    need_search: bool = False
    search_queries: list[str] = field(default_factory=list)

    def assistant_message(self) -> str:
        """渲染成前端可识别的 CHOICES 消息。"""
        if not self.options:
            return self.question
        payload = {
            "question": self.question,
            "multi": False,
            "options": self.options,
            "allow_custom": True,
        }
        return (
            f"{self.question}\n\n"
            "<<<CHOICES>>>\n"
            "```json\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "```\n"
            "<<<END>>>"
        )


@dataclass(frozen=True)
class _ConceptRule:
    keyword: str
    concept_type: str
    options: list[str]
    search_queries: list[str]
    clear_markers: tuple[str, ...] = ()
    ai_markers: tuple[str, ...] = ("ai", "llm", "agent", "智能体", "模型", "人工智能")


_RULES: tuple[_ConceptRule, ...] = (
    _ConceptRule(
        keyword="harness",
        concept_type="ambiguous_technical_term",
        options=[],
        search_queries=[
            "AI evaluation harness LLM",
            "agent tool orchestration harness",
            "AI application testing harness",
        ],
        clear_markers=("harness.io", "ci/cd", "devops", "continuous delivery"),
        ai_markers=(
            "ai",
            "llm",
            "agent",
            "智能体",
            "模型",
            "人工智能",
        ),
    ),
    _ConceptRule(
        keyword="cursor",
        concept_type="ambiguous_product_or_term",
        options=[],
        search_queries=["Cursor AI IDE official", "AI coding IDE Cursor"],
        clear_markers=("cursor ai", "cursor ide"),
    ),
    _ConceptRule(
        keyword="agent",
        concept_type="ambiguous_technical_term",
        options=[],
        search_queries=["AI agent workflow tooling", "LLM agent tool use evaluation"],
    ),
    _ConceptRule(
        keyword="copilot",
        concept_type="ambiguous_product_or_term",
        options=[],
        search_queries=["AI copilot product design", "GitHub Copilot docs"],
    ),
    _ConceptRule(
        keyword="fde",
        concept_type="ambiguous_role",
        options=[],
        search_queries=["Forward Deployed Engineer AI role", "AI solutions engineer field deployment"],
    ),
)

_COMMON_GOALS = (
    "写作",
    "英语",
    "日语",
    "考研",
    "营养",
    "化学",
    "数学",
    "健身",
    "演讲",
)

_CORRECTION_PATTERNS = (
    r"不是.+是",
    r"我(指|说|想要|意思是)",
    r"我的意思是",
    r"是.+方面",
    r"按.+理解",
    r"不是.+公司",
)

_GOAL_REQUEST_PATTERNS = (
    r"我想(学|学习|了解|掌握)",
    r"我要(学|学习|了解|掌握)",
    r"想(学|学习|了解|掌握)",
    r"改(学|成|为)",
    r"换(成|个|一种)",
    r"转向",
    r"学习目标",
    r"技术能力",
    r"落地能力",
)

_COMMON_TECH_TERMS = (
    "ai",
    "api",
    "mcp",
    "rag",
    "llm",
    "gpt",
    "chatgpt",
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "vue",
    "node",
    "sql",
    "html",
    "css",
    "excel",
    "github",
)

_INITIAL_GOAL_OUTPUT_OPTIONS = [
    "进入/转岗到相关岗位",
    "在当前工作里负责相关产品或项目",
    "做一个可展示的作品/项目",
    "准备面试、考试或认证",
    "先系统理解，再决定具体产出",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    low = _norm(text)
    return any(marker.lower() in low for marker in markers)


def is_correction(message: str) -> bool:
    """用户是否正在纠偏模型理解。"""
    text = _norm(message)
    return any(re.search(pat, text) for pat in _CORRECTION_PATTERNS)


def _looks_like_goal_request(message: str) -> bool:
    text = _norm(message)
    return any(re.search(pat, text) for pat in _GOAL_REQUEST_PATTERNS)


def _is_quiz_answer_submission(message: str) -> bool:
    """前端摸底题回传会包含题干里的技术词，不能当作新目标重新澄清。"""
    text = _norm(message)
    return (
        text.startswith("我的摸底选择")
        or text.startswith("已完成摸底选择")
        or ("摸底选择" in text and "→" in message)
        or ("q1." in text and "→" in message)
    )


def initial_goal_output_choices(
    message: str,
    *,
    turn_count: int = 0,
    history: Optional[list[Message]] = None,
) -> Optional[IntentDecision]:
    """首轮先用“选项 + 补充输入”收集用户想要的学习产出。

    这是通用入口，不解释具体名词，也不替用户定方向。后续 LLM 会结合
    用户选择、补充文本和来源接地继续追问基础、时间与偏好。
    """
    text = _norm(message)
    if (
        turn_count != 0
        or history
        or not text
        or is_correction(text)
        or not _looks_like_goal_request(text)
    ):
        return None

    return IntentDecision(
        action="choose_goal_output",
        user_phrase=message.strip(),
        concept_type="goal_output",
        question="你这次学习最想先产出什么？",
        options=list(_INITIAL_GOAL_OUTPUT_OPTIONS),
    )


def _find_rule(message: str) -> Optional[_ConceptRule]:
    text = _norm(message)
    for rule in _RULES:
        if rule.keyword in text:
            return rule
    return None


def _find_unclear_term(message: str) -> str:
    """从目标描述里找可能需要接地的新术语/缩写。"""
    text = _norm(message)
    if not _looks_like_goal_request(text):
        return ""
    candidates = re.findall(r"[a-z][a-z0-9._-]{2,}", text)
    for token in candidates:
        if token not in _COMMON_TECH_TERMS:
            return token
    return ""


def _tech_query_terms(message: str) -> list[str]:
    """抽取需要资料接地的技术词/缩写。

    这是通用启发式，不试图在代码里解释具体名词。解释工作交给来源搜索
    和 LLM，测试集负责约束输出行为。
    """
    text = _norm(message)
    tokens: list[str] = []
    for token in re.findall(r"[a-z][a-z0-9._-]{1,}", text):
        if token in {"the", "and", "for", "with", "from", "into"}:
            continue
        if token in {"html", "css"} and not _looks_like_goal_request(text):
            continue
        if token not in tokens:
            tokens.append(token)
    phrases = (
        "model context protocol",
        "function calling",
        "tool calling",
        "retrieval augmented generation",
    )
    for phrase in phrases:
        if phrase in text and phrase not in tokens:
            tokens.insert(0, phrase)
    return tokens[:4]


def _recently_asked_anchor(rule: _ConceptRule, history: Optional[list[Message]]) -> bool:
    """上一轮已经问过同概念澄清时，不要因为选项文本再次触发澄清。"""
    if not history:
        return False
    recent_assistant = [
        m.content
        for m in history[-4:]
        if m.role == "assistant" and "<<<CHOICES>>>" in m.content
    ]
    return any(rule.keyword in _norm(content) for content in recent_assistant)


def _matches_choice_option(message: str, rule: _ConceptRule) -> bool:
    text = re.sub(r"^(我选|我选择|选择|选)[:：]\s*", "", _norm(message))
    options = list(rule.options)
    if rule.keyword == "harness":
        options.extend(_HARNESS_LEGACY_OPTIONS)
    return any(text == _norm(option) for option in options)


def _recent_user_text(history: Optional[list[Message]], *, limit: int = 8) -> str:
    if not history:
        return ""
    return "\n".join(m.content for m in history[-limit:] if m.role == "user")


def _harness_ai_context_confirmed(
    message: str,
    history: Optional[list[Message]],
) -> bool:
    """当前会话是否已把 harness 锚定到 AI 工程语境。"""
    current = _norm(message)
    if "harness" in current and _contains_any(current, _HARNESS_AI_EXPLICIT_MARKERS):
        return True

    recent = _norm(_recent_user_text(history))
    if "harness" not in recent:
        return False
    return _contains_any(recent, _HARNESS_AI_EXPLICIT_MARKERS) or any(
        _norm(option) in recent for option in _HARNESS_LEGACY_OPTIONS
    )


def _clarify_question_for_rule(rule: _ConceptRule) -> str:
    return (
        f"{rule.keyword} 在不同语境里可能不是同一个东西。"
        "为了把学习路径做准，先用一句话确认：你指的是哪个场景，最终想交付什么？"
    )


def should_clarify_intent(
    message: str,
    *,
    turn_count: int = 0,
    history: Optional[list[Message]] = None,
) -> Optional[IntentDecision]:
    """判断是否要在 LLM 前先问一个语义锚定问题。

    只在早期对话触发，避免用户已经进入计划细节时被打断。
    """
    text = _norm(message)
    if not text or is_correction(text):
        return None
    if _is_quiz_answer_submission(message):
        return None

    rule = _find_rule(text)
    if rule is None:
        unclear_term = _find_unclear_term(text)
        # 含英文技术词的新目标优先走“资料接地 + LLM 追问”，不要在规则层
        # 直接反问“它属于哪个领域”。没有明显技术词时才不拦截。
        if unclear_term or _tech_query_terms(text):
            return None
        if turn_count > 2:
            return None
        return None

    if turn_count > 2 and not _looks_like_goal_request(text):
        return None

    if _recently_asked_anchor(rule, history) or _matches_choice_option(message, rule):
        return None

    if _contains_any(text, rule.clear_markers):
        return None

    if rule.keyword == "harness" and _harness_ai_context_confirmed(message, history):
        return None

    # 普通目标不拦截；但普通目标里夹了多义英文技术词时仍需要锚定。
    if any(goal in text for goal in _COMMON_GOALS) and rule.keyword not in text:
        return None

    return IntentDecision(
        action="clarify",
        user_phrase=message.strip(),
        concept_type=rule.concept_type,
        question=_clarify_question_for_rule(rule),
        options=list(rule.options),
        need_search=True,
        search_queries=list(rule.search_queries),
    )


def build_anchor_context(message: str, history: list[Message]) -> str:
    """为本轮 LLM 调用构建短上下文。

    这个上下文只在用户纠偏或确认多义概念时注入，不持久写入数据库。
    """
    text = _norm(message)
    if not text:
        return ""
    if _is_quiz_answer_submission(message):
        return ""

    rule = _find_rule(text)
    if rule is None:
        return ""

    prior = "\n".join(m.content for m in history[-4:])
    prior_low = _norm(prior)
    has_clear_ai_harness = rule.keyword == "harness" and _harness_ai_context_confirmed(
        message,
        history,
    )
    if not (
        has_clear_ai_harness
        or is_correction(text)
        or "<<<choices>>>" in prior_low
        or rule.keyword in prior_low
    ):
        return ""

    if has_clear_ai_harness:
        return (
            "【语义锚定】用户已把 harness 纠正/确认到 AI 语境。"
            "后续不要按 Harness.io 公司或 DevOps 平台理解；"
            "把“AI harness 工程”暂定理解为 AI 应用的运行与落地支撑层："
            "把模型调用、Agent/工具编排、数据流/RAG、评测与回归测试、"
            "权限/安全、上线观测和产品工程化串成可交付系统。"
            "这只是当前假设，回复时先复述并允许用户校正，然后继续追问目标产出和应用场景。"
        )

    return (
        f"【语义锚定】用户正在确认多义概念 {rule.keyword} 的含义。"
        "请优先复述用户确认后的语境，再继续问学习目标；不要沿用未确认的旧假设。"
    )


def source_queries_for_goal(
    message: str,
    *,
    turn_count: int = 0,
    history: Optional[list[Message]] = None,
) -> list[str]:
    """为“语义接地”提供可选搜索查询。

    只返回查询，不直接决定回复。调用方可把搜索结果作为额外 system context
    注入 LLM，让模型少猜一点、多参考一点。
    """
    text = _norm(message)
    if not text:
        return []
    if _is_quiz_answer_submission(message):
        return []
    if turn_count > 4 and not (_looks_like_goal_request(text) or is_correction(text)):
        return []

    rule = _find_rule(text)
    if rule is not None:
        if rule.keyword == "harness" and _harness_ai_context_confirmed(message, history):
            return [
                "AI application harness agent tool orchestration evaluation observability",
                *rule.search_queries,
            ]
        return list(rule.search_queries)

    tech_terms = _tech_query_terms(text)
    if tech_terms:
        return [" ".join(tech_terms)]
    return []


__all__ = [
    "IntentDecision",
    "build_anchor_context",
    "initial_goal_output_choices",
    "is_correction",
    "should_clarify_intent",
    "source_queries_for_goal",
]
