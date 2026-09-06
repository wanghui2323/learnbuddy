"""知识图谱（Concept Graph）· Batch 2A keystone。

把一条学习路径拆成带「前置依赖 + 难度」的 Concept 图谱，路径推进在图谱上做
**拓扑排序**而非线性课表；用户"已掌握集合"是动态前沿，前沿之外锁定。

- generate_graph：教研 Agent（LLM）从周主题生成 Concept 图谱；失败→线性兜底
- load_or_generate：读 concepts.json 缓存，没有则生成并落盘
- sanitize / topo_order：保证是 DAG（去非法边、断环）并给出拓扑序
- concept_status：给定"已掌握知识点名集合"，算每个 Concept 的 已征服/前线/锁定

Concept.name 就是一节微课的 topic，和 lesson / 学情（按 topic 记录）天然对齐。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from core.llm_client import LLMClient, LLMError, get_default_client
from core.schemas import Message
from core.tracing import mark_fallback, trace_llm

PROMPTS_DIR = Path(__file__).parent / "prompts"
GRAPH_PROMPT_PATH = PROMPTS_DIR / "concept_graph.md"

_JSON_OBJECT_PATTERN = re.compile(r"\{[\s\S]*\}", re.DOTALL)
_JSON_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.DOTALL)

MASTERY_THRESHOLD = 0.7  # 掌握度 ≥ 此值视为"已征服"
_SOURCE_KINDS = {"official", "paper", "book", "journal", "website", "other"}


def _load_prompt() -> str:
    if not GRAPH_PROMPT_PATH.exists():
        raise FileNotFoundError(f"找不到知识图谱 prompt: {GRAPH_PROMPT_PATH}")
    return GRAPH_PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json_object(text: str) -> Optional[str]:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    m = _JSON_CODE_BLOCK_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    m = _JSON_OBJECT_PATTERN.search(text)
    if m:
        return m.group(0)
    return None


# ----------------------------- 校验 / DAG -----------------------------

def sanitize(concepts: list) -> list[dict]:
    """规整字段 + 去非法/自指/重复 id + 断环，返回保证是 DAG 的 Concept 列表（按拓扑序）。"""
    clean: list[dict] = []
    seen: set[str] = set()
    for i, c in enumerate(concepts or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        cid = str(c.get("id") or f"c{i + 1}").strip() or f"c{i + 1}"
        if cid in seen:
            cid = f"{cid}-{i + 1}"
        seen.add(cid)
        try:
            week = max(1, int(c.get("week") or 1))
        except (TypeError, ValueError):
            week = 1
        try:
            diff = int(c.get("difficulty") or 3)
        except (TypeError, ValueError):
            diff = 3
        diff = min(10, max(1, diff))
        prereqs = [str(p).strip() for p in (c.get("prerequisites") or []) if str(p).strip()]
        clean.append({
            "id": cid,
            "name": name,
            "week": week,
            "difficulty": diff,
            "prerequisites": prereqs,
            "sources": _sanitize_sources(c.get("sources") or []),
        })

    ids = {c["id"] for c in clean}
    for c in clean:
        c["prerequisites"] = [p for p in c["prerequisites"] if p in ids and p != c["id"]]

    return _topo_break_cycles(clean)


def _sanitize_sources(sources: list) -> list[dict]:
    out: list[dict] = []
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()[:120]
        url = str(s.get("url") or "").strip()[:500]
        note = str(s.get("note") or "").strip()[:180]
        kind = str(s.get("kind") or "other").strip().lower()
        if kind not in _SOURCE_KINDS:
            kind = "other"
        if url and not (url.startswith("https://") or url.startswith("http://")):
            url = ""
        if not title and url:
            title = url
        if not title:
            continue
        out.append({"title": title, "url": url, "kind": kind, "note": note})
        if len(out) >= 5:
            break
    return out


def _topo_break_cycles(concepts: list[dict]) -> list[dict]:
    """Kahn 拓扑排序；若有环，丢掉无法满足的前置边强行断环。返回按拓扑序排列的列表。"""
    placed: set[str] = set()
    order: list[dict] = []
    remaining = list(concepts)
    progress = True
    while remaining and progress:
        progress = False
        rest = []
        for c in remaining:
            if all(p in placed for p in c["prerequisites"]):
                order.append(c)
                placed.add(c["id"])
                progress = True
            else:
                rest.append(c)
        remaining = rest
    # 剩下的都在环里 → 裁掉未满足的前置，强制落位
    for c in remaining:
        c["prerequisites"] = [p for p in c["prerequisites"] if p in placed]
        order.append(c)
        placed.add(c["id"])
    return order


def topo_order(concepts: list[dict]) -> list[str]:
    """返回拓扑序的 id 列表（sanitize 已保证顺序，这里直接取）。"""
    return [c["id"] for c in concepts]


# ----------------------------- 生成 -----------------------------

def _format_weekly_themes(master: dict) -> str:
    weeks = (master or {}).get("weeks") or []
    lines = []
    for w in weeks:
        title = str(w.get("title", "")).strip()
        kos = [str(k).strip() for k in (w.get("key_outcomes") or []) if str(k).strip()]
        ko = ("：" + "；".join(kos)) if kos else ""
        lines.append(f"- 第 {w.get('week')} 周：{title}{ko}")
    return "\n".join(lines) or "（无周主题）"


def _fallback_linear(master: dict) -> list[dict]:
    """LLM 失败时的兜底：把每周 key_outcomes（无则 title）拆成线性链 Concept。"""
    weeks = (master or {}).get("weeks") or []
    concepts: list[dict] = []
    prev_id: Optional[str] = None
    idx = 0
    for w in weeks:
        wk = w.get("week") or 1
        outs = [str(k).strip() for k in (w.get("key_outcomes") or []) if str(k).strip() and "待" not in str(k)]
        names = outs or [str(w.get("title", "")).strip() or f"第 {wk} 周"]
        for name in names:
            idx += 1
            cid = f"c{idx}"
            concepts.append({
                "id": cid,
                "name": name,
                "week": wk,
                "difficulty": min(10, 2 + (wk - 1) // 2),
                "prerequisites": [prev_id] if prev_id else [],
            })
            prev_id = cid
    return sanitize(concepts)


def generate_graph(
    master: dict,
    *,
    n_min: int = 12,
    n_max: int = 30,
    client: Optional[LLMClient] = None,
) -> dict:
    """教研 Agent 生成 Concept 图谱；任何失败回退到线性兜底。返回 {concepts, _source}。"""
    ns = (master or {}).get("north_star") or {}
    prompt = (
        _load_prompt()
        .replace("{domain}", ns.get("domain", "") or "（未知领域）")
        .replace("{target}", ns.get("target", "") or "（未明确目标）")
        .replace("{baseline}", ns.get("baseline_summary", "") or "（未知基线）")
        .replace("{weekly_themes}", _format_weekly_themes(master))
        .replace("{n_min}", str(n_min))
        .replace("{n_max}", str(n_max))
    )
    with trace_llm(scene="graph"):
        try:
            client = client or get_default_client()
            text = client.chat([Message(role="user", content=prompt)], temperature=0.4, max_tokens=3000)
        except LLMError:
            mark_fallback()
            return {"concepts": _fallback_linear(master), "_source": "fallback"}

        json_str = _extract_json_object(text)
        if not json_str:
            mark_fallback()
            return {"concepts": _fallback_linear(master), "_source": "fallback"}
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            mark_fallback()
            return {"concepts": _fallback_linear(master), "_source": "fallback"}

        concepts = sanitize(data.get("concepts") or [])
        if len(concepts) < 3:
            mark_fallback()
            return {"concepts": _fallback_linear(master), "_source": "fallback"}
        return {"concepts": concepts, "_source": "llm"}


def load_or_generate(inst_dir: Path, master: dict, *, client: Optional[LLMClient] = None, regenerate: bool = False) -> dict:
    """读 concepts.json 缓存；没有或 regenerate 则生成并落盘。"""
    cache_path = inst_dir / "concepts.json"
    if cache_path.exists() and not regenerate:
        try:
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            if saved.get("concepts"):
                # 老缓存也跑一遍 sanitize，保证 DAG 不变性
                saved["concepts"] = sanitize(saved["concepts"])
                return saved
        except (json.JSONDecodeError, OSError):
            pass

    graph = generate_graph(master, client=client)
    from datetime import datetime
    graph["generated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        cache_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return graph


# ----------------------------- 状态 -----------------------------

def concept_status(concepts: list[dict], mastered_names: set[str]) -> dict[str, str]:
    """给定"已掌握知识点名"集合，算每个 Concept 的状态：mastered / frontier / locked。"""
    by_id = {c["id"]: c for c in concepts}
    out: dict[str, str] = {}
    for c in concepts:
        if c["name"] in mastered_names:
            out[c["id"]] = "mastered"
            continue
        prereqs_ok = all(
            (by_id.get(p) is not None and by_id[p]["name"] in mastered_names)
            for p in c["prerequisites"]
        )
        out[c["id"]] = "frontier" if prereqs_ok else "locked"
    return out


def build_unlocks(concepts: list[dict]) -> dict[str, list[str]]:
    """反向边：每个 Concept 解锁哪些后继。"""
    unlocks: dict[str, list[str]] = {c["id"]: [] for c in concepts}
    for c in concepts:
        for p in c["prerequisites"]:
            if p in unlocks:
                unlocks[p].append(c["id"])
    return unlocks


__all__ = [
    "generate_graph",
    "load_or_generate",
    "sanitize",
    "topo_order",
    "concept_status",
    "build_unlocks",
    "MASTERY_THRESHOLD",
    "GRAPH_PROMPT_PATH",
]
