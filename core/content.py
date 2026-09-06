"""LearnBuddy 内容生成 workflow。

职责：
- 输入：领域 / 目标 / 基线 / 本节主题
- 输出：一小节"学 + 练"微课（讲解卡 + N 道点选练习），结构化 dict

这是"学练单元"的真实内容来源——点一个知识点时实时调 DeepSeek 现场生成，
而不是写死。后续可拆出"匹配 / 质检"子 agent，这里先把端到端跑通。

使用：
    from core.content import generate_lesson
    lesson = generate_lesson(domain="日语 N3", target="...", topic="～たことがある", baseline="零基础")
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from core.content_quality import evaluate_lesson_quality, format_quality_issues
from core.llm_client import LLMClient, LLMError, get_default_client, get_reasoner_model
from core.schemas import Message
from core.tracing import mark_fallback, trace_llm

PROMPTS_DIR = Path(__file__).parent / "prompts"
LESSON_PROMPT_PATH = PROMPTS_DIR / "lesson.md"
LESSON_REVIEW_PROMPT_PATH = PROMPTS_DIR / "lesson_review.md"
LESSON_DESIGN_PROMPT_PATH = PROMPTS_DIR / "lesson_design.md"
LESSON_WRITE_PROMPT_PATH = PROMPTS_DIR / "lesson_write.md"
LESSON_SECTION_PROMPT_PATH = PROMPTS_DIR / "lesson_section.md"
LESSON_EXPAND_PROMPT_PATH = PROMPTS_DIR / "lesson_expand.md"
LESSON_CRITIC_PROMPT_PATH = PROMPTS_DIR / "lesson_critic.md"
LESSON_REVISE_PROMPT_PATH = PROMPTS_DIR / "lesson_revise.md"

_JSON_OBJECT_PATTERN = re.compile(r"\{[\s\S]*\}", re.DOTALL)
_JSON_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.DOTALL | re.I)
_CARD_TYPES = {
    "goal",
    "source",
    "think",
    "explain",
    "visual",
    "compare",
    "example",
    "practice",
    "produce",
    "check",
    "next",
}
_EXPANSION_KINDS = {
    "mechanism",
    "boundary",
    "contrast",
    "worked_example",
    "counterexample",
    "common_mistake",
    "source_evidence",
    "advanced",
}


def _load_prompt() -> str:
    if not LESSON_PROMPT_PATH.exists():
        raise FileNotFoundError(f"找不到内容生成 prompt: {LESSON_PROMPT_PATH}")
    return LESSON_PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json_object(text: str) -> Optional[str]:
    """从真实模型回复里尽量稳地抠出第一个完整 JSON object。

    DeepSeek 偶尔会把嵌套 JSON 包在 ```json 代码块里；原来的非贪婪正则会
    截到第一个 `}` 就停，导致课设/评审解析失败并掉回 legacy。
    """
    stripped = text.strip()
    candidates = [stripped]
    candidates.extend(m.group(1).strip() for m in _JSON_CODE_BLOCK_PATTERN.finditer(text))
    m = _JSON_OBJECT_PATTERN.search(text)
    if m:
        candidates.append(m.group(0))

    decoder = json.JSONDecoder()
    for candidate in candidates:
        start = candidate.find("{")
        while start >= 0:
            try:
                value, end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                start = candidate.find("{", start + 1)
                continue
            if isinstance(value, dict):
                return candidate[start : start + end].strip()
            start = candidate.find("{", start + 1)
    return None


def _fallback_lesson(topic: str) -> dict:
    """LLM 失败时的兜底，保证前端不崩。"""
    return {
        "topic": topic,
        "_fallback": True,
        "explain": {
            "title": topic,
            "summary": "内容生成暂时不可用（LLM 调用失败），请稍后在本节点击重试。",
            "points": ["稍后重试即可重新生成本节讲解与练习。"],
            "examples": [{"text": "（暂无）", "note": ""}],
            "tip": "若反复失败，检查网络与 DeepSeek API key。",
        },
        "practice": [],
    }


def _clean_str(value, limit: int = 2000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clean_items(items) -> list:
    if isinstance(items, (str, dict)):
        items = [items]
    clean = []
    for item in items or []:
        if isinstance(item, dict):
            d = {
                str(k).strip(): _clean_str(v, 1200)
                for k, v in item.items()
                if str(k).strip() and _clean_str(v)
            }
            if d:
                clean.append(d)
        else:
            text = _clean_str(item, 1200)
            if text:
                clean.append(text)
    return clean


def _clean_expansions(expansions) -> list[dict]:
    """规整卡片内的可展开信息层，承载比首屏正文更长的机制/边界/例子。"""
    if isinstance(expansions, dict):
        expansions = [expansions]
    clean: list[dict] = []
    for block in expansions or []:
        if not isinstance(block, dict):
            continue
        kind = _clean_str(block.get("kind") or block.get("type"), 40).lower()
        if kind not in _EXPANSION_KINDS:
            kind = "advanced"
        item = {
            "kind": kind,
            "title": _clean_str(block.get("title"), 140),
            "body": _clean_str(block.get("body") or block.get("content"), 5000),
        }
        items = _clean_items(block.get("items") or block.get("steps"))
        if items:
            item["items"] = items[:10]
        source_refs = _clean_items(block.get("source_refs"))
        if source_refs:
            item["source_refs"] = source_refs[:6]
        if item["title"] or item["body"] or item.get("items"):
            clean.append(item)
    return clean[:6]


def _clean_activity_plan(plan) -> list[dict]:
    """规整学习活动链：用于让一节内容支撑 60–120 分钟的真实学习时段。"""
    if isinstance(plan, dict):
        plan = [plan]
    clean: list[dict] = []
    for block in plan or []:
        if not isinstance(block, dict):
            continue
        try:
            minutes = int(float(block.get("minutes") or block.get("duration_minutes") or 0))
        except (TypeError, ValueError):
            minutes = 0
        item = {
            "type": _clean_str(block.get("type") or block.get("kind"), 60).lower(),
            "title": _clean_str(block.get("title") or block.get("name"), 160),
            "minutes": max(0, min(240, minutes)),
            "task": _clean_str(block.get("task") or block.get("body") or block.get("instruction"), 1600),
        }
        items = _clean_items(block.get("items") or block.get("steps") or block.get("checks"))
        if items:
            item["items"] = items[:8]
        if item["type"] or item["title"] or item["task"] or item.get("items"):
            clean.append({k: v for k, v in item.items() if v or k == "minutes"})
    return clean[:10]


def _clean_rubric(rubric) -> list:
    return _clean_items(rubric)[:8]


def _clean_session_contract(contract: Optional[dict]) -> dict:
    if not isinstance(contract, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in ("label", "mode", "source", "pace"):
        value = _clean_str(contract.get(key), 120)
        if value:
            clean[key] = value
    for key in ("target_minutes", "min_estimated_minutes"):
        try:
            value = int(float(contract.get(key)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            clean[key] = max(1, min(360, value))
    required = _clean_items(contract.get("required_activity_types"))
    if required:
        clean["required_activity_types"] = required[:8]
    return clean


def _format_session_contract(contract: Optional[dict]) -> str:
    clean = _clean_session_contract(contract)
    if not clean:
        return ""
    target = clean.get("target_minutes") or 40
    min_est = clean.get("min_estimated_minutes") or max(25, int(float(target) * 0.7))
    label = clean.get("label") or f"约 {target} 分钟"
    required = clean.get("required_activity_types") or [
        "lecture",
        "worked_example",
        "drill",
        "produce",
        "reflection",
    ]
    return "\n".join([
        "【学习时长合同】",
        f"- 本节不是固定 20–40 分钟微课；必须对齐学习者当前可投入时长：{label}（目标约 {target} 分钟）。",
        f"- 输出 JSON 必须包含 `estimated_minutes`，且不低于 {min_est} 分钟；必须包含 `activity_plan`，每段写 type/title/minutes/task。",
        "- 如果目标时长 >= 60 分钟，本节必须是一段完整学习单元：讲解 → worked example → 分层练习 → 主动产出 → 自查/延展。",
        "- `activity_plan` 必须覆盖这些活动类型：" + "、".join(str(x) for x in required) + "。",
        "- `produce.task` 不能只是 3–5 句话复述；要要求学习者完成可检查的设计、推演、改写、案例分析或小项目产物。",
        "- 内容可以把深度放进 cards[].expansions，但不能把首屏变成目录；活动链和质量门会检查是否足以支撑该学习时长。",
    ])


def _merge_blocks(*blocks: str) -> str:
    return "\n\n".join(str(x).strip() for x in blocks if str(x or "").strip())


def _attach_session_contract(lesson: dict, contract: Optional[dict]) -> dict:
    clean = _clean_session_contract(contract)
    if clean and isinstance(lesson, dict):
        lesson["_session_contract"] = clean
    return lesson


def _clean_diagram(diagram) -> dict:
    """规整结构化图，前端可直接渲染，不依赖 ASCII 图。"""
    if not isinstance(diagram, dict):
        return {}
    nodes = []
    for node in diagram.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        clean_node = {
            "id": _clean_str(node.get("id"), 80),
            "title": _clean_str(node.get("title") or node.get("name"), 120),
            "desc": _clean_str(node.get("desc") or node.get("note") or node.get("body"), 500),
            "kind": _clean_str(node.get("kind") or node.get("type"), 40),
        }
        if clean_node["title"] or clean_node["desc"]:
            nodes.append({k: v for k, v in clean_node.items() if v})
    edges = []
    for edge in diagram.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        clean_edge = {
            "from": _clean_str(edge.get("from") or edge.get("source"), 80),
            "to": _clean_str(edge.get("to") or edge.get("target"), 80),
            "label": _clean_str(edge.get("label") or edge.get("relation"), 120),
        }
        if clean_edge["from"] and clean_edge["to"]:
            edges.append({k: v for k, v in clean_edge.items() if v})
    out = {
        "title": _clean_str(diagram.get("title"), 160),
        "caption": _clean_str(diagram.get("caption"), 500),
        "nodes": nodes[:8],
        "edges": edges[:12],
    }
    return {k: v for k, v in out.items() if v}


def _coerce_design(data: dict, *, topic: str, explain: dict) -> dict:
    """规整 lesson 级教学设计摘要。

    新 prompt 会显式输出 design；旧缓存/旧模型没有时，从 explain 合成轻量骨架，
    保持历史数据可打开，同时让前端有“本节到底学什么”的入口。
    """
    raw = data.get("design") if isinstance(data.get("design"), dict) else {}
    if not raw and any(
        key in data
        for key in ("learning_question", "outcome", "main_thread", "boundary", "key_steps", "quick_checks")
    ):
        raw = data

    def clean(value, limit=360):
        return _clean_str(value, limit)

    key_steps = []
    for step in raw.get("key_steps") or []:
        if not isinstance(step, dict):
            continue
        item = {
            "name": clean(step.get("name"), 120),
            "purpose": clean(step.get("purpose"), 360),
            "learner_action": clean(step.get("learner_action"), 360),
            "stuck_point": clean(step.get("stuck_point"), 360),
        }
        if any(item.values()):
            key_steps.append(item)

    quick_checks = [clean(x, 220) for x in (raw.get("quick_checks") or []) if clean(x, 220)]
    summary = clean((explain or {}).get("summary"), 500)
    points = [clean(x, 220) for x in ((explain or {}).get("points") or []) if clean(x, 220)]
    design = {
        "learning_question": clean(raw.get("learning_question"), 500) or f"这一节要解决「{topic}」里的核心判断问题。",
        "outcome": clean(raw.get("outcome"), 500) or summary or f"学完后能用自己的话讲清「{topic}」并完成一个小输出。",
        "main_thread": clean(raw.get("main_thread"), 500) or "围绕一个真实任务，从问题判断推进到例子、输出和练习。",
        "boundary": clean(raw.get("boundary"), 500) or "本节只处理当前最小可学问题，复杂延展放到后续学习。",
        "key_steps": key_steps[:5],
        "quick_checks": quick_checks[:4],
    }
    if len(design["key_steps"]) < 3:
        fallback_steps = points[:3] or [
            "先说清它解决什么问题",
            "再识别关键判断规则",
            "最后用自己的例子验证",
        ]
        while len(fallback_steps) < 3:
            fallback_steps.append("用一个新例子检查是否真的会用")
        for i, step in enumerate(fallback_steps):
            if len(design["key_steps"]) >= 3:
                break
            design["key_steps"].append({
                "name": clean(step, 80) or f"关键判断 {i + 1}",
                "purpose": "把知识点转成可使用的判断规则。",
                "learner_action": "用自己的话解释，并判断一个例子是否符合规则。",
                "stuck_point": "只记住术语，但说不出什么时候用。",
            })
    fallback_checks = [
        f"能不能一句话说清「{topic}」解决什么问题？",
        "能不能自己造一个不照抄的新例子？",
    ]
    for check in fallback_checks:
        if len(design["quick_checks"]) >= 2:
            break
        if check not in design["quick_checks"]:
            design["quick_checks"].append(check)
    return design


def _coerce_cards(cards) -> list[dict]:
    """规整互动课程卡，给新前端使用；旧前端可忽略。"""
    clean = []
    if not isinstance(cards, list):
        return clean

    for card in cards:
        if not isinstance(card, dict):
            continue
        ctype = _clean_str(card.get("type"), 40).lower()
        if ctype not in _CARD_TYPES:
            ctype = "explain"
        item = {
            "type": ctype,
            "title": _clean_str(card.get("title"), 160),
            "body": _clean_str(card.get("body") or card.get("content"), 2400),
        }
        raw_options = card.get("options") or []
        if isinstance(raw_options, str):
            raw_options = [raw_options]
        options = [str(x).strip() for x in raw_options if str(x).strip()]
        if options:
            item["options"] = options[:6]
        items = _clean_items(card.get("items"))
        if items:
            item["items"] = items[:8]
        if card.get("answer") is not None:
            try:
                item["answer"] = int(card.get("answer"))
            except (TypeError, ValueError):
                pass
        why = _clean_str(card.get("why"), 1200)
        if why:
            item["why"] = why
        for source_key in ("completion_rule", "expected_signal", "free_input_prompt", "answer_schema"):
            value = _clean_str(card.get(source_key), 900)
            if value:
                item[source_key] = value
        source_refs = _clean_items(card.get("source_refs"))
        if source_refs:
            item["source_refs"] = source_refs[:6]
        expansions = _clean_expansions(card.get("expansions"))
        if expansions:
            item["expansions"] = expansions
        diagram = _clean_diagram(card.get("diagram"))
        if diagram:
            item["diagram"] = diagram
        if item["title"] or item["body"] or item.get("items") or item.get("options"):
            clean.append(item)

    return clean[:10]


def _synthesize_cards(lesson: dict) -> list[dict]:
    """当模型没给 cards 时，从旧结构合成一版，保证旧缓存也能被新前端消费。"""
    explain = lesson.get("explain") or {}
    cards: list[dict] = []

    summary = explain.get("summary") or ""
    if summary:
        cards.append({"type": "goal", "title": "这一节先解决什么", "body": summary})

    analogy = explain.get("analogy") or ""
    if analogy:
        cards.append({"type": "visual", "title": "先建立直觉", "body": analogy})

    points = explain.get("points") or []
    if points:
        cards.append({"type": "explain", "title": "关键判断", "items": points})

    examples = explain.get("examples") or []
    if examples:
        cards.append(
            {
                "type": "example",
                "title": "具体例子",
                "items": [
                    {"text": e.get("text", ""), "note": e.get("note", "")}
                    for e in examples
                    if isinstance(e, dict)
                ],
            }
        )

    produce = lesson.get("produce") or {}
    if produce.get("task"):
        cards.append(
            {
                "type": "produce",
                "title": "轮到你输出",
                "body": produce.get("task", ""),
                "items": [produce.get("hint", "")] if produce.get("hint") else [],
            }
        )

    return _coerce_cards(cards)


def _complete_lesson_cards(lesson: dict, cards: list[dict]) -> list[dict]:
    """已有局部 cards 时补齐关键学习链卡，避免为结构缺口再跑一轮 LLM。"""
    out = list(cards or [])
    types = {str(c.get("type") or "").strip().lower() for c in out if isinstance(c, dict)}
    explain = lesson.get("explain") if isinstance(lesson.get("explain"), dict) else {}
    design = lesson.get("design") if isinstance(lesson.get("design"), dict) else {}
    additions: list[dict] = []

    if "goal" not in types:
        body = _clean_str(design.get("outcome") or explain.get("summary") or lesson.get("topic"), 900)
        additions.append({"type": "goal", "title": "本节学习目标", "body": body})
    if "source" not in types:
        citations = lesson.get("citations") if isinstance(lesson.get("citations"), list) else []
        if citations:
            body = "本节会优先依据已检索资料；关键事实仍建议结合原文核对。"
            items = citations[:4]
        else:
            body = "本节暂无可引用的外部资料，按通用知识和学习目标生成；关键事实建议结合原文核对。"
            items = []
        additions.append({"type": "source", "title": "本节知识来源", "body": body, "items": items})
    if "think" not in types and lesson.get("practice"):
        first = next((q for q in lesson.get("practice") or [] if isinstance(q, dict)), None)
        if first:
            additions.append({
                "type": "think",
                "title": "先判断一个边界问题",
                "body": _clean_str(first.get("q"), 700),
                "options": first.get("options") or [],
                "answer": first.get("answer"),
                "why": _clean_str(first.get("why"), 900),
            })
    if "example" not in types:
        examples = explain.get("examples") if isinstance(explain.get("examples"), list) else []
        if examples:
            additions.append({
                "type": "example",
                "title": "具体例子",
                "items": [
                    {"text": _clean_str(e.get("text"), 1200), "note": _clean_str(e.get("note"), 500)}
                    for e in examples
                    if isinstance(e, dict) and _clean_str(e.get("text"))
                ][:3],
            })
    if "produce" not in types and isinstance(lesson.get("produce"), dict):
        task = _clean_str(lesson["produce"].get("task"), 1800)
        if task:
            additions.append({
                "type": "produce",
                "title": "主动产出",
                "body": task,
                "items": [_clean_str(lesson["produce"].get("hint"), 700)] if _clean_str(lesson["produce"].get("hint")) else [],
            })
    if "check" not in types:
        checks = _clean_rubric(lesson.get("rubric"))
        if not checks:
            checks = _clean_items(design.get("quick_checks"))[:4]
        if checks:
            additions.append({
                "type": "check",
                "title": "自查清单",
                "body": "完成后用这些标准验收自己是否真的掌握，而不是只看完内容。",
                "items": checks[:6],
            })

    for card in _coerce_cards(additions):
        ctype = str(card.get("type") or "").strip().lower()
        if ctype in types:
            continue
        if len(out) >= 10:
            replace_at = next(
                (
                    i for i, existing in enumerate(out)
                    if str(existing.get("type") or "").strip().lower()
                    not in {"goal", "source", "think", "compare", "example", "explain", "practice", "produce", "check"}
                ),
                len(out) - 1,
            )
            out[replace_at] = card
        else:
            out.append(card)
        types.add(ctype)
    return out[:10]


def _coerce_lesson(data: dict, topic: str, n_questions: int) -> dict:
    """把 LLM 输出规整成前端可用的结构，缺字段补默认，非法练习题过滤掉。"""
    explain = data.get("explain") or {}
    practice_in = data.get("practice") or []
    clean_topic = str(data.get("topic") or topic).strip()

    clean_practice = []
    for p in practice_in:
        if not isinstance(p, dict):
            continue
        q = str(p.get("q", "")).strip()
        options = p.get("options") or []
        if not q or not isinstance(options, list) or len(options) < 2:
            continue
        options = [str(o).strip() for o in options if str(o).strip()]
        if len(options) < 2:
            continue
        try:
            answer = int(p.get("answer", 0))
        except (TypeError, ValueError):
            answer = 0
        if answer < 0 or answer >= len(options):
            answer = 0
        clean_practice.append(
            {
                "q": q,
                "options": options,
                "answer": answer,
                "why": str(p.get("why", "")).strip(),
            }
        )

    lesson = {
        "topic": clean_topic,
        "explain": {
            "title": str(explain.get("title") or topic).strip(),
            "summary": str(explain.get("summary") or "").strip(),
            "analogy": str(explain.get("analogy") or "").strip(),
            "points": [str(x).strip() for x in (explain.get("points") or []) if str(x).strip()],
            "examples": [
                {"text": str(e.get("text", "")).strip(), "note": str(e.get("note", "")).strip()}
                for e in (explain.get("examples") or [])
                if isinstance(e, dict) and str(e.get("text", "")).strip()
            ],
            "tip": str(explain.get("tip") or "").strip(),
        },
        "practice": clean_practice[:n_questions] if clean_practice else [],
    }
    lesson["design"] = _coerce_design(data, topic=clean_topic, explain=lesson["explain"])

    citations = [str(c).strip() for c in (data.get("citations") or []) if str(c).strip()]
    if citations:
        lesson["citations"] = citations

    try:
        estimated_minutes = int(float(data.get("estimated_minutes") or 0))
    except (TypeError, ValueError):
        estimated_minutes = 0
    if estimated_minutes > 0:
        lesson["estimated_minutes"] = max(1, min(360, estimated_minutes))

    activity_plan = _clean_activity_plan(data.get("activity_plan"))
    if activity_plan:
        lesson["activity_plan"] = activity_plan

    rubric = _clean_rubric(data.get("rubric"))
    if rubric:
        lesson["rubric"] = rubric

    produce = data.get("produce")
    if isinstance(produce, dict) and str(produce.get("task", "")).strip():
        lesson["produce"] = {
            "task": str(produce.get("task", "")).strip(),
            "hint": str(produce.get("hint") or "").strip(),
        }

    lab = data.get("lab")
    if isinstance(lab, dict) and str(lab.get("starter_code", "")).strip():
        lesson["lab"] = {
            "language": str(lab.get("language") or "python").strip().lower(),
            "title": str(lab.get("title") or "").strip(),
            "instructions": str(lab.get("instructions") or "").strip(),
            "starter_code": str(lab.get("starter_code", "")).rstrip(),
            "hint": str(lab.get("hint") or "").strip(),
        }

    cards = _coerce_cards(data.get("cards")) or _synthesize_cards(lesson)
    if cards:
        lesson["cards"] = cards

    return lesson


def ensure_lesson_cards(lesson: dict) -> dict:
    """为旧缓存/外部调用补齐 cards，保持 lesson schema 向后兼容。"""
    if not isinstance(lesson, dict):
        return lesson
    if not isinstance(lesson.get("design"), dict) or not lesson.get("design"):
        topic = _clean_str(lesson.get("topic") or (lesson.get("explain") or {}).get("title") or "本节学习", 180)
        explain = lesson.get("explain") if isinstance(lesson.get("explain"), dict) else {}
        lesson["design"] = _coerce_design(lesson, topic=topic, explain=explain)
    cards = _coerce_cards(lesson.get("cards")) or _synthesize_cards(lesson)
    if cards:
        lesson["cards"] = _complete_lesson_cards(lesson, cards)
    return lesson


def generate_lesson(
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    n_questions: int = 3,
    context: str = "",
    custom_instruction: str = "",
    client: Optional[LLMClient] = None,
) -> dict:
    """为指定主题现场生成一小节微课（讲解 + N 道点选练习）。

    context：来自 RAG 检索的参考资料（编号片段），非空时讲解会基于它并标注 citation。
    任何失败都返回兜底结构（带 _fallback=True），不抛异常，保证前端可用。
    """
    client = client or get_default_client()
    prompt = (
        _load_prompt()
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{topic}", topic or "（未指定主题）")
        .replace("{custom_instruction}", custom_instruction.strip() or "（无）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{n_questions}", str(n_questions))
    )

    with trace_llm(scene="lesson"):
        try:
            text = client.chat(
                [Message(role="user", content=prompt)],
                temperature=0.5,
                max_tokens=_lesson_write_max_tokens(custom_instruction),
            )
        except LLMError:
            mark_fallback()
            return _fallback_lesson(topic)

        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return _fallback_lesson(topic)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return _fallback_lesson(topic)
        if not isinstance(data, dict):
            mark_fallback()
            return _fallback_lesson(topic)

        lesson = _coerce_lesson(data, topic, n_questions)
        if not lesson["practice"]:
            # 讲解有了但练习全废，仍可用，但标注
            lesson["_no_practice"] = True
        return lesson


# ============================================================================
# evaluator-optimizer 工作流（Batch L-3）：检索 → 起草 → 自检 → 修订
# ============================================================================


def _review_enabled() -> bool:
    """讲解自检/锐度评审默认开（ITUTOR_LESSON_REVIEW=0/false/off 可关，省 token）。"""
    return os.getenv("ITUTOR_LESSON_REVIEW", "1").strip().lower() not in ("0", "false", "no", "off")


def _pipeline_enabled() -> bool:
    """三段式流水线（课设→写作→评审）默认开；ITUTOR_LESSON_PIPELINE=0 回退单段式。"""
    return os.getenv("ITUTOR_LESSON_PIPELINE", "1").strip().lower() not in ("0", "false", "no", "off")


def _load_prompt_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"找不到 prompt: {path}")
    return path.read_text(encoding="utf-8")


def _quality_gate_enabled() -> bool:
    """本地质量门默认开；不调模型，只做确定性检查。"""
    return os.getenv("ITUTOR_LESSON_QUALITY_GATE", "1").strip().lower() not in ("0", "false", "no", "off")


def _quality_repair_enabled() -> bool:
    """质量门不通过时，默认最多追加一次定向修订。"""
    return os.getenv("ITUTOR_LESSON_QUALITY_REPAIR", "1").strip().lower() not in ("0", "false", "no", "off")


def _lesson_quality_loop_enabled() -> bool:
    """内容质量迭代 loop 默认开启；可用 ITUTOR_LESSON_QUALITY_LOOP=0 退回一次修订。"""
    return os.getenv("ITUTOR_LESSON_QUALITY_LOOP", "1").strip().lower() not in ("0", "false", "no", "off")


def _lesson_expand_enabled() -> bool:
    """长学习单元的分段补强默认开启；只做一次局部 patch，避免巨型 JSON。"""
    return os.getenv("ITUTOR_LESSON_EXPAND", "1").strip().lower() not in ("0", "false", "no", "off")


def _lesson_section_writer_enabled() -> bool:
    """60 分钟以上学习单元默认走分段写作 + 本地 composer，避免单次巨型 JSON。"""
    return os.getenv("ITUTOR_LESSON_SECTION_WRITER", "1").strip().lower() not in ("0", "false", "no", "off")


def _should_use_section_writer(session_contract: Optional[dict]) -> bool:
    clean = _clean_session_contract(session_contract)
    return _lesson_section_writer_enabled() and int(clean.get("target_minutes") or 0) >= 60


def _lesson_max_revisions() -> int:
    """最多定向修订轮数。默认 3，最高 5，避免成本/延迟失控。"""
    raw = os.getenv("ITUTOR_LESSON_MAX_REVISIONS", "3")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = 3
    return max(0, min(5, value))


def _full_unit_prompt_present(*texts: str) -> bool:
    blob = "\n".join(str(x or "") for x in texts)
    return "学习时长合同" in blob or "1–2 小时" in blob or "目标时长 >= 60" in blob


def _lesson_write_max_tokens(*texts: str) -> int:
    return 7600 if _full_unit_prompt_present(*texts) else 5200


def _lesson_revise_max_tokens(draft: Optional[dict] = None, *texts: str) -> int:
    target = _lesson_target_minutes(draft or {}) if isinstance(draft, dict) else 0
    return 8200 if target >= 60 or _full_unit_prompt_present(*texts) else 6200


def _draft_for_review(lesson: dict) -> dict:
    """把草稿里供评审的字段挑出来（去掉 _ 开头的内部标记）。"""
    keys = (
        "topic",
        "design",
        "estimated_minutes",
        "activity_plan",
        "rubric",
        "_session_contract",
        "explain",
        "cards",
        "produce",
        "lab",
        "practice",
        "citations",
    )
    draft = {k: lesson[k] for k in keys if k in lesson}
    if lesson.get("_learner_signals"):
        draft["learner_signals"] = _clean_str(lesson.get("_learner_signals"), 1800)
    return draft


def _attach_learner_signals(lesson: dict, learner_signals: str) -> dict:
    signals = _clean_str(learner_signals, 1800)
    if signals:
        lesson["_learner_signals"] = signals
    return lesson


def _compact_card_for_critic(card: dict) -> dict:
    out = {
        "type": _clean_str(card.get("type"), 80),
        "title": _clean_str(card.get("title"), 160),
        "body": _clean_str(card.get("body"), 700),
    }
    for key in ("why", "completion_rule", "expected_signal"):
        if card.get(key):
            out[key] = _clean_str(card.get(key), 400)
    if card.get("items"):
        out["items"] = _clean_items(card.get("items"))[:5]
    if card.get("options"):
        out["options"] = _clean_items(card.get("options"))[:6]
    expansions = []
    for block in card.get("expansions") or []:
        if not isinstance(block, dict):
            continue
        compact = {
            "kind": _clean_str(block.get("kind") or block.get("type"), 80),
            "title": _clean_str(block.get("title"), 160),
            "body": _clean_str(block.get("body"), 900),
        }
        if block.get("items"):
            compact["items"] = _clean_items(block.get("items"))[:5]
        expansions.append({k: v for k, v in compact.items() if v})
    if expansions:
        out["expansions"] = expansions[:4]
    return {k: v for k, v in out.items() if v}


def _draft_for_critic(lesson: dict) -> dict:
    """给 reasoner 评审用的压缩稿：保留教学证据，避免整份长课把 JSON 输出挤坏。"""
    draft = _draft_for_review(lesson)
    if isinstance(draft.get("explain"), dict):
        explain = draft["explain"]
        draft["explain"] = {
            "title": _clean_str(explain.get("title"), 160),
            "summary": _clean_str(explain.get("summary"), 700),
            "points": _clean_items(explain.get("points"))[:6],
            "examples": _clean_items(explain.get("examples"))[:3],
            "tip": _clean_str(explain.get("tip"), 400),
        }
    if isinstance(draft.get("cards"), list):
        draft["cards"] = [
            _compact_card_for_critic(card)
            for card in draft["cards"]
            if isinstance(card, dict)
        ]
    if isinstance(draft.get("practice"), list):
        draft["practice"] = _clean_items(draft["practice"])[:6]
    if isinstance(draft.get("activity_plan"), list):
        draft["activity_plan"] = _clean_items(draft["activity_plan"])[:8]
    if isinstance(draft.get("rubric"), list):
        draft["rubric"] = _clean_items(draft["rubric"])[:8]
    return draft


def _apply_review_revision(review: dict, *, draft: dict, topic: str, n_questions: int) -> dict:
    """把 LLM 修订结果规整成 lesson，并用草稿字段兜底。"""
    revised = _coerce_lesson(review["lesson"], topic, n_questions)
    if not revised.get("practice") and draft.get("practice"):
        revised["practice"] = draft["practice"]
    for k in ("design", "estimated_minutes", "activity_plan", "rubric", "_session_contract", "_learner_signals", "_pipeline", "_sections", "cards", "produce", "lab", "citations"):
        if k not in revised and k in draft:
            revised[k] = draft[k]
    if not revised.get("practice"):
        revised["_no_practice"] = True
    revised["_reviewed"] = True
    revised["_revised"] = True
    issues = [str(x).strip() for x in (review.get("issues") or []) if str(x).strip()]
    if issues:
        revised["_review_issues"] = issues
    return revised


def _quality_gate_and_repair(
    lesson: dict,
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str,
    n_questions: int,
    context: str,
    custom_instruction: str,
    client: LLMClient,
) -> dict:
    """本地质量门：不合格时最多追加一次定向修订，避免无限循环。"""
    if not _quality_gate_enabled() or lesson.get("_fallback"):
        return lesson

    _normalize_lesson_before_quality(lesson, context=context)
    report = evaluate_lesson_quality(lesson, context=context)
    lesson["_quality"] = report
    if report.get("passed") or not _review_enabled() or not _quality_repair_enabled():
        return lesson

    repair_instruction = "\n\n".join(
        x for x in (custom_instruction.strip(), format_quality_issues(report)) if x
    )
    repair = review_lesson(
        lesson,
        domain=domain,
        target=target,
        baseline=baseline,
        context=context,
        custom_instruction=repair_instruction,
        client=client,
    )
    lesson["_quality_repair_attempted"] = True
    if not repair or str(repair.get("verdict") or "").strip().lower() != "revise" or not isinstance(repair.get("lesson"), dict):
        return lesson

    revised = _apply_review_revision(repair, draft=lesson, topic=topic, n_questions=n_questions)
    revised["_quality_repaired"] = True
    revised["_quality_before"] = report
    _normalize_lesson_before_quality(revised, context=context)
    final_report = evaluate_lesson_quality(revised, context=context)
    revised["_quality"] = final_report
    return revised


def review_lesson(
    draft: dict,
    *,
    domain: str,
    target: str,
    baseline: str = "",
    context: str = "",
    custom_instruction: str = "",
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """自检+修订环节：返回 {verdict, issues, lesson?}；失败/不可用返回 None（调用方退回草稿）。"""
    client = client or get_default_client()
    if not LESSON_REVIEW_PROMPT_PATH.exists():
        return None
    prompt = (
        LESSON_REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{custom_instruction}", custom_instruction.strip() or "（无）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{draft}", json.dumps(_draft_for_review(draft), ensure_ascii=False))
    )
    with trace_llm(scene="lesson_review"):
        try:
            text = client.chat([Message(role="user", content=prompt)], temperature=0.3, max_tokens=5200)
        except LLMError:
            mark_fallback()
            return None
        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return None
        return data if isinstance(data, dict) else None


def _coerce_design_doc(data: dict, *, topic: str) -> dict:
    """规整"课设"段产物：核心字段沿用 _coerce_design，并补 angle / anti_generic。"""
    design = _coerce_design(data, topic=topic, explain={})
    angle = _clean_str(data.get("angle"), 500)
    if angle:
        design["angle"] = angle
    anti = [_clean_str(x, 240) for x in (data.get("anti_generic") or []) if _clean_str(x, 240)]
    if anti:
        design["anti_generic"] = anti[:6]
    return design


def design_lesson(
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    context: str = "",
    learner_signals: str = "",
    custom_instruction: str = "",
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """流水线第 1 段：课设（reasoner）。返回 design dict；失败返回 None（调用方回退单段式）。"""
    client = client or get_default_client()
    if not LESSON_DESIGN_PROMPT_PATH.exists():
        return None
    prompt = (
        _load_prompt_file(LESSON_DESIGN_PROMPT_PATH)
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{topic}", topic or "（未指定主题）")
        .replace("{learner_signals}", learner_signals.strip() or "（暂无学员信号）")
        .replace("{custom_instruction}", custom_instruction.strip() or "（无）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
    )
    with trace_llm(scene="lesson_design"):
        try:
            text = client.chat(
                [Message(role="user", content=prompt)],
                temperature=0.4,
                max_tokens=4000,
                model=get_reasoner_model(),
            )
        except LLMError:
            mark_fallback()
            return None
        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return None
        if not isinstance(data, dict):
            mark_fallback()
            return None
        return _coerce_design_doc(data, topic=topic)


def write_lesson(
    *,
    design: dict,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    n_questions: int = 3,
    context: str = "",
    learner_signals: str = "",
    custom_instruction: str = "",
    client: Optional[LLMClient] = None,
) -> dict:
    """流水线第 2 段：围绕锁定课设把内容写深（chat，全 token 预算）。

    失败返回兜底结构（_fallback=True），调用方据此回退单段式。
    """
    client = client or get_default_client()
    if not LESSON_WRITE_PROMPT_PATH.exists():
        return _fallback_lesson(topic)
    prompt = (
        _load_prompt_file(LESSON_WRITE_PROMPT_PATH)
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{topic}", topic or "（未指定主题）")
        .replace("{learner_signals}", learner_signals.strip() or "（暂无学员信号）")
        .replace("{custom_instruction}", custom_instruction.strip() or "（无）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{design}", json.dumps(design, ensure_ascii=False))
        .replace("{n_questions}", str(n_questions))
    )
    with trace_llm(scene="lesson_write"):
        try:
            text = client.chat(
                [Message(role="user", content=prompt)],
                temperature=0.5,
                max_tokens=_lesson_write_max_tokens(learner_signals, custom_instruction),
            )
        except LLMError:
            mark_fallback()
            return _fallback_lesson(topic)
        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return _fallback_lesson(topic)
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return _fallback_lesson(topic)
        if not isinstance(data, dict):
            mark_fallback()
            return _fallback_lesson(topic)
        lesson = _coerce_lesson(data, topic, n_questions)
        # 课设是锁定输入：合并时让锁定课设的核心字段胜出，防止写作环节漂移。
        lesson["design"] = {**(lesson.get("design") or {}), **(design or {})}
        if not lesson["practice"]:
            lesson["_no_practice"] = True
        return lesson


def _lesson_section_specs(n_questions: int) -> list[dict]:
    return [
        {
            "id": "core",
            "max_tokens": 3000,
            "requirements": (
                "输出 explain 和 4 张 cards：goal/source/think/compare。"
                "think 必须有 options/answer/why；compare 必须有 mechanism 和 boundary/contrast expansions。"
                "无参考资料时 source 卡必须写暂无外部来源，不得点名具体文档、URL、论文或框架。"
            ),
        },
        {
            "id": "examples",
            "max_tokens": 2800,
            "requirements": (
                "输出 explain.examples 2 个（至少一个反例或边界例）和 1–2 张 example/explain cards。"
                "其中至少 1 张 example card 必须有 worked_example expansion，按 4–6 步推演，包含失败分支。"
            ),
        },
        {
            "id": "practice",
            "max_tokens": 2600,
            "requirements": (
                f"输出 practice {n_questions} 题，至少 3 个选项，answer 下标合法，why 要解释正确项和主要干扰项。"
                "同时输出 1 张 check 或 practice card，其中必须带 options/answer/why 形成互动卡。"
            ),
        },
        {
            "id": "produce",
            "max_tokens": 2600,
            "requirements": (
                "输出 produce、activity_plan、rubric 和 1 张 produce/check card。"
                "activity_plan 必须覆盖 lecture/worked_example/drill/produce/reflection，合计不低于 90 分钟或时长合同最低值；"
                "produce.task 必须是可检查产物，不是短复述。rubric 至少 4 条。"
                "必须包含可填写模板、参考答案/样例产物、批判/修复/设计评审类高阶练习；"
                "用户可见内容不要出现课堂话术或原始学员标签。"
            ),
        },
    ]


def _section_summary(section: dict) -> dict:
    cards = section.get("cards") if isinstance(section.get("cards"), list) else []
    return {
        "cards": [
            {"type": c.get("type"), "title": c.get("title")}
            for c in cards[:6]
            if isinstance(c, dict)
        ],
        "practice_count": len(section.get("practice") or []) if isinstance(section.get("practice"), list) else 0,
        "has_produce": isinstance(section.get("produce"), dict),
        "has_activity_plan": isinstance(section.get("activity_plan"), list),
    }


def write_lesson_section(
    *,
    section: dict,
    design: dict,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    context: str = "",
    learner_signals: str = "",
    custom_instruction: str = "",
    previous_sections: Optional[list[dict]] = None,
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """长课分段写作：只生成一个 section 的局部 JSON。"""
    client = client or get_default_client()
    if not LESSON_SECTION_PROMPT_PATH.exists():
        return None
    prompt = (
        _load_prompt_file(LESSON_SECTION_PROMPT_PATH)
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{topic}", topic or "（未指定主题）")
        .replace("{learner_signals}", learner_signals.strip() or "（暂无学员信号）")
        .replace("{custom_instruction}", custom_instruction.strip() or "（无）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{design}", json.dumps(design, ensure_ascii=False))
        .replace("{section_id}", str(section.get("id") or "section"))
        .replace("{section_requirements}", str(section.get("requirements") or "输出本段需要的局部 JSON。"))
        .replace("{previous_sections}", json.dumps(previous_sections or [], ensure_ascii=False))
    )
    with trace_llm(scene=f"lesson_section_{section.get('id') or 'unknown'}"):
        try:
            text = client.chat(
                [Message(role="user", content=prompt)],
                temperature=0.42,
                max_tokens=int(section.get("max_tokens") or 2600),
            )
        except LLMError:
            mark_fallback()
            return None
    json_str = _extract_json_object(text)
    if json_str is None:
        mark_fallback()
        return None
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        mark_fallback()
        return None
    return data if isinstance(data, dict) else None


def _compose_section_lesson(
    sections: list[dict],
    *,
    design: dict,
    topic: str,
    n_questions: int,
    context: str,
    session_contract: Optional[dict],
) -> dict:
    """把 section 局部 JSON 合成完整 lesson；本地确定性合并，避免 composer 再耗一次 LLM。"""
    data: dict[str, Any] = {"topic": topic, "design": design, "citations": []}
    explain: dict[str, Any] = {"title": topic, "summary": "", "analogy": "", "points": [], "examples": [], "tip": ""}
    cards: list[dict] = []
    practice: list[dict] = []
    rubric: list = []

    for section in sections:
        if not isinstance(section, dict):
            continue
        sec_explain = section.get("explain") if isinstance(section.get("explain"), dict) else {}
        for key in ("title", "summary", "analogy", "tip"):
            value = _clean_str(sec_explain.get(key), 1200)
            if value and len(value) > len(_clean_str(explain.get(key), 1200)):
                explain[key] = value
        explain["points"] = _append_unique_items(explain.get("points") or [], _clean_items(sec_explain.get("points")), limit=8)
        raw_examples = sec_explain.get("examples") or section.get("explain_examples") or []
        examples = []
        for e in raw_examples:
            if isinstance(e, dict) and _clean_str(e.get("text")):
                examples.append({"text": _clean_str(e.get("text"), 2400), "note": _clean_str(e.get("note"), 1000)})
        existing_example_keys = {_clean_str(e.get("text"), 220) for e in explain["examples"] if isinstance(e, dict)}
        for e in examples:
            key = _clean_str(e.get("text"), 220)
            if key and key not in existing_example_keys:
                explain["examples"].append(e)
                existing_example_keys.add(key)
            if len(explain["examples"]) >= 4:
                break

        cards.extend(_coerce_cards(section.get("cards")))
        if isinstance(section.get("practice"), list):
            practice.extend(section["practice"])
        rubric = _append_unique_items(rubric, _clean_rubric(section.get("rubric") or section.get("rubric_additions")), limit=8)

        if isinstance(section.get("produce"), dict):
            current_task = _clean_str((data.get("produce") or {}).get("task")) if isinstance(data.get("produce"), dict) else ""
            patch_task = _clean_str(section["produce"].get("task"), 2400)
            if len(patch_task) > len(current_task):
                data["produce"] = {
                    "task": patch_task,
                    "hint": _clean_str(section["produce"].get("hint"), 1000),
                }
        activity_plan = _clean_activity_plan(section.get("activity_plan"))
        if activity_plan:
            data["activity_plan"] = activity_plan
        try:
            estimated = int(float(section.get("estimated_minutes") or 0))
        except (TypeError, ValueError):
            estimated = 0
        if estimated > int(data.get("estimated_minutes") or 0):
            data["estimated_minutes"] = estimated

    data["explain"] = explain
    data["cards"] = cards
    data["practice"] = practice
    data["rubric"] = rubric
    contract = _clean_session_contract(session_contract)
    if contract and not data.get("estimated_minutes"):
        data["estimated_minutes"] = contract.get("target_minutes")
    if contract and not data.get("activity_plan"):
        target = int(contract.get("target_minutes") or 90)
        data["activity_plan"] = [
            {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": "看懂核心机制、边界和判断规则，并记录一个易混点。"},
            {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "按步骤推演一个真实案例，标出失败分支。"},
            {"type": "drill", "title": "分层练习", "minutes": 20, "task": "完成基础判断和边界判断，并写出排除理由。"},
            {"type": "produce", "title": "主动产出", "minutes": max(20, target - 70), "task": "产出一份可检查方案，包含规则、反例、失败分支和验收标准。"},
            {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 检查产物并记录下一步要补的知识。"},
        ]
    lesson = _coerce_lesson(data, topic, n_questions)
    lesson["design"] = design
    _attach_session_contract(lesson, session_contract)
    if not context.strip():
        lesson.pop("citations", None)
    return _normalize_lesson_before_quality(lesson, context=context)


def write_lesson_sections(
    *,
    design: dict,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    n_questions: int = 3,
    context: str = "",
    learner_signals: str = "",
    custom_instruction: str = "",
    session_contract: Optional[dict] = None,
    client: Optional[LLMClient] = None,
    tick: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """完整学习单元的受控多段生成；任一关键段失败时返回 None 让调用方回退。"""
    client = client or get_default_client()
    sections: list[dict] = []
    summaries: list[dict] = []
    for spec in _lesson_section_specs(n_questions):
        if tick:
            tick(f"lesson_section_{spec['id']}")
        section = write_lesson_section(
            section=spec,
            design=design,
            domain=domain,
            target=target,
            topic=topic,
            baseline=baseline,
            context=context,
            learner_signals=learner_signals,
            custom_instruction=custom_instruction,
            previous_sections=summaries,
            client=client,
        )
        if not section:
            return None
        sections.append(section)
        summaries.append({"id": spec["id"], **_section_summary(section)})
    lesson = _compose_section_lesson(
        sections,
        design=design,
        topic=topic,
        n_questions=n_questions,
        context=context,
        session_contract=session_contract,
    )
    lesson["_pipeline"] = "design_sections"
    lesson["_sections"] = summaries
    return lesson


def critic_lesson(
    draft: dict,
    *,
    domain: str,
    target: str,
    baseline: str = "",
    context: str = "",
    quality_report: Optional[dict] = None,
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """流水线第 3 段：锐度评审（reasoner）。返回 {verdict, scores, issues, rewrite_instructions}；失败返回 None。"""
    client = client or get_default_client()
    if not LESSON_CRITIC_PROMPT_PATH.exists():
        return None
    prompt = (
        _load_prompt_file(LESSON_CRITIC_PROMPT_PATH)
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{quality_report}", json.dumps(quality_report or {}, ensure_ascii=False))
        .replace("{draft}", json.dumps(_draft_for_critic(draft), ensure_ascii=False))
    )
    with trace_llm(scene="lesson_critic"):
        try:
            text = client.chat(
                [Message(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=4200,
                model=get_reasoner_model(),
            )
        except LLMError:
            mark_fallback()
            return None
        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return None
        return data if isinstance(data, dict) else None


def revise_lesson(
    draft: dict,
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    context: str = "",
    instructions: str = "",
    n_questions: int = 3,
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """流水线第 4 段：按锐度评审的重写指令做一次定向返工（chat）。"""
    client = client or get_default_client()
    if LESSON_REVISE_PROMPT_PATH.exists():
        prompt = (
            _load_prompt_file(LESSON_REVISE_PROMPT_PATH)
            .replace("{domain}", domain or "（未知领域）")
            .replace("{target}", target or "（未明确目标）")
            .replace("{baseline}", baseline or "（未知基线）")
            .replace("{topic}", topic or str(draft.get("topic") or "（未指定主题）"))
            .replace("{context}", context.strip() or "（本次无参考资料）")
            .replace("{instructions}", instructions.strip() or "请修订内容，使其更具体、更有洞见。")
            .replace("{draft}", json.dumps(_draft_for_review(draft), ensure_ascii=False))
            .replace("{n_questions}", str(n_questions))
        )
        with trace_llm(scene="lesson_revise"):
            try:
                text = client.chat(
                    [Message(role="user", content=prompt)],
                    temperature=0.35,
                    max_tokens=_lesson_revise_max_tokens(draft, instructions),
                )
            except LLMError:
                mark_fallback()
                return None
            json_str = _extract_json_object(text)
            if json_str is None:
                mark_fallback()
                return None
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                mark_fallback()
                return None
            if not isinstance(data, dict):
                return None
            if isinstance(data.get("lesson"), dict):
                review = {
                    "verdict": data.get("verdict") or "revise",
                    "issues": data.get("issues") or [],
                    "lesson": data["lesson"],
                }
                return _apply_review_revision(review, draft=draft, topic=topic, n_questions=n_questions)
            if isinstance(data.get("topic"), str):
                review = {"verdict": "revise", "issues": [], "lesson": data}
                return _apply_review_revision(review, draft=draft, topic=topic, n_questions=n_questions)
            return None

    # 兜底：如果新 prompt 不存在，沿用旧 evaluator-optimizer review prompt。
    review = review_lesson(
        draft,
        domain=domain,
        target=target,
        baseline=baseline,
        context=context,
        custom_instruction=instructions,
        client=client,
    )
    if not review or str(review.get("verdict") or "").strip().lower() != "revise":
        return None
    if not isinstance(review.get("lesson"), dict):
        return None
    return _apply_review_revision(review, draft=draft, topic=topic or str(draft.get("topic") or ""), n_questions=n_questions)


def _lesson_target_minutes(lesson: dict) -> int:
    contract = lesson.get("_session_contract") if isinstance(lesson.get("_session_contract"), dict) else {}
    try:
        return int(float(contract.get("target_minutes") or 0))
    except (TypeError, ValueError):
        return 0


def _should_expand_lesson(lesson: dict, report: Optional[dict]) -> bool:
    if not _lesson_expand_enabled() or not isinstance(report, dict) or report.get("passed"):
        return False
    if _lesson_target_minutes(lesson) < 60:
        return False
    checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
    if _as_int(checks.get("dense_teaching_cards")) < 3:
        return True
    if _as_int(checks.get("expansion_blocks")) < 3:
        return True
    kinds = set(checks.get("expansion_kinds") or [])
    if not (kinds & {"boundary", "contrast", "counterexample", "common_mistake"}):
        return True
    if any("讲解例子少于" in str(x) or "核心教学内容不足" in str(x) for x in (report.get("issues") or [])):
        return True
    return False


def _append_unique_items(existing: list, additions: list, *, limit: int) -> list:
    out = list(existing or [])
    seen = {_clean_str(item, 160) for item in out}
    for item in additions or []:
        key = _clean_str(item, 160)
        if not key or key in seen:
            continue
        out.append(item)
        seen.add(key)
        if len(out) >= limit:
            break
    return out


def _merge_lesson_patch(draft: dict, patch: dict, *, topic: str, n_questions: int) -> dict:
    """把分段补丁合并回 lesson；补丁失败不能破坏原草稿。"""
    merged = copy.deepcopy(draft)
    payload = patch.get("patch") if isinstance(patch.get("patch"), dict) else patch
    if isinstance(payload.get("lesson_patch"), dict):
        payload = payload["lesson_patch"]
    if isinstance(payload.get("lesson"), dict):
        payload = payload["lesson"]

    explain = merged.setdefault("explain", {})
    if not isinstance(explain, dict):
        explain = {}
        merged["explain"] = explain
    patch_explain = payload.get("explain") if isinstance(payload.get("explain"), dict) else {}
    if len(_clean_str(patch_explain.get("summary"), 500)) > len(_clean_str(explain.get("summary"), 500)):
        explain["summary"] = _clean_str(patch_explain.get("summary"), 900)
    examples = [
        {"text": _clean_str(e.get("text"), 2400), "note": _clean_str(e.get("note"), 1000)}
        for e in (explain.get("examples") or [])
        if isinstance(e, dict) and _clean_str(e.get("text"))
    ]
    patch_examples = []
    raw_examples = payload.get("explain_examples") or patch_explain.get("examples") or []
    for e in raw_examples:
        if isinstance(e, dict) and _clean_str(e.get("text")):
            patch_examples.append({"text": _clean_str(e.get("text"), 2400), "note": _clean_str(e.get("note"), 1000)})
    seen_examples = {_clean_str(e.get("text"), 220) for e in examples}
    for e in patch_examples:
        key = _clean_str(e.get("text"), 220)
        if key and key not in seen_examples:
            examples.append(e)
            seen_examples.add(key)
        if len(examples) >= 4:
            break
    if examples:
        explain["examples"] = examples

    existing_cards = [c for c in (merged.get("cards") or []) if isinstance(c, dict)]
    patch_cards = _coerce_cards(payload.get("cards"))[:3]
    seen_cards = {
        (str(c.get("type") or "").strip().lower(), _clean_str(c.get("title"), 120))
        for c in existing_cards
    }
    for card in patch_cards:
        key = (str(card.get("type") or "").strip().lower(), _clean_str(card.get("title"), 120))
        if key[1] and key not in seen_cards:
            existing_cards.append(card)
            seen_cards.add(key)
        if len(existing_cards) >= 10:
            break
    if existing_cards:
        merged["cards"] = existing_cards[:10]

    try:
        patch_estimated = int(float(payload.get("estimated_minutes") or 0))
    except (TypeError, ValueError):
        patch_estimated = 0
    current_estimated = _lesson_target_minutes(merged) if not merged.get("estimated_minutes") else int(merged.get("estimated_minutes") or 0)
    if patch_estimated > 0 and (not merged.get("estimated_minutes") or patch_estimated > current_estimated):
        merged["estimated_minutes"] = max(1, min(360, patch_estimated))

    patch_plan = _clean_activity_plan(payload.get("activity_plan"))
    current_plan = _clean_activity_plan(merged.get("activity_plan"))
    if patch_plan and (len(current_plan) < 5 or sum(int(x.get("minutes") or 0) for x in patch_plan) > sum(int(x.get("minutes") or 0) for x in current_plan)):
        merged["activity_plan"] = patch_plan

    patch_rubric = _clean_rubric(payload.get("rubric_additions") or payload.get("rubric"))
    if patch_rubric:
        merged["rubric"] = _append_unique_items(merged.get("rubric") or [], patch_rubric, limit=8)

    patch_produce = payload.get("produce")
    if isinstance(patch_produce, dict) and _clean_str(patch_produce.get("task")):
        current_task = _clean_str((merged.get("produce") or {}).get("task")) if isinstance(merged.get("produce"), dict) else ""
        patch_task = _clean_str(patch_produce.get("task"), 2400)
        if len(patch_task) > len(current_task):
            merged["produce"] = {
                "task": patch_task,
                "hint": _clean_str(patch_produce.get("hint"), 1000),
            }

    patch_practice = payload.get("practice")
    if isinstance(patch_practice, list) and len(merged.get("practice") or []) < n_questions:
        supplemental = _coerce_lesson({"topic": topic, "practice": patch_practice}, topic, n_questions).get("practice") or []
        merged["practice"] = (merged.get("practice") or []) + supplemental
        merged["practice"] = merged["practice"][:n_questions]

    issues_fixed = [str(x).strip() for x in (payload.get("issues_fixed") or []) if str(x).strip()]
    if issues_fixed:
        merged["_expanded_issues_fixed"] = issues_fixed[:6]
    merged["_expanded"] = True
    return ensure_lesson_cards(merged)


def _lesson_has_interactive_card(lesson: dict) -> bool:
    for card in lesson.get("cards") or []:
        if not isinstance(card, dict):
            continue
        options = card.get("options") or []
        if not isinstance(options, list) or len([x for x in options if str(x).strip()]) < 2:
            continue
        try:
            answer = int(card.get("answer"))
        except (TypeError, ValueError):
            continue
        if 0 <= answer < len(options) and _clean_str(card.get("why"), 1200):
            return True
    return False


def _normalize_ungrounded_sources(lesson: dict, *, context: str) -> None:
    if str(context or "").strip():
        return
    lesson.pop("citations", None)
    for card in lesson.get("cards") or []:
        if not isinstance(card, dict) or str(card.get("type") or "").strip().lower() != "source":
            continue
        card["body"] = "本节暂无外部来源，按通用知识生成；未引用具体 URL、论文或官方文档。"
        card["items"] = ["本节不伪造外部来源；关键事实仍建议在后续结合权威资料核对。"]
        card.pop("source_refs", None)


def _normalize_activity_tasks(lesson: dict) -> None:
    if _lesson_target_minutes(lesson) < 60:
        return
    plan = lesson.get("activity_plan") or []
    if not isinstance(plan, list):
        return
    for block in plan:
        if not isinstance(block, dict):
            continue
        task = _clean_str(block.get("task"), 1600)
        compact = task.replace(" ", "").replace("\n", "")
        if len(compact) < 20:
            task = _merge_blocks(task, "写下判断依据、失败分支和自查标准，确保产出可检查。")
        block["task"] = task


def _full_unit_plan_needs_repair(plan: list[dict], contract: dict) -> bool:
    if not contract:
        return False
    try:
        target = int(float(contract.get("target_minutes") or 0))
    except (TypeError, ValueError):
        target = 0
    if target < 60:
        return False
    try:
        min_minutes = int(float(contract.get("min_estimated_minutes") or max(60, target * 0.7)))
    except (TypeError, ValueError):
        min_minutes = 60
    clean = _clean_activity_plan(plan)
    if len(clean) < 5:
        return True
    total = sum(int(x.get("minutes") or 0) for x in clean)
    if total < min_minutes:
        return True
    required = {str(x).strip().lower() for x in (contract.get("required_activity_types") or []) if str(x).strip()}
    if not required:
        required = {"lecture", "worked_example", "drill", "produce", "reflection"}
    present = {str(x.get("type") or "").strip().lower() for x in clean}
    return not required.issubset(present)


def _default_full_unit_activity_plan(topic: str, contract: dict) -> list[dict]:
    try:
        target = int(float(contract.get("target_minutes") or 90))
    except (TypeError, ValueError):
        target = 90
    produce_minutes = max(25, target - 65)
    return [
        {"type": "lecture", "title": "核心讲解", "minutes": 20, "task": f"看懂《{topic}》的核心机制、边界和判断规则，并写下一个最容易混淆的点。"},
        {"type": "worked_example", "title": "完整例题", "minutes": 20, "task": "跟着 worked example 逐步推演一个真实案例，标出每一步的判断依据和失败分支。"},
        {"type": "drill", "title": "分层练习", "minutes": 15, "task": "完成基础判断、边界判断和迁移判断，并为每题写一句排除干扰项的理由。"},
        {"type": "produce", "title": "主动产出", "minutes": produce_minutes, "task": "填写模板，完成一个可检查方案，再对照参考答案修复一个错误方案。"},
        {"type": "reflection", "title": "回顾自查", "minutes": 10, "task": "按 rubric 和检查清单验收产物，记录仍不确定的判断点和下一步要补的知识。"},
    ]


def _full_unit_produce_task(topic: str, design: dict) -> str:
    outcome = _clean_str(design.get("outcome"), 500) if isinstance(design, dict) else ""
    main_thread = _clean_str(design.get("main_thread"), 500) if isinstance(design, dict) else ""
    return (
        f"围绕《{topic}》完成一份可检查产物。"
        "填写模板（逐项填写，不要只写一段说明）：1）场景与目标；2）关键输入；3）分层设计或判断步骤；"
        "4）写入触发；5）检索排序规则；6）遗忘/更新策略；7）权限或污染边界；8）验收标准。"
        f"参考答案示例：以“客户支持 Agent 处理订单延迟”为样例，短时记忆只保留本轮订单号和最近消息；"
        "工作记忆保存已查询的物流状态、待确认问题和当前推理摘要；长期记忆只保存用户确认后的稳定偏好、历史投诉事实和业务记录引用。"
        "写入触发是“用户确认或任务完成后写摘要”，检索规则是“先当前任务，再跨会话偏好”，遗忘策略是“临时意图过期、旧偏好降权、被修正事实保留版本标记”。"
        f"这个样例服务于“{outcome or main_thread or '本节目标'}”，并给出可复现判断规则：短时不做语义检索、工作记忆按任务生命周期清理、长期记忆必须有确认信号和衰减/覆盖策略。"
        "高阶练习：设计评审并修复一个错误方案；这个错误方案只列存储技术、没有写入触发、没有遗忘策略、没有污染防护。"
        "修复时写出错在哪里、为什么会影响真实 Agent、你会怎样改。"
    )


def _ensure_full_unit_self_study_contract(lesson: dict) -> None:
    contract = lesson.get("_session_contract") if isinstance(lesson.get("_session_contract"), dict) else {}
    if not contract:
        return
    try:
        target = int(float(contract.get("target_minutes") or 0))
    except (TypeError, ValueError):
        target = 0
    if target < 60:
        return
    try:
        min_estimated = int(float(contract.get("min_estimated_minutes") or max(60, target * 0.7)))
    except (TypeError, ValueError):
        min_estimated = 60
    current_estimated = int(lesson.get("estimated_minutes") or 0)
    if current_estimated < min_estimated:
        lesson["estimated_minutes"] = max(target, min_estimated)

    if _full_unit_plan_needs_repair(_clean_activity_plan(lesson.get("activity_plan")), contract):
        lesson["activity_plan"] = _default_full_unit_activity_plan(_clean_str(lesson.get("topic"), 160), contract)

    design = lesson.get("design") if isinstance(lesson.get("design"), dict) else {}
    produce = lesson.get("produce") if isinstance(lesson.get("produce"), dict) else {}
    current_task = _clean_str(produce.get("task"), 2400)
    contract_task = _full_unit_produce_task(_clean_str(lesson.get("topic"), 160), design)
    need_contract_task = not all(k in current_task for k in ("模板", "参考答案")) or not any(k in current_task for k in ("设计评审", "修复一个", "错误方案"))
    if need_contract_task or len(current_task.replace(" ", "")) < 160:
        produce["task"] = _merge_blocks(current_task, contract_task)
        produce["hint"] = _clean_str(produce.get("hint"), 1000) or "先填模板，再对照参考答案找差距，最后修复错误方案。"
        lesson["produce"] = produce

    rubric = _append_unique_items(
        _clean_rubric(lesson.get("rubric")),
        [
            "检查清单：是否填写了场景、输入条件、判断步骤、失败分支和验收标准。",
            "参考答案对照：自己的产物是否能解释至少 3 条规则为什么成立，而不是只列技术名词。",
            "设计评审：是否能指出错误方案缺少写入触发、遗忘策略或污染防护，并给出修复方案。",
        ],
        limit=8,
    )
    if rubric:
        lesson["rubric"] = rubric


def _normalize_interaction_from_practice(lesson: dict) -> None:
    if _lesson_has_interactive_card(lesson):
        return
    practice = [q for q in (lesson.get("practice") or []) if isinstance(q, dict)]
    if not practice:
        return
    q = practice[0]
    options = [str(x).strip() for x in (q.get("options") or []) if str(x).strip()]
    if len(options) < 2:
        return
    try:
        answer = int(q.get("answer") or 0)
    except (TypeError, ValueError):
        answer = 0
    if answer < 0 or answer >= len(options):
        answer = 0
    target_card = None
    for card in lesson.get("cards") or []:
        if isinstance(card, dict) and str(card.get("type") or "").strip().lower() in {"think", "check", "practice"}:
            target_card = card
            break
    if target_card is None:
        target_card = {"type": "think", "title": "先判断一个边界问题", "body": _clean_str(q.get("q"), 1200)}
        lesson.setdefault("cards", []).insert(2, target_card)
    target_card.setdefault("body", _clean_str(q.get("q"), 1200))
    target_card["options"] = options[:6]
    target_card["answer"] = answer
    target_card["why"] = _clean_str(q.get("why"), 1200) or "用本节的判断规则排除干扰项，而不是只看表面关键词。"


def _copy_boundary_expansion_to_teaching_card(lesson: dict) -> None:
    cards = [c for c in (lesson.get("cards") or []) if isinstance(c, dict)]
    counted_types = {"explain", "visual", "compare", "example", "practice", "check"}
    has_counted_boundary = False
    boundary_block = None
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        for block in _clean_expansions(card.get("expansions")):
            kind = str(block.get("kind") or "").strip().lower()
            if kind in {"boundary", "contrast", "counterexample", "common_mistake"}:
                boundary_block = boundary_block or block
                if ctype in counted_types:
                    has_counted_boundary = True
    if has_counted_boundary:
        return
    if not boundary_block:
        design = lesson.get("design") if isinstance(lesson.get("design"), dict) else {}
        boundary = _clean_str(design.get("boundary"), 700) or "本节的规则不能机械套用；如果关键输入缺失、输出无法被检查，或只是在表面上相似，就要切换到澄清或相邻概念。"
        boundary_block = {
            "kind": "boundary",
            "title": "什么时候不用这个判断",
            "body": boundary + " 错用的信号是：只能写出结论，却说不清触发条件、失败分支和验收标准。",
        }
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype not in counted_types:
            continue
        expansions = _clean_expansions(card.get("expansions"))
        if any(str(x.get("kind") or "").strip().lower() in {"boundary", "contrast", "counterexample", "common_mistake"} for x in expansions):
            continue
        expansions.append(boundary_block)
        card["expansions"] = expansions[:6]
        return


def _copy_mechanism_expansion_to_teaching_card(lesson: dict) -> None:
    cards = [c for c in (lesson.get("cards") or []) if isinstance(c, dict)]
    counted_types = {"explain", "visual", "compare", "example", "practice", "check"}
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype not in counted_types:
            continue
        if any(str(x.get("kind") or "").strip().lower() == "mechanism" for x in _clean_expansions(card.get("expansions"))):
            return
    design = lesson.get("design") if isinstance(lesson.get("design"), dict) else {}
    angle = _clean_str(design.get("angle") or design.get("main_thread") or (lesson.get("explain") or {}).get("summary"), 900)
    if not angle:
        angle = "这条规则成立不是因为名称相似，而是因为输入条件、处理过程和可验收产出同时满足。"
    mechanism_block = {
        "kind": "mechanism",
        "title": "为什么这个判断成立",
        "body": (
            f"{angle} 机制判断要沿着“触发条件 → 中间状态 → 可验收结果”三步走："
            "先确认信息为什么进入这一层或这一流程，再确认系统如何更新、检索或丢弃它，最后确认学习者或用户能用什么证据判断结果正确。"
            "如果只能说出结论，却说不清触发条件和验收信号，就说明还没有真正掌握本节规则。"
        ),
    }
    for card in cards:
        ctype = str(card.get("type") or "").strip().lower()
        if ctype in counted_types:
            expansions = _clean_expansions(card.get("expansions"))
            expansions.insert(0, mechanism_block)
            card["expansions"] = expansions[:6]
            return


def _learner_signal_value(signals: str, label: str) -> str:
    for raw in str(signals or "").splitlines():
        line = raw.strip()
        if not line.startswith(label):
            continue
        if "：" in line:
            return _clean_str(line.split("：", 1)[1], 700)
        if ":" in line:
            return _clean_str(line.split(":", 1)[1], 700)
        return _clean_str(line[len(label):], 700)
    return ""


def _find_first_card(lesson: dict, types: set[str]) -> Optional[dict]:
    for card in lesson.get("cards") or []:
        if isinstance(card, dict) and str(card.get("type") or "").strip().lower() in types:
            return card
    return None


def _append_card_body(card: dict, addition: str) -> None:
    addition = _clean_str(addition, 900)
    if not addition:
        return
    body = _clean_str(card.get("body"), 2200)
    if addition in body:
        return
    card["body"] = _merge_blocks(body, addition)


def _apply_personalization_scaffold(lesson: dict) -> None:
    signals = _clean_str(lesson.get("_learner_signals"), 1800)
    if not signals or lesson.get("_personalization_scaffolded"):
        return
    known = _learner_signal_value(signals, "已具备")
    weak = _learner_signal_value(signals, "薄弱点")
    preference = _learner_signal_value(signals, "验收偏好")
    learning_goal = _learner_signal_value(signals, "学习目标")
    focus_parts = []
    if known:
        focus_parts.append(f"基础概念可快速扫过，时间更多放在迁移和边界判断上（已有基础：{known}）。")
    if weak:
        focus_parts.append(f"本节优先把容易卡住的点变成可检查动作：{weak}。")
    if preference:
        focus_parts.append(f"验收会落到你偏好的产物形式：{preference}。")
    if learning_goal:
        focus_parts.append(f"所有练习都围绕你的目标产出收束：{learning_goal}。")
    if not focus_parts:
        focus_parts.append(signals.splitlines()[0])
    lesson["_personalization"] = {"focus": focus_parts[:4]}

    explain = lesson.setdefault("explain", {})
    if isinstance(explain, dict):
        point = "按你的基线，本节不重复铺陈 LLM/Agent/向量检索基础概念，而把时间集中在写入时机、更新/遗忘、权限边界和可检查产出。"
        if point not in (explain.get("points") or []):
            explain["points"] = [point] + list(explain.get("points") or [])[:7]
        tip = _merge_blocks(
            _clean_str(explain.get("tip"), 900),
            f"个性化提醒：如果你已经能说清基础概念，就快速扫过定义，精读机制/边界/反例；如果卡在薄弱点（{weak or '写入、更新、遗忘、权限边界'}），先做前置诊断再进入 worked example。",
        )
        explain["tip"] = _clean_str(tip, 1200)

    goal_card = _find_first_card(lesson, {"goal"})
    if goal_card:
        _append_card_body(
            goal_card,
            f"本节会按你的学习状态取舍：{('；'.join(focus_parts[:3]))}因此重心不是概念名词复述，而是把关键卡点落到架构图、判断规则和可验收产出。",
        )

    boundary_card = _find_first_card(lesson, {"compare", "explain", "example"})
    if boundary_card and weak:
        expansions = _clean_expansions(boundary_card.get("expansions"))
        title = "个性化补强：写入/更新/遗忘/权限边界"
        if not any(_clean_str(x.get("title")) == title for x in expansions):
            expansions.append({
                "kind": "boundary",
                "title": title,
                "body": (
                    f"你的薄弱点是：{weak}。验收时不要只问“这条记忆存在哪里”，要连续追问四件事："
                    "谁触发写入、谁有权限更新、什么时候遗忘或降权、跨会话检索时如何防止污染。"
                    "如果一个设计只能说出存储介质，却说不清这四个边界，它就还不是可落地的记忆系统设计。"
                ),
            })
            boundary_card["expansions"] = expansions[:6]

    plan = _clean_activity_plan(lesson.get("activity_plan"))
    if plan and not any(str(x.get("type") or "").lower() == "diagnostic" for x in plan):
        diagnostic = {
            "type": "diagnostic",
            "title": "前置诊断：先定位你的薄弱点",
            "minutes": 5,
            "task": f"用 2–3 个判断题快速确认是否仍混淆：{weak or '短时/工作/长期记忆的边界'}。全对就快速扫过基础定义，错题对应精读机制卡和边界卡。",
        }
        if plan and int(plan[0].get("minutes") or 0) >= 15:
            plan[0]["minutes"] = max(10, int(plan[0].get("minutes") or 0) - 5)
        plan.insert(0, diagnostic)
        lesson["activity_plan"] = plan[:10]

    produce = lesson.get("produce") if isinstance(lesson.get("produce"), dict) else {}
    if produce.get("task"):
        task = _merge_blocks(
            _clean_str(produce.get("task"), 2200),
            "个性化要求：从智能客服、个人助理、代码助手等场景中选择一个最接近你真实目标的场景，说明该场景的特殊记忆需求，再完成架构设计。",
        )
        produce["task"] = _clean_str(task, 2400)
        lesson["produce"] = produce
    produce_card = _find_first_card(lesson, {"produce"})
    if produce_card:
        items = _clean_items(produce_card.get("items"))
        items = _append_unique_items(
            items,
            ["先选择一个最贴近自己目标的 Agent 场景，并说明这个场景为什么特别需要跨会话记忆、写入治理或权限边界。"],
            limit=8,
        )
        produce_card["items"] = items

    rubric = _append_unique_items(
        _clean_rubric(lesson.get("rubric")),
        [
            f"是否针对你的薄弱点（{weak or '记忆边界'}）写出至少 3 条判断规则，而不是只列存储技术。",
            "是否选择了一个贴近自己目标的 Agent 场景，并说明该场景独有的记忆风险和验收标准。",
        ],
        limit=8,
    )
    if rubric:
        lesson["rubric"] = rubric
    lesson["_personalization_scaffolded"] = True


def _normalize_lesson_before_quality(lesson: dict, *, context: str) -> dict:
    ensure_lesson_cards(lesson)
    _apply_personalization_scaffold(lesson)
    _ensure_full_unit_self_study_contract(lesson)
    _normalize_ungrounded_sources(lesson, context=context)
    _normalize_activity_tasks(lesson)
    _normalize_interaction_from_practice(lesson)
    _copy_mechanism_expansion_to_teaching_card(lesson)
    _copy_boundary_expansion_to_teaching_card(lesson)
    return lesson


def expand_lesson_content(
    draft: dict,
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    context: str = "",
    quality_report: Optional[dict] = None,
    n_questions: int = 3,
    client: Optional[LLMClient] = None,
) -> Optional[dict]:
    """长学习单元的局部分段补强：只生成补丁，再合并回现有 lesson。"""
    client = client or get_default_client()
    if not LESSON_EXPAND_PROMPT_PATH.exists():
        return None
    prompt = (
        _load_prompt_file(LESSON_EXPAND_PROMPT_PATH)
        .replace("{domain}", domain or "（未知领域）")
        .replace("{target}", target or "（未明确目标）")
        .replace("{baseline}", baseline or "（未知基线）")
        .replace("{topic}", topic or str(draft.get("topic") or "（未指定主题）"))
        .replace("{context}", context.strip() or "（本次无参考资料）")
        .replace("{quality_report}", json.dumps(quality_report or {}, ensure_ascii=False))
        .replace("{draft}", json.dumps(_draft_for_review(draft), ensure_ascii=False))
    )
    with trace_llm(scene="lesson_expand"):
        try:
            text = client.chat([Message(role="user", content=prompt)], temperature=0.35, max_tokens=6200)
        except LLMError:
            mark_fallback()
            return None
        json_str = _extract_json_object(text)
        if json_str is None:
            mark_fallback()
            return None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return None
        if not isinstance(data, dict):
            return None
        return _merge_lesson_patch(draft, data, topic=topic, n_questions=n_questions)


def _format_critic_instructions(critic: dict, report: Optional[dict]) -> str:
    """把锐度评审结果 + 结构质量门问题转成给返工 prompt 的具体要求。"""
    lines = ["锐度评审判定需要修订；请针对性重写，只解决下列问题，不要为改而改："]
    for x in (critic.get("issues") or []):
        if str(x).strip():
            lines.append(f"- 问题：{str(x).strip()}")
    for name, score in _critic_low_score_items(critic):
        guidance = {
            "personalization": "把学员信号显式落实到讲解取舍、练习难度、活动时长和产出验收里。",
            "demand_fit": "对齐学习目标、基线和学习时长合同，删掉不服务本节目标的泛泛内容。",
            "learning_quality": "补齐理解、例题、练习、产出、自查的学习链条。",
            "knowledge_completeness": "补齐本主题必须覆盖的机制、边界、易混点和典型应用。",
            "self_study_fit": "把内容改成学习者可独立执行的工作单，避免课堂话术和内部画像标签。",
            "artifact_completeness": "补齐可填写模板、参考答案/样例产物、rubric 和可检查产出。",
            "assessment_authenticity": "加入批判、修复、设计评审或边界诊断任务，真正测迁移能力。",
        }.get(name, "补强这个维度，不要只扩写字数。")
        lines.append(f"- 低分维度：{name}={score}，{guidance}")
    for x in (critic.get("rewrite_instructions") or []):
        if str(x).strip():
            lines.append(f"- 重写：{str(x).strip()}")
    if report and not report.get("passed"):
        lines.append(format_quality_issues(report))
    lines.append("修订后保持原 schema；同时提升知识完备性、学习质量、需求匹配度和个性化学习构建，删掉换个主题也成立的通用套话。")
    return "\n".join(lines)


def _critic_summary(critic: dict) -> dict:
    return {
        k: critic[k]
        for k in ("verdict", "scores", "issues", "rewrite_instructions")
        if k in critic
    }


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _quality_rank(report: Optional[dict], critic: Optional[dict] = None) -> int:
    """用于 best-version 保留的粗排序：通过质量门 > 分数高 > 展开层完整 > critic 放行。"""
    report = report or {}
    checks = report.get("checks") or {}
    rank = _as_int(report.get("score"))
    if report.get("passed"):
        rank += 1000
    rank += min(8, _as_int(checks.get("dense_teaching_cards"))) * 3
    rank += min(8, _as_int(checks.get("expansion_blocks"))) * 2
    if checks.get("self_study_template"):
        rank += 5
    if checks.get("reference_answer"):
        rank += 5
    if checks.get("high_order_practice"):
        rank += 5
    rank -= len(checks.get("classroom_terms") or []) * 4
    rank -= len(checks.get("raw_personalization_labels") or []) * 5
    kinds = set(checks.get("expansion_kinds") or [])
    rank += 4 * len(kinds & {"mechanism", "boundary", "contrast", "worked_example"})
    if critic:
        verdict = str(critic.get("verdict") or "").strip().lower()
        if verdict == "pass":
            rank += 40
        elif verdict == "revise":
            rank -= 18
    return rank


def _critic_verdict(critic: Optional[dict]) -> str:
    return str((critic or {}).get("verdict") or "").strip().lower()


CRITIC_MIN_ACCEPT_SCORE = 75


def _critic_low_score_items(critic: Optional[dict]) -> list[tuple[str, int]]:
    scores = (critic or {}).get("scores")
    if not isinstance(scores, dict):
        return []
    low = []
    for key, value in scores.items():
        score = _as_int(value, 100)
        if score < CRITIC_MIN_ACCEPT_SCORE:
            low.append((str(key), score))
    return low


def _needs_revise(critic: Optional[dict], report: Optional[dict]) -> bool:
    if _critic_verdict(critic) == "revise" or not (report or {}).get("passed"):
        return True
    return bool(_critic_low_score_items(critic))


def _loop_round_summary(
    *,
    round_no: int,
    report: Optional[dict],
    critic: Optional[dict] = None,
    action: str,
    accepted: bool,
    rank: int,
    note: str = "",
) -> dict:
    checks = (report or {}).get("checks") or {}
    summary = {
        "round": round_no,
        "action": action,
        "accepted": accepted,
        "rank": rank,
        "quality_passed": bool((report or {}).get("passed")),
        "quality_score": _as_int((report or {}).get("score")),
        "dense_teaching_cards": _as_int(checks.get("dense_teaching_cards")),
        "expansion_blocks": _as_int(checks.get("expansion_blocks")),
        "expansion_kinds": checks.get("expansion_kinds") or [],
        "self_study_template": bool(checks.get("self_study_template")),
        "reference_answer": bool(checks.get("reference_answer")),
        "high_order_practice": bool(checks.get("high_order_practice")),
    }
    if critic:
        summary["critic_verdict"] = _critic_verdict(critic) or "unknown"
        scores = critic.get("scores")
        if isinstance(scores, dict):
            summary["critic_scores"] = scores
        issues = [str(x).strip() for x in (critic.get("issues") or []) if str(x).strip()]
        if issues:
            summary["critic_issues"] = issues[:4]
    if note:
        summary["note"] = note
    return summary


def _attach_quality_loop(
    lesson: dict,
    *,
    rounds: list[dict],
    best_round: int,
    stop_reason: str,
    max_revisions: int,
) -> dict:
    lesson["_quality_loop"] = {
        "rounds": rounds,
        "best_round": best_round,
        "stop_reason": stop_reason,
        "max_revisions": max_revisions,
    }
    return lesson


def _run_lesson_quality_loop(
    lesson: dict,
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str,
    context: str,
    n_questions: int,
    client: LLMClient,
    tick: Callable[[str], None],
) -> dict:
    """有边界的 evaluator-optimizer loop：critic → revise → re-critic，保留最佳版本。"""
    max_revisions = _lesson_max_revisions() if _lesson_quality_loop_enabled() else 1
    if not _quality_repair_enabled():
        max_revisions = 0

    _normalize_lesson_before_quality(lesson, context=context)
    report = lesson.get("_quality") if isinstance(lesson.get("_quality"), dict) else evaluate_lesson_quality(lesson, context=context)
    lesson["_quality"] = report

    initial_report = copy.deepcopy(report)
    current = lesson
    current_report = report
    current_critic: Optional[dict] = None
    best = lesson
    best_report = report
    best_round = 0
    best_rank = _quality_rank(report)
    rounds: list[dict] = []
    stop_reason = "max_revisions"
    small_gain_streak = 0

    revision_no = 0
    while True:
        tick("lesson_critic")
        critic = critic_lesson(
            current, domain=domain, target=target, baseline=baseline, context=context,
            quality_report=current_report, client=client,
        )
        if not critic:
            current["_critic"] = current.get("_critic") or {}
            rounds.append(_loop_round_summary(
                round_no=revision_no,
                report=current_report,
                critic=None,
                action="critic_failed",
                accepted=(current is best),
                rank=_quality_rank(current_report),
                note="critic returned no usable JSON; keeping best lesson",
            ))
            stop_reason = "critic_failed"
            break

        current_critic = _critic_summary(critic)
        current["_critic"] = current_critic
        current_rank = _quality_rank(current_report, critic)
        if current is best and current_rank >= best_rank:
            best_rank = current_rank
            best_report = current_report

        needs_revise = _needs_revise(critic, current_report)
        rounds.append(_loop_round_summary(
            round_no=revision_no,
            report=current_report,
            critic=critic,
            action="critic",
            accepted=(current is best),
            rank=current_rank,
        ))

        if not needs_revise:
            stop_reason = "critic_pass"
            break
        if max_revisions <= 0:
            stop_reason = "repair_disabled"
            break
        if revision_no >= max_revisions:
            stop_reason = "max_revisions"
            break

        tick("lesson_revise")
        before_rank = best_rank
        instructions = _format_critic_instructions(critic, current_report)
        revised = revise_lesson(
            current, domain=domain, target=target, topic=topic, baseline=baseline,
            context=context, instructions=instructions, n_questions=n_questions, client=client,
        )
        if not revised:
            stop_reason = "revise_failed"
            break

        revision_no += 1
        revised["_pipeline"] = current.get("_pipeline") or "design_write"
        if "_sections" not in revised and current.get("_sections"):
            revised["_sections"] = current["_sections"]
        revised["_critic_revised"] = True
        revised["_quality_before"] = initial_report
        if "design" not in revised and current.get("design"):
            revised["design"] = current["design"]
        _normalize_lesson_before_quality(revised, context=context)
        revised_report = evaluate_lesson_quality(revised, context=context)
        revised["_quality"] = revised_report
        revised_rank = _quality_rank(revised_report)
        accepted = revised_rank >= best_rank

        rounds.append(_loop_round_summary(
            round_no=revision_no,
            report=revised_report,
            critic=None,
            action="revise",
            accepted=accepted,
            rank=revised_rank,
            note="accepted as new best" if accepted else "rejected because quality rank regressed",
        ))

        if not accepted:
            stop_reason = "candidate_regressed"
            break

        best = revised
        best_report = revised_report
        best_rank = revised_rank
        best_round = revision_no
        current = revised
        current_report = revised_report

        gain = best_rank - before_rank
        small_gain_streak = small_gain_streak + 1 if gain < 3 else 0
        if revision_no >= max_revisions:
            stop_reason = "max_revisions"
            break
        if small_gain_streak >= 2:
            stop_reason = "plateau"
            break

    best["_quality"] = best_report
    if current_critic and best is current:
        best["_critic"] = current_critic
    if best_round > 0:
        best["_critic_revised"] = True
        best["_quality_before"] = initial_report
    return _attach_quality_loop(
        best,
        rounds=rounds,
        best_round=best_round,
        stop_reason=stop_reason,
        max_revisions=max_revisions,
    )


def _generate_lesson_reviewed_legacy(
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str,
    n_questions: int,
    context: str,
    custom_instruction: str,
    session_contract: Optional[dict],
    client: LLMClient,
    tick: Callable[[str], None],
) -> dict:
    """单段式兜底链路：起草（generate_lesson）→ 自检 → 本地质量门。

    当三段式流水线被关闭、或课设/写作环节失败时回退到这里，行为与历史一致。
    """
    tick("lesson_draft")
    draft = generate_lesson(
        domain=domain, target=target, topic=topic, baseline=baseline,
        n_questions=n_questions, context=context, custom_instruction=custom_instruction,
        client=client,
    )
    _attach_session_contract(draft, session_contract)
    if draft.get("_fallback") or not _review_enabled():
        tick("quality_gate")
        _quality_gate_and_repair(
            draft,
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=custom_instruction,
            client=client,
        )
        return draft

    tick("lesson_review")
    review = review_lesson(
        draft, domain=domain, target=target, baseline=baseline, context=context,
        custom_instruction=custom_instruction, client=client,
    )
    if not review:
        draft["_reviewed"] = True
        tick("quality_gate")
        return _quality_gate_and_repair(
            draft,
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=custom_instruction,
            client=client,
        )

    issues = [str(x).strip() for x in (review.get("issues") or []) if str(x).strip()]
    verdict = str(review.get("verdict") or "").strip().lower()

    if verdict == "revise" and isinstance(review.get("lesson"), dict):
        revised = _apply_review_revision(review, draft=draft, topic=topic, n_questions=n_questions)
        tick("quality_gate")
        return _quality_gate_and_repair(
            revised,
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=custom_instruction,
            client=client,
        )

    draft["_reviewed"] = True
    if issues:
        draft["_review_issues"] = issues
    tick("quality_gate")
    return _quality_gate_and_repair(
        draft,
        domain=domain, target=target, topic=topic, baseline=baseline,
        n_questions=n_questions, context=context, custom_instruction=custom_instruction,
        client=client,
    )


def generate_lesson_reviewed(
    *,
    domain: str,
    target: str,
    topic: str,
    baseline: str = "",
    n_questions: int = 3,
    context: str = "",
    custom_instruction: str = "",
    learner_signals: str = "",
    session_contract: Optional[dict] = None,
    client: Optional[LLMClient] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """教学内容生成流水线：课设(reasoner) → 写深内容(chat) → 结构门 → 锐度评审(reasoner) → 定向返工。

    设计目标是治"空洞 + 太浅 + 千篇一律"：先把"本节教什么独特判断"想清楚（课设），
    再用全预算围绕主线写深（写作），最后用"会不会太通用"做锐度质检，而不是让一个模型
    在一次调用里同时编结构 + 写内容、再靠结构门刷分。

    任何环节失败都回退（流水线失败→单段式；评审失败→保留写作稿），不抛异常。
    """
    def tick(stage_id: str) -> None:
        if progress:
            progress(stage_id)

    client = client or get_default_client()
    session_instruction = _format_session_contract(session_contract)
    effective_learner_signals = _merge_blocks(learner_signals, session_instruction)
    effective_custom_instruction = _merge_blocks(custom_instruction, session_instruction)

    if not _pipeline_enabled():
        return _generate_lesson_reviewed_legacy(
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=effective_custom_instruction,
            session_contract=session_contract,
            client=client, tick=tick,
        )

    # Stage 1 · 课设（reasoner）
    tick("lesson_design")
    design = design_lesson(
        domain=domain, target=target, topic=topic, baseline=baseline,
        context=context, learner_signals=effective_learner_signals, custom_instruction=effective_custom_instruction,
        client=client,
    )
    if not design:
        # 课设失败 → 回退单段式（把学员信号并进 custom_instruction，尽量不丢个性化）
        merged_instruction = _merge_blocks(
            effective_custom_instruction,
            ("学员信号：\n" + effective_learner_signals.strip()) if effective_learner_signals.strip() else "",
        )
        return _generate_lesson_reviewed_legacy(
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=merged_instruction,
            session_contract=session_contract,
            client=client, tick=tick,
        )

    # Stage 2 · 写深内容。60 分钟以上单元优先走 section writer，避免一次吐巨型 JSON。
    if _should_use_section_writer(session_contract):
        tick("lesson_sections")
        lesson = write_lesson_sections(
            design=design, domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, learner_signals=effective_learner_signals,
            custom_instruction=effective_custom_instruction, session_contract=session_contract,
            client=client, tick=tick,
        )
        if not lesson:
            tick("lesson_write")
            lesson = write_lesson(
                design=design, domain=domain, target=target, topic=topic, baseline=baseline,
                n_questions=n_questions, context=context, learner_signals=effective_learner_signals,
                custom_instruction=effective_custom_instruction, client=client,
            )
    else:
        tick("lesson_write")
        lesson = write_lesson(
            design=design, domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, learner_signals=effective_learner_signals,
            custom_instruction=effective_custom_instruction, client=client,
        )
    lesson = lesson or _fallback_lesson(topic)
    if lesson.get("_fallback"):
        merged_instruction = _merge_blocks(
            effective_custom_instruction,
            ("学员信号：\n" + effective_learner_signals.strip()) if effective_learner_signals.strip() else "",
        )
        return _generate_lesson_reviewed_legacy(
            domain=domain, target=target, topic=topic, baseline=baseline,
            n_questions=n_questions, context=context, custom_instruction=merged_instruction,
            session_contract=session_contract,
            client=client, tick=tick,
        )
    lesson["_pipeline"] = lesson.get("_pipeline") or "design_write"
    lesson["_reviewed"] = True
    _attach_session_contract(lesson, session_contract)
    _attach_learner_signals(lesson, effective_learner_signals)

    # Stage 3 · 结构质量门（便宜的确定性前置过滤；不单独占进度阶段）
    _normalize_lesson_before_quality(lesson, context=context)
    report = evaluate_lesson_quality(lesson, context=context)
    lesson["_quality"] = report
    if _should_expand_lesson(lesson, report):
        tick("lesson_expand")
        expanded = expand_lesson_content(
            lesson,
            domain=domain,
            target=target,
            topic=topic,
            baseline=baseline,
            context=context,
            quality_report=report,
            n_questions=n_questions,
            client=client,
        )
        if expanded:
            expanded["_quality_before_expand"] = report
            lesson = expanded
            _normalize_lesson_before_quality(lesson, context=context)
            report = evaluate_lesson_quality(lesson, context=context)
            lesson["_quality"] = report

    if not _review_enabled():
        return lesson

    return _run_lesson_quality_loop(
        lesson,
        domain=domain,
        target=target,
        topic=topic,
        baseline=baseline,
        context=context,
        n_questions=n_questions,
        client=client,
        tick=tick,
    )


__all__ = [
    "generate_lesson",
    "generate_lesson_reviewed",
    "design_lesson",
    "write_lesson",
    "write_lesson_sections",
    "critic_lesson",
    "revise_lesson",
    "ensure_lesson_cards",
    "review_lesson",
    "LESSON_PROMPT_PATH",
    "LESSON_REVIEW_PROMPT_PATH",
    "LESSON_DESIGN_PROMPT_PATH",
    "LESSON_WRITE_PROMPT_PATH",
    "LESSON_SECTION_PROMPT_PATH",
    "LESSON_CRITIC_PROMPT_PATH",
    "LESSON_REVISE_PROMPT_PATH",
]
