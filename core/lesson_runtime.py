"""学练单元运行态持久化。

V0.46 把“内容生成结果”和“用户在本节里的学习状态”拆开保存。
这样课程缓存可以复用，用户点击选项、写下产出、回看步骤的状态也不会因为刷新丢失。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def lesson_runtime_key(topic: str) -> str:
    """用 topic 生成稳定文件名；不改变旧 lesson 缓存路径。"""
    raw = str(topic or "lesson").strip() or "lesson"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def lesson_runtime_path(inst_dir: Path, topic: str) -> Path:
    return inst_dir / "lesson_runtime" / f"{lesson_runtime_key(topic)}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def empty_lesson_runtime(topic: str) -> dict:
    return {
        "schema_version": 1,
        "topic": _clean_text(topic, 240),
        "status": "started",
        "current_step": 0,
        "furthest_step": 0,
        "completed_steps": [],
        "selected_options": {},
        "free_inputs": {},
        "outputs": {},
        "updated_at": "",
    }


def load_lesson_runtime(inst_dir: Path, topic: str) -> dict:
    path = lesson_runtime_path(inst_dir, topic)
    if not path.exists():
        return empty_lesson_runtime(topic)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_lesson_runtime(topic)
    if not isinstance(data, dict):
        return empty_lesson_runtime(topic)
    runtime = empty_lesson_runtime(topic)
    runtime.update({k: v for k, v in data.items() if k in runtime})
    runtime["selected_options"] = _as_dict(runtime.get("selected_options"))
    runtime["free_inputs"] = _as_dict(runtime.get("free_inputs"))
    runtime["outputs"] = _as_dict(runtime.get("outputs"))
    runtime["completed_steps"] = [
        _clean_text(x, 160)
        for x in _as_list(runtime.get("completed_steps"))
        if _clean_text(x, 160)
    ]
    return runtime


def _merge_text_map(old: dict, new: Any) -> dict:
    merged = dict(old)
    for key, value in _as_dict(new).items():
        clean_key = _clean_text(key, 120)
        if not clean_key:
            continue
        if isinstance(value, dict):
            old_child = merged.get(clean_key)
            merged[clean_key] = {
                **(old_child if isinstance(old_child, dict) else {}),
                **value,
            }
        else:
            text = _clean_text(value)
            if text:
                merged[clean_key] = text
    return merged


def merge_lesson_runtime(topic: str, existing: dict, patch: dict) -> dict:
    runtime = empty_lesson_runtime(topic)
    runtime.update(existing if isinstance(existing, dict) else {})

    try:
        current_step = int(patch.get("current_step", runtime.get("current_step", 0)))
    except (TypeError, ValueError):
        current_step = int(runtime.get("current_step", 0) or 0)
    try:
        furthest_step = int(patch.get("furthest_step", runtime.get("furthest_step", 0)))
    except (TypeError, ValueError):
        furthest_step = int(runtime.get("furthest_step", 0) or 0)
    runtime["current_step"] = max(0, current_step)
    runtime["furthest_step"] = max(runtime["current_step"], max(0, furthest_step))

    old_completed = [
        _clean_text(x, 160)
        for x in _as_list(runtime.get("completed_steps"))
        if _clean_text(x, 160)
    ]
    new_completed = [
        _clean_text(x, 160)
        for x in _as_list(patch.get("completed_steps"))
        if _clean_text(x, 160)
    ]
    seen = set()
    runtime["completed_steps"] = [
        x for x in old_completed + new_completed if not (x in seen or seen.add(x))
    ][:80]

    runtime["selected_options"] = _merge_text_map(runtime.get("selected_options") or {}, patch.get("selected_options"))
    runtime["free_inputs"] = _merge_text_map(runtime.get("free_inputs") or {}, patch.get("free_inputs"))
    runtime["outputs"] = _merge_text_map(runtime.get("outputs") or {}, patch.get("outputs"))
    if _clean_text(patch.get("status"), 40):
        runtime["status"] = _clean_text(patch.get("status"), 40)
    runtime["topic"] = _clean_text(topic, 240)
    runtime["updated_at"] = _now()
    runtime["schema_version"] = 1
    return runtime


def save_lesson_runtime(inst_dir: Path, topic: str, patch: dict) -> dict:
    existing = load_lesson_runtime(inst_dir, topic)
    runtime = merge_lesson_runtime(topic, existing, patch if isinstance(patch, dict) else {})
    path = lesson_runtime_path(inst_dir, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    return runtime


__all__ = [
    "empty_lesson_runtime",
    "lesson_runtime_key",
    "lesson_runtime_path",
    "load_lesson_runtime",
    "merge_lesson_runtime",
    "save_lesson_runtime",
]
