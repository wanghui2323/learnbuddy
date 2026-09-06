"""学情数据层（V2）。

把"学习过程"沉淀成事实数据，再派生出真正有用的学情：
- record_lesson_result：一次学练完成 → 写入完成记录 + 逐题作答
- topic_stats：每个知识点的双坐标（独立答对率 / 自评协作掌握度）+ 复习到期
- weak_points / review_queue：弱项清单 + 反遗忘复习队列
- wrong_questions：错题本（最近一次仍答错的题）
- analytics_summary：学情页一次性数据包

数据存 SQLite（learning_answers / lesson_completions），跨设备、可喂记忆系统。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional

from core.db import get_conn
from core.scheduler import sm2, next_interval

# 自评三档（前端传码值）
SELF_RATINGS = {"cant", "with_help", "independent"}
_SELF_SCORE = {"cant": 25, "with_help": 60, "independent": 100}
_SELF_LABEL = {"cant": "还不行", "with_help": "借助能用", "independent": "能独立用"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_date(s: str) -> date:
    try:
        return datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return date.today()


# ----------------------------- 写入 -----------------------------

def record_lesson_result(
    *,
    user_id: int,
    instance_id: str,
    topic: str,
    week: Optional[int],
    answers: list[dict],
    self_rating: Optional[str],
) -> dict:
    """记录一次学练完成：完成记录 + 逐题作答。返回小结。"""
    topic = (topic or "").strip() or "（未命名主题）"
    rating = self_rating if self_rating in SELF_RATINGS else None
    correct_count = sum(1 for a in answers if a.get("correct"))
    total = len(answers)
    ts = _now()

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO lesson_completions
               (user_id, instance_id, topic, week, correct_count, total, self_rating, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, instance_id, topic, week, correct_count, total, rating, ts),
        )
        for a in answers:
            opts = a.get("options")
            conn.execute(
                """INSERT INTO learning_answers
                   (user_id, instance_id, topic, question, options, answer_index, chosen_index, correct, why, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    instance_id,
                    topic,
                    str(a.get("question", "")).strip(),
                    json.dumps(opts, ensure_ascii=False) if isinstance(opts, list) else None,
                    a.get("answer_index"),
                    a.get("chosen_index"),
                    1 if a.get("correct") else 0,
                    str(a.get("why", "")).strip() or None,
                    ts,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"correct": correct_count, "total": total, "self_rating": rating}


def record_artifact(
    *,
    user_id: int,
    instance_id: str,
    topic: str,
    content: str,
    kind: str = "recall",
    prompt: str = "",
) -> dict:
    """记录一个学习产物（4-2-1 的"1 产出物"）。返回 {id, created_at}。"""
    topic = (topic or "").strip() or "（未命名主题）"
    content = (content or "").strip()
    if not content:
        return {"ok": False}
    ts = _now()
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO artifacts (user_id, instance_id, topic, kind, prompt, content, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, instance_id, topic, kind or "recall", (prompt or "").strip() or None, content, ts),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "created_at": ts}
    finally:
        conn.close()


