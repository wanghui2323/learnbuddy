"""LessonFlow 运行时适配层。

把旧版 lesson（explain/cards/practice/produce）增量升级成统一的学习元素序列。
这里不做数据库迁移，也不删除旧字段；前端和后台可以优先消费 `_lesson_flow`，
旧缓存读取时即时补齐即可。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_ALLOWED_TYPES = {
    "source",
    "think",
    "explain",
    "visual",
    "compare",
    "example",
    "practice",
    "produce",
    "check",
    "reflect",
    "goal",
    "next",
}

_TYPE_STAGE = {
    "goal": "先想",
    "source": "依据",
    "think": "先想",
    "explain": "看懂",
    "visual": "看懂",
    "compare": "看懂",
    "example": "例子",
    "practice": "练习",
    "produce": "输出",
    "check": "自检",
    "reflect": "收束",
    "next": "下一步",
}


def _clean_text(value: Any, limit: int = 2400) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_clean_text(x, 500) for x in parts if _clean_text(x, 500))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_diagram(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    nodes = []
    for node in value.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        item = {
            "id": _clean_text(node.get("id"), 80),
            "title": _clean_text(node.get("title") or node.get("name"), 140),
            "desc": _clean_text(node.get("desc") or node.get("note") or node.get("body"), 520),
            "kind": _clean_text(node.get("kind") or node.get("type"), 40),
        }
        if item["title"] or item["desc"]:
            nodes.append({k: v for k, v in item.items() if v})
    edges = []
    for edge in value.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        item = {
            "from": _clean_text(edge.get("from") or edge.get("source"), 80),
            "to": _clean_text(edge.get("to") or edge.get("target"), 80),
            "label": _clean_text(edge.get("label") or edge.get("relation"), 140),
        }
        if item["from"] and item["to"]:
            edges.append({k: v for k, v in item.items() if v})
    out = {
        "title": _clean_text(value.get("title"), 160),
        "caption": _clean_text(value.get("caption"), 520),
        "nodes": nodes[:8],
        "edges": edges[:12],
    }
    return {k: v for k, v in out.items() if v}


def _normalize_card_type(value: Any) -> str:
    ctype = _clean_text(value, 40).lower()
    if ctype in _ALLOWED_TYPES:
        return ctype
    return "explain"


def _quality_summary(lesson: dict, element_type: str) -> dict:
    quality = lesson.get("_quality") or {}
    dimensions = quality.get("dimension_scores") or {}
    mapping = {
        "goal": "pedagogy",
        "source": "source_coverage",
        "think": "interactivity",
        "practice": "interactivity",
        "produce": "actionability",
        "check": "actionability",
        "reflect": "rhythm",
    }
    key = mapping.get(element_type, "depth")
    dim = dimensions.get(key) or {}
    return {
        "score": dim.get("score"),
        "dimension": key,
        "note": dim.get("note", ""),
    }


def _element_from_card(card: dict, *, index: int, lesson: dict) -> dict:
    ctype = _normalize_card_type(card.get("type"))
    title = _clean_text(card.get("title") or _TYPE_STAGE.get(ctype) or "学习环节", 180)
    body = _clean_text(card.get("body") or card.get("content"), 3200)
    element = {
        "id": f"{index + 1:02d}-{ctype}-{_stable_id(ctype, title, body)}",
        "type": ctype,
        "stage": _TYPE_STAGE.get(ctype, "学习"),
        "title": title,
        "body": body,
        "status": "ready",
        "reviewable": True,
        "quality": _quality_summary(lesson, ctype),
    }
    items = _as_list(card.get("items"))
    if items:
        element["items"] = items[:10]
    options = [_clean_text(x, 600) for x in _as_list(card.get("options")) if _clean_text(x, 600)]
    if options:
        element["options"] = options[:6]
        try:
            answer = int(card.get("answer"))
        except (TypeError, ValueError):
            answer = -1
        if 0 <= answer < len(options):
            element["answer"] = answer
        why = _clean_text(card.get("why"), 1200)
        if why:
            element["why"] = why
    for key in ("completion_rule", "expected_signal", "free_input_prompt", "answer_schema"):
        value = _clean_text(card.get(key), 1200)
        if value:
            element[key] = value
    source_refs = _as_list(card.get("source_refs"))
    if source_refs:
        element["source_refs"] = source_refs[:8]
    diagram = _normalize_diagram(card.get("diagram"))
    if diagram:
        element["diagram"] = diagram
    return element


def _source_element(source_pack: dict | None, sources: list[dict], lesson: dict) -> dict | None:
    if not sources and not source_pack:
        return None
    status = _clean_text((source_pack or {}).get("status") or "unknown", 40)
    note = _clean_text((source_pack or {}).get("note"), 700)
    body = "本节内容会优先依据这些资料，关键事实仍建议结合原文核对。"
    if status in {"disabled", "empty", "empty_query", "error"} and note:
        body = note
    return {
        "id": f"00-source-{_stable_id(status, note, sources)}",
        "type": "source",
        "stage": "依据",
        "title": "本节依据",
        "body": body,
        "items": sources[:8],
        "status": "ready",
        "reviewable": True,
        "quality": _quality_summary(lesson, "source"),
        "source_pack_status": status,
    }


def _practice_elements(lesson: dict, start_index: int) -> list[dict]:
    out: list[dict] = []
    for offset, question in enumerate(lesson.get("practice") or []):
        if not isinstance(question, dict):
            continue
        q = _clean_text(question.get("q"), 1600)
        options = [_clean_text(x, 600) for x in _as_list(question.get("options")) if _clean_text(x, 600)]
        if not q or len(options) < 2:
            continue
        try:
            answer = int(question.get("answer"))
        except (TypeError, ValueError):
            answer = -1
        element = {
            "id": f"{start_index + offset + 1:02d}-practice-{_stable_id(q, options)}",
            "type": "practice",
            "stage": "练习",
            "title": f"判断练习 {offset + 1}",
            "body": q,
            "options": options,
            "status": "ready",
            "reviewable": True,
            "quality": _quality_summary(lesson, "practice"),
        }
        if 0 <= answer < len(options):
            element["answer"] = answer
        why = _clean_text(question.get("why"), 1200)
        if why:
            element["why"] = why
        out.append(element)
    return out


def _reflect_element(lesson: dict, index: int) -> dict:
    quality = lesson.get("_quality") or {}
    score = quality.get("score")
    issues = quality.get("issues") or []
    body = "完成本节后，用自己的话复盘：我学会了什么、还有哪里不确定、下一次要怎么复习。"
    if isinstance(score, int):
        body += f" 本节内容质量门得分 {score}/100。"
    return {
        "id": f"{index + 1:02d}-reflect-{_stable_id(lesson.get('topic'), score)}",
        "type": "reflect",
        "stage": "收束",
        "title": "收束与复盘",
        "body": body,
        "items": [str(x) for x in issues[:3]],
        "status": "ready",
        "reviewable": True,
        "quality": _quality_summary(lesson, "reflect"),
    }


def build_lesson_flow(
    lesson: dict,
    *,
    source_pack: dict | None = None,
    generated_at: str = "",
) -> dict:
    """从兼容 lesson 构建 LessonFlow，不修改入参。"""
    if not isinstance(lesson, dict):
        lesson = {}
    topic = _clean_text(lesson.get("topic") or (lesson.get("explain") or {}).get("title") or "本节学习", 180)
    sources = lesson.get("_sources") or []
    source_pack = source_pack or lesson.get("_source_pack") or {}
    design = lesson.get("design") if isinstance(lesson.get("design"), dict) else {}
    elements: list[dict] = []

    src_el = _source_element(source_pack if isinstance(source_pack, dict) else {}, sources, lesson)
    if src_el:
        elements.append(src_el)

    for card in lesson.get("cards") or []:
        if not isinstance(card, dict):
            continue
        el = _element_from_card(card, index=len(elements), lesson=lesson)
        if el["type"] == "source" and src_el:
            continue
        elements.append(el)

    has_produce = any(el.get("type") == "produce" for el in elements)
    produce = lesson.get("produce") or {}
    if isinstance(produce, dict) and _clean_text(produce.get("task")) and not has_produce:
        elements.append(
            {
                "id": f"{len(elements) + 1:02d}-produce-{_stable_id(produce.get('task'))}",
                "type": "produce",
                "stage": "输出",
                "title": "轮到你输出",
                "body": _clean_text(produce.get("task"), 2400),
                "items": [_clean_text(produce.get("hint"), 1200)] if _clean_text(produce.get("hint")) else [],
                "status": "ready",
                "reviewable": True,
                "quality": _quality_summary(lesson, "produce"),
            }
        )

    elements.extend(_practice_elements(lesson, len(elements)))
    elements.append(_reflect_element(lesson, len(elements)))

    return {
        "schema_version": 2,
        "contract_version": "lesson-loop.v0.46",
        "id": f"flow-{_stable_id(topic, generated_at)}",
        "topic": topic,
        "objective": _clean_text(design.get("outcome") or (lesson.get("explain") or {}).get("summary"), 600),
        "design": {
            "learning_question": _clean_text(design.get("learning_question"), 700),
            "outcome": _clean_text(design.get("outcome"), 700),
            "main_thread": _clean_text(design.get("main_thread"), 700),
            "boundary": _clean_text(design.get("boundary"), 700),
            "key_steps": [
                {
                    "name": _clean_text(s.get("name"), 140),
                    "purpose": _clean_text(s.get("purpose"), 360),
                    "learner_action": _clean_text(s.get("learner_action"), 360),
                    "stuck_point": _clean_text(s.get("stuck_point"), 360),
                }
                for s in (design.get("key_steps") or [])[:5]
                if isinstance(s, dict)
            ],
            "quick_checks": [_clean_text(x, 220) for x in (design.get("quick_checks") or [])[:4] if _clean_text(x, 220)],
        } if design else {},
        "generated_at": generated_at,
        "source_pack_status": _clean_text((source_pack or {}).get("status"), 40),
        "quality_score": (lesson.get("_quality") or {}).get("score"),
        "quality_passed": (lesson.get("_quality") or {}).get("passed"),
        "elements": elements[:16],
    }


def attach_lesson_flow(
    lesson: dict,
    *,
    source_pack: dict | None = None,
    generated_at: str = "",
) -> dict:
    """给 lesson 增量挂载 `_lesson_flow`，保留旧字段向后兼容。"""
    if not isinstance(lesson, dict):
        return lesson
    lesson["_lesson_flow"] = build_lesson_flow(
        lesson,
        source_pack=source_pack,
        generated_at=generated_at or str(lesson.get("_generated_at") or ""),
    )
    return lesson


__all__ = ["attach_lesson_flow", "build_lesson_flow"]
