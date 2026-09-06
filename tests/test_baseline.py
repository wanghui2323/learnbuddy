"""V0.53 真实能力诊断事实流测试。"""

from __future__ import annotations

import importlib

import pytest

from core.schemas import EvalQuestion


class FakeEvaluator:
    def generate_questions(self, domain, baseline_hint="", num_questions=6):
        specs = [
            ("q1", "independent", "independent_foundation", "基础概念"),
            ("q2", "independent", "independent_foundation", "基础辨析"),
            ("q3", "independent", "independent_transfer", "迁移落地"),
            ("q4", "independent", "learning_strategy", "学习策略"),
            ("q5", "ai_collaboration", "ai_collaboration", "AI 协作"),
            ("q6", "ai_collaboration", "verification_correction", "验收纠错"),
        ]
        return [
            EvalQuestion(
                id=qid,
                question=f"{domain} · {label}",
                type="choice",
                options=["A", "B", "C"],
                expected_dimension=label,
                phase=phase,
                capability=capability,
                grading_method="deterministic",
                correct_answer="A",
            )
            for qid, phase, capability, label in specs
        ]


class FakeAssistClient:
    model = "fake-assist"

    def chat(self, messages, **kwargs):
        return "先列出约束，再给两个候选方案，最后用反例验证。"


@pytest.fixture()
def baseline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "baseline.db"))
    import core.auth as auth
    import core.baseline as baseline
    import core.db as db
    import core.learning as learning

    importlib.reload(db)
    importlib.reload(auth)
    importlib.reload(baseline)
    importlib.reload(learning)
    db.init_db()
    user = auth.register_user("baseline@example.com", "secret123", "Baseline")
    other = auth.register_user("other@example.com", "secret123", "Other")
    return baseline, learning, user, other


def _stage_responses(stage: str, answer="A", confidence=4):
    ids = ("q1", "q2", "q3", "q4") if stage == "independent" else ("q5", "q6")
    return [
        {
            "question_id": qid,
            "phase": stage,
            "answer": answer,
            "self_confidence": confidence,
            "verification_notes": "用反例和边界条件复核后修订。" if stage == "ai_collaboration" else "",
        }
        for qid in ids
    ]


def _complete(baseline, user, assessment):
    mid = baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase="independent",
        responses=_stage_responses("independent"),
    )
    assert mid["stage"] == "ai_collaboration"
    return baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase="ai_collaboration",
        responses=_stage_responses("ai_collaboration"),
    )


def test_create_is_idempotent_and_hides_scoring_reference(baseline_env):
    baseline, _learning, user, _other = baseline_env
    first = baseline.create_assessment(
        user_id=user.id, session_id="sess-1", domain="AI Agent", evaluator=FakeEvaluator()
    )
    second = baseline.create_assessment(
        user_id=user.id, session_id="sess-1", domain="另一个目标", evaluator=FakeEvaluator()
    )

    assert first["id"] == second["id"]
    assert first["status"] == "in_progress"
    assert first["contract_version"] == "baseline.v0.53"
    assert first["stage"] == "independent"
    assert len(first["questions"]) == 6
    assert len(first["current_questions"]) == 4
    assert all("correct_answer" not in question for question in first["questions"])


def test_incremental_save_restores_and_rejects_cross_user(baseline_env):
    baseline, _learning, user, other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-2", domain="AI Agent", evaluator=FakeEvaluator()
    )
    with pytest.raises(ValueError, match="不属于"):
        baseline.save_response(
            user_id=other.id,
            assessment_id=assessment["id"],
            question_id="q1",
            payload=_stage_responses("independent")[0],
        )
    baseline.save_response(
        user_id=user.id,
        assessment_id=assessment["id"],
        question_id="q1",
        payload=_stage_responses("independent")[0],
    )
    resumed = baseline.get_current_assessment(user_id=user.id, session_id="sess-2")
    assert resumed["progress"]["answered"] == 1
    assert resumed["responses"][0]["answer"] == "A"


