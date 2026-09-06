#!/usr/bin/env python3
"""真实 LLM 内容质量 loop 验收脚本。

用途：
- 固定 golden case，跑线上 DeepSeek 生成。
- 记录阶段耗时、token usage、quality/critic/loop 结果。
- 用受控阈值判断本轮工程迭代是否可收口。

默认只跑一个 90 分钟 Agent memory 样本，避免成本和耗时失控。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from core.content import generate_lesson_reviewed
from core.llm_client import LLMClient, set_usage_observer


DEFAULT_OUT = Path("/private/tmp/learnbuddy_lesson_quality_loop_report.json")


CASES: dict[str, dict[str, Any]] = {
    "agent_memory_90min": {
        "domain": "AI Agent 工程化",
        "target": (
            "能独立设计一个可落地的 AI Agent 记忆系统，并解释短时、工作、长期记忆"
            "在工程中的职责、检索、写入和治理边界。"
        ),
        "topic": "Agent 记忆系统的分层设计：短时、工作、长期记忆如何映射到工程架构",
        "baseline": "学习者有产品和工程基础，了解 LLM/Agent/向量检索的基本概念，但缺少系统化的记忆架构设计经验。",
        "n_questions": 6,
        "context": "",
        "custom_instruction": (
            "本节必须支撑 1–2 小时学习。不要写成摘要；要包含机制解释、工程边界、"
            "反例、worked example、练习活动和明确验收 rubric。还必须包含可填写模板、"
            "参考答案/样例产物、批判/修复/设计评审类高阶练习；用户可见内容不要出现课堂话术或内部学员标签。"
        ),
        "learner_signals": "\n".join([
            "学习者在 onboarding 中选择每次学习 1–2 小时，需要完整学习单元，不接受 10 分钟摘要。",
            "学习目标：能把 AI Agent 记忆系统从概念落到工程设计，尤其是跨会话记忆、写入时机、检索策略、污染与过期治理。",
            "已具备：基本 LLM、Agent、工具调用和向量检索概念。",
            "薄弱点：容易把短时记忆等同于缓存；容易只讲存储，不讲写入/更新/遗忘/权限边界。",
            "验收偏好：希望有可画架构图、可复述判断规则、可完成小型设计产出的深度内容。",
        ]),
        "session_contract": {
            "label": "1–2 小时",
            "target_minutes": 90,
            "min_estimated_minutes": 70,
            "mode": "full_unit",
            "source": "onboarding.schedule.weekday_hours",
            "required_activity_types": ["lecture", "worked_example", "drill", "produce", "reflection"],
        },
    }
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _activity_minutes(lesson: dict) -> int:
    total = 0
    for item in lesson.get("activity_plan") or []:
        if isinstance(item, dict):
            total += _safe_int(item.get("minutes") or item.get("duration_minutes"))
    return total


def _min_critic_score(critic: dict) -> int | None:
    scores = critic.get("scores")
    if not isinstance(scores, dict) or not scores:
        return None
    return min(_safe_int(v, 100) for v in scores.values())


def _judge(case: dict, lesson: dict, elapsed_seconds: float, wall_budget_seconds: int) -> tuple[bool, list[str]]:
    quality = lesson.get("_quality") if isinstance(lesson.get("_quality"), dict) else {}
    critic = lesson.get("_critic") if isinstance(lesson.get("_critic"), dict) else {}
    loop = lesson.get("_quality_loop") if isinstance(lesson.get("_quality_loop"), dict) else {}
    checks = quality.get("checks") if isinstance(quality.get("checks"), dict) else {}
    issues: list[str] = []

    if lesson.get("_fallback"):
        issues.append("生成返回 fallback。")
    if lesson.get("_pipeline") not in {"design_write", "design_sections"}:
        issues.append("未走通 design_write/design_sections 主流水线。")
    if not quality.get("passed"):
        issues.append("本地 quality gate 未通过。")
    if _safe_int(quality.get("score")) < 85:
        issues.append(f"quality score 低于 85：{quality.get('score')}")
    if not loop:
        issues.append("缺少 _quality_loop 观测字段。")
    min_critic = _min_critic_score(critic)
    if min_critic is None:
        issues.append("缺少 critic scores。")
    elif min_critic < 75:
        issues.append(f"critic 最低分低于 75：{min_critic}")
    if elapsed_seconds > wall_budget_seconds:
        issues.append(f"端到端耗时超过预算：{elapsed_seconds:.2f}s > {wall_budget_seconds}s")

    contract = case.get("session_contract") or {}
    min_minutes = _safe_int(contract.get("min_estimated_minutes"))
    if min_minutes:
        if _safe_int(lesson.get("estimated_minutes")) < min_minutes:
            issues.append("estimated_minutes 低于学习时长合同最低值。")
        if _activity_minutes(lesson) < min_minutes:
            issues.append("activity_plan 合计分钟数低于学习时长合同最低值。")
    if _safe_int(checks.get("dense_teaching_cards")) < 3:
        issues.append("深讲卡少于 3 张。")
    if _safe_int(checks.get("expansion_blocks")) < 3:
        issues.append("展开层少于 3 块。")
    if min_minutes:
        if not checks.get("self_study_template"):
            issues.append("缺少可填写模板/架构图/检查清单。")
        if not checks.get("reference_answer"):
            issues.append("缺少参考答案/样例产物/合格示例。")
        if not checks.get("high_order_practice"):
            issues.append("缺少批判/修复/设计评审类高阶练习。")
    if checks.get("classroom_terms"):
        issues.append("出现课堂话术：" + "、".join(str(x) for x in checks.get("classroom_terms")[:4]))
    if checks.get("raw_personalization_labels"):
        issues.append("出现内部学员标签：" + "、".join(str(x) for x in checks.get("raw_personalization_labels")[:4]))

    return not issues, issues


def _summarize_case(
    *,
    name: str,
    case: dict,
    lesson: dict,
    stages: list[dict],
    usage: list[dict],
    elapsed_seconds: float,
    wall_budget_seconds: int,
    lesson_path: Path,
) -> dict:
    quality = lesson.get("_quality") if isinstance(lesson.get("_quality"), dict) else {}
    critic = lesson.get("_critic") if isinstance(lesson.get("_critic"), dict) else {}
    loop = lesson.get("_quality_loop") if isinstance(lesson.get("_quality_loop"), dict) else {}
    ok, issues = _judge(case, lesson, elapsed_seconds, wall_budget_seconds)
    return {
        "case": name,
        "accepted": ok,
        "acceptance_issues": issues,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "wall_budget_seconds": wall_budget_seconds,
        "lesson_path": str(lesson_path),
        "fallback": bool(lesson.get("_fallback")),
        "pipeline": lesson.get("_pipeline"),
        "estimated_minutes": lesson.get("estimated_minutes"),
        "activity_minutes": _activity_minutes(lesson),
        "cards": len(lesson.get("cards") or []) if isinstance(lesson.get("cards"), list) else 0,
        "practice": len(lesson.get("practice") or []) if isinstance(lesson.get("practice"), list) else 0,
        "rubric_items": len(lesson.get("rubric") or []) if isinstance(lesson.get("rubric"), list) else 0,
        "quality_passed": quality.get("passed"),
        "quality_score": quality.get("score"),
        "quality_checks": quality.get("checks") or {},
        "self_study_checks": {
            "template": bool((quality.get("checks") or {}).get("self_study_template")),
            "reference_answer": bool((quality.get("checks") or {}).get("reference_answer")),
            "high_order_practice": bool((quality.get("checks") or {}).get("high_order_practice")),
            "classroom_terms": (quality.get("checks") or {}).get("classroom_terms") or [],
            "raw_personalization_labels": (quality.get("checks") or {}).get("raw_personalization_labels") or [],
        },
        "quality_issues": quality.get("issues") or [],
        "critic_verdict": critic.get("verdict"),
        "critic_scores": critic.get("scores"),
        "quality_loop": loop,
        "stages": stages,
        "usage": usage,
    }


def run_case(name: str, case: dict, *, args: argparse.Namespace, out_dir: Path) -> dict:
    os.environ["ITUTOR_LESSON_PIPELINE"] = "1"
    os.environ["ITUTOR_LESSON_REVIEW"] = "1"
    os.environ["ITUTOR_LESSON_REASONER"] = "1" if args.reasoner else "0"
    os.environ["ITUTOR_LESSON_QUALITY_LOOP"] = "1"
    os.environ["ITUTOR_LESSON_MAX_REVISIONS"] = str(args.max_revisions)

    stages: list[dict] = []
    usage: list[dict] = []
    started = time.perf_counter()

    def progress(stage: str) -> None:
        stages.append({"stage": stage, "elapsed_seconds": round(time.perf_counter() - started, 2)})

    def observe_usage(model: str, usage_dict: dict) -> None:
        usage.append({"model": model, **usage_dict})

    set_usage_observer(observe_usage)
    try:
        lesson = generate_lesson_reviewed(
            domain=case["domain"],
            target=case["target"],
            topic=case["topic"],
            baseline=case["baseline"],
            n_questions=case.get("n_questions", 6),
            context=case.get("context", ""),
            custom_instruction=case.get("custom_instruction", ""),
            learner_signals=case.get("learner_signals", ""),
            session_contract=case.get("session_contract"),
            client=LLMClient(timeout=args.llm_timeout),
            progress=progress,
        )
    finally:
        set_usage_observer(None)

    elapsed_seconds = time.perf_counter() - started
    lesson_path = out_dir / f"{name}.lesson.json"
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2), encoding="utf-8")
    return _summarize_case(
        name=name,
        case=case,
        lesson=lesson,
        stages=stages,
        usage=usage,
        elapsed_seconds=elapsed_seconds,
        wall_budget_seconds=args.wall_budget,
        lesson_path=lesson_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run online lesson quality loop verification.")
    parser.add_argument("--case", choices=[*CASES.keys(), "all"], default="agent_memory_90min")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--llm-timeout", type=float, default=180.0)
    parser.add_argument("--wall-budget", type=int, default=300)
    parser.add_argument("--max-revisions", type=int, default=3)
    parser.add_argument("--reasoner", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out_path.with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)

    names = list(CASES) if args.case == "all" else [args.case]
    started = time.perf_counter()
    results = [run_case(name, CASES[name], args=args, out_dir=out_dir) for name in names]
    report = {
        "accepted": all(item["accepted"] for item in results),
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "cases": results,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
