"""阶段闸门 Phase Gate（Batch 2C · #5）。

把"路径"按 master 的 **months（月/阶段）** 切成若干阶段；每个阶段聚合其知识点的真实掌握度，
算出闸门是否通关：**掌握达标率 ≥ 阈值才解锁下一阶段**。这是在 concept 级前置锁之上，
再加一道"阶段级"的进度闸门——避免学习者夹生地冲进下一阶段。

设计：纯函数、确定性、不调 LLM。输入是 master + 已带 status/掌握度的 concept 列表（来自 /graph）。
- 阶段 = master.months（每个 month 有 weeks[]）；concept 按 week 归入对应阶段。
- 掌握达标率 = 该阶段 status=mastered 的 concept 占比。
- 闸门：达标率 ≥ gate_ratio → cleared；第一个未通关阶段 = current；其后全部 locked。
- 空阶段（无 concept）视为已通关，不阻断。
"""

from __future__ import annotations

from typing import Optional

GATE_RATIO = 0.7  # 一个阶段掌握 ≥70% 的知识点即通关，解锁下一阶段


def _week_to_month(months: list[dict]) -> dict:
    w2m: dict = {}
    for m in months:
        for w in (m.get("weeks") or []):
            w2m[w] = m.get("id")
    return w2m


def compute_phases(master: dict, concept_items: list[dict], *, gate_ratio: float = GATE_RATIO) -> list[dict]:
    """按阶段聚合掌握进度 + 闸门状态。

    concept_items：每个含 `week` 与 `status`（mastered/frontier/locked）。
    返回 list[{id,title,deliverable,date_range,weeks,total,mastered,ratio,gate_ratio,gate_passed,status}]。
    status ∈ cleared / current / locked。无 months → 返回 []（调用方退化为无阶段闸门）。
    """
    months = (master or {}).get("months") or []
    if not months:
        return []

    w2m = _week_to_month(months)
    order = [m.get("id") for m in months]
    last_mid = order[-1]
    buckets: dict = {mid: [] for mid in order}
    for c in concept_items:
        mid = w2m.get(c.get("week"))
        if mid not in buckets:
            mid = last_mid  # 周次不在任何阶段 → 归到最后阶段，避免丢失
        buckets[mid].append(c)

    phases: list[dict] = []
    prev_all_cleared = True
    for m in months:
        cs = buckets.get(m.get("id"), [])
        total = len(cs)
        mastered = sum(1 for c in cs if c.get("status") == "mastered")
        ratio = round(mastered / total, 3) if total else 0.0
        cleared = (total == 0) or (ratio >= gate_ratio)
        if cleared:
            status = "cleared"
        elif prev_all_cleared:
            status = "current"
        else:
            status = "locked"
        phases.append({
            "id": m.get("id"),
            "title": m.get("title", ""),
            "deliverable": m.get("deliverable", ""),
            "date_range": m.get("date_range", ""),
            "weeks": m.get("weeks") or [],
            "total": total,
            "mastered": mastered,
            "ratio": ratio,
            "gate_ratio": gate_ratio,
            "gate_passed": cleared,
            "status": status,
        })
        prev_all_cleared = prev_all_cleared and cleared

    return phases


def current_phase(phases: list[dict]) -> Optional[dict]:
    """当前阶段 = 第一个未通关阶段；全通关 → 最后一个阶段；无阶段 → None。"""
    if not phases:
        return None
    return next((p for p in phases if p["status"] == "current"), phases[-1])


def current_phase_max_week(phases: list[dict]) -> Optional[int]:
    """允许学习的最大周次（阶段闸门用）：当前阶段的最后一周；无阶段 → None（不限制）。"""
    cur = current_phase(phases)
    if not cur:
        return None
    weeks = cur.get("weeks") or []
    return max(weeks) if weeks else None


__all__ = ["compute_phases", "current_phase", "current_phase_max_week", "GATE_RATIO"]
