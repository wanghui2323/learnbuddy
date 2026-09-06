"""core.evaluator 单元测试（不依赖真实 LLM）。

跑法：
    pytest tests/test_evaluator.py -v
"""

from __future__ import annotations

import pytest

from core.evaluator import BaselineEvaluator, _extract_json_array, _fallback_questions
from core.llm_client import LLMError
from core.schemas import EvalQuestion, Message


# ============================================================================
# _extract_json_array 内部工具
# ============================================================================


class TestExtractJsonArray:
    def test_direct_array(self):
        s = '[{"id":"q1"}, {"id":"q2"}]'
        assert _extract_json_array(s) == s

    def test_array_with_whitespace(self):
        s = '\n  [{"id":"q1"}]  \n'
        assert _extract_json_array(s) == s.strip()

    def test_code_block_with_lang(self):
        s = '```json\n[{"id":"q1"}]\n```'
        assert _extract_json_array(s) == '[{"id":"q1"}]'

    def test_code_block_without_lang(self):
        s = '```\n[{"id":"q1"}]\n```'
        assert _extract_json_array(s) == '[{"id":"q1"}]'

    def test_array_in_prose(self):
        """LLM 偶尔不严格按指令，前后多余文字也要能挖出来。"""
        s = '好的，这是题目：[{"id":"q1","question":"a","type":"open","expected_dimension":"x"}] 请查收。'
        result = _extract_json_array(s)
        assert result is not None
        assert result.startswith("[") and result.endswith("]")

    def test_no_array(self):
        assert _extract_json_array("没有数组") is None
        assert _extract_json_array("{}") is None


# ============================================================================
# _fallback_questions
# ============================================================================


class TestFallbackQuestions:
    def test_default_returns_3(self):
        qs = _fallback_questions("日语 N3", num=3)
        assert len(qs) == 3
        for q in qs:
            assert isinstance(q, EvalQuestion)
        assert qs[0].type == "choice"
        assert qs[1].type == "choice"
        assert qs[2].type == "choice"
        assert qs[0].options is not None
        assert qs[1].options is not None
        assert qs[2].options is not None
        assert "AI" in qs[1].question
        assert "验收" in qs[2].expected_dimension

    def test_clamp_low(self):
        """num<3 应被 clamp 到 3。"""
        qs = _fallback_questions("x", num=1)
        assert len(qs) == 3

    def test_clamp_high(self):
        """V0.53 fallback 固定提供覆盖双阶段的 6 题。"""
        qs = _fallback_questions("x", num=10)
        assert len(qs) == 6

    def test_domain_appears_in_question(self):
        qs = _fallback_questions("营养学入门", num=3)
        joined = " ".join(q.question for q in qs)
        assert "营养学入门" in joined

    def test_fallback_questions_are_concrete_not_self_rating(self):
        qs = _fallback_questions("具身智能产品经理", num=3)
        joined_questions = " ".join(q.question for q in qs)
        joined_options = " ".join(" ".join(q.options or []) for q in qs)

        assert "你现在最接近哪种状态" not in joined_questions
        assert "你能做到哪一步" not in joined_questions
        assert "目标产出、约束和验收标准" in joined_options
        assert "任务拆解、缺失信息和风险清单" in joined_options
        assert "核对来源、边界条件、失败样例和结果指标" in joined_options


# ============================================================================
# BaselineEvaluator（用 fake / failing client）
# ============================================================================


