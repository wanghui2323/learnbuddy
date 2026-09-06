"""core.engine 单元测试（不依赖真实 LLM，可纯本地跑）。

跑法：
    pytest tests/test_engine.py -v
"""

from __future__ import annotations

import asyncio

import pytest

from core.engine import (
    OPENING_GREETING,
    ConversationEngine,
    domain_to_slug,
    extract_generate_params,
    has_generate_signal,
    opening_greeting,
)
from core.llm_client import LLMNetworkError
from core.schemas import Message


# ============================================================================
# opening_greeting
# ============================================================================


class TestOpeningGreeting:
    def test_returns_constant(self):
        assert opening_greeting() == OPENING_GREETING
        assert "LearnBuddy" in OPENING_GREETING
        assert "10–30 分钟" in OPENING_GREETING


# ============================================================================
# has_generate_signal / extract_generate_params
# ============================================================================


VALID_GENERATE_BLOCK = """很好，我现在为你生成完整的学习路径...

<<<GENERATE>>>
```json
{
  "domain": "日语 N3 备考",
  "domain_category": "language",
  "target": "3 个月内通过 JLPT N3",
  "weeks": 12,
  "intensity": "standard",
  "weekday_hours": 2,
  "weekend_hours": 4,
  "weekly_total_hours": 18,
  "baseline_summary": "N5 水平，能识平假/片假名，词汇约 600",
  "preferences": "重听说，每周 1 次外教对话",
  "milestones": [
    {"month": 1, "title": "N5 巩固 + N4 词汇", "deliverable": "词汇量 1500+"},
    {"month": 2, "title": "N4 语法体系", "deliverable": "N4 模考 ≥ 80%"},
    {"month": 3, "title": "N3 冲刺", "deliverable": "N3 模考 ≥ 65%"}
  ],
  "weekly_themes": [
    {"week": 1, "title": "起跑 + 节奏建立", "key_outcomes": ["完成 1 次 N5 模考"]}
  ],
  "learning_methods": {
    "core_method": "背 + 造句 + 影子跟读",
    "anti_forgetting": ["每日 Anki 单词", "每周写日记 1 篇", "周日整周回顾"],
    "self_check_dimensions": ["听", "说", "读", "写", "翻译", "教别人"]
  }
}
```
<<<END>>>
"""


class TestHasGenerateSignal:
    def test_positive(self):
        assert has_generate_signal(VALID_GENERATE_BLOCK)

    def test_negative_no_marker(self):
        assert not has_generate_signal("普通对话内容")

    def test_negative_only_start(self):
        assert not has_generate_signal("<<<GENERATE>>> 缺尾")

    def test_negative_only_end(self):
        assert not has_generate_signal("<<<END>>> 缺头")


class TestExtractGenerateParams:
    def test_valid_block(self):
        params = extract_generate_params(VALID_GENERATE_BLOCK)
        assert params is not None
        assert params.domain == "日语 N3 备考"
        assert params.weeks == 12
        assert params.weekly_total_hours == 18
        assert len(params.milestones) == 3
        assert len(params.learning_methods.self_check_dimensions) == 6

    def test_no_marker_returns_none(self):
        assert extract_generate_params("普通对话内容") is None

    def test_broken_json_returns_none(self):
        bad = """<<<GENERATE>>>
```json
{ this is not valid json }
```
<<<END>>>"""
        assert extract_generate_params(bad) is None

    def test_missing_required_field_returns_none(self):
        bad = """<<<GENERATE>>>
```json
{"domain": "日语", "target": "N3"}
```
<<<END>>>"""
        assert extract_generate_params(bad) is None

    def test_invalid_weeks_returns_none(self):
        """weeks=2 < 4，pydantic 校验失败应返回 None。"""
        bad_weeks = VALID_GENERATE_BLOCK.replace('"weeks": 12', '"weeks": 2')
        assert extract_generate_params(bad_weeks) is None

    def test_invalid_category_returns_none(self):
        """domain_category 不在枚举中应返回 None。"""
        bad_cat = VALID_GENERATE_BLOCK.replace(
            '"domain_category": "language"',
            '"domain_category": "made_up"',
        )
        assert extract_generate_params(bad_cat) is None


