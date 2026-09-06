"""Token 消耗记录 + 用户行为日志 + 管理后台聚合查询。

设计取向：
- **零侵入采集**：LLM 客户端只认一个"usage 观察者"回调；本模块用 contextvars
  把"当前用户 / 场景 / 实例"注入回调，server 在处理请求时 `with usage_context(...)`
  设置一次即可，不必修改 engine/generator/content 的函数签名。
  （asyncio.to_thread 会自动复制当前 contextvars，所以同步生成也能正确归属。）
- 事实表 usage 存 token 流水，events 存行为；查询时再聚合。
- 成本为**估算**：按可配置单价（¥/百万 token）算，仅供参考。
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

from core.credits import balance as credit_balance
from core.credits import balances_map, credits_enabled, ensure_signup_grant, spend_for_tokens
from core.db import get_conn
from core.runtime import runtime_edition

# 当前请求的归属上下文（None = 不记录，例如 CLI / 测试）
_ctx_user_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "itutor_usage_user_id", default=None
)
_ctx_scene: contextvars.ContextVar[str] = contextvars.ContextVar(
    "itutor_usage_scene", default="other"
)
_ctx_instance: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "itutor_usage_instance", default=None
)


def current_user_id() -> Optional[int]:
    """当前归属上下文的 user_id（供 tracing 复用，避免改生成函数签名）。"""
    return _ctx_user_id.get()


def current_scene() -> str:
    return _ctx_scene.get()


def current_instance() -> Optional[str]:
    return _ctx_instance.get()


@contextmanager
def usage_context(
    user_id: Optional[int], scene: str, instance_id: Optional[str] = None
) -> Iterator[None]:
    """在此上下文内产生的 LLM 调用，token 都归属到 (user_id, scene, instance_id)。"""
    t1 = _ctx_user_id.set(user_id)
    t2 = _ctx_scene.set(scene)
    t3 = _ctx_instance.set(instance_id)
    try:
        yield
    finally:
        _ctx_user_id.reset(t1)
        _ctx_scene.reset(t2)
        _ctx_instance.reset(t3)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def record_usage(
    user_id: Optional[int],
    *,
    model: str,
    scene: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    instance_id: Optional[str] = None,
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO usage
                 (user_id, created_at, model, scene,
                  prompt_tokens, completion_tokens, total_tokens, instance_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                user_id,
                datetime.now().isoformat(timespec="seconds"),
                model,
                scene,
                int(prompt_tokens or 0),
                int(completion_tokens or 0),
                int(total_tokens or 0),
                instance_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    spend_for_tokens(user_id, total_tokens, scene=scene, instance_id=instance_id)


def record_current_usage(model: str, usage: dict) -> None:
    """LLM 客户端的 usage 观察者：从 contextvars 取归属并落库。

    usage = {"prompt_tokens":..,"completion_tokens":..,"total_tokens":..}
    无 total 时回退为 prompt+completion。
    """
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    tt = int(usage.get("total_tokens") or (pt + ct))
    record_usage(
        _ctx_user_id.get(),
        model=model,
        scene=_ctx_scene.get(),
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=tt,
        instance_id=_ctx_instance.get(),
    )


def log_event(
    user_id: Optional[int],
    action: str,
    detail: str = "",
    instance_id: Optional[str] = None,
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO events (user_id, created_at, action, detail, instance_id)"
            " VALUES (?,?,?,?,?)",
            (
                user_id,
                datetime.now().isoformat(timespec="seconds"),
                action,
                (detail or "")[:500],
                instance_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 成本估算（仅参考，单价可经环境变量覆盖，单位 ¥/百万 token）
# ---------------------------------------------------------------------------


def _price_in() -> float:
    return float(os.getenv("ITUTOR_PRICE_INPUT", "2"))


def _price_out() -> float:
    return float(os.getenv("ITUTOR_PRICE_OUTPUT", "8"))


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        prompt_tokens / 1_000_000 * _price_in()
        + completion_tokens / 1_000_000 * _price_out(),
        4,
    )


# ---------------------------------------------------------------------------
# 查询：用户自己看
# ---------------------------------------------------------------------------


def user_summary(user_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(prompt_tokens),0) AS pt,
                      COALESCE(SUM(completion_tokens),0) AS ct,
                      COALESCE(SUM(total_tokens),0) AS tt
                 FROM usage WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        by_scene = conn.execute(
            """SELECT scene, COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS tt
                 FROM usage WHERE user_id = ? GROUP BY scene ORDER BY tt DESC""",
            (user_id,),
        ).fetchall()
        return {
            "calls": row["calls"],
            "prompt_tokens": row["pt"],
            "completion_tokens": row["ct"],
            "total_tokens": row["tt"],
            "est_cost": estimate_cost(row["pt"], row["ct"]),
            "by_scene": [dict(r) for r in by_scene],
        }
    finally:
        conn.close()


def user_recent_usage(user_id: int, limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT created_at, model, scene, prompt_tokens, completion_tokens,
                      total_tokens, instance_id
                 FROM usage WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 查询：管理后台
# ---------------------------------------------------------------------------


def admin_overview() -> dict:
    """全局总览 + 每个用户的消耗与活跃度。"""
    conn = get_conn()
    try:
        totals = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(prompt_tokens),0) AS pt,
                      COALESCE(SUM(completion_tokens),0) AS ct,
                      COALESCE(SUM(total_tokens),0) AS tt
                 FROM usage"""
        ).fetchone()
        users = conn.execute(
            """SELECT u.id, u.email, u.name, u.is_admin, u.token_limit, u.created_at,
                      COALESCE(SUM(g.total_tokens),0)      AS tt,
                      COALESCE(SUM(g.prompt_tokens),0)     AS pt,
                      COALESCE(SUM(g.completion_tokens),0) AS ct,
                      COUNT(g.id)                          AS calls,
                      MAX(g.created_at)                    AS last_usage
                 FROM users u LEFT JOIN usage g ON g.user_id = u.id
                GROUP BY u.id ORDER BY tt DESC"""
        ).fetchall()
        month_start = _month_start()
        mrows = conn.execute(
            "SELECT user_id, COALESCE(SUM(total_tokens),0) AS mt FROM usage"
            " WHERE created_at >= ? GROUP BY user_id",
            (month_start,),
        ).fetchall()
        month_map = {r["user_id"]: r["mt"] for r in mrows}
        if credits_enabled():
            for u in users:
                ensure_signup_grant(u["id"])
        credit_map = balances_map() if credits_enabled() else {}
        default_limit = _default_user_limit()
        ev = conn.execute(
            """SELECT u.id AS user_id, COUNT(e.id) AS events,
                      MAX(e.created_at) AS last_event
                 FROM users u LEFT JOIN events e ON e.user_id = u.id
                GROUP BY u.id"""
        ).fetchall()
        ev_map = {r["user_id"]: r for r in ev}

        user_rows = []
        for u in users:
            e = ev_map.get(u["id"])
            limit = u["token_limit"] if u["token_limit"] is not None else default_limit
            user_rows.append(
                {
                    "id": u["id"],
                    "email": u["email"],
                    "name": u["name"],
                    "is_admin": bool(u["is_admin"]),
                    "created_at": u["created_at"],
                    "total_tokens": u["tt"],
                    "prompt_tokens": u["pt"],
                    "completion_tokens": u["ct"],
                    "calls": u["calls"],
                    "est_cost": estimate_cost(u["pt"], u["ct"]),
                    "month_tokens": month_map.get(u["id"], 0),
                    "limit": limit,
                    "limit_custom": u["token_limit"] is not None,
                    "events": (e["events"] if e else 0),
                    "last_active": (e["last_event"] if e else None) or u["last_usage"],
                    "credits": credit_map.get(u["id"], {"granted": 0, "spent": 0, "balance": 0}),
                }
            )
        total_credit_balance = sum((u["credits"] or {}).get("balance", 0) for u in user_rows)
        return {
            "totals": {
                "users": len(user_rows),
                "calls": totals["calls"],
                "prompt_tokens": totals["pt"],
                "completion_tokens": totals["ct"],
                "total_tokens": totals["tt"],
                "est_cost": estimate_cost(totals["pt"], totals["ct"]),
                "credit_balance": total_credit_balance,
            },
            "users": user_rows,
            "daily": daily_usage(30),
        }
    finally:
        conn.close()


