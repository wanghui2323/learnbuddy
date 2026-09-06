"""路径级 DeepBuild：研究包 + 大纲质量契约。

这里负责检查一条学习路径是不是经过了足够的“教研构建”，而不是只把
对话摘要套进周计划模板。它不替代 lesson 级内容质量检查，而是在实例生成
阶段给 master.json 写入可追溯的研究与质检元数据。
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from core.concepts import sanitize
from core.llm_client import LLMClient, LLMError, get_default_client
from core.schemas import Message, UserParams, WeekPlan
from core.source_search import build_source_pack, normalize_source_pack

PATH_OUTLINE_PROMPT_PATH = Path(__file__).parent / "prompts" / "path_outline.md"

GENERIC_OUTCOME_MARKERS = ("待", "补充", "完成", "学习", "掌握", "了解")
REVIEW_ONLY_TASK_MARKERS = (
    "轻量消化",
    "复习昨日重点",
    "Anki 推送 + 30 分钟复习",
    "整天复习",
)
REVIEW_MARKERS = ("复习", "回顾", "消化", "Anki", "错题")
PROGRESS_MARKERS = (
    "新内容",
    "推进",
    "知识点",
    "案例",
    "练习",
    "实战",
    "迁移",
    "实现",
    "写",
    "画",
    "讲",
    "输出",
    "产出",
)


class OutlineRefinementError(RuntimeError):
    """路径大纲无法满足结构合同。"""


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _source_pack_without_network(query: str) -> dict:
    """构建不联网的来源包，保留 query_plan/ambiguity 等结构。"""
    return normalize_source_pack(
        {
            "status": "pending_search",
            "query": query,
            "sources": [],
            "note": "路径生成器已生成检索计划；本次未启用联网搜索。",
        },
        fallback_query=query,
        count=6,
    )


def _build_query(params: UserParams) -> str:
    bits = [
        params.domain,
        params.target,
        params.baseline_summary,
        params.preferences,
        "learning path curriculum authoritative sources latest best practices",
    ]
    return _clean_text(" ".join(x for x in bits if x), 220)


def build_outline_research(
    params: UserParams,
    *,
    allow_network: bool = False,
    timeout: float | None = None,
    user_sources: list[dict] | None = None,
) -> dict:
    """为学习路径生成阶段构建研究包。

    allow_network=False 时不触网，适合测试/离线；服务端真实生成会打开。
    """
    query = _build_query(params)
    if allow_network:
        pack = build_source_pack(
            query,
            count=6,
            timeout=timeout or float(os.getenv("LEARNBUDDY_OUTLINE_SEARCH_TIMEOUT", "6")),
        )
    else:
        pack = _source_pack_without_network(query)

    sources = []
    for idx, raw in enumerate(pack.get("sources") or [], start=1):
        source = dict(raw)
        source["ref_id"] = _clean_text(source.get("ref_id") or f"web:{idx}", 80)
        sources.append(source)
    normalized_user_sources = []
    for idx, raw in enumerate(user_sources or [], start=1):
        source_id = raw.get("source_id") or raw.get("id") or idx
        text = _clean_text(raw.get("text") or raw.get("content"), 1200)
        title = _clean_text(raw.get("title") or f"用户知识库资料 {idx}", 180)
        if not text and not title:
            continue
        normalized_user_sources.append(
            {
                "ref_id": f"user:{source_id}",
                "source_id": source_id,
                "instance_id": _clean_text(raw.get("instance_id"), 80),
                "title": title,
                "text": text,
                "score": float(raw.get("score") or 0),
                "source_type": "user_kb",
            }
        )
        if len(normalized_user_sources) >= 8:
            break
    source_types = [str(s.get("source_type") or s.get("kind") or "web") for s in sources]
    authority_count = sum(1 for t in source_types if t in {"official", "paper", "book", "journal", "github"})
    claims: list[str] = []
    for src in sources:
        for claim in src.get("claims") or []:
            text = _clean_text(claim, 140)
            if text:
                claims.append(text)
        if len(claims) >= 6:
            break

    return {
        "version": "0.1",
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "status": pack.get("status") or "unknown",
        "note": pack.get("note", ""),
        "query_plan": pack.get("query_plan") or [],
        "ambiguity": pack.get("ambiguity") or {"term": "", "candidates": [], "confidence": 0.0},
        "source_count": len(sources),
        "user_source_count": len(normalized_user_sources),
        "total_source_count": len(sources) + len(normalized_user_sources),
        "sources": sources,
        "user_sources": normalized_user_sources,
        "signals": {
            "authority_sources": authority_count,
            "source_types": sorted(set(source_types)),
            "has_external_grounding": bool(sources),
            "has_user_grounding": bool(normalized_user_sources),
            "claims_collected": len(claims),
        },
        "teaching_assumptions": [
            "先确认概念边界，再拆能力维度。",
            "周计划必须有可观察产出，不只列知识点。",
            "第一周必须落到每天的学习路径，后续周保留目录与解锁条件。",
        ],
        "claims": claims[:6],
    }


def _extract_json_object(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise OutlineRefinementError("LLM 未返回 JSON 对象。")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OutlineRefinementError(f"大纲 JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise OutlineRefinementError("大纲返回值必须是 JSON 对象。")
    return data


def _load_outline_prompt() -> str:
    if not PATH_OUTLINE_PROMPT_PATH.exists():
        raise OutlineRefinementError(f"找不到路径大纲 prompt: {PATH_OUTLINE_PROMPT_PATH}")
    return PATH_OUTLINE_PROMPT_PATH.read_text(encoding="utf-8")


def _research_prompt_payload(research: dict) -> dict:
    return {
        "query": research.get("query"),
        "status": research.get("status"),
        "query_plan": research.get("query_plan") or [],
        "claims": research.get("claims") or [],
        "external_sources": [
            {
                "ref_id": source.get("ref_id"),
                "title": source.get("title"),
                "snippet": source.get("snippet") or source.get("summary") or "",
                "source_type": source.get("source_type") or source.get("kind") or "web",
            }
            for source in (research.get("sources") or [])[:8]
        ],
        "user_sources": [
            {
                "ref_id": source.get("ref_id"),
                "title": source.get("title"),
                "text": source.get("text"),
            }
            for source in (research.get("user_sources") or [])[:8]
        ],
    }


def _validate_refined_outline(params: UserParams, base_master: dict, data: dict) -> dict:
    raw_weeks = data.get("weeks")
    if not isinstance(raw_weeks, list):
        raise OutlineRefinementError("大纲缺少 weeks 数组。")
    by_week: dict[int, dict] = {}
    for raw in raw_weeks:
        if not isinstance(raw, dict):
            raise OutlineRefinementError("每个周大纲必须是对象。")
        try:
            week_no = int(raw.get("week"))
        except (TypeError, ValueError) as exc:
            raise OutlineRefinementError("周序号必须是整数。") from exc
        if week_no in by_week:
            raise OutlineRefinementError(f"W{week_no} 重复。")
        by_week[week_no] = raw
    expected = set(range(1, params.weeks + 1))
    if set(by_week) != expected:
        missing = sorted(expected - set(by_week))
        extra = sorted(set(by_week) - expected)
        raise OutlineRefinementError(f"周数不完整，missing={missing}, extra={extra}。")

    available_refs = {
        str(source.get("ref_id"))
        for source in [
            *(data.get("_available_sources") or []),
        ]
        if source.get("ref_id")
    }
    refined = deepcopy(base_master)
    refined_weeks = []
    for base_week in base_master.get("weeks") or []:
        week_no = int(base_week.get("week") or 0)
        raw = by_week[week_no]
        title = _clean_text(raw.get("title"), 120)
        outcomes = [_clean_text(x, 160) for x in (raw.get("key_outcomes") or []) if _clean_text(x, 160)]
        if len(title) < 4 or "待" in title:
            raise OutlineRefinementError(f"W{week_no} 标题不够具体。")
        if len(outcomes) < 2 or len(outcomes) > 4 or any(_is_generic_outcome(x) for x in outcomes):
            raise OutlineRefinementError(f"W{week_no} 需要 2-4 个可观察产出。")
        source_refs = []
        for ref in raw.get("source_refs") or []:
            clean_ref = _clean_text(ref, 80)
            if clean_ref and (not available_refs or clean_ref in available_refs):
                source_refs.append(clean_ref)
        refined_week = dict(base_week)
        refined_week.update(
            {
                "title": title,
                "key_outcomes": outcomes,
                "source_refs": list(dict.fromkeys(source_refs)),
                "rationale": _clean_text(raw.get("rationale"), 300),
            }
        )
        refined_weeks.append(refined_week)
    refined["weeks"] = refined_weeks

    month_updates = {
        int(raw.get("month")): raw
        for raw in (data.get("months") or [])
        if isinstance(raw, dict) and str(raw.get("month") or "").isdigit()
    }
    for idx, month in enumerate(refined.get("months") or [], start=1):
        raw = month_updates.get(idx)
        if not raw:
            continue
        title = _clean_text(raw.get("title"), 120)
        deliverable = _clean_text(raw.get("deliverable"), 180)
        if title:
            month["title"] = title
        if deliverable:
            month["deliverable"] = deliverable
    refined["version"] = "0.50.1-grounded-outline"
    return refined


def refine_master_from_research(
    params: UserParams,
    master: dict,
    research: dict,
    *,
    client: LLMClient | None = None,
    issues: list[str] | None = None,
) -> tuple[dict, dict]:
    """让教研 LLM 把研究证据真正写入大纲，并执行严格结构校验。"""
    payload = _research_prompt_payload(research)
    available_sources = [
        *(payload.get("external_sources") or []),
        *(payload.get("user_sources") or []),
    ]
    prompt = (
        _load_outline_prompt()
        .replace("{domain}", params.domain)
        .replace("{target}", params.target)
        .replace("{weeks}", str(params.weeks))
        .replace("{baseline}", params.baseline_summary or "（未知）")
        .replace("{preferences}", params.preferences or "（未知）")
        .replace("{current_master_json}", json.dumps(master, ensure_ascii=False))
        .replace("{research_json}", json.dumps(payload, ensure_ascii=False))
        .replace("{repair_issues_json}", json.dumps(issues or [], ensure_ascii=False))
    )
    try:
        llm = client or get_default_client()
        raw_text = llm.chat(
            [Message(role="user", content=prompt)],
            temperature=0.25,
            max_tokens=max(3000, params.weeks * 260),
        )
    except LLMError as exc:
        raise OutlineRefinementError(f"大纲生成调用失败：{exc}") from exc
    data = _extract_json_object(raw_text)
    data["_available_sources"] = available_sources
    refined = _validate_refined_outline(params, master, data)
    trace = {
        "status": "done",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "external_sources_used": len(payload.get("external_sources") or []),
        "user_sources_used": len(payload.get("user_sources") or []),
        "repair_issues": issues or [],
    }
    return refined, trace


def _week_outcomes(week: dict) -> list[str]:
    return [_clean_text(x, 140) for x in (week.get("key_outcomes") or []) if _clean_text(x)]


def _is_generic_outcome(text: str) -> bool:
    stripped = _clean_text(text)
    if not stripped:
        return True
    if "（" in stripped and "待" in stripped:
        return True
    return len(stripped) <= 4 or stripped in GENERIC_OUTCOME_MARKERS


def _slot_text(slot: Any, key: str) -> str:
    if isinstance(slot, dict):
        return _clean_text(slot.get(key), 240)
    return _clean_text(getattr(slot, key, ""), 240)


def _day_value(day: Any, key: str, default: Any = None) -> Any:
    if isinstance(day, dict):
        return day.get(key, default)
    return getattr(day, key, default)


def _day_slots(day: Any) -> list[Any]:
    return list(_day_value(day, "slots", []) or [])


def _day_is_weekend(day: Any) -> bool:
    explicit = _day_value(day, "is_weekend", None)
    if explicit is not None:
        return bool(explicit)
    try:
        return int(_day_value(day, "day", 0) or 0) >= 6
    except (TypeError, ValueError):
        return False


def _day_planned_hours(day: Any) -> float:
    try:
        return float(_day_value(day, "planned_hours", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _day_number(day: Any) -> int:
    try:
        return int(_day_value(day, "day", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _slot_is_review_only(slot: Any) -> bool:
    task = _slot_text(slot, "task")
    output = _slot_text(slot, "output")
    text = f"{task} {output}"
    if any(marker in task for marker in REVIEW_ONLY_TASK_MARKERS):
        return True
    has_review = any(marker in task for marker in REVIEW_MARKERS)
    has_progress = any(marker in text for marker in PROGRESS_MARKERS)
    return bool(has_review and not has_progress)


def _slot_has_progress(slot: Any) -> bool:
    task = _slot_text(slot, "task")
    output = _slot_text(slot, "output")
    text = f"{task} {output}"
    if _slot_is_review_only(slot):
        return False
    if any(marker in text for marker in PROGRESS_MARKERS):
        return True
    return bool(output and not any(marker in output for marker in REVIEW_MARKERS))


def _week1_rhythm_checks(days: list[Any]) -> dict[str, Any]:
    planned_weekdays = [
        day for day in days if not _day_is_weekend(day) and _day_planned_hours(day) > 0
    ]
    review_only_days: list[int] = []
    progress_days: list[int] = []
    for day in planned_weekdays:
        slots = [slot for slot in _day_slots(day) if _slot_text(slot, "task")]
        if not slots:
            continue
        if any(_slot_has_progress(slot) for slot in slots):
            progress_days.append(_day_number(day))
        if all(_slot_is_review_only(slot) for slot in slots):
            review_only_days.append(_day_number(day))
    return {
        "planned_weekdays": len(planned_weekdays),
        "progress_weekdays": [d for d in progress_days if d],
        "review_only_weekdays": [d for d in review_only_days if d],
    }


def evaluate_outline_quality(
    params: UserParams,
    master: dict,
    week_plan: WeekPlan | dict,
    research: dict,
    concept_graph: dict | None = None,
    *,
    min_score: int = 76,
) -> dict:
    """确定性评估路径大纲质量，返回可展示/可追踪的质量报告。"""
    issues: list[str] = []
    checks: dict[str, Any] = {}
    score = 100

    weeks = (master or {}).get("weeks") or []
    checks["weeks_count"] = len(weeks)
    if len(weeks) != params.weeks:
        issues.append("周计划数量与用户设定周数不一致。")
        score -= 16

    weak_weeks = []
    for week in weeks:
        outcomes = _week_outcomes(week)
        if len(outcomes) < 2 or all(_is_generic_outcome(x) for x in outcomes):
            weak_weeks.append(int(week.get("week") or 0))
    checks["weeks_with_weak_outcomes"] = weak_weeks[:8]
    if weak_weeks:
        issues.append("部分周缺少可衡量产出：" + "、".join(f"W{x}" for x in weak_weeks[:6]) + "。")
        score -= min(24, len(weak_weeks) * 4)

    if isinstance(week_plan, WeekPlan):
        days = week_plan.days
        day_slots = [slot for day in days for slot in day.slots]
        output_slots = [slot for slot in day_slots if slot.output]
    else:
        days = (week_plan or {}).get("days") or []
        day_slots = [slot for day in days for slot in (day.get("slots") or [])]
        output_slots = [slot for slot in day_slots if slot.get("output")]
    checks["w1_days_count"] = len(days)
    checks["w1_slots_count"] = len(day_slots)
    checks["w1_output_slots"] = len(output_slots)
    rhythm = _week1_rhythm_checks(list(days))
    checks["w1_planned_weekdays"] = rhythm["planned_weekdays"]
    checks["w1_progress_weekdays"] = rhythm["progress_weekdays"]
    checks["w1_review_only_weekdays"] = rhythm["review_only_weekdays"]
    if len(days) < 7:
        issues.append("第一周没有完整拆成 7 天。")
        score -= 14
    if len(output_slots) < 3:
        issues.append("第一周产出点不足，用户难以感知每天学完留下什么。")
        score -= 10
    if rhythm["review_only_weekdays"]:
        days_label = "、".join(f"D{x}" for x in rhythm["review_only_weekdays"][:5])
        issues.append(f"第一周存在工作日整天复习/轻量消化：{days_label}，应改成微复习 + 新内容/练习推进。")
        score -= min(20, len(rhythm["review_only_weekdays"]) * 10)
    if rhythm["planned_weekdays"] >= 4 and len(rhythm["progress_weekdays"]) < max(3, rhythm["planned_weekdays"] - 1):
        issues.append("第一周工作日推进天数不足，节奏会显得像复习模板而不是学习路径。")
        score -= 12

    external_source_count = int(research.get("source_count") or 0)
    user_source_count = int(research.get("user_source_count") or 0)
    source_count = external_source_count + user_source_count
    query_plan = research.get("query_plan") or []
    available_refs = {
        str(source.get("ref_id"))
        for source in [
            *(research.get("sources") or []),
            *(research.get("user_sources") or []),
        ]
        if source.get("ref_id")
    }
    grounded_weeks = [
        int(week.get("week") or 0)
        for week in weeks
        if any(str(ref) in available_refs for ref in (week.get("source_refs") or []))
    ]
    checks["source_count"] = external_source_count
    checks["user_source_count"] = user_source_count
    checks["total_source_count"] = source_count
    checks["grounded_weeks"] = grounded_weeks
    checks["query_plan_count"] = len(query_plan)
    if not query_plan:
        issues.append("缺少检索计划，路径生成没有留下研究过程。")
        score -= 10
    required_grounded_weeks = min(params.weeks, max(2, params.weeks // 2))
    if source_count <= 0:
        issues.append("本次路径没有外部或用户知识库来源支撑，适合先作为草稿。")
        score -= 8
    elif len(grounded_weeks) < required_grounded_weeks:
        issues.append("研究来源未覆盖足够周大纲，证据与路径仍有脱节。")
        score -= 12

    concepts = (concept_graph or {}).get("concepts") or []
    graph_source = (concept_graph or {}).get("_source") or "not_generated"
    checks["concept_count"] = len(concepts)
    checks["concept_source"] = graph_source
    expected_concepts = max(8, min(30, params.weeks * 2))
    if len(concepts) < expected_concepts:
        issues.append(f"概念图谱偏薄，建议至少拆到 {expected_concepts} 个能力/知识节点。")
        score -= 14
    if graph_source == "fallback":
        issues.append("概念图谱使用线性兜底，后续应由教研 Agent 重建依赖关系。")
        score -= 6

    baseline = _clean_text(params.baseline_summary, 500)
    checks["baseline_chars"] = len(baseline)
    if len(baseline) < 20:
        issues.append("基线信息偏短，路径个性化依据不足。")
        score -= 8

    score = max(0, min(100, score))
    critical_failures = []
    if len(weeks) != params.weeks:
        critical_failures.append("weeks_count")
    if weak_weeks:
        critical_failures.append("measurable_outcomes")
    if rhythm["review_only_weekdays"]:
        critical_failures.append("weekday_rhythm")
    if source_count > 0 and len(grounded_weeks) < required_grounded_weeks:
        critical_failures.append("research_linkage")
    return {
        "version": "0.1",
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "passed": bool(score >= min_score and not critical_failures),
        "score": score,
        "min_score": min_score,
        "issues": issues[:10],
        "critical_failures": critical_failures,
        "checks": checks,
        "dimension_scores": _dimension_scores(
            source_count=source_count,
            query_plan_count=len(query_plan),
            weak_weeks=len(weak_weeks),
            output_slots=len(output_slots),
            planned_weekdays=int(rhythm["planned_weekdays"]),
            progress_weekdays=len(rhythm["progress_weekdays"]),
            review_only_weekdays=len(rhythm["review_only_weekdays"]),
            concept_count=len(concepts),
            expected_concepts=expected_concepts,
            baseline_chars=len(baseline),
        ),
    }


def build_linear_concept_graph(master: dict) -> dict:
    """不调 LLM 的概念图谱兜底，用于测试/离线生成。

    真实服务端生成会优先调用教研 Agent；这里保证即使离线，也能把周产出
    拆成可追踪节点，并写入 concepts.json。
    """
    concepts: list[dict] = []
    prev_id = ""
    idx = 0
    for week in (master or {}).get("weeks") or []:
        week_no = int(week.get("week") or 1)
        names = [
            _clean_text(x, 90)
            for x in (week.get("key_outcomes") or [])
            if _clean_text(x, 90) and "待" not in _clean_text(x, 90)
        ] or [_clean_text(week.get("title"), 90) or f"第 {week_no} 周目标"]
        for name in names:
            idx += 1
            cid = f"c{idx}"
            concepts.append(
                {
                    "id": cid,
                    "name": name,
                    "week": week_no,
                    "difficulty": min(10, 2 + (week_no - 1) // 2),
                    "prerequisites": [prev_id] if prev_id else [],
                    "sources": [],
                }
            )
            prev_id = cid
    return {
        "concepts": sanitize(concepts),
        "_source": "linear_deepbuild",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _dimension_scores(
    *,
    source_count: int,
    query_plan_count: int,
    weak_weeks: int,
    output_slots: int,
    planned_weekdays: int,
    progress_weekdays: int,
    review_only_weekdays: int,
    concept_count: int,
    expected_concepts: int,
    baseline_chars: int,
) -> dict[str, dict[str, Any]]:
    def clamp(v: float) -> int:
        return max(0, min(100, int(round(v))))

    return {
        "research_grounding": {
            "score": clamp(45 + source_count * 9 + query_plan_count * 5),
            "note": "是否形成检索计划，并把外部来源纳入路径依据。",
        },
        "structure_depth": {
            "score": clamp(92 - weak_weeks * 8),
            "note": "周目标是否有清晰递进与可衡量产出。",
        },
        "daily_actionability": {
            "score": clamp(42 + output_slots * 7 + progress_weekdays * 7 - review_only_weekdays * 15),
            "note": "第一周是否拆成每天能执行、能留下产物的学习任务。",
        },
        "schedule_fit": {
            "score": clamp(
                55
                + progress_weekdays * 9
                - review_only_weekdays * 18
                - max(0, planned_weekdays - progress_weekdays - 1) * 8
            ),
            "note": "学习节奏是否匹配用户的每日时长，工作日复习应嵌入推进任务而不是占满整天。",
        },
        "concept_coverage": {
            "score": clamp(35 + min(1.0, concept_count / max(1, expected_concepts)) * 60),
            "note": "是否拆出了足够的能力/知识节点，便于后续解锁和复盘。",
        },
        "personalization": {
            "score": clamp(45 + min(35, baseline_chars / 3)),
            "note": "是否利用了用户基线、偏好和目标，而不是通用大纲。",
        },
    }


def build_generation_workflow(
    research: dict,
    quality: dict,
    concept_graph: dict | None,
    refinement: dict | None = None,
) -> dict:
    """写入 master.json 的生成过程记录。"""
    return {
        "version": "0.1",
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "stages": [
            {
                "id": "conversation_profile",
                "title": "对话画像",
                "status": "done",
                "note": "从目标、基线、时间和偏好生成参数包。",
            },
            {
                "id": "outline_research",
                "title": "路径研究",
                "status": research.get("status") or "done",
                "note": (
                    f"{len(research.get('query_plan') or [])} 个检索意图，"
                    f"{research.get('source_count') or 0} 条外部来源，"
                    f"{research.get('user_source_count') or 0} 条用户知识库来源。"
                ),
            },
            {
                "id": "outline_refinement",
                "title": "证据化大纲",
                "status": (refinement or {}).get("status") or "not_run",
                "note": f"共执行 {len((refinement or {}).get('attempts') or [])} 轮大纲生成/修复。",
            },
            {
                "id": "concept_graph",
                "title": "概念图谱",
                "status": (concept_graph or {}).get("_source") or "not_generated",
                "note": f"{len((concept_graph or {}).get('concepts') or [])} 个节点。",
            },
            {
                "id": "quality_gate",
                "title": "质量审查",
                "status": "passed" if quality.get("passed") else "needs_review",
                "note": f"路径质检 {quality.get('score')}/{quality.get('min_score')}。",
            },
        ],
    }


__all__ = [
    "build_generation_workflow",
    "build_linear_concept_graph",
    "build_outline_research",
    "evaluate_outline_quality",
    "OutlineRefinementError",
    "refine_master_from_research",
]
