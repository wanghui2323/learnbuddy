"""V0.53 固定 30 条跨领域离线评测集。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from core.assessment_scoring import score_response
from core.schemas import EvalQuestion, EvalResponse


CASES = json.loads((Path(__file__).parent / "fixtures" / "assessment_eval_cases.json").read_text(encoding="utf-8"))


class FixtureScorer:
    model = "fixture-rubric"

    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kwargs):
        return json.dumps(self.reply, ensure_ascii=False)


def test_eval_set_has_six_domains_five_cases_and_required_failure_modes():
    assert len(CASES) == 30
    assert Counter(item["domain"] for item in CASES) == {
        "语言": 5, "编程": 5, "写作": 5, "考试": 5, "职业技能": 5, "通识": 5,
    }
    assert {item["kind"] for item in CASES} == {
        "objective_correct", "high_conf_wrong", "right_reason_wrong", "ai_proxy", "ambiguous_disagreement",
    }


@pytest.mark.parametrize("case", CASES, ids=[item["id"] for item in CASES])
def test_fixed_eval_case(case):
    rubric = case.get("rubric_reply")
    question = EvalQuestion(
        id=case["id"],
        question=f'{case["domain"]}固定诊断材料',
        type="open" if rubric else "choice",
        options=None if rubric else ["标准答案", "错误答案"],
        expected_dimension=case["domain"],
        phase="ai_collaboration" if case["kind"] == "ai_proxy" else "independent",
        capability="ai_collaboration" if case["kind"] == "ai_proxy" else "independent_foundation",
        grading_method="rubric" if rubric else "deterministic",
        correct_answer=None if rubric else "标准答案",
        rubric=[{"id": "accuracy", "label": "准确性", "weight": 0.5}, {"id": "reasoning", "label": "理由", "weight": 0.5}],
    )
    response = EvalResponse(
        question_id=case["id"],
        answer=case["answer"],
        phase=question.phase,
        self_confidence=case["confidence"],
    )
    evidence, trace = score_response(
        assessment_id="eval-set",
        question=question,
        response=response,
        client=FixtureScorer(rubric) if rubric else None,
    )
    assert evidence.observed_score == case["expected_score"]
    assert set(case["expected_flags"]).issubset(evidence.flags)
    assert trace["prompt_version"]
