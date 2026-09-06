"""路径级 Loop 状态层（V0.47）。

这一层只保存"路径推进事实"，不改 master/W*.json，也不搬迁旧学习记录。
旧数据仍以 lesson_completions 为准；path_loop.json 丢失时可以重新推导。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
CONTRACT_VERSION = "path-loop.v0.47"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    return default


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{secrets.token_hex(5)}.tmp"
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def path_loop_path(inst_dir: Path) -> Path:
    return inst_dir / "path_loop.json"


def _clean_task(task: str) -> str:
    text = str(task or "").strip()
    for pat in (
        r"^推进新内容[：:]\s*",
        r"^轻量消化[：:]\s*",
        r"^巩固 \+ 输出[：:]\s*",
        r"^实战 \+ 错题[：:]\s*",
    ):
        text = re.sub(pat, "", text).strip()
    return text


def _primary_slot(day: dict) -> dict:
    slots = day.get("slots") if isinstance(day.get("slots"), list) else []
    return slots[0] if slots and isinstance(slots[0], dict) else {}


def _day_title(day: dict, fallback: str) -> str:
    if day.get("title") or day.get("topic"):
        return str(day.get("title") or day.get("topic")).strip()
    task = _clean_task(str(_primary_slot(day).get("task") or ""))
    return task or fallback


def _day_topic(week_title: str, day: dict) -> str:
    explicit = str(day.get("topic") or day.get("title") or "").strip()
    if explicit:
        return explicit
    day_no = _safe_int(day.get("day"), 1)
    if day_no == 1:
        return week_title
    title = _day_title(day, "学习任务")
    short = title[:34].strip()
    return f"{week_title} · D{day_no} {short}".strip()


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _week_plans(inst_dir: Path) -> dict[int, dict]:
    plans: dict[int, dict] = {}
    for path in inst_dir.glob("W*.json"):
        suffix = path.stem[1:]
        if not suffix.isdigit():
            continue
        plan = _read_json(path, {})
        if isinstance(plan, dict):
            plans[int(suffix)] = plan
    return plans


def ordered_units(inst_dir: Path) -> list[dict]:
    """只把已生成 Wn.json 的周展开成可执行单元。"""
    master = _read_json(inst_dir / "master.json", {})
    weeks = master.get("weeks") if isinstance(master.get("weeks"), list) else []
    plans = _week_plans(inst_dir)
    units: list[dict] = []

    for week in weeks:
        if not isinstance(week, dict):
            continue
        week_no = _safe_int(week.get("week"), len(units) + 1)
        week_title = str(week.get("title") or f"第 {week_no} 周").strip()
        plan = plans.get(week_no) or {}
        days = plan.get("days") if isinstance(plan.get("days"), list) else []
        if not days:
            continue
        for index, day in enumerate(days, 1):
            if not isinstance(day, dict):
                continue
            day_no = _safe_int(day.get("day"), index)
            slot = _primary_slot(day)
            title = _day_title(day, week_title)
            topic = _day_topic(week_title, day)
            legacy_topic = f"{week_title} · D{day_no} {title}".strip()
            aliases = {topic, legacy_topic, title}
            if day_no == 1:
                aliases.add(week_title)
            units.append(
                {
                    "unit_id": f"w{week_no}-d{day_no}",
                    "week": week_no,
                    "day": day_no,
                    "topic": topic,
                    "title": title,
                    "date": str(day.get("date") or ""),
                    "weekday": str(day.get("weekday") or ""),
                    "time": str(slot.get("time") or ""),
                    "task": str(slot.get("task") or ""),
                    "output": str(slot.get("output") or ""),
                    "aliases": sorted(a for a in aliases if a),
                }
            )
    return units


def _normal_events(saved: dict) -> list[dict]:
    events = saved.get("events") if isinstance(saved.get("events"), list) else []
    out = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        key = str(event.get("idempotency_key") or event.get("event_id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(event)
    return out[-500:]


def _find_unit(units: list[dict], *, topic: str = "", week: int | None = None, unit_id: str = "") -> dict | None:
    if unit_id:
        for unit in units:
            if unit.get("unit_id") == unit_id:
                return unit
    topic_norm = str(topic or "").strip()
    if not topic_norm:
        return None
    week_no = _safe_int(week, 0) if week is not None else None
    candidates = [u for u in units if week_no is None or _safe_int(u.get("week")) == week_no]
    for unit in candidates:
        if topic_norm == unit.get("topic"):
            return unit
    for unit in candidates:
        if topic_norm in set(unit.get("aliases") or []):
            return unit
    return None


def _completion_ids(units: list[dict], events: list[dict], completed_topics: Iterable[str]) -> set[str]:
    topic_set = {str(t or "").strip() for t in completed_topics if str(t or "").strip()}
    completed: set[str] = set()
    for event in events:
        if event.get("kind") != "lesson_completed":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        unit_id = str(payload.get("unit_id") or "").strip()
        if unit_id:
            completed.add(unit_id)
            continue
        unit = _find_unit(units, topic=str(payload.get("topic") or ""), week=payload.get("week"))
        if unit:
            completed.add(str(unit["unit_id"]))

    for unit in units:
        aliases = set(unit.get("aliases") or [])
        aliases.add(str(unit.get("topic") or ""))
        if topic_set.intersection(aliases):
            completed.add(str(unit["unit_id"]))
    return completed


def _decorate_units(units: list[dict], completed_ids: set[str]) -> tuple[list[dict], dict | None, dict | None]:
    current = None
    next_unit = None
    out = []
    for unit in units:
        copied = {k: v for k, v in unit.items() if k != "aliases"}
        copied["completed"] = unit["unit_id"] in completed_ids
        if current is None and not copied["completed"]:
            current = copied
            copied["status"] = "current"
        elif copied["completed"]:
            copied["status"] = "done"
        else:
            copied["status"] = "locked"
        out.append(copied)
    if current:
        try:
            idx = next(i for i, u in enumerate(out) if u["unit_id"] == current["unit_id"])
            next_unit = out[idx + 1] if idx + 1 < len(out) else None
        except StopIteration:
            next_unit = None
    return out, current, next_unit


def load_path_loop_state(
    inst_dir: Path,
    user_id: int,
    completed_topics: Iterable[str] | None = None,
    *,
    persist: bool = True,
) -> dict:
    """读取/推导路径级状态。completed_topics 通常来自 SQLite 学习记录。"""
    saved = _read_json(path_loop_path(inst_dir), {})
    if not isinstance(saved, dict):
        saved = {}
    units = ordered_units(inst_dir)
    events = _normal_events(saved)
    completed_ids = _completion_ids(units, events, completed_topics or [])
    decorated, current, next_unit = _decorate_units(units, completed_ids)
    master = _read_json(inst_dir / "master.json", {})
    total_weeks = len((master or {}).get("weeks") or [])
    generated_weeks = sorted(_week_plans(inst_dir))
    all_generated = bool(total_weeks and len(generated_weeks) >= total_weeks)
    all_generated_units_done = bool(decorated and all(u.get("completed") for u in decorated))
    if all_generated and all_generated_units_done:
        status = "completed"
    elif decorated and current is None:
        status = "awaiting_self_check"
    else:
        status = "active"
    pre_generation = saved.get("pre_generation") if isinstance(saved.get("pre_generation"), dict) else {}
    week_gate = saved.get("week_gate") if isinstance(saved.get("week_gate"), dict) else {}
    state = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "user_id": user_id,
        "status": status,
        "total_weeks": total_weeks,
        "generated_weeks": generated_weeks,
        "units": decorated,
        "completed_unit_ids": sorted(completed_ids),
        "completed_topics": sorted({str(t) for t in (completed_topics or []) if str(t).strip()}),
        "current_unit": current,
        "next_unit": next_unit,
        "pre_generation": pre_generation,
        "week_gate": week_gate,
        "events": events,
        "updated_at": _now(),
    }
    if persist:
        _write_json(path_loop_path(inst_dir), state)
    return state


def _append_event(state: dict, event: dict) -> tuple[dict, bool]:
    events = _normal_events(state)
    key = str(event.get("idempotency_key") or "")
    if key and any(str(e.get("idempotency_key") or "") == key for e in events):
        return state, False
    events.append(event)
    state["events"] = events[-500:]
    return state, True


def record_path_event(
    inst_dir: Path,
    user_id: int,
    *,
    kind: str,
    payload: dict,
    idempotency_key: str,
    completed_topics: Iterable[str] | None = None,
) -> dict:
    state = load_path_loop_state(inst_dir, user_id, completed_topics, persist=False)
    event = {
        "kind": kind,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "created_at": _now(),
    }
    state, _ = _append_event(state, event)
    _write_json(path_loop_path(inst_dir), state)
    return load_path_loop_state(inst_dir, user_id, completed_topics)


def record_lesson_completed(
    inst_dir: Path,
    user_id: int,
    *,
    topic: str,
    week: int | None,
    summary: dict | None = None,
    completed_topics: Iterable[str] | None = None,
) -> dict:
    units = ordered_units(inst_dir)
    unit = _find_unit(units, topic=topic, week=week)
    payload = {
        "topic": topic,
        "week": week,
        "unit_id": unit.get("unit_id") if unit else "",
        "summary": summary or {},
    }
    key = f"lesson_completed:{user_id}:{payload['unit_id'] or topic}"
    topics = set(completed_topics or [])
    if topic:
        topics.add(topic)
    return record_path_event(
        inst_dir,
        user_id,
        kind="lesson_completed",
        payload=payload,
        idempotency_key=key,
        completed_topics=topics,
    )


def week_completion(state: dict, week: int) -> dict:
    """返回某个已生成周的真实日单元完成情况。"""
    week_no = _safe_int(week, 0)
    units = [u for u in (state.get("units") or []) if _safe_int(u.get("week")) == week_no]
    done = sum(1 for unit in units if unit.get("completed"))
    return {
        "week": week_no,
        "done": done,
        "total": len(units),
        "completed": bool(units and done == len(units)),
    }


def _update_master_week_status(inst_dir: Path, completed_week: int, active_week: int | None) -> None:
    master_path = inst_dir / "master.json"
    master = _read_json(master_path, {})
    changed = False
    for week in (master or {}).get("weeks") or []:
        week_no = _safe_int(week.get("week"), 0)
        desired = None
        if week_no == completed_week:
            desired = "completed"
        elif active_week is not None and week_no == active_week:
            desired = "active"
        elif week_no > completed_week and (active_week is None or week_no > active_week):
            desired = "pending"
        if desired and week.get("status") != desired:
            week["status"] = desired
            changed = True
    if changed:
        master["updated_at"] = _now()
        _write_json(master_path, master)


def advance_after_self_check(
    inst_dir: Path,
    user_id: int,
    *,
    week: int,
    passed: bool,
    passed_count: int,
    completed_topics: Iterable[str] | None = None,
) -> dict:
    """周完成 + 自检通过后生成下一周；未通过只记录阻断事实。"""
    week_no = _safe_int(week, 0)
    state = load_path_loop_state(inst_dir, user_id, completed_topics, persist=False)
    completion = week_completion(state, week_no)
    if not completion["total"]:
        raise ValueError(f"W{week_no} 还没有生成可执行的日计划。")
    if not completion["completed"]:
        raise ValueError(
            f"W{week_no} 还有 {completion['total'] - completion['done']} 天未完成，暂不能提交周自检。"
        )

    master = _read_json(inst_dir / "master.json", {})
    total_weeks = len((master or {}).get("weeks") or [])
    next_week = week_no + 1 if week_no < total_weeks else None
    gate = {
        "week": week_no,
        "passed": bool(passed),
        "passed_count": _safe_int(passed_count, 0),
        "status": "passed" if passed else "blocked",
        "next_week": next_week,
        "updated_at": _now(),
    }
    state["week_gate"] = gate
    event = {
        "kind": "weekly_self_check",
        "payload": gate,
        "idempotency_key": f"weekly_self_check:{user_id}:{week_no}:{_safe_int(passed_count, 0)}",
        "created_at": _now(),
    }
    state, _ = _append_event(state, event)
    _write_json(path_loop_path(inst_dir), state)

    if not passed:
        return load_path_loop_state(inst_dir, user_id, completed_topics)
    if next_week is None:
        _update_master_week_status(inst_dir, week_no, None)
        gate["status"] = "path_completed"
        state["week_gate"] = gate
        _write_json(path_loop_path(inst_dir), state)
        return load_path_loop_state(inst_dir, user_id, completed_topics)

    from core.generator import ensure_week_plan

    _, created = ensure_week_plan(inst_dir, next_week)
    _update_master_week_status(inst_dir, week_no, next_week)
    unlocked = load_path_loop_state(inst_dir, user_id, completed_topics, persist=False)
    unlocked["week_gate"] = {
        **gate,
        "status": "unlocked",
        "plan_created": created,
    }
    unlocked, _ = _append_event(
        unlocked,
        {
            "kind": "week_unlocked",
            "payload": {"from_week": week_no, "week": next_week, "plan_created": created},
            "idempotency_key": f"week_unlocked:{user_id}:{next_week}",
            "created_at": _now(),
        },
    )
    _write_json(path_loop_path(inst_dir), unlocked)
    return load_path_loop_state(inst_dir, user_id, completed_topics)


def set_pregeneration_state(
    inst_dir: Path,
    user_id: int,
    *,
    unit: dict | None,
    status: str,
    job_id: str = "",
    detail: str = "",
    error: str = "",
    stage_id: str = "",
    stage_label: str = "",
    progress_percent: int = 0,
    completed_topics: Iterable[str] | None = None,
) -> dict:
    state = load_path_loop_state(inst_dir, user_id, completed_topics, persist=False)
    state["pre_generation"] = {
        "unit_id": unit.get("unit_id", "") if unit else "",
        "topic": unit.get("topic", "") if unit else "",
        "week": unit.get("week") if unit else None,
        "day": unit.get("day") if unit else None,
        "status": status,
        "job_id": job_id,
        "stage_id": stage_id,
        "stage_label": stage_label,
        "progress_percent": max(0, min(100, int(progress_percent or 0))),
        "detail": detail,
        "error": error,
        "updated_at": _now(),
    }
    _write_json(path_loop_path(inst_dir), state)
    return load_path_loop_state(inst_dir, user_id, completed_topics)


__all__ = [
    "advance_after_self_check",
    "load_path_loop_state",
    "ordered_units",
    "path_loop_path",
    "record_lesson_completed",
    "record_path_event",
    "set_pregeneration_state",
    "week_completion",
]