# ============================================================================
# domain_to_slug
# ============================================================================


class TestDomainToSlug:
    @pytest.mark.parametrize(
        "domain, expected",
        [
            ("日语 N3 备考", "japanese-n3"),
            ("英语雅思 7.0", "english-ielts-7-0"),
            ("AI 全栈学习", "ai-fullstack"),
            ("营养学入门", "nutrition"),
            ("前端中高级进阶", "frontend"),
            ("化学竞赛备考", "chemistry"),
            ("Python 编程", "python-programming"),
        ],
    )
    def test_typical(self, domain, expected):
        assert domain_to_slug(domain) == expected

    def test_empty_falls_back(self):
        assert domain_to_slug("") == "instance"

    def test_special_chars_only_falls_back(self):
        assert domain_to_slug("！！！") == "instance"

    def test_truncate_long(self):
        long = "a" * 100
        out = domain_to_slug(long)
        assert len(out) <= 40
        assert out == "a" * 40

    def test_idempotent(self):
        """已经是合法 slug 的输入应保持不变。"""
        assert domain_to_slug("ielts") == "ielts"
        assert domain_to_slug("frontend-advanced") == "frontend-advanced"


# ============================================================================
# ConversationEngine（用 fake client 避免真实 LLM 调用）
# ============================================================================


class FakeLLMClient:
    """假 LLM 客户端：按预设的 reply 队列依次返回。"""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[list[Message]] = []
        self.model = "fake-model"

    def chat(self, messages, **kwargs) -> str:
        self.calls.append(list(messages))
        if not self._replies:
            return "（fake client 已无预设 reply）"
        return self._replies.pop(0)


class HangingStreamClient:
    """假流式 client：一直不给 token，用来验证超时兜底。"""

    model = "fake-hanging"

    async def chat_stream(self, messages, **kwargs):
        await asyncio.sleep(0.05)
        if False:
            yield ""  # pragma: no cover


