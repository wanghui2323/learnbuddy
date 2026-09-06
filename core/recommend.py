"""每日推荐编排器（Batch L · 环 A 的"决策"）。

回答"我今天该学什么"——一个**确定性的动态工作流**（L3）：按学习者当前状态路由，
把三路信号合成一份有优先级的今日计划，不调 LLM（能用规则别用模型，省 token、可测）。

三路信号 → 优先级：
1. **复习**（到期的已学知识点）——抗遗忘最高优先；
2. **新课**（知识图谱里的"前线"概念，前置已就绪）——推进进度；
3. **攻弱项**（独立答对率低 / 自评 cant 的主题）——补强。

输入只读既有引擎（learning 学情 + concepts 图谱），不重复造逻辑；图谱不存在就跳过新课，
不在推荐里触发昂贵的图谱生成。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from core import learning


def _current_week(master: dict) -> int:
    ns = master.get("north_star") or {}
    weeks = ns.get("weeks") or len(master.get("weeks") or []) or 1
    sd = (master.get("schedule") or {}).get("start_date")
    if not sd:
        return 1
    try:
        start = date.fromisoformat(sd)
    except ValueError:
        return 1
    diff = (date.today() - start).days
    if diff < 0:
        return 1
    return min(weeks, diff // 7 + 1)


def _week_theme(master: dict, w: int) -> str:
    for wk in master.get("weeks") or []:
        if wk.get("week") == w:
            return wk.get("title") or ""
    ns = master.get("north_star") or {}
    return ns.get("domain") or "本周内容"


def _graph_state(user_id: int, instance_id: str, inst_dir: Path, master: dict) -> tuple[list[str], list[dict], Optional[dict]]:
    """从已落盘的知识图谱取（前线概念名[受阶段闸门约束], 阶段列表, 当前阶段）。无图谱→([],[],None)。

    阶段闸门：只推荐"当前阶段及之前"的前线新课——避免夹生地冲进尚未解锁的阶段。
    """
    if not (inst_dir / "concepts.json").exists():
        return [], [], None
    try:
        from core.concepts import (
            MASTERY_THRESHOLD,
            concept_status,
            load_or_generate,
        )
        from core.mastery import concept_mastery, mastered_set
        from core.phases import compute_phases, current_phase, current_phase_max_week

        graph = load_or_generate(inst_dir, master)
        concepts = graph.get("concepts") or []
        mastery = concept_mastery(user_id, instance_id)
        mastered = mastered_set(mastery, MASTERY_THRESHOLD)
        status = concept_status(concepts, mastered)
        items = [
            {"week": c.get("week"), "status": status.get(c["id"], "locked"), "name": c["name"]}
            for c in concepts
        ]
        phases = compute_phases(master, items)
        max_week = current_phase_max_week(phases)
        front = [c for c in concepts if status.get(c["id"]) == "frontier"]
        if max_week is not None:  # 阶段闸门：不越级推新课
            front = [c for c in front if (c.get("week") or 0) <= max_week]
        front.sort(key=lambda c: (c.get("week", 99), c.get("difficulty", 5)))
        return [c["name"] for c in front], phases, current_phase(phases)
    except Exception:  # noqa: BLE001 — 推荐不应因图谱异常而失败
        return [], [], None


def daily_plan(
    *,
    user_id: int,
    instance_id: str,
    inst_dir: Path,
    master: dict,
    max_items: int = 4,
    review_ratio: float = 0.7,
) -> dict:
    """合成今日推荐（混合复习 ~7:3）。返回 {date, week, theme, items[], counts, mix, phase, rationale}。

    item: {kind: review|new|weak, topic, why, due?}
    混合复习：有到期 backlog 时，约 70% 名额给复习（抗遗忘优先），但始终给新课留 ≥1 个位置；
    新课受**阶段闸门**约束（不越级）。
    """
    a = learning.analytics_summary(user_id, instance_id)
    review = a.get("review_queue") or []
    weak = a.get("weak_points") or []
    w = _current_week(master)
    theme = _week_theme(master, w)
    frontier, phases, cur_phase = _graph_state(user_id, instance_id, inst_dir, master)

    items: list[dict] = []
    used: set[str] = set()
    has_history = a.get("topics_studied", 0) > 0

    # 混合复习：复习名额 = ceil(max_items * review_ratio)，但留 ≥1 给新课
    review_cap = max(1, round(max_items * review_ratio)) if review else 0
    review_cap = min(review_cap, max_items - 1)  # 永远给新课留一个位置

    for r in review[:review_cap]:
        topic = (r.get("topic") or "").strip()
        if not topic or topic in used:
            continue
        used.add(topic)
        iv = r.get("interval_days")
        tail = f"，复习对了下次 {iv} 天后再见" if iv else ""
        items.append({
            "kind": "review",
            "topic": topic,
            "why": f"到期复习（自评 {r.get('self_label', '—')}）——趁还没忘巩固一遍{tail}。",
            "due": r.get("next_due"),
        })

    n_new = 1 if review else 2  # 没有复习压力时多推一节新课
    new_added = 0
    for name in frontier:
        if new_added >= n_new or len(items) >= max_items:
            break
        if name in used:
            continue
        used.add(name)
        items.append({"kind": "new", "topic": name, "why": "前置已就绪的新知识点——该往前推一步了。"})
        new_added += 1

    # 图谱没给新课（无图谱/前线清空/阶段闸门挡住）→ 用本周主题兜一节新课
    if new_added == 0 and theme and theme not in used and len(items) < max_items:
        used.add(theme)
        items.append({"kind": "new", "topic": theme, "why": f"本周主题「{theme}」——按计划推进。"})

    for wpt in weak:
        if len(items) >= max_items:
            break
        topic = (wpt.get("topic") or "").strip()
        if not topic or topic in used:
            continue
        used.add(topic)
        pct = wpt.get("objective_pct")
        tail = f"独立答对 {pct}%" if pct is not None else "自评还不会"
        items.append({"kind": "weak", "topic": topic, "why": f"弱项（{tail}）——专门攻一下。"})

    items = items[:max_items]
    mix = {"review": 0, "new": 0, "weak": 0}
    for it in items:
        mix[it["kind"]] = mix.get(it["kind"], 0) + 1

    if not has_history:
        rationale = f"还没有学练记录——先从「{theme or '第一节'}」开始，建立第一条学情基线。"
    elif review:
        rationale = f"有 {len(review)} 个知识点到期复习，今天按 7:3 先抗遗忘（{mix['review']} 复习）再推进新课。"
    elif weak:
        rationale = "进度不错、没有到期复习——重点补一下弱项，再学新内容。"
    else:
        rationale = "状态很好，没有到期复习和明显弱项——大胆往前推进新课。"

    phase_brief = None
    if cur_phase:
        phase_brief = {
            "id": cur_phase.get("id"),
            "title": cur_phase.get("title"),
            "ratio": cur_phase.get("ratio"),
            "gate_ratio": cur_phase.get("gate_ratio"),
            "status": cur_phase.get("status"),
        }

    return {
        "date": date.today().isoformat(),
        "week": w,
        "theme": theme,
        "items": items,
        "counts": {"due": len(review), "weak": len(weak), "frontier": len(frontier)},
        "mix": mix,
        "phase": phase_brief,
        "rationale": rationale,
    }


__all__ = ["daily_plan"]
