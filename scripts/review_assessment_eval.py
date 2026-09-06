"""V0.53 开放题双人人工复核工具。

用法：
  .venv/bin/python scripts/review_assessment_eval.py prepare /tmp/v053-human-review.csv
  # 两位评审分别填写 rater_1_score / rater_2_score（0-100）
  .venv/bin/python scripts/review_assessment_eval.py score /tmp/v053-human-review.csv

发布门：两位评审都完成；evaluator 对人工共识档位一致率 >= 80%；MAE <= 10。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from core.assessment_scoring import score_response
from core.schemas import EvalQuestion, EvalResponse


ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "tests" / "fixtures" / "assessment_eval_cases.json"
HUMAN_CASES_PATH = ROOT / "tests" / "fixtures" / "assessment_open_human_cases.json"
FIELDS = (
    "case_id",
    "domain",
    "failure_mode",
    "prompt",
    "rubric",
    "answer",
    "evaluator_score",
    "evaluator_reason",
    "evaluator_model",
    "prompt_version",
    "rater_1_score",
    "rater_1_note",
    "rater_2_score",
    "rater_2_note",
)


def score_band(score: float) -> str:
    if score < 40:
        return "insufficient"
    if score < 60:
        return "developing"
    if score < 80:
        return "competent"
    return "strong"


def load_open_cases(path: Path = HUMAN_CASES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _live_evaluation(item: dict[str, Any]) -> tuple[float, str, str, str]:
    phase = "ai_collaboration" if item["failure_mode"] == "ai_proxy" else "independent"
    capability = "ai_collaboration" if phase == "ai_collaboration" else "independent_transfer"
    question = EvalQuestion(
        id=item["id"],
        question=item["prompt"],
        type="open",
        expected_dimension=item["domain"],
        phase=phase,
        capability=capability,
        grading_method="rubric",
        rubric=[
            {"id": "accuracy", "label": "结论与事实准确性", "weight": 0.4},
            {"id": "reasoning", "label": item["rubric"], "weight": 0.6},
        ],
        answer_hint=item["rubric"],
    )
    response = EvalResponse(
        question_id=item["id"],
        answer=item["answer"],
        phase=phase,
        self_confidence=4,
    )
    evidence, trace = score_response(
        assessment_id="human-review-v053",
        question=question,
        response=response,
        assist_trace=[{"role": "assistant", "content": "候选输出"}] if phase == "ai_collaboration" else [],
    )
    return evidence.observed_score, evidence.reason, trace["model"], trace["prompt_version"]


def prepare_review(output: Path, *, cases_path: Path = HUMAN_CASES_PATH, live: bool = False) -> int:
    rows = []
    for item in load_open_cases(cases_path):
        if live:
            score, reason, model, prompt_version = _live_evaluation(item)
            print(f"[{len(rows) + 1:02d}/18] {item['id']} -> {score:.0f}", flush=True)
        else:
            score = item["evaluator_score"]
            reason = item["evaluator_reason"]
            model = "frozen-reference"
            prompt_version = "assessment-rubric.v1"
        rows.append(
            {
                "case_id": item["id"],
                "domain": item["domain"],
                "failure_mode": item["failure_mode"],
                "prompt": item["prompt"],
                "rubric": item["rubric"],
                "answer": item["answer"],
                "evaluator_score": score,
                "evaluator_reason": reason,
                "evaluator_model": model,
                "prompt_version": prompt_version,
                "rater_1_score": "",
                "rater_1_note": "",
                "rater_2_score": "",
                "rater_2_note": "",
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _rating(value: str, field: str, case_id: str) -> float:
    if str(value or "").strip() == "":
        raise ValueError(f"{case_id} 缺少 {field}")
    score = float(value)
    if not 0 <= score <= 100:
        raise ValueError(f"{case_id} 的 {field} 必须在 0-100")
    return score


def calculate_review(rows: list[dict[str, str]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("复核文件没有开放题样本")
    details = []
    for row in rows:
        case_id = row.get("case_id") or "unknown"
        evaluator = _rating(row.get("evaluator_score", ""), "evaluator_score", case_id)
        rater_1 = _rating(row.get("rater_1_score", ""), "rater_1_score", case_id)
        rater_2 = _rating(row.get("rater_2_score", ""), "rater_2_score", case_id)
        consensus = (rater_1 + rater_2) / 2
        details.append(
            {
                "case_id": case_id,
                "evaluator_score": evaluator,
                "human_consensus": consensus,
                "absolute_error": abs(evaluator - consensus),
                "evaluator_band": score_band(evaluator),
                "human_band": score_band(consensus),
                "rater_band_agree": score_band(rater_1) == score_band(rater_2),
            }
        )
    evaluator_agreement = mean(item["evaluator_band"] == item["human_band"] for item in details)
    human_agreement = mean(item["rater_band_agree"] for item in details)
    mae = mean(item["absolute_error"] for item in details)
    return {
        "cases": len(details),
        "human_band_agreement": round(human_agreement, 4),
        "evaluator_band_agreement": round(evaluator_agreement, 4),
        "mean_absolute_error": round(mae, 2),
        "passed": evaluator_agreement >= 0.8 and mae <= 10,
        "details": details,
    }


def score_review(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return calculate_review(list(csv.DictReader(handle)))


def main() -> int:
    parser = argparse.ArgumentParser(description="V0.53 开放题双人人工复核")
    parser.add_argument("command", choices=("prepare", "score"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--live", action="store_true", help="调用当前 DeepSeek evaluator 生成待复核分数")
    args = parser.parse_args()
    if args.command == "prepare":
        count = prepare_review(args.path, live=args.live)
        print(json.dumps({"prepared": count, "path": str(args.path)}, ensure_ascii=False))
        return 0
    try:
        result = score_review(args.path)
    except (OSError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