def test_two_stage_submit_scores_observed_answers_not_self_rating(baseline_env):
    baseline, _learning, user, _other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-3", domain="AI Agent", evaluator=FakeEvaluator()
    )
    result = _complete(baseline, user, assessment)
    profile = result["profile"]

    assert result["status"] == "completed"
    assert profile["assessment_method"] == "observed_v2"
    assert profile["observed_independent_score"] == 100
    assert profile["self_confidence"] == 80
    assert profile["calibration_gap"] == -20
    assert len(profile["score_evidence"]) == 6
    assert {item["key"] for item in profile["abilities"]} == {
        "independent_foundation", "independent_transfer", "ai_collaboration",
        "verification_correction", "self_calibration", "learning_strategy",
    }
    dispute = baseline.create_dispute(
        user_id=user.id,
        assessment_id=assessment["id"],
        evidence_id=profile["score_evidence"][0]["evidence_id"],
        reason="这项评分没有考虑我补充的边界条件。",
    )
    assert dispute["status"] == "open"
    assert baseline.assessment_quality_summary()["totals"]["open_disputes"] == 1


def test_missing_stage_answers_are_rejected(baseline_env):
    baseline, _learning, user, _other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-4", domain="AI Agent", evaluator=FakeEvaluator()
    )
    with pytest.raises(ValueError, match="完整回答"):
        baseline.submit_assessment(
            user_id=user.id,
            assessment_id=assessment["id"],
            phase="independent",
            responses=_stage_responses("independent")[:-1],
        )


def test_ai_assist_trace_and_force_new_attempt(baseline_env):
    baseline, _learning, user, _other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-5", domain="AI Agent", evaluator=FakeEvaluator()
    )
    baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase="independent",
        responses=_stage_responses("independent"),
    )
    reply = baseline.request_assistance(
        user_id=user.id,
        assessment_id=assessment["id"],
        question_id="q5",
        prompt="帮我拆解，不要直接作答",
        client=FakeAssistClient(),
    )
    assert "约束" in reply["reply"]

    replacement = baseline.create_assessment(
        user_id=user.id,
        session_id="sess-5",
        domain="AI Agent",
        evaluator=FakeEvaluator(),
        force_new=True,
    )
    assert replacement["id"] != assessment["id"]
    assert baseline.get_assessment(user_id=user.id, assessment_id=assessment["id"])["status"] == "superseded"


def test_completed_profile_binds_to_learning_analytics(baseline_env):
    baseline, learning, user, _other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-6", domain="AI Agent", evaluator=FakeEvaluator()
    )
    _complete(baseline, user, assessment)
    assert baseline.bind_assessment_to_instance(
        user_id=user.id, session_id="sess-6", instance_id="agent-path"
    )
    analytics = learning.analytics_summary(user.id, "agent-path")
    assert analytics["baseline_profile"]["assessment_id"] == assessment["id"]
    assert analytics["baseline_profile"]["assessment_method"] == "observed_v2"


def test_recover_inflight_assessment_makes_grading_retryable(baseline_env):
    baseline, _learning, user, _other = baseline_env
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-7", domain="AI Agent", evaluator=FakeEvaluator()
    )
    from core.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE baseline_assessments SET status='grading', stage='grading' WHERE id=?", (assessment["id"],))
    conn.commit(); conn.close()
    assert baseline.recover_inflight_assessments() == 1
    recovered = baseline.get_assessment(user_id=user.id, assessment_id=assessment["id"])
    assert recovered["status"] == "failed"
    assert "重新评分" in recovered["error"]


def test_low_confidence_evidence_triggers_validation_then_resolves(baseline_env, monkeypatch):
    baseline, _learning, user, _other = baseline_env
    real_score = baseline.score_response

    def uncertain_transfer(**kwargs):
        evidence, trace = real_score(**kwargs)
        if kwargs["question"].id == "q3":
            evidence = evidence.model_copy(
                update={"confidence": 0.55, "flags": ["lucky_correct", "needs_review"]}
            )
        return evidence, trace

    monkeypatch.setattr(baseline, "score_response", uncertain_transfer)
    assessment = baseline.create_assessment(
        user_id=user.id, session_id="sess-8", domain="AI Agent", evaluator=FakeEvaluator()
    )
    baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase="independent",
        responses=_stage_responses("independent"),
    )
    validation = baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase="ai_collaboration",
        responses=_stage_responses("ai_collaboration"),
    )
    assert validation["status"] == "in_progress"
    assert validation["blueprint"]["validation_item_ids"] == ["qv1"]
    question = validation["current_questions"][0]
    completed = baseline.submit_assessment(
        user_id=user.id,
        assessment_id=assessment["id"],
        phase=validation["stage"],
        responses=[
            {
                "question_id": question["id"],
                "answer": question["options"][1],
                "self_confidence": 4,
            }
        ],
    )
    assert completed["status"] == "completed"
    assert len(completed["profile"]["score_evidence"]) == 7