def admin_user_detail(user_id: int, limit: int = 80) -> dict:
    conn = get_conn()
    try:
        u = conn.execute(
            "SELECT id, email, name, is_admin, token_limit, created_at"
            " FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not u:
            return {}
        events = conn.execute(
            """SELECT created_at, action, detail, instance_id
                 FROM events WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        ulimit = get_user_limit(user_id)
        return {
            "user": dict(u),
            "summary": user_summary(user_id),
            "quota": {
                "month_tokens": month_tokens(user_id),
                "limit": ulimit,
                "limit_custom": u["token_limit"] is not None,
            },
            "recent_usage": user_recent_usage(user_id, limit),
            "recent_events": [dict(r) for r in events],
            "daily": daily_usage(30, user_id),
            "credits": credit_balance(user_id),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 趋势（按天）
# ---------------------------------------------------------------------------


def daily_usage(days: int = 30, user_id: Optional[int] = None) -> list[dict]:
    """最近 days 天的逐日 token 消耗（连续日期，无数据补 0）。

    user_id=None → 全局；否则单用户。返回按日期升序。
    """
    days = max(1, min(days, 365))
    start = date.today() - timedelta(days=days - 1)
    conn = get_conn()
    try:
        where = "WHERE created_at >= ?"
        params: list = [start.isoformat()]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)
        rows = conn.execute(
            f"""SELECT substr(created_at,1,10) AS day,
                       COALESCE(SUM(total_tokens),0) AS tt,
                       COUNT(*) AS calls
                  FROM usage {where}
                 GROUP BY day""",
            params,
        ).fetchall()
    finally:
        conn.close()
    by_day = {r["day"]: r for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        r = by_day.get(d)
        out.append(
            {"date": d, "total_tokens": (r["tt"] if r else 0), "calls": (r["calls"] if r else 0)}
        )
    return out


# ---------------------------------------------------------------------------
# 额度（按自然月）
# ---------------------------------------------------------------------------


def _month_start() -> str:
    return datetime.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")


def _default_user_limit() -> int:
    return int(os.getenv("ITUTOR_USER_TOKEN_LIMIT", "0") or 0)


def _global_limit() -> int:
    return int(os.getenv("ITUTOR_GLOBAL_TOKEN_LIMIT", "0") or 0)


def month_tokens(user_id: Optional[int] = None) -> int:
    conn = get_conn()
    try:
        if user_id is not None:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS tt FROM usage"
                " WHERE user_id = ? AND created_at >= ?",
                (user_id, _month_start()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(total_tokens),0) AS tt FROM usage"
                " WHERE created_at >= ?",
                (_month_start(),),
            ).fetchone()
        return int(row["tt"])
    finally:
        conn.close()


def get_user_limit(user_id: int) -> int:
    """该用户本月上限：库内 token_limit 优先，NULL 用环境默认；0 = 无限。"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT token_limit FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if row and row["token_limit"] is not None:
        return int(row["token_limit"])
    return _default_user_limit()


def set_user_limit(user_id: int, limit: Optional[int]) -> None:
    """设置个人上限。limit=None → 回退环境默认；0 → 无限。"""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET token_limit = ? WHERE id = ?",
            (None if limit is None else int(limit), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def check_quota(user_id: int) -> dict:
    """检查是否还能调用。返回 {allowed, scope, used, limit, remaining}。

    优先判用户月度上限，再判全局月度上限；0/未设 = 无限。
    """
    if runtime_edition() == "personal":
        return {
            "allowed": True,
            "scope": "provider",
            "used": 0,
            "limit": None,
            "remaining": None,
        }

    glimit = _global_limit()
    if glimit:
        gused = month_tokens(None)
        if gused >= glimit:
            return {
                "allowed": False, "scope": "global",
                "used": gused, "limit": glimit, "remaining": 0,
            }
    if credits_enabled():
        c = credit_balance(user_id)
        if c["balance"] <= 0:
            return {
                "allowed": False,
                "scope": "credits",
                "used": c["spent"],
                "limit": c["granted"],
                "remaining": 0,
                "credits": c,
            }
        return {
            "allowed": True,
            "scope": "credits",
            "used": c["spent"],
            "limit": c["granted"],
            "remaining": c["balance"],
            "credits": c,
        }
    user_limit = get_user_limit(user_id)
    used = month_tokens(user_id)
    if user_limit and used >= user_limit:
        return {
            "allowed": False, "scope": "user",
            "used": used, "limit": user_limit, "remaining": 0,
        }
    remaining = (user_limit - used) if user_limit else None
    return {
        "allowed": True, "scope": "user",
        "used": used, "limit": user_limit, "remaining": remaining,
    }


__all__ = [
    "usage_context",
    "record_usage",
    "record_current_usage",
    "log_event",
    "estimate_cost",
    "user_summary",
    "user_recent_usage",
    "admin_overview",
    "admin_user_detail",
    "daily_usage",
    "month_tokens",
    "get_user_limit",
    "set_user_limit",
    "check_quota",
]
