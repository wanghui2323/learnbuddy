"""语义锚定层测试。"""

from __future__ import annotations

from core.intent import (
    build_anchor_context,
    initial_goal_output_choices,
    is_correction,
    should_clarify_intent,
    source_queries_for_goal,
)
from core.schemas import Message


def test_initial_goal_output_uses_choices_with_custom_input():
    decision = initial_goal_output_choices("我想学习具身智能行业的产品经理的技能", turn_count=0)

    assert decision is not None
    assert decision.action == "choose_goal_output"
    assert decision.question == "你这次学习最想先产出什么？"
    assert "进入/转岗到相关岗位" in decision.options
    assert "在当前工作里负责相关产品或项目" in decision.options
    message = decision.assistant_message()
    assert "<<<CHOICES>>>" in message
    assert '"allow_custom": true' in message
    assert "1." not in message


def test_initial_goal_output_only_runs_on_first_turn():
    history = [Message(role="user", content="我想学写作")]

    assert initial_goal_output_choices("我想学习具身智能产品经理", turn_count=1) is None
    assert initial_goal_output_choices("我想学习具身智能产品经理", turn_count=0, history=history) is None


def test_harness_first_turn_asks_clarification():
    decision = should_clarify_intent("我想学会harness产品全套技术能力", turn_count=0)

    assert decision is not None
    assert decision.action == "clarify"
    assert "不同语境" in decision.question
    assert "最终想交付什么" in decision.question
    assert decision.options == []
    assert decision.need_search is True
    assert "<<<CHOICES>>>" not in decision.assistant_message()


def test_broad_harness_goal_asks_for_context_not_fixed_options():
    decision = should_clarify_intent("我想学习harness技术，掌握整体技术和落地细节", turn_count=0)

    assert decision is not None
    assert "不同语境" in decision.question
    assert "场景" in decision.question
    assert decision.options == []


def test_ai_harness_does_not_force_options():
    decision = should_clarify_intent("我想学 AI 方面的 harness", turn_count=0)

    assert decision is None


def test_mcp_tech_term_uses_source_grounding_not_domain_question():
    decision = should_clarify_intent("我想学 mcp 方面的系统知识", turn_count=0)
    context = build_anchor_context("我想学 mcp 方面的系统知识", [])
    queries = source_queries_for_goal("我想学 mcp 方面的系统知识", turn_count=0)

    assert decision is None
    assert context == ""
    assert any("mcp" in q.lower() for q in queries)


def test_unknown_english_term_asks_generic_grounding_question():
    decision = should_clarify_intent("我想学习 foobarization 的完整落地能力", turn_count=0)
    queries = source_queries_for_goal("我想学习 foobarization 的完整落地能力", turn_count=0)

    assert decision is None
    assert any("foobarization" in q for q in queries)


def test_clear_harness_io_does_not_interrupt():
    decision = should_clarify_intent("我想学习 Harness.io 的 CI/CD 平台能力", turn_count=0)

    assert decision is None


def test_common_goal_does_not_interrupt():
    decision = should_clarify_intent("我想学写作", turn_count=0)

    assert decision is None


def test_late_turn_does_not_interrupt():
    decision = should_clarify_intent("我们再聊一下 agent", turn_count=4)

    assert decision is None


def test_late_goal_change_can_clarify():
    decision = should_clarify_intent("我想改学 agent 工作流", turn_count=4)

    assert decision is not None
    assert decision.action == "clarify"


def test_quiz_answer_submission_does_not_reopen_agent_anchor():
    message = (
        "我的摸底选择：\n"
        "q1. 【独立底座】Agent memory 应该怎么分层？ → "
        "存储结构要从 session 级别改成用户级别，还要考虑持久化、更新和过期策略\n"
        "q2. 【AI 协作】RAG 记忆如何验收？ → 先定义回归样例再选技术"
    )

    decision = should_clarify_intent(message, turn_count=2)
    queries = source_queries_for_goal(message, turn_count=2)
    context = build_anchor_context(message, [])

    assert decision is None
    assert queries == []
    assert context == ""


def test_choice_after_anchor_does_not_repeat_clarification():
    first = should_clarify_intent("我想学习harness相关的全套技术，具备相关的技术落地能力", turn_count=0)
    assert first is not None
    history = [
        Message(role="user", content="我想学习harness相关的全套技术，具备相关的技术落地能力"),
        Message(role="assistant", content=first.assistant_message()),
    ]

    decision = should_clarify_intent("Agent 或工具调用的编排 harness", turn_count=1, history=history)

    assert decision is None


def test_history_confirmed_ai_harness_does_not_forget():
    history = [
        Message(role="user", content="我想学习harness整体系统的落地"),
        Message(role="assistant", content="我先按最可能的 AI 应用落地语境理解 harness。"),
        Message(role="user", content="AI 领域的harness工程"),
        Message(role="assistant", content="明白，先按 AI 应用的运行与落地支撑层来理解。"),
    ]

    decision = should_clarify_intent(
        "全流程落地，我想构建一套harness开发系统",
        turn_count=2,
        history=history,
    )
    context = build_anchor_context(
        "全流程落地，我想构建一套harness开发系统",
        history,
    )

    assert decision is None
    assert "AI harness 工程" in context
    assert "不要按 Harness.io" in context


def test_prefixed_choice_does_not_repeat_clarification():
    decision = should_clarify_intent("我选择：Agent 或工具调用的编排 harness", turn_count=1)

    assert decision is None


def test_new_broad_choice_does_not_repeat_clarification():
    decision = should_clarify_intent(
        "我选择：AI 领域的完整 harness 工程（模型 + Agent + 工具调用 + 数据流 + 上线观测）",
        turn_count=1,
    )

    assert decision is None


def test_correction_gets_anchor_context():
    history = [
        Message(role="user", content="我想学会harness产品全套技术能力"),
        Message(role="assistant", content="harness 在不同技术语境里含义不一样。你更想学哪一类？"),
    ]

    context = build_anchor_context("是 AI 方面的 harness", history)

    assert "Harness.io" in context
    assert "不要按 Harness.io" in context
    assert "AI harness 工程" in context
    assert "这只是当前假设" in context


def test_clear_ai_harness_gets_context_without_prior_history():
    context = build_anchor_context("我说的是 AI 领域的 harness 工程，包含模型和 agent 的落地", [])

    assert "不要按 Harness.io" in context
    assert "模型调用" in context
    assert "允许用户校正" in context


def test_correction_detector():
    assert is_correction("不是那个公司，是 AI 方面的 harness")
    assert is_correction("我的意思是 AI 应用里的 harness")
    assert not is_correction("我想学写作")
