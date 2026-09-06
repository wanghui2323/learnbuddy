"""每日运营分析。

先用确定性规则从事实表里提取痛点信号，避免早期把分析本身做成黑盒。
后续可以把 `issues` 和样例对话再交给 LLM 做更自然的归纳。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta

from core.db import get_conn

PAIN_KEYWORDS = {
    "收费/替代": ["收费", "付费", "免费", "替代", "WPS", "wps"],
    "不会/太难": ["不会", "不懂", "太难", "看不懂", "卡住", "卡壳"],
    "计划调整": ["调整", "修改", "编辑", "改计划", "换一个", "不合适"],
    "来源/可信度": ["来源", "权威", "链接", "文献", "书", "资料", "出处"],
    "登录/账号": ["登录", "密码", "注册", "账号", "后台"],
    "积分/额度": ["积分", "额度", "用完", "充值", "卡号"],
    "生成失败": ["失败", "报错", "生成不了", "没反应", "超时"],
}


def run_daily_analysis(day: str | None = None) -> dict:
    target_day = day or (date.today() - timedelta(days=1)).isoformat()
    start = f"{target_day}T00:00:00"
    end = (date.fromisoformat(target_day) + timedelta(days=1)).isoformat() + "T00:00:00"
    metrics = _metrics(start, end)
    issues = _issues(start, end, metrics)
    suggestions = _suggestions(issues, metrics)
    report = {
        "day": target_day,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": metrics,
        "issues": issues,
        "suggestions": suggestions,
    }
    _save(report)
    return report


def latest_reports(limit: int = 7) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT day, created_at, metrics, issues, suggestions
                 FROM daily_analysis_reports ORDER BY day DESC LIMIT ?""",
            (max(1, min(30, int(limit or 7))),),
        ).fetchall()
        return [_decode(r) for r in rows]
    finally:
        conn.close()


