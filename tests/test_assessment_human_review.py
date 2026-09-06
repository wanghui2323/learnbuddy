"""开放题双人人工复核发布门测试。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.review_assessment_eval import calculate_review, prepare_review, score_review


FROZEN_REVIEW = Path(__file__).parent / "fixtures" / "assessment_open_human_review.csv"


def _row(case_id: str, evaluator: float, rater_1: float, rater_2: float) -> dict[str, str]:
    return {
        "case_id": case_id,
        "evaluator_score": str(evaluator),
        "rater_1_score": str(rater_1),
        "rater_2_score": str(rater_2),
    }


def test_review_gate_passes_at_required_thresholds():
    rows = [_row(f"c{i}", 70, 66, 70) for i in range(8)]
    rows += [_row("c8", 58, 62, 62), _row("c9", 58, 62, 62)]

    result = calculate_review(rows)

    assert result["evaluator_band_agreement"] == 0.8
    assert result["mean_absolute_error"] <= 10
    assert result["passed"] is True


def test_review_gate_rejects_large_error_even_when_band_matches():
    result = calculate_review([_row("c1", 60, 78, 78), _row("c2", 80, 98, 98)])

    assert result["evaluator_band_agreement"] == 1
    assert result["mean_absolute_error"] == 18
    assert result["passed"] is False


def test_review_requires_both_human_scores():
    with pytest.raises(ValueError, match="rater_2_score"):
        calculate_review([{"case_id": "c1", "evaluator_score": "70", "rater_1_score": "72"}])


def test_prepare_and_score_roundtrip(tmp_path):
    output = tmp_path / "review.csv"
    count = prepare_review(output)
    assert count == 18

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert "田中" in rows[0]["prompt"]
    assert rows[0]["rubric"]
    for row in rows:
        score = float(row["evaluator_score"])
        row["rater_1_score"] = str(score)
        row["rater_2_score"] = str(score)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    result = score_review(output)
    assert result["cases"] == 18
    assert result["passed"] is True


def test_frozen_live_review_contains_all_cases_and_waits_for_humans():
    with FROZEN_REVIEW.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 18
    assert {row["domain"] for row in rows} == {"语言", "编程", "写作", "考试", "职业技能", "通识"}
    assert all(row["evaluator_model"] == "deepseek-chat" for row in rows)
    assert all(row["evaluator_score"] and row["evaluator_reason"] for row in rows)
    assert all(not row["rater_1_score"] and not row["rater_2_score"] for row in rows)