class TestConversationEngine:
    def test_init_loads_system_prompt(self):
        client = FakeLLMClient(["你好，想学什么？"])
        engine = ConversationEngine(client=client)
        assert "LearnBuddy" in engine.system_prompt
        assert "GENERATE" in engine.system_prompt

    def test_system_prompt_forces_preference_choices(self):
        """可枚举偏好不能再退回开放题，尤其是反遗忘机制。"""
        client = FakeLLMClient([])
        engine = ConversationEngine(client=client)

        assert "偏好类问题" in engine.system_prompt
        assert "必须**用 `<<<CHOICES>>>`" in engine.system_prompt
        assert "反遗忘机制偏好必须用 `multi: true`" in engine.system_prompt
        assert "Anki/卡片间隔重复" in engine.system_prompt
        assert "不要问\"你希望路径里内置哪些复习/对抗遗忘的方式？\"" in engine.system_prompt

    def test_system_prompt_forces_single_question_goal_choices(self):
        """首轮目标澄清必须是单问题 + 选择补充，不再开放双问。"""
        client = FakeLLMClient([])
        engine = ConversationEngine(client=client)

        assert "一次只问 1 个需要用户回答的问题" in engine.system_prompt
        assert "目标产出 / 学习用途" in engine.system_prompt
        assert "不要在同一条回复里同时问“最终产出”和“为什么想学”" in engine.system_prompt
        assert "不要输出两个编号问题" in engine.system_prompt

    def test_system_prompt_delegates_quiz_generation_to_baseline_evaluator(self):
        """主对话只请求摸底，避免与 BaselineEvaluator 重复出题。"""
        client = FakeLLMClient([])
        engine = ConversationEngine(client=client)

        assert "由后端 `BaselineEvaluator`" in engine.system_prompt
        assert "`questions` 必须是空数组" in engine.system_prompt
        assert "题目生成、参考要点隔离、双坐标持久化" in engine.system_prompt
        assert '"domain": "已和用户确认的学习领域' in engine.system_prompt

    def test_system_prompt_asks_average_daily_time_only(self):
        """时间盘点只问平均每天，不再拆工作日和周末两轮。"""
        client = FakeLLMClient([])
        engine = ConversationEngine(client=client)

        assert "平均每天能投入多久" in engine.system_prompt
        assert "不要再拆成工作日 / 周末两轮问" in engine.system_prompt
        assert "30 分钟以内" in engine.system_prompt
        assert "3 小时以上" in engine.system_prompt
        assert "其他 / 补充说明" in engine.system_prompt
        assert "默认让 `weekday_hours` 和 `weekend_hours` 都等于该平均值" in engine.system_prompt
        assert '"weekday_hours": 2,\n  "weekend_hours": 2,\n  "weekly_total_hours": 14' in engine.system_prompt

    def test_respond_appends_history(self):
        client = FakeLLMClient(["你好，想学什么？", "很好，再聊聊..."])
        engine = ConversationEngine(client=client)

        r1 = engine.respond("我想学日语 N3")
        assert r1 == "你好，想学什么？"
        assert engine.turn_count == 1
        assert len(engine.history) == 2

        r2 = engine.respond("3 个月时间")
        assert r2 == "很好，再聊聊..."
        assert engine.turn_count == 2
        assert len(engine.history) == 4

    def test_restore_rebuilds_history_without_calling_llm(self):
        client = FakeLLMClient([])
        engine = ConversationEngine(client=client)

        engine.restore(
            [
                {"role": "assistant", "content": "被截断的孤立回复"},
                {"role": "user", "content": "我想学营养学"},
                {"role": "assistant", "content": "你希望解决什么实际问题？"},
                {"role": "system", "content": "不能恢复的系统消息"},
            ]
        )

        assert [(message.role, message.content) for message in engine.history] == [
            ("user", "我想学营养学"),
            ("assistant", "你希望解决什么实际问题？"),
        ]
        assert engine.turn_count == 1
        assert engine._last_assistant_text == "你希望解决什么实际问题？"
        assert client.calls == []
        assert len(engine.history) == 2

    def test_respond_stream_times_out_when_model_stalls(self, monkeypatch):
        """流式模型长时间不给 token 时要显式失败，不能让用户无限等。"""
        monkeypatch.setenv("ITUTOR_CHAT_STREAM_TIMEOUT", "0.01")
        engine = ConversationEngine(client=HangingStreamClient())

        async def run():
            with pytest.raises(LLMNetworkError) as exc:
                async for _ in engine.respond_stream("确认生成"):
                    pass
            assert "对话生成超时" in str(exc.value)

        asyncio.run(run())

    def test_respond_sends_system_prompt_first(self):
        """每次调 LLM 前 system prompt 都应该排在最前。"""
        client = FakeLLMClient(["回复"])
        engine = ConversationEngine(client=client)
        engine.respond("hi")

        assert len(client.calls) == 1
        sent_messages = client.calls[0]
        assert sent_messages[0].role == "system"
        assert "LearnBuddy" in sent_messages[0].content
        assert sent_messages[1].role == "user"
        assert sent_messages[1].content == "hi"

    def test_try_extract_params_on_generate(self):
        """LLM 输出 GENERATE 块时，try_extract_params 应返回 UserParams。"""
        client = FakeLLMClient([VALID_GENERATE_BLOCK])
        engine = ConversationEngine(client=client)
        engine.respond("确认这套生成")

        params = engine.try_extract_params()
        assert params is not None
        assert params.domain == "日语 N3 备考"
        assert params.weeks == 12

    def test_try_extract_params_returns_none_before_generate(self):
        client = FakeLLMClient(["你好，想学什么？"])
        engine = ConversationEngine(client=client)
        engine.respond("我想学日语")
        assert engine.try_extract_params() is None

    def test_reset(self):
        client = FakeLLMClient(["回复 1", "回复 2"])
        engine = ConversationEngine(client=client)
        engine.respond("msg1")
        assert engine.turn_count == 1
        engine.reset()
        assert engine.turn_count == 0
        assert engine.history == []