def _metrics(start: str, end: str) -> dict:
    conn = get_conn()
    try:
        conv = conn.execute(
            """SELECT COUNT(*) AS messages,
                      COUNT(DISTINCT user_id) AS users,
                      COUNT(DISTINCT session_id) AS sessions
                 FROM conversation_messages
                WHERE created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        user_msgs = conn.execute(
            """SELECT COUNT(*) AS n FROM conversation_messages
                WHERE role = 'user' AND created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        feedback = conn.execute(
            """SELECT rating, COUNT(*) AS n FROM feedback
                WHERE created_at >= ? AND created_at < ? GROUP BY rating""",
            (start, end),
        ).fetchall()
        traces = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END),0) AS errors,
                      COALESCE(SUM(CASE WHEN fallback = 1 THEN 1 ELSE 0 END),0) AS fallbacks,
                      COALESCE(AVG(latency_ms),0) AS avg_ms
                 FROM llm_traces WHERE created_at >= ? AND created_at < ?""",
            (start, end),
        ).fetchone()
        zero_credits = conn.execute(
            """SELECT COUNT(*) AS n
                 FROM users u
                 LEFT JOIN (
                   SELECT user_id, COALESCE(SUM(delta),0) AS balance
                     FROM credit_ledger GROUP BY user_id
                 ) c ON c.user_id = u.id
                WHERE COALESCE(c.balance,0) <= 0"""
        ).fetchone()
        cards = conn.execute(
            """SELECT COUNT(*) AS n FROM credit_cards
                WHERE redeemed_at >= ? AND redeemed_at < ?""",
            (start, end),
        ).fetchone()
        pain_counts, samples = _pain_counts(conn, start, end)
        fb = {r["rating"]: r["n"] for r in feedback}
        return {
            "messages": int(conv["messages"]),
            "user_messages": int(user_msgs["n"]),
            "active_users": int(conv["users"]),
            "sessions": int(conv["sessions"]),
            "feedback_negative": int(fb.get("down", 0) + fb.get("error", 0)),
            "feedback_up": int(fb.get("up", 0)),
            "llm_calls": int(traces["calls"]),
            "llm_errors": int(traces["errors"]),
            "llm_fallbacks": int(traces["fallbacks"]),
            "avg_latency_ms": int(traces["avg_ms"] or 0),
            "zero_credit_users": int(zero_credits["n"]),
            "card_redemptions": int(cards["n"]),
            "pain_counts": dict(pain_counts),
            "samples": samples,
        }
    finally:
        conn.close()


def _pain_counts(conn, start: str, end: str) -> tuple[Counter, dict]:
    rows = conn.execute(
        """SELECT created_at, user_id, content FROM conversation_messages
            WHERE role = 'user' AND created_at >= ? AND created_at < ?
            ORDER BY id DESC LIMIT 500""",
        (start, end),
    ).fetchall()
    counts: Counter = Counter()
    samples: dict[str, list[dict]] = {}
    for r in rows:
        text = r["content"] or ""
        for label, kws in PAIN_KEYWORDS.items():
            if any(k.lower() in text.lower() for k in kws):
                counts[label] += 1
                samples.setdefault(label, [])
                if len(samples[label]) < 3:
                    samples[label].append({
                        "created_at": r["created_at"],
                        "user_id": r["user_id"],
                        "content": text[:160],
                    })
    return counts, samples


def _issues(start: str, end: str, metrics: dict) -> list[dict]:
    issues: list[dict] = []
    for label, count in sorted(metrics["pain_counts"].items(), key=lambda x: x[1], reverse=True):
        if count:
            issues.append({
                "title": label,
                "severity": "high" if count >= 5 else "medium",
                "count": count,
                "evidence": metrics["samples"].get(label, []),
            })
    if metrics["feedback_negative"]:
        issues.append({"title": "负向反馈", "severity": "high", "count": metrics["feedback_negative"], "evidence": []})
    if metrics["llm_errors"] or metrics["llm_fallbacks"]:
        issues.append({
            "title": "生成质量/稳定性",
            "severity": "high" if metrics["llm_errors"] else "medium",
            "count": metrics["llm_errors"] + metrics["llm_fallbacks"],
            "evidence": [],
        })
    if metrics["zero_credit_users"]:
        issues.append({"title": "积分耗尽用户", "severity": "medium", "count": metrics["zero_credit_users"], "evidence": []})
    return issues[:10]


def _suggestions(issues: list[dict], metrics: dict) -> list[str]:
    out = []
    titles = {i["title"] for i in issues}
    if "收费/替代" in titles:
        out.append("把学习计划里的付费工具建议改成可替代选项，并在任务旁展示免费方案。")
    if "来源/可信度" in titles:
        out.append("优先补齐高频知识点的权威来源，避免用户反复追问出处。")
    if "计划调整" in titles:
        out.append("提升计划编辑入口的显眼度，并把改动记录展示给用户确认。")
    if "登录/账号" in titles:
        out.append("检查登录页提示和管理员后台权限说明，减少账号类阻塞。")
    if "积分/额度" in titles or metrics["zero_credit_users"]:
        out.append("在积分低于阈值时提前提醒，并准备管理员赠送/积分卡运营动作。")
    if "生成质量/稳定性" in titles:
        out.append("查看 LLM tracing 中的失败 scene，优先修复 fallback 高的生成链路。")
    return out or ["暂无明显痛点；继续观察对话样例、负向反馈和转化漏斗。"]


def _save(report: dict) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO daily_analysis_reports
                 (day, created_at, metrics, issues, suggestions)
               VALUES (?,?,?,?,?)""",
            (
                report["day"],
                report["created_at"],
                json.dumps(report["metrics"], ensure_ascii=False),
                json.dumps(report["issues"], ensure_ascii=False),
                json.dumps(report["suggestions"], ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _decode(row) -> dict:
    return {
        "day": row["day"],
        "created_at": row["created_at"],
        "metrics": json.loads(row["metrics"] or "{}"),
        "issues": json.loads(row["issues"] or "[]"),
        "suggestions": json.loads(row["suggestions"] or "[]"),
    }


__all__ = ["run_daily_analysis", "latest_reports", "PAIN_KEYWORDS"]
