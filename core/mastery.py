"""掌握度推断（规则版 BKT · Batch 2A）。

方案 §3.3 / 优化点 #3：不用"正确率"粗暴衡量掌握度，而是给每个 Concept 一个
**0–1 的掌握度概率**。V0 先用可解释的规则版（rule-based BKT），用户量起来后再
升级到 DKT。

影响因子（当前规则版）：
- 客观答对率（独立作答，权重 0.6）
- 自评协作掌握度（cant/with_help/independent，权重 0.4）
- 练习次数（次数太少 → 置信度低，向下收缩）
- 复习到期（遗忘衰减：到期未复习 → 掌握度打折）

掌握度概率驱动：路径前沿/锁定、复习调度、（后续）项目难度选择。
按 topic（= Concept.name）聚合，和 lesson / 学情天然对齐。
"""

from __future__ import annotations

from core.learning import topic_stats


def _prob_from_stat(s: dict) -> dict:
    obj = s.get("objective_pct")          # 0-100 或 None
    self_score = s.get("self_score")      # 0/25/60/100 或 None
    times = s.get("times_studied") or 0

    o = (obj / 100.0) if obj is not None else None
    se = (self_score / 100.0) if self_score is not None else None
    if o is not None and se is not None:
        base = 0.6 * o + 0.4 * se
    elif o is not None:
        base = o
    elif se is not None:
        base = se
    else:
        base = 0.0

    # 置信度收缩：只练过 1 次 → 向 0.5 收一点（证据不足，别轻易判"已掌握"）
    if times <= 1:
        base = base * 0.85 + 0.5 * 0.15

    # 遗忘衰减：到期未复习 → 打折
    decay = 0.85 if s.get("due") else 1.0
    prob = max(0.0, min(1.0, base * decay))

    return {
        "prob": round(prob, 3),
        "objective_pct": obj,
        "self_score": self_score,
        "self_label": s.get("self_label", "—"),
        "times": times,
        "due": bool(s.get("due")),
    }


def concept_mastery(user_id: int, instance_id: str) -> dict[str, dict]:
    """返回 {concept_name: {prob, objective_pct, self_score, ...}}，只含学过的知识点。"""
    return {s["topic"]: _prob_from_stat(s) for s in topic_stats(user_id, instance_id)}


def mastered_set(mastery: dict[str, dict], threshold: float) -> set[str]:
    """掌握度 ≥ 阈值的知识点名集合（= 已征服）。"""
    return {name for name, m in mastery.items() if m.get("prob", 0.0) >= threshold}


__all__ = ["concept_mastery", "mastered_set"]
