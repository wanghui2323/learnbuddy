"""对话入口多轮评测集。

这组测试不是为了证明某个名词被硬编码理解，而是防止入口路由退化：
- 技术词目标应先资料接地，不要直接反问“它属于哪个领域”
- 多义词才做开放澄清，且不硬塞固定选项
- 用户纠偏后必须保留会话锚点
"""

from __future__ import annotations

import json
from pathlib import Path

from core.intent import build_anchor_context, should_clarify_intent, source_queries_for_goal
from core.schemas import Message


FIXTURE = Path(__file__).parent / "fixtures" / "onboarding_eval_cases.json"


def _load_cases() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["cases"]


def _messages(rows: list[dict]) -> list[Message]:
    return [Message(role=row["role"], content=row["content"]) for row in rows]


def test_onboarding_eval_cases_contract():
    cases = _load_cases()

    assert len(cases) >= 8
    assert {case["category"] for case in cases} >= {
        "known_or_groundable_tech_concept",
        "ambiguous_term",
        "correction_memory",
        "unknown_or_emerging_term",
        "common_learning_goal",
    }


def test_onboarding_semantic_grounding_cases():
    for case in _load_cases():
        history = _messages(case.get("history") or [])
        message = case["message"]
        expect = case["expect"]

        decision = should_clarify_intent(
            message,
            turn_count=int(case.get("turn_count") or 0),
            history=history,
        )
        queries = source_queries_for_goal(
            message,
            turn_count=int(case.get("turn_count") or 0),
            history=history,
        )
        anchor = build_anchor_context(message, history)

        if expect.get("clarify") is True:
            assert decision is not None, case["id"]
            if expect.get("choices") is False:
                assert decision.options == [], case["id"]
            for text in expect.get("question_contains") or []:
                assert text.lower() in decision.question.lower(), case["id"]
        elif expect.get("clarify") is False:
            assert decision is None, case["id"]

        if "source_query_contains" in expect:
            if not expect["source_query_contains"]:
                assert queries == [], case["id"]
            query_blob = " | ".join(queries).lower()
            for text in expect["source_query_contains"]:
                assert text.lower() in query_blob, case["id"]

        for text in expect.get("anchor_contains") or []:
            assert text in anchor, case["id"]

        rendered = decision.assistant_message() if decision is not None else ""
        for text in expect.get("forbid_question_contains") or []:
            assert text.lower() not in rendered.lower(), case["id"]
