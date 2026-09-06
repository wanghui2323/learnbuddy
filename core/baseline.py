"""V0.53 真实能力诊断：双阶段作答、评分证据、能力画像与旧基线兼容。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from statistics import mean
from typing import Any, Optional

from pydantic import ValidationError

from core.assessment_scoring import ABILITY_LABELS, canonical_capability, score_response
from core.db import get_conn
from core.evaluator import BaselineEvaluator, _fallback_questions
from core.llm_client import LLMClient, LLMError, get_default_client
from core.schemas import (
    AbilityDimensionScore,
    BaselineDimensionScore,
    BaselineProfile,
    EvalQuestion,
    EvalResponse,
    Message,
    ScoreEvidence,
)


CONTRACT_VERSION = "baseline.v0.53"
BLUEPRINT_VERSION = "assessment-blueprint.v0.53"
PROFILE_VERSION = "ability-profile.v0.53"
ACTIVE_STAGES = {"independent", "ai_collaboration"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _assessment_id() -> str:
    return "ba_" + secrets.token_hex(10)


def _profile_id() -> str:
    return "bp_" + secrets.token_hex(10)


def _dispute_id() -> str:
    return "bd_" + secrets.token_hex(10)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _row_value(row, key: str, default=None):
    return row[key] if key in row.keys() else default


def _json(value: Any, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed
    except (json.JSONDecodeError, TypeError):
        return fallback


def _public_question(question: EvalQuestion) -> dict:
    data = question.model_dump(mode="json")
    data.pop("answer_hint", None)
    data.pop("correct_answer", None)
    return data


def _public_questions(questions: list[EvalQuestion]) -> list[dict]:
    return [_public_question(question) for question in questions]


def _questions_from_json(raw: str) -> list[EvalQuestion]:
    data = _json(raw, None)
    if not isinstance(data, list):
        raise ValueError("基线题目快照已损坏。")
    try:
        return [EvalQuestion.model_validate(item) for item in data]
    except ValidationError as exc:
        raise ValueError("基线题目快照不符合数据合同。") from exc


def _canonical_dimension(label: str) -> str:
    text = _clean(label, 120).lower()
    if any(mark in text for mark in ("验收", "纠错", "评测", "校验", "review", "evaluation")):
        return "验收纠错"
    if any(mark in text for mark in ("ai", "prompt", "协作", "上下文", "agent")):
        return "AI 协作"
    if any(mark in text for mark in ("独立", "基础", "概念", "mental", "语法", "知识")):
        return "独立底座"
    return "迁移落地"


def _infer_correct_answer(question: EvalQuestion) -> str:
    if question.correct_answer:
        return str(question.correct_answer).strip()
    hint = str(question.answer_hint or "")
    options = question.options or []
    matches = [str(option) for option in options if str(option).strip() and str(option).strip() in hint]
    return matches[0] if len(matches) == 1 else ""


def _normalize_questions(questions: list[EvalQuestion], domain: str, target_count: int) -> list[EvalQuestion]:
    """把旧 evaluator 输出升级为 V0.53 ItemSnapshot；覆盖不足时使用可信 fallback。"""
    if len(questions) < 6 or len({q.id for q in questions}) != len(questions):
        questions = _fallback_questions(domain, max(6, target_count))
    normalized: list[EvalQuestion] = []
    for index, raw in enumerate(questions[:10], start=1):
        data = raw.model_dump(mode="json")
        data["id"] = _clean(data.get("id") or f"q{index}", 40)
        probe = EvalQuestion.model_validate(data)
        capability = canonical_capability(probe)
        phase = str(data.get("phase") or "")
        if phase not in ACTIVE_STAGES:
            phase = "ai_collaboration" if capability in {"ai_collaboration", "verification_correction"} else "independent"
        method = "deterministic" if probe.type in {"choice", "fill"} else "rubric"
        data.update(
            {
                "phase": phase,
                "capability": capability,
                "grading_method": method,
                "correct_answer": _infer_correct_answer(probe) if method == "deterministic" else None,
            }
        )
        if method == "rubric" and not data.get("rubric"):
            data["rubric"] = [
                {"id": "accuracy", "label": "答案准确与完整", "weight": 0.5},
                {"id": "reasoning", "label": "推理、边界与可验证性", "weight": 0.5},
            ]
        normalized.append(EvalQuestion.model_validate(data))

    independent = [q for q in normalized if q.phase == "independent"]
    assisted = [q for q in normalized if q.phase == "ai_collaboration"]
    capabilities = {canonical_capability(q) for q in normalized}
    required = {"independent_foundation", "independent_transfer", "ai_collaboration", "verification_correction", "learning_strategy"}
    if len(independent) < 3 or len(assisted) < 2 or not required.issubset(capabilities):
        return _fallback_questions(domain, max(6, target_count))
    return normalized


def _build_blueprint(*, domain: str, baseline_hint: str, questions: list[EvalQuestion], mode: str) -> dict:
    return {
        "blueprint_id": "ab_" + secrets.token_hex(8),
        "version": BLUEPRINT_VERSION,
        "domain": domain,
        "baseline_hint": baseline_hint,
        "mode": mode,
        "time_budget_minutes": 12 if mode == "quick" else 20,
        "capabilities": sorted({canonical_capability(q) for q in questions}),
        "stage_plan": {
            "independent": [q.id for q in questions if q.phase == "independent"],
            "ai_collaboration": [q.id for q in questions if q.phase == "ai_collaboration"],
        },
        "item_count": len(questions),
    }


def _responses_for_assessment(conn, assessment_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT question_id,phase,answer,self_confidence,verification_notes,duration_ms,created_at,updated_at
             FROM baseline_responses WHERE assessment_id=? ORDER BY created_at,question_id""",
        (assessment_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _assist_trace(conn, *, user_id: int, assessment_id: str, question_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT role,content,created_at FROM baseline_assist_messages
             WHERE user_id=? AND assessment_id=? AND question_id=? ORDER BY id""",
        (user_id, assessment_id, question_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_profile(row) -> Optional[dict]:
    raw = _row_value(row, "summary")
    if not raw:
        return None
    try:
        return BaselineProfile.model_validate_json(raw).model_dump(mode="json")
    except (ValidationError, ValueError):
        return None


def _row_payload(row, *, include_responses: bool = True) -> dict:
    questions = _questions_from_json(row["questions"])
    contract = _row_value(row, "contract_version", "baseline.v0.50.2") or "baseline.v0.50.2"
    stage = _row_value(row, "stage", "legacy") or "legacy"
    status = row["status"]
    conn = get_conn()
    try:
        responses = _responses_for_assessment(conn, row["id"]) if include_responses else []
    finally:
        conn.close()
    answered = {item["question_id"] for item in responses}
    blueprint = _json(_row_value(row, "blueprint_json", "{}"), {})
    validation_ids = set(blueprint.get("validation_item_ids") or [])
    if contract == CONTRACT_VERSION and validation_ids and status == "in_progress":
        stage_questions = [q for q in questions if q.id in validation_ids and q.phase == stage]
    else:
        stage_questions = [q for q in questions if q.phase == stage] if contract == CONTRACT_VERSION else questions
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "instance_id": row["instance_id"],
        "domain": row["domain"],
        "baseline_hint": row["baseline_hint"] or "",
        "contract_version": contract,
        "assessment_method": "observed_v2" if contract == CONTRACT_VERSION else "self_report_v1",
        "mode": _row_value(row, "mode", "quick") or "quick",
        "status": status,
        "stage": stage,
        "questions": _public_questions(questions),
        "current_questions": _public_questions(stage_questions),
        "responses": responses,
        "progress": {
            "answered": len(answered),
            "total": len(questions),
            "stage_answered": sum(q.id in answered for q in stage_questions),
            "stage_total": len(stage_questions),
        },
        "blueprint": blueprint,
        "profile": _load_profile(row),
        "profile_confidence": _row_value(row, "profile_confidence"),
        "error": _row_value(row, "error"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "submitted_at": row["submitted_at"],
    }


def create_assessment(
    *,
    user_id: int,
    session_id: str,
    domain: str,
    baseline_hint: str = "",
    num_questions: int = 6,
    mode: str = "quick",
    evaluator: BaselineEvaluator | None = None,
    force_new: bool = False,
) -> dict:
    """创建 V0.53 attempt；同一 user+session 默认恢复，force_new 显式重评。"""
    session_id = _clean(session_id, 120)
    domain = _clean(domain, 240)
    baseline_hint = _clean(baseline_hint, 500)
    mode = mode if mode in {"quick", "full"} else "quick"
    if not session_id:
        raise ValueError("session_id 必填。")
    if not domain:
        raise ValueError("学习领域必填。")

    conn = get_conn()
    existing = None
    try:
        existing = conn.execute(
            """SELECT * FROM baseline_assessments WHERE user_id=? AND session_id=?
                 ORDER BY created_at DESC LIMIT 1""",
            (user_id, session_id),
        ).fetchone()
        if existing is not None and not force_new:
            return _row_payload(existing)
    finally:
        conn.close()

    target_count = max(6, min(int(num_questions or 6), 10))
    try:
        questions = (evaluator or BaselineEvaluator()).generate_questions(
            domain=domain,
            baseline_hint=baseline_hint,
            num_questions=target_count,
        )
    except (LLMError, TypeError, ValueError):
        questions = _fallback_questions(domain, target_count)
    questions = _normalize_questions(questions, domain, target_count)
    blueprint = _build_blueprint(
        domain=domain,
        baseline_hint=baseline_hint,
        questions=questions,
        mode=mode,
    )

    assessment_id = _assessment_id()
    ts = _now()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if existing is not None and force_new:
            conn.execute(
                "UPDATE baseline_assessments SET status='superseded', superseded_by=?, updated_at=? WHERE id=? AND user_id=?",
                (assessment_id, ts, existing["id"], user_id),
            )
        conn.execute(
            """INSERT INTO baseline_assessments
               (id,user_id,session_id,domain,baseline_hint,status,questions,contract_version,mode,stage,blueprint_json,created_at,updated_at)
               VALUES (?,?,?,?,?,'in_progress',?,?,?,?,?,?,?)""",
            (
                assessment_id,
                user_id,
                session_id,
                domain,
                baseline_hint or None,
                json.dumps([q.model_dump(mode="json") for q in questions], ensure_ascii=False),
                CONTRACT_VERSION,
                mode,
                "independent",
                json.dumps(blueprint, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (assessment_id,)).fetchone()
        return _row_payload(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current_assessment(*, user_id: int, session_id: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM baseline_assessments WHERE user_id=? AND session_id=?
                 ORDER BY created_at DESC LIMIT 1""",
            (user_id, _clean(session_id, 120)),
        ).fetchone()
        return _row_payload(row) if row is not None else None
    finally:
        conn.close()


def get_assessment(*, user_id: int, assessment_id: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM baseline_assessments WHERE id=? AND user_id=?",
            (_clean(assessment_id, 80), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("基线评估不存在或不属于当前用户。")
        return _row_payload(row)
    finally:
        conn.close()


def save_response(*, user_id: int, assessment_id: str, question_id: str, payload: dict) -> dict:
    """按 item 增量 upsert，刷新/断网后从同一 assessment_id 恢复。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM baseline_assessments WHERE id=? AND user_id=?",
            (_clean(assessment_id, 80), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("基线评估不存在或不属于当前用户。")
        if _row_value(row, "contract_version") != CONTRACT_VERSION:
            raise ValueError("历史自评基线不支持增量作答，请重新开始真实诊断。")
        stage = _row_value(row, "stage")
        if stage not in ACTIVE_STAGES:
            raise ValueError("当前评估阶段不能继续作答。")
        questions = _questions_from_json(row["questions"])
        question = next((q for q in questions if q.id == question_id), None)
        if question is None:
            raise ValueError("题目不存在。")
        if question.phase != stage:
            raise ValueError("题目不属于当前评估阶段。")
        response = EvalResponse.model_validate(
            {
                **payload,
                "question_id": question.id,
                "phase": stage,
                "self_confidence": payload.get("self_confidence"),
            }
        )
        answer = _clean(response.answer, 4000)
        if not answer:
            raise ValueError("答案不能为空。")
        if response.self_confidence is None:
            raise ValueError("请在作答后填写信心分。")
        ts = _now()
        assist = _assist_trace(conn, user_id=user_id, assessment_id=row["id"], question_id=question.id)
        independent_legacy = response.self_confidence if stage == "independent" else 0
        with_ai_legacy = response.self_confidence if stage == "ai_collaboration" else 0
        conn.execute(
            """INSERT INTO baseline_responses
               (assessment_id,question_id,user_id,dimension,answer,independent_score,with_ai_score,
                phase,self_confidence,verification_notes,assist_trace,duration_ms,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(assessment_id,question_id) DO UPDATE SET
                 answer=excluded.answer, independent_score=excluded.independent_score,
                 with_ai_score=excluded.with_ai_score, phase=excluded.phase,
                 self_confidence=excluded.self_confidence, verification_notes=excluded.verification_notes,
                 assist_trace=excluded.assist_trace, duration_ms=excluded.duration_ms, updated_at=excluded.updated_at""",
            (
                row["id"],
                question.id,
                user_id,
                ABILITY_LABELS[canonical_capability(question)],
                answer,
                independent_legacy,
                with_ai_legacy,
                stage,
                response.self_confidence,
                _clean(response.verification_notes, 2000),
                json.dumps(assist, ensure_ascii=False),
                response.duration_ms,
                ts,
                ts,
            ),
        )
        conn.execute(
            "UPDATE baseline_assessments SET updated_at=?, error=NULL WHERE id=? AND user_id=?",
            (ts, row["id"], user_id),
        )
        conn.commit()
        return {"ok": True, "assessment_id": row["id"], "question_id": question.id, "stage": stage}
    finally:
        conn.close()


def request_assistance(
    *,
    user_id: int,
    assessment_id: str,
    question_id: str,
    prompt: str,
    client: Optional[LLMClient] = None,
) -> dict:
    """AI 协作阶段的站内助手；不暴露 reference/rubric，只提供拆解和质疑。"""
    prompt = _clean(prompt, 3000)
    if not prompt:
        raise ValueError("请先写下你要让 AI 协助什么。")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM baseline_assessments WHERE id=? AND user_id=?",
            (_clean(assessment_id, 80), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("基线评估不存在或不属于当前用户。")
        if _row_value(row, "stage") != "ai_collaboration":
            raise ValueError("只有 AI 协作阶段可以调用站内助手。")
        question = next((q for q in _questions_from_json(row["questions"]) if q.id == question_id), None)
        if question is None or question.phase != "ai_collaboration":
            raise ValueError("题目不属于 AI 协作阶段。")
        history = _assist_trace(conn, user_id=user_id, assessment_id=row["id"], question_id=question.id)[-8:]
        messages = [
            Message(
                role="system",
                content=(
                    "你是能力诊断中的协作助手。不要替用户提交最终答案，也不要猜测评分标准。"
                    "帮助用户拆任务、暴露缺失信息、提出候选和校验方法；明确提醒用户独立修改并验收。"
                ),
            ),
            Message(role="user", content=f"任务材料：{question.question}"),
        ]
        messages.extend(
            Message(role=item["role"], content=item["content"])
            for item in history
            if item.get("role") in {"user", "assistant"}
        )
        messages.append(Message(role="user", content=prompt))
        reply = (client or get_default_client()).chat(messages, temperature=0.4, max_tokens=1000).strip()
        ts = _now()
        conn.execute(
            "INSERT INTO baseline_assist_messages (assessment_id,question_id,user_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
            (row["id"], question.id, user_id, "user", prompt, ts),
        )
        conn.execute(
            "INSERT INTO baseline_assist_messages (assessment_id,question_id,user_id,role,content,created_at) VALUES (?,?,?,?,?,?)",
            (row["id"], question.id, user_id, "assistant", reply[:6000], ts),
        )
        conn.commit()
        return {"reply": reply, "question_id": question.id}
    except LLMError:
        raise
    finally:
        conn.close()


def _response_models(rows: list[dict]) -> dict[str, EvalResponse]:
    return {
        row["question_id"]: EvalResponse(
            question_id=row["question_id"],
            answer=row["answer"],
            phase=row.get("phase") if row.get("phase") in ACTIVE_STAGES else None,
            self_confidence=row.get("self_confidence"),
            verification_notes=row.get("verification_notes") or "",
            duration_ms=int(row.get("duration_ms") or 0),
        )
        for row in rows
    }


def _ability(abilities: dict[str, AbilityDimensionScore], key: str) -> float:
    return float(abilities.get(key).score if abilities.get(key) else 0.0)


def _build_observed_profile(
    *,
    assessment_id: str,
    questions: list[EvalQuestion],
    responses: list[EvalResponse],
    evidence: list[ScoreEvidence],
    status: str,
    completed_at: str,
) -> BaselineProfile:
    grouped: dict[str, list[ScoreEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.capability, []).append(item)
    abilities: dict[str, AbilityDimensionScore] = {}
    for key, label in ABILITY_LABELS.items():
        if key == "self_calibration":
            continue
        items = grouped.get(key) or []
        weights = [max(0.05, item.confidence) for item in items]
        score = sum(item.observed_score * weight for item, weight in zip(items, weights)) / sum(weights) if items else 0.0
        confidence = mean(item.confidence for item in items) if items else 0.0
        abilities[key] = AbilityDimensionScore(
            key=key,
            label=label,
            score=round(score, 2),
            confidence=round(confidence, 3),
            evidence_ids=[item.evidence_id for item in items],
        )
    evidence_by_q = {item.question_id: item for item in evidence}
    calibration_values = []
    calibration_evidence = []
    for response in responses:
        item = evidence_by_q.get(response.question_id)
        if item is None or response.self_confidence is None:
            continue
        calibration_values.append(max(0.0, 100.0 - abs(response.self_confidence * 20.0 - item.observed_score)))
        calibration_evidence.append(item.evidence_id)
    abilities["self_calibration"] = AbilityDimensionScore(
        key="self_calibration",
        label=ABILITY_LABELS["self_calibration"],
        score=round(mean(calibration_values), 2) if calibration_values else 0.0,
        confidence=round(mean(item.confidence for item in evidence), 3) if evidence else 0.0,
        evidence_ids=calibration_evidence,
    )

    independent_values = [
        _ability(abilities, "independent_foundation"),
        _ability(abilities, "independent_transfer"),
    ]
    observed_independent = round(mean(independent_values), 2)
    observed_ai = round(_ability(abilities, "ai_collaboration"), 2)
    confidence_values = [item.confidence for item in evidence]
    coverage = len([key for key in ABILITY_LABELS if abilities.get(key) and abilities[key].evidence_ids]) / len(ABILITY_LABELS)
    profile_confidence = round((mean(confidence_values) if confidence_values else 0.0) * coverage, 3)
    self_confidence = round(mean(r.self_confidence * 20 for r in responses if r.self_confidence is not None), 2)
    ordered = sorted(abilities.values(), key=lambda x: x.score, reverse=True)
    strengths = [item.label for item in ordered if item.score >= 75][:3]
    weaknesses = [item.label for item in reversed(ordered) if item.score < 60][:3]
    if not strengths and ordered:
        strengths = [ordered[0].label]
    if not weaknesses and ordered:
        weaknesses = [ordered[-1].label]
    summary = (
        f"真实能力诊断：独立表现 {observed_independent:.0f}/100，AI 协作 {observed_ai:.0f}/100，"
        f"画像置信度 {profile_confidence:.0%}。优先补强：{' / '.join(weaknesses) or '待持续观察'}。"
    )
    independent_5 = round(observed_independent / 20, 2)
    ai_5 = round(observed_ai / 20, 2)
    legacy_dimensions = [
        BaselineDimensionScore(
            dimension="独立底座",
            independent_score=round(_ability(abilities, "independent_foundation") / 20, 2),
            with_ai_score=round(_ability(abilities, "independent_foundation") / 20, 2),
            gap=0,
            response_count=max(1, len(grouped.get("independent_foundation") or [])),
        ),
        BaselineDimensionScore(
            dimension="AI 协作",
            independent_score=0,
            with_ai_score=ai_5,
            gap=ai_5,
            response_count=max(1, len(grouped.get("ai_collaboration") or [])),
        ),
        BaselineDimensionScore(
            dimension="验收纠错",
            independent_score=round(_ability(abilities, "verification_correction") / 20, 2),
            with_ai_score=round(_ability(abilities, "verification_correction") / 20, 2),
            gap=0,
            response_count=max(1, len(grouped.get("verification_correction") or [])),
        ),
        BaselineDimensionScore(
            dimension="迁移落地",
            independent_score=round(_ability(abilities, "independent_transfer") / 20, 2),
            with_ai_score=round(_ability(abilities, "independent_transfer") / 20, 2),
            gap=0,
            response_count=max(1, len(grouped.get("independent_transfer") or [])),
        ),
    ]
    return BaselineProfile(
        assessment_id=assessment_id,
        profile_id=_profile_id(),
        profile_version=PROFILE_VERSION,
        contract_version=CONTRACT_VERSION,
        assessment_method="observed_v2",
        evidence_level="observed" if status == "completed" else "provisional",
        status=status,
        summary=summary,
        independent_score=independent_5,
        with_ai_score=ai_5,
        gap=round(ai_5 - independent_5, 2),
        observed_independent_score=observed_independent,
        observed_with_ai_score=observed_ai,
        self_confidence=self_confidence,
        calibration_gap=round(self_confidence - observed_independent, 2),
        profile_confidence=profile_confidence,
        abilities=list(abilities.values()),
        score_evidence=evidence,
        dimensions=legacy_dimensions,
        strengths=strengths,
        weaknesses=weaknesses,
        questions=[EvalQuestion.model_validate({**q.model_dump(mode="json"), "answer_hint": None, "correct_answer": None}) for q in questions],
        responses=responses,
        completed_at=completed_at,
    )


def _append_validation_questions(*, conn, row, questions: list[EvalQuestion], evidence: list[ScoreEvidence]) -> dict:
    """为首次出现的低置信/偶然命中证据追加最多两道确定性验证题。"""
    blueprint = _json(_row_value(row, "blueprint_json", "{}"), {})
    if blueprint.get("validation_item_ids"):
        return {}
    suspects = [
        item for item in evidence
        if item.confidence < 0.7 or "needs_review" in item.flags or "lucky_correct" in item.flags
    ]
    unique: list[ScoreEvidence] = []
    for item in suspects:
        if item.capability not in {hit.capability for hit in unique}:
            unique.append(item)
        if len(unique) >= 2:
            break
    if not unique:
        return {}
    additions: list[EvalQuestion] = []
    for index, suspect in enumerate(unique, start=1):
        phase = "ai_collaboration" if suspect.capability in {"ai_collaboration", "verification_correction"} else "independent"
        correct = "先明确判断标准，换一个新材料独立重做，并用反例或测试核对结果"
        additions.append(
            EvalQuestion(
                id=f"qv{index}",
                question=(
                    f"上一轮关于“{ABILITY_LABELS.get(suspect.capability, suspect.capability)}”的证据置信度不足。"
                    "为了判断这是稳定掌握还是偶然命中，你下一步应该怎么做？"
                ),
                type="choice",
                options=[
                    "沿用原答案，把信心分调高",
                    correct,
                    "让 AI 重复原答案，出现两次就算正确",
                    "跳过验证，直接把该能力写入学习路径",
                ],
                expected_dimension=f"{ABILITY_LABELS.get(suspect.capability, suspect.capability)}追加验证",
                phase=phase,
                capability=suspect.capability,
                difficulty=2,
                grading_method="deterministic",
                correct_answer=correct,
                common_errors=["用重复回答代替独立验证", "用信心分覆盖客观证据"],
            )
        )
    stage = additions[0].phase
    # 一次只追加同阶段题，避免 UI 在同一提交里混合阶段；另一阶段留给人工复核。
    additions = [item for item in additions if item.phase == stage]
    validation_ids = [item.id for item in additions]
    blueprint["validation_item_ids"] = validation_ids
    blueprint["validation_targets"] = {
        item.id: next(hit.evidence_id for hit in unique if hit.capability == item.capability)
        for item in additions
    }
    all_questions = [*questions, *additions]
    conn.execute(
        """UPDATE baseline_assessments
              SET questions=?, blueprint_json=?, status='in_progress', stage=?,
                  error='发现低置信或偶然命中证据，请完成追加验证题。', updated_at=?
            WHERE id=?""",
        (
            json.dumps([q.model_dump(mode="json") for q in all_questions], ensure_ascii=False),
            json.dumps(blueprint, ensure_ascii=False),
            stage,
            _now(),
            row["id"],
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (row["id"],)).fetchone()
    return _row_payload(updated)


def _grade_assessment(*, user_id: int, row, scorer_client: Optional[LLMClient] = None) -> dict:
    questions = _questions_from_json(row["questions"])
    conn = get_conn()
    try:
        response_rows = _responses_for_assessment(conn, row["id"])
        responses = _response_models(response_rows)
        if set(responses) != {q.id for q in questions}:
            raise ValueError("必须完成两个阶段的全部题目后才能评分。")
        evidence: list[ScoreEvidence] = []
        traces: dict[str, dict] = {}
        for question in questions:
            item, trace = score_response(
                assessment_id=row["id"],
                question=question,
                response=responses[question.id],
                assist_trace=_assist_trace(conn, user_id=user_id, assessment_id=row["id"], question_id=question.id),
                client=scorer_client,
            )
            evidence.append(item)
            traces[question.id] = trace
        validation_payload = _append_validation_questions(
            conn=conn, row=row, questions=questions, evidence=evidence
        )
        if validation_payload:
            return validation_payload
        blueprint = _json(_row_value(row, "blueprint_json", "{}"), {})
        validation_ids = set(blueprint.get("validation_item_ids") or [])
        validated_capabilities = {
            item.capability
            for item in evidence
            if item.question_id in validation_ids and item.confidence >= 0.7 and item.observed_score >= 70
        }
        needs_review = any(
            (item.confidence < 0.7 or "needs_review" in item.flags or "lucky_correct" in item.flags)
            and item.question_id not in validation_ids
            and item.capability not in validated_capabilities
            for item in evidence
        )
        profile_status = "needs_review" if needs_review else "completed"
        ts = _now()
        profile = _build_observed_profile(
            assessment_id=row["id"],
            questions=questions,
            responses=list(responses.values()),
            evidence=evidence,
            status=profile_status,
            completed_at=ts,
        )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM baseline_score_evidence WHERE assessment_id=?", (row["id"],))
        for item in evidence:
            trace = traces[item.question_id]
            conn.execute(
                """INSERT INTO baseline_score_evidence
                   (id,assessment_id,question_id,user_id,phase,capability,observed_score,confidence,
                    grading_method,reason,rubric_scores_json,flags_json,evaluator_version,prompt_version,model,latency_ms,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.evidence_id,
                    row["id"],
                    item.question_id,
                    user_id,
                    item.phase,
                    item.capability,
                    item.observed_score,
                    item.confidence,
                    item.grading_method,
                    item.reason,
                    json.dumps(item.rubric_scores, ensure_ascii=False),
                    json.dumps(item.flags, ensure_ascii=False),
                    item.evaluator_version,
                    trace["prompt_version"],
                    trace["model"],
                    trace["latency_ms"],
                    ts,
                ),
            )
        conn.execute(
            """INSERT INTO baseline_profiles (id,assessment_id,user_id,version,status,confidence,profile_json,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(assessment_id) DO UPDATE SET id=excluded.id,version=excluded.version,status=excluded.status,
                 confidence=excluded.confidence,profile_json=excluded.profile_json,updated_at=excluded.updated_at""",
            (
                profile.profile_id,
                row["id"],
                user_id,
                PROFILE_VERSION,
                profile_status,
                profile.profile_confidence,
                profile.model_dump_json(),
                ts,
                ts,
            ),
        )
        conn.execute(
            """UPDATE baseline_assessments
                 SET status=?,stage=?,summary=?,profile_confidence=?,error=NULL,submitted_at=?,updated_at=?
               WHERE id=? AND user_id=?""",
            (
                profile_status,
                "completed" if profile_status == "completed" else "grading",
                profile.model_dump_json(),
                profile.profile_confidence,
                ts if profile_status == "completed" else None,
                ts,
                row["id"],
                user_id,
            ),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (row["id"],)).fetchone()
        return _row_payload(updated)
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE baseline_assessments SET status='failed',stage='grading',error=?,updated_at=? WHERE id=? AND user_id=?",
            (f"{type(exc).__name__}: {exc}"[:500], _now(), row["id"], user_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def _submit_legacy(*, user_id: int, row, responses: list[dict]) -> dict:
    """只服务已存在的 V0.50.2 draft；明确产出 self_report_v1，不冒充 observed。"""
    questions = _questions_from_json(row["questions"])
    try:
        parsed = [EvalResponse.model_validate(item) for item in responses]
    except ValidationError as exc:
        raise ValueError("历史基线回答或双坐标分数不合法。") from exc
    if {item.question_id for item in parsed} != {q.id for q in questions}:
        raise ValueError("必须完整回答本次基线的所有题目。")
    if any(item.independent_score is None or item.with_ai_score is None for item in parsed):
        raise ValueError("历史基线必须填写独立与 AI 协作自评分。")
    independent = round(mean(float(item.independent_score) for item in parsed), 2)
    with_ai = round(mean(float(item.with_ai_score) for item in parsed), 2)
    ts = _now()
    dimensions = []
    qmap = {q.id: q for q in questions}
    for name in ("独立底座", "AI 协作", "验收纠错", "迁移落地"):
        items = [item for item in parsed if _canonical_dimension(qmap[item.question_id].expected_dimension) == name]
        if items:
            i = round(mean(float(x.independent_score) for x in items), 2)
            a = round(mean(float(x.with_ai_score) for x in items), 2)
            dimensions.append(BaselineDimensionScore(dimension=name, independent_score=i, with_ai_score=a, gap=round(a-i, 2), response_count=len(items)))
    profile = BaselineProfile(
        assessment_id=row["id"],
        summary=f"历史自评画像：独立把握 {independent:.1f}/5，AI 协作把握 {with_ai:.1f}/5；尚未经过客观判分。",
        independent_score=independent,
        with_ai_score=with_ai,
        gap=round(with_ai-independent, 2),
        dimensions=dimensions,
        strengths=[],
        weaknesses=[],
        questions=[EvalQuestion.model_validate({**q.model_dump(mode="json"), "answer_hint": None, "correct_answer": None}) for q in questions],
        responses=parsed,
        completed_at=ts,
        contract_version="baseline.v0.50.2",
        assessment_method="self_report_v1",
        evidence_level="self_report",
        profile_confidence=0,
    )
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM baseline_responses WHERE assessment_id=?", (row["id"],))
        for item in parsed:
            conn.execute(
                """INSERT INTO baseline_responses
                   (assessment_id,question_id,user_id,dimension,answer,independent_score,with_ai_score,phase,self_confidence,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (row["id"], item.question_id, user_id, _canonical_dimension(qmap[item.question_id].expected_dimension), _clean(item.answer, 4000), item.independent_score, item.with_ai_score, "legacy", item.independent_score, ts, ts),
            )
        conn.execute(
            "UPDATE baseline_assessments SET status='submitted',stage='legacy',summary=?,submitted_at=?,updated_at=? WHERE id=? AND user_id=?",
            (profile.model_dump_json(), ts, ts, row["id"], user_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (row["id"],)).fetchone()
        return _row_payload(updated)
    finally:
        conn.close()


def submit_assessment(
    *,
    user_id: int,
    assessment_id: str,
    responses: list[dict],
    phase: str = "",
    scorer_client: Optional[LLMClient] = None,
) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM baseline_assessments WHERE id=? AND user_id=?",
            (_clean(assessment_id, 80), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("基线评估不存在或不属于当前用户。")
    finally:
        conn.close()
    if _row_value(row, "contract_version") != CONTRACT_VERSION:
        if row["status"] == "submitted" and row["summary"]:
            return _row_payload(row)
        return _submit_legacy(user_id=user_id, row=row, responses=responses)

    current_stage = _row_value(row, "stage")
    if current_stage == "completed" and row["summary"]:
        return _row_payload(row)
    if current_stage in {"grading"} or row["status"] in {"needs_review", "failed"}:
        return _grade_assessment(user_id=user_id, row=row, scorer_client=scorer_client)
    if current_stage not in ACTIVE_STAGES:
        raise ValueError("当前评估状态不能提交。")
    if phase and phase != current_stage:
        raise ValueError("提交阶段与当前评估阶段不一致。")
    for item in responses:
        save_response(
            user_id=user_id,
            assessment_id=row["id"],
            question_id=str(item.get("question_id") or ""),
            payload=item,
        )

    conn = get_conn()
    try:
        questions = _questions_from_json(row["questions"])
        blueprint = _json(_row_value(row, "blueprint_json", "{}"), {})
        validation_ids = set(blueprint.get("validation_item_ids") or [])
        expected = (
            validation_ids
            if validation_ids and row["status"] == "in_progress" and all(
                item not in {r["question_id"] for r in _responses_for_assessment(conn, row["id"])}
                for item in validation_ids
            )
            else {q.id for q in questions if q.phase == current_stage}
        )
        received = {
            item["question_id"]
            for item in _responses_for_assessment(conn, row["id"])
            if item.get("phase") == current_stage
        }
        if not expected.issubset(received):
            raise ValueError("必须完整回答当前阶段的所有题目。")
        if current_stage == "independent" and not validation_ids:
            conn.execute(
                "UPDATE baseline_assessments SET stage='ai_collaboration',status='in_progress',updated_at=? WHERE id=? AND user_id=?",
                (_now(), row["id"], user_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (row["id"],)).fetchone()
            return _row_payload(updated)
        conn.execute(
            "UPDATE baseline_assessments SET stage='grading',status='grading',error=NULL,updated_at=? WHERE id=? AND user_id=?",
            (_now(), row["id"], user_id),
        )
        conn.commit()
        grading_row = conn.execute("SELECT * FROM baseline_assessments WHERE id=?", (row["id"],)).fetchone()
    finally:
        conn.close()
    return _grade_assessment(user_id=user_id, row=grading_row, scorer_client=scorer_client)


def create_dispute(*, user_id: int, assessment_id: str, reason: str, evidence_id: str = "") -> dict:
    reason = _clean(reason, 2000)
    if not reason:
        raise ValueError("请说明你对哪项评分有异议。")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM baseline_assessments WHERE id=? AND user_id=?",
            (_clean(assessment_id, 80), user_id),
        ).fetchone()
        if row is None:
            raise ValueError("基线评估不存在或不属于当前用户。")
        if evidence_id:
            hit = conn.execute(
                "SELECT id FROM baseline_score_evidence WHERE id=? AND assessment_id=? AND user_id=?",
                (_clean(evidence_id, 80), row["id"], user_id),
            ).fetchone()
            if hit is None:
                raise ValueError("评分证据不存在。")
        dispute_id = _dispute_id()
        ts = _now()
        conn.execute(
            "INSERT INTO baseline_disputes (id,assessment_id,evidence_id,user_id,reason,status,created_at,updated_at) VALUES (?,?,?,?,?,'open',?,?)",
            (dispute_id, row["id"], evidence_id or None, user_id, reason, ts, ts),
        )
        conn.commit()
        return {"id": dispute_id, "status": "open", "assessment_id": row["id"]}
    finally:
        conn.close()


def submitted_profile_for_session(*, user_id: int, session_id: str) -> BaselineProfile | None:
    current = get_current_assessment(user_id=user_id, session_id=session_id)
    if not current or current["status"] not in {"submitted", "completed"} or not current.get("profile"):
        return None
    profile = BaselineProfile.model_validate(current["profile"])
    if profile.assessment_method == "observed_v2" and profile.status != "completed":
        return None
    return profile


def bind_assessment_to_instance(*, user_id: int, session_id: str, instance_id: str) -> bool:
    conn = get_conn()
    try:
        cur = conn.execute(
            """UPDATE baseline_assessments SET instance_id=?, updated_at=?
                WHERE id=(SELECT id FROM baseline_assessments
                           WHERE user_id=? AND session_id=? AND status IN ('submitted','completed')
                           ORDER BY created_at DESC LIMIT 1) AND user_id=?""",
            (instance_id, _now(), user_id, _clean(session_id, 120), user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def profile_for_instance(*, user_id: int, instance_id: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT summary FROM baseline_assessments
                WHERE user_id=? AND instance_id=? AND status IN ('submitted','completed')
                ORDER BY submitted_at DESC LIMIT 1""",
            (user_id, instance_id),
        ).fetchone()
        if row is None or not row["summary"]:
            return None
        return BaselineProfile.model_validate_json(row["summary"]).model_dump(mode="json")
    finally:
        conn.close()


def assessment_quality_summary() -> dict:
    """管理员匿名聚合：不读取或返回用户原始回答。"""
    conn = get_conn()
    try:
        totals = conn.execute(
            """SELECT COUNT(*) attempts,
                      SUM(status='completed') completed,
                      SUM(status='needs_review') needs_review,
                      SUM(status='failed') failed,
                      AVG(profile_confidence) avg_confidence
                 FROM baseline_assessments WHERE contract_version=?""",
            (CONTRACT_VERSION,),
        ).fetchone()
        by_capability = conn.execute(
            """SELECT capability,COUNT(*) evidence,AVG(observed_score) avg_score,AVG(confidence) avg_confidence
                 FROM baseline_score_evidence GROUP BY capability ORDER BY capability"""
        ).fetchall()
        disputes = conn.execute("SELECT COUNT(*) n FROM baseline_disputes WHERE status='open'").fetchone()["n"]
        return {
            "totals": {
                "attempts": int(totals["attempts"] or 0),
                "completed": int(totals["completed"] or 0),
                "needs_review": int(totals["needs_review"] or 0),
                "failed": int(totals["failed"] or 0),
                "avg_confidence": round(float(totals["avg_confidence"] or 0), 3),
                "open_disputes": int(disputes or 0),
            },
            "by_capability": [dict(row) for row in by_capability],
        }
    finally:
        conn.close()


def recover_inflight_assessments() -> int:
    """服务重启后把中断的判分显式标记为可重试失败，避免永久卡在 grading。"""
    conn = get_conn()
    try:
        cursor = conn.execute(
            """UPDATE baseline_assessments
                  SET status='failed',
                      error='服务重启中断了本次评分，请点击重新评分。',
                      updated_at=?
                WHERE contract_version=? AND status='grading'""",
            (_now(), CONTRACT_VERSION),
        )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))
    finally:
        conn.close()


__all__ = [
    "CONTRACT_VERSION",
    "assessment_quality_summary",
    "bind_assessment_to_instance",
    "create_assessment",
    "create_dispute",
    "get_assessment",
    "get_current_assessment",
    "profile_for_instance",
    "request_assistance",
    "recover_inflight_assessments",
    "save_response",
    "submit_assessment",
    "submitted_profile_for_session",
]