class FakeChatClient:
    """假 LLM client：返回预设 chat 文本。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.last_messages: list[Message] = []
        self.model = "fake"

    def chat(self, messages, **kwargs) -> str:
        self.last_messages = list(messages)
        return self.reply


class FailingClient:
    """假 LLM client：调用就抛 LLMError。"""

    def __init__(self):
        self.model = "fake-failing"

    def chat(self, messages, **kwargs) -> str:
        raise LLMError("模拟 LLM 故障")


VALID_EVAL_REPLY = """[
  {
    "id": "q1",
    "question": "「いらっしゃいませ」 通常出现在哪种场合？",
    "type": "choice",
    "options": ["商店欢迎客人", "电话问候", "正式信函", "葬礼"],
    "expected_dimension": "日常会话场景",
    "answer_hint": "商店欢迎客人"
  },
  {
    "id": "q2",
    "question": "用一句话解释「て形」在日语中的三种主要用法。",
    "type": "open",
    "expected_dimension": "语法体系理解",
    "answer_hint": "连接动作、表请求、构成进行时"
  },
  {
    "id": "q3",
    "question": "把这句话改成敬语：「先生が来た。」",
    "type": "fill",
    "expected_dimension": "敬语基础",
    "answer_hint": "先生がいらっしゃった"
  }
]"""


class TestBaselineEvaluator:
    def test_generate_questions_happy_path(self):
        client = FakeChatClient(VALID_EVAL_REPLY)
        evaluator = BaselineEvaluator(client=client)
        questions = evaluator.generate_questions(
            domain="日语 N3 备考",
            baseline_hint="N5 水平",
            num_questions=3,
        )
        assert len(questions) == 3
        assert questions[0].id == "q1"
        assert questions[0].type == "choice"
        assert questions[0].options is not None
        assert len(questions[0].options) == 4
        assert questions[1].type == "open"
        assert questions[2].type == "fill"

    def test_prompt_substitution(self):
        """模板里的 {domain} / {baseline_hint} / {num_questions} 应被替换。"""
        client = FakeChatClient(VALID_EVAL_REPLY)
        evaluator = BaselineEvaluator(client=client)
        evaluator.generate_questions(
            domain="日语 N3",
            baseline_hint="N5",
            num_questions=3,
        )
        sent = client.last_messages[0].content
        assert "日语 N3" in sent
        assert "N5" in sent
        assert "3" in sent
        assert "{domain}" not in sent
        assert "{baseline_hint}" not in sent
        assert "{num_questions}" not in sent
        assert "不要把" in sent and "直接等同于" in sent
        assert "验收纠错" in sent
        assert "场景化诊断原则" in sent
        assert "严禁把 choice 题写成纯自评档位" in sent

    def test_clamp_num_questions(self):
        """num_questions 应被 clamp 到 3-10。"""
        client = FakeChatClient(VALID_EVAL_REPLY)
        evaluator = BaselineEvaluator(client=client)
        evaluator.generate_questions(domain="x", num_questions=10)
        assert "10" in client.last_messages[0].content
        evaluator.generate_questions(domain="x", num_questions=1)
        assert "3" in client.last_messages[0].content

    def test_llm_error_falls_back_by_default(self):
        evaluator = BaselineEvaluator(client=FailingClient())
        qs = evaluator.generate_questions(domain="日语 N3", num_questions=3)
        assert len(qs) == 3
        for q in qs:
            assert "日语 N3" in q.question

    def test_llm_error_raises_when_fallback_disabled(self):
        evaluator = BaselineEvaluator(client=FailingClient())
        with pytest.raises(LLMError):
            evaluator.generate_questions(
                domain="日语",
                num_questions=3,
                use_fallback_on_error=False,
            )

    def test_garbage_reply_falls_back(self):
        """LLM 返回完全乱的内容，应回退到 fallback。"""
        evaluator = BaselineEvaluator(client=FakeChatClient("Sorry I cannot do that."))
        qs = evaluator.generate_questions(domain="日语", num_questions=3)
        assert len(qs) == 3

    def test_partially_valid_reply(self):
        """LLM 返回 3 题但其中 1 题字段不全，应只取有效的 2 题。"""
        partial = """[
            {"id":"q1","question":"a","type":"open","expected_dimension":"x"},
            {"id":"q2"},
            {"id":"q3","question":"c","type":"open","expected_dimension":"y"}
        ]"""
        evaluator = BaselineEvaluator(client=FakeChatClient(partial))
        qs = evaluator.generate_questions(domain="x", num_questions=3)
        assert len(qs) == 2
        assert qs[0].question == "a"
        assert qs[1].question == "c"

    def test_empty_array_falls_back(self):
        evaluator = BaselineEvaluator(client=FakeChatClient("[]"))
        qs = evaluator.generate_questions(domain="x", num_questions=3)
        assert len(qs) == 3  # fallback
