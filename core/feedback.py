"""用户反馈 + 质量信号（Batch L · 环 B 的"眼睛"）。

- record_feedback：落一条用户对 AI 产出的显式反馈（👍 up / 👎 down / ✋ error 纠错）。
- feedback_summary：反馈聚合（总量 / down 率 / 按 scene / 最近纠错），给管理后台质量面板。
- trace_summary：LLM 调用聚合（调用数 / fallback 率 / 平均延迟 / 按 scene），来自 llm_traces。

都是只读聚合 + 一次写入，纯 SQLite，不调 LLM。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.db import get_conn

_RATINGS = {"up", "down", "error"}


def record_feedback(
    *,
    user_id: Optional[int],
    instance_id: Optional[str],
    scene: str,
    rating: str,
    topic: str = "",
    reason: str = "",
) -> dict:
    """记录一条反馈。rating 必须是 up/down/error，否则抛 ValueError。返回 {id, created_at}。"""
    rating = (rating or "").strip().lower()
    if rating not in _RATINGS:
        raise ValueError(f"rating 必须是 {_RATINGS} 之一")
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO feedback
                 (user_id, instance_id, scene, topic, rating, reason, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, instance_id, (scene or "lesson").strip(), (topic or "").strip()[:200],
             rating, (reason or "").strip()[:1000], now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "created_at": now}
    finally:
        conn.close()


def feedback_summary(limit_recent: int = 20) -> dict:
    """全局反馈聚合（管理后台用）。"""
    conn = get_conn()
    try:
        totals = conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 COALESCE(SUM(rating='up'),0)    AS up,
                 COALESCE(SUM(rating='down'),0)  AS down,
                 COALESCE(SUM(rating='error'),0) AS error
               FROM feedback"""
        ).fetchone()
        by_scene = conn.execute(
            """SELECT scene,
                 COUNT(*) AS n,
                 COALESCE(SUM(rating='up'),0)              AS up,
                 COALESCE(SUM(rating IN ('down','error')),0) AS neg
               FROM feedback GROUP BY scene ORDER BY n DESC"""
        ).fetchall()
        recent_neg = conn.execute(
            """SELECT created_at, scene, topic, rating, reason
               FROM feedback WHERE rating IN ('down','error') AND COALESCE(reason,'') <> ''
               ORDER BY id DESC LIMIT ?""",
            (int(limit_recent),),
        ).fetchall()
        t = dict(totals) if totals else {"total": 0, "up": 0, "down": 0, "error": 0}
        neg = (t["down"] or 0) + (t["error"] or 0)
        t["neg"] = neg
        t["neg_rate"] = round(neg / t["total"], 3) if t["total"] else 0.0
        return {
            "totals": t,
            "by_scene": [dict(r) for r in by_scene],
            "recent_negative": [dict(r) for r in recent_neg],
        }
    finally:
        conn.close()


def trace_summary(limit_recent: int = 20) -> dict:
    """全局 LLM 调用聚合（管理后台质量面板用）。"""
    conn = get_conn()
    try:
        totals = conn.execute(
            """SELECT
                 COUNT(*) AS calls,
                 COALESCE(SUM(fallback),0)        AS fallbacks,
                 COALESCE(SUM(1-ok),0)            AS errors,
                 COALESCE(AVG(latency_ms),0)      AS avg_ms
               FROM llm_traces"""
        ).fetchone()
        by_scene = conn.execute(
            """SELECT scene,
                 COUNT(*) AS calls,
                 COALESCE(SUM(fallback),0)   AS fallbacks,
                 COALESCE(AVG(latency_ms),0) AS avg_ms
               FROM llm_traces GROUP BY scene ORDER BY calls DESC"""
        ).fetchall()
        recent = conn.execute(
            """SELECT created_at, scene, ok, fallback, latency_ms, error
               FROM llm_traces ORDER BY id DESC LIMIT ?""",
            (int(limit_recent),),
        ).fetchall()
        t = dict(totals) if totals else {"calls": 0, "fallbacks": 0, "errors": 0, "avg_ms": 0}
        t["avg_ms"] = int(t["avg_ms"] or 0)
        t["fallback_rate"] = round((t["fallbacks"] or 0) / t["calls"], 3) if t["calls"] else 0.0
        return {
            "totals": t,
            "by_scene": [dict(r) for r in by_scene],
            "recent": [dict(r) for r in recent],
        }
    finally:
        conn.close()


__all__ = ["record_feedback", "feedback_summary", "trace_summary"]