def list_artifacts(user_id: int, instance_id: str, limit: int = 100) -> list[dict]:
    """该空间内的学习产物，按时间倒序。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, topic, kind, prompt, content, created_at
               FROM artifacts WHERE user_id=? AND instance_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, instance_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "kind": r["kind"],
            "prompt": r["prompt"] or "",
            "content": r["content"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ----------------------------- 派生 -----------------------------

def topic_stats(user_id: int, instance_id: str) -> list[dict]:
    """每个学过的知识点：独立答对率 + 自评 + 复习到期。"""
    conn = get_conn()
    try:
        comp_rows = conn.execute(
            """SELECT topic,
                      COUNT(*) AS times,
                      MAX(created_at) AS last_at
               FROM lesson_completions
               WHERE user_id=? AND instance_id=?
               GROUP BY topic""",
            (user_id, instance_id),
        ).fetchall()

        # 每个 topic 最近一次自评 + 完整自评序列（SM-2 调度回放用）
        latest_rating = {}
        rating_seq: dict = {}
        for r in conn.execute(
            """SELECT topic, self_rating FROM lesson_completions
               WHERE user_id=? AND instance_id=?
               ORDER BY created_at ASC""",
            (user_id, instance_id),
        ):
            rating_seq.setdefault(r["topic"], []).append(r["self_rating"])
            if r["self_rating"]:
                latest_rating[r["topic"]] = r["self_rating"]

        # 每个 topic 客观答对率（全部作答）
        obj = {}
        for r in conn.execute(
            """SELECT topic, AVG(correct) AS pct, COUNT(*) AS n
               FROM learning_answers
               WHERE user_id=? AND instance_id=?
               GROUP BY topic""",
            (user_id, instance_id),
        ):
            obj[r["topic"]] = (round((r["pct"] or 0) * 100), r["n"])
    finally:
        conn.close()

    today = date.today()
    out = []
    for r in comp_rows:
        topic = r["topic"]
        rating = latest_rating.get(topic)
        times = r["times"]
        last_at = r["last_at"]
        objective_pct, n_ans = obj.get(topic, (None, 0))
        sched = next_interval(rating_seq.get(topic, []))
        due_date = _to_date(last_at) + timedelta(days=sched["interval"])
        out.append(
            {
                "topic": topic,
                "objective_pct": objective_pct,
                "answers_count": n_ans,
                "self_rating": rating,
                "self_label": _SELF_LABEL.get(rating, "—"),
                "self_score": _SELF_SCORE.get(rating),
                "times_studied": times,
                "last_studied": last_at,
                "interval_days": sched["interval"],
                "ease": sched["ease"],
                "next_due": due_date.isoformat(),
                "due": due_date <= today,
            }
        )
    out.sort(key=lambda x: x["last_studied"] or "", reverse=True)
    return out


def weak_points(user_id: int, instance_id: str) -> list[dict]:
    """弱项：客观答对率偏低 或 自评仍'还不行'。"""
    stats = topic_stats(user_id, instance_id)
    weak = [
        s
        for s in stats
        if (s["objective_pct"] is not None and s["objective_pct"] < 60)
        or s["self_rating"] == "cant"
    ]
    weak.sort(key=lambda x: (x["objective_pct"] if x["objective_pct"] is not None else 0))
    return weak


def review_queue(user_id: int, instance_id: str) -> list[dict]:
    """到期复习队列（按到期日升序，最该复习的在前）。"""
    due = [s for s in topic_stats(user_id, instance_id) if s["due"]]
    due.sort(key=lambda x: x["next_due"])
    return due


def studied_topics(user_id: int, instance_id: str) -> list[str]:
    conn = get_conn()
    try:
        return [
            r["topic"]
            for r in conn.execute(
                """SELECT DISTINCT topic FROM lesson_completions
                   WHERE user_id=? AND instance_id=?""",
                (user_id, instance_id),
            )
        ]
    finally:
        conn.close()


def wrong_questions(user_id: int, instance_id: str) -> list[dict]:
    """错题本：每道题取最近一次作答，仍答错的收进来。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT topic, question, options, answer_index, chosen_index, correct, why, created_at
               FROM learning_answers
               WHERE user_id=? AND instance_id=?
               ORDER BY created_at ASC""",
            (user_id, instance_id),
        ).fetchall()
    finally:
        conn.close()

    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["question"]] = r  # 后写覆盖 → 最终是最近一次
    out = []
    for r in latest.values():
        if r["correct"]:
            continue
        try:
            opts = json.loads(r["options"]) if r["options"] else []
        except (json.JSONDecodeError, TypeError):
            opts = []
        out.append(
            {
                "topic": r["topic"],
                "question": r["question"],
                "options": opts,
                "answer_index": r["answer_index"],
                "chosen_index": r["chosen_index"],
                "why": r["why"] or "",
                "created_at": r["created_at"],
            }
        )
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


def daily_accuracy(user_id: int, instance_id: str, days: int = 30) -> list[dict]:
    """近 N 天每日答题数与正确率（趋势）。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT substr(created_at,1,10) AS d,
                      COUNT(*) AS n,
                      SUM(correct) AS c
               FROM learning_answers
               WHERE user_id=? AND instance_id=?
               GROUP BY d""",
            (user_id, instance_id),
        ).fetchall()
    finally:
        conn.close()
    by_day = {r["d"]: (r["n"], r["c"] or 0) for r in rows}
    today = date.today()
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        n, c = by_day.get(d, (0, 0))
        out.append({"date": d, "answered": n, "correct": c, "pct": round(c / n * 100) if n else 0})
    return out


def analytics_summary(user_id: int, instance_id: str) -> dict:
    """学情页一次性数据包。"""
    stats = topic_stats(user_id, instance_id)
    conn = get_conn()
    try:
        a = conn.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(correct),0) AS c
               FROM learning_answers WHERE user_id=? AND instance_id=?""",
            (user_id, instance_id),
        ).fetchone()
        lessons = conn.execute(
            """SELECT COUNT(*) AS n FROM lesson_completions
               WHERE user_id=? AND instance_id=?""",
            (user_id, instance_id),
        ).fetchone()["n"]
    finally:
        conn.close()

    total_ans = a["n"] or 0
    total_correct = a["c"] or 0
    weak = [s for s in stats if (s["objective_pct"] is not None and s["objective_pct"] < 60) or s["self_rating"] == "cant"]
    weak.sort(key=lambda x: (x["objective_pct"] if x["objective_pct"] is not None else 0))
    review = [s for s in stats if s["due"]]
    review.sort(key=lambda x: x["next_due"])

    try:
        from core.baseline import profile_for_instance

        baseline_profile = profile_for_instance(user_id=user_id, instance_id=instance_id)
    except Exception:  # noqa: BLE001 — 旧库未迁移时学情仍可用
        baseline_profile = None

    return {
        "baseline_profile": baseline_profile,
        "lessons_done": lessons,
        "topics_studied": len(stats),
        "answers_total": total_ans,
        "answers_correct": total_correct,
        "objective_pct": round(total_correct / total_ans * 100) if total_ans else 0,
        "topics": stats,
        "weak_points": weak,
        "review_queue": review,
        "trend": daily_accuracy(user_id, instance_id),
    }


__all__ = [
    "record_lesson_result",
    "topic_stats",
    "weak_points",
    "review_queue",
    "studied_topics",
    "wrong_questions",
    "daily_accuracy",
    "analytics_summary",
    "SELF_RATINGS",
]
