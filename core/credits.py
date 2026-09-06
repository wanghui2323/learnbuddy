"""积分账户与积分卡。

积分是用户可理解的业务额度，token 仍保留为真实消耗流水：
- 新用户默认赠送 1000 积分；
- LLM 调用按 `ITUTOR_TOKENS_PER_POINT` 折算扣减；
- 管理员可直接赠送积分或生成积分卡，用户兑换后生效。
"""

from __future__ import annotations

import math
import os
import secrets
import string
from datetime import date, datetime, timedelta
from typing import Optional

from core.db import get_conn
from core.runtime import runtime_edition


def credits_enabled() -> bool:
    if runtime_edition() == "personal":
        return False
    return os.getenv("ITUTOR_CREDITS_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def default_signup_points() -> int:
    return max(0, int(os.getenv("ITUTOR_SIGNUP_POINTS", "1000") or 0))


def tokens_per_point() -> int:
    return max(1, int(os.getenv("ITUTOR_TOKENS_PER_POINT", "1000") or 1000))


def study_reward_points() -> int:
    return max(0, int(os.getenv("ITUTOR_STUDY_REWARD_POINTS", "20") or 0))


def study_reward_daily_limit() -> int:
    return max(0, int(os.getenv("ITUTOR_STUDY_REWARD_DAILY_LIMIT", "60") or 0))


def points_for_tokens(total_tokens: int) -> int:
    return max(0, int(math.ceil(max(0, int(total_tokens or 0)) / tokens_per_point())))


def ensure_signup_grant(user_id: int) -> None:
    """给历史/新用户补一笔默认注册送分；幂等，不改已有流水。"""
    if not credits_enabled() or not user_id:
        return
    pts = default_signup_points()
    if pts <= 0:
        return
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM credit_ledger WHERE user_id = ? AND reason = 'signup_bonus' LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return
        conn.execute(
            """INSERT INTO credit_ledger
                 (user_id, delta, reason, detail, created_at)
               VALUES (?,?,?,?,?)""",
            (user_id, pts, "signup_bonus", "注册默认赠送", _now()),
        )
        conn.commit()
    finally:
        conn.close()


def grant_points(user_id: int, points: int, *, admin_id: Optional[int] = None, reason: str = "admin_grant", detail: str = "", card_code: str = "") -> dict:
    points = int(points or 0)
    if points <= 0:
        raise ValueError("赠送积分必须大于 0")
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO credit_ledger
                 (user_id, delta, reason, detail, admin_id, card_code, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, points, reason, detail[:300], admin_id, card_code, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return balance(user_id)


def spend_for_tokens(user_id: Optional[int], total_tokens: int, *, scene: str = "", instance_id: Optional[str] = None) -> int:
    if not credits_enabled() or not user_id:
        return 0
    ensure_signup_grant(user_id)
    pts = points_for_tokens(total_tokens)
    if pts <= 0:
        return 0
    detail = f"{scene or 'llm'} · {int(total_tokens or 0)} tokens"
    if instance_id:
        detail += f" · {instance_id}"
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO credit_ledger
                 (user_id, delta, reason, detail, created_at)
               VALUES (?,?,?,?,?)""",
            (user_id, -pts, "llm_usage", detail[:300], _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return pts


def reward_study_completion(user_id: int, *, instance_id: str, topic: str) -> dict:
    """完成一节学习后的少量积分奖励。

    规则保守：同一用户/空间/知识点/自然日只奖励一次，且有每日上限，防刷。
    """
    empty = {"awarded": False, "points": 0, "reason": "disabled", "daily_limit": study_reward_daily_limit()}
    if not credits_enabled() or not user_id:
        return empty
    pts = study_reward_points()
    limit = study_reward_daily_limit()
    if pts <= 0 or limit <= 0:
        return empty
    ensure_signup_grant(user_id)
    today = date.today()
    day = today.isoformat()
    next_day = (today + timedelta(days=1)).isoformat()
    safe_topic = (topic or "（未命名主题）").strip()[:120]
    key = f"{day}|{instance_id}|{safe_topic}"
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM credit_ledger WHERE user_id = ? AND reason = 'study_reward' AND detail = ? LIMIT 1",
            (user_id, key),
        ).fetchone()
        if existing:
            return {"awarded": False, "points": 0, "reason": "already_rewarded", "daily_limit": limit}
        row = conn.execute(
            """SELECT COALESCE(SUM(delta),0) AS pts
                 FROM credit_ledger
                WHERE user_id = ? AND reason = 'study_reward'
                  AND created_at >= ? AND created_at < ?""",
            (user_id, day, next_day),
        ).fetchone()
        earned_today = int(row["pts"] or 0)
        if earned_today >= limit:
            return {"awarded": False, "points": 0, "reason": "daily_limit", "daily_limit": limit}
        award = min(pts, limit - earned_today)
        conn.execute(
            """INSERT INTO credit_ledger
                 (user_id, delta, reason, detail, created_at)
               VALUES (?,?,?,?,?)""",
            (user_id, award, "study_reward", key, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "awarded": True,
        "points": award,
        "reason": "study_completed",
        "daily_limit": limit,
        "balance": balance(user_id)["balance"],
    }


def balance(user_id: int) -> dict:
    if credits_enabled():
        ensure_signup_grant(user_id)
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END),0) AS granted,
                      COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END),0) AS spent,
                      COALESCE(SUM(delta),0) AS balance
                 FROM credit_ledger WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        recent = conn.execute(
            """SELECT created_at, delta, reason, detail, card_code
                 FROM credit_ledger WHERE user_id = ? ORDER BY id DESC LIMIT 30""",
            (user_id,),
        ).fetchall()
        return {
            "enabled": credits_enabled(),
            "granted": int(row["granted"]),
            "spent": int(row["spent"]),
            "balance": int(row["balance"]),
            "tokens_per_point": tokens_per_point(),
            "default_signup_points": default_signup_points(),
            "study_reward_points": study_reward_points(),
            "study_reward_daily_limit": study_reward_daily_limit(),
            "recent": [dict(r) for r in recent],
        }
    finally:
        conn.close()


def balances_map() -> dict[int, dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT user_id,
                      COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END),0) AS granted,
                      COALESCE(SUM(CASE WHEN delta < 0 THEN -delta ELSE 0 END),0) AS spent,
                      COALESCE(SUM(delta),0) AS balance
                 FROM credit_ledger GROUP BY user_id"""
        ).fetchall()
        return {
            int(r["user_id"]): {
                "granted": int(r["granted"]),
                "spent": int(r["spent"]),
                "balance": int(r["balance"]),
            }
            for r in rows
        }
    finally:
        conn.close()


def create_cards(points: int, count: int, *, admin_id: int, note: str = "") -> list[dict]:
    points = int(points or 0)
    count = max(1, min(100, int(count or 1)))
    if points <= 0:
        raise ValueError("积分卡面额必须大于 0")
    cards = []
    conn = get_conn()
    try:
        for _ in range(count):
            code = _new_code()
            conn.execute(
                """INSERT INTO credit_cards
                     (code, points, status, note, created_by, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (code, points, "unused", note[:200], admin_id, _now()),
            )
            cards.append({"code": code, "points": points, "status": "unused", "note": note})
        conn.commit()
    finally:
        conn.close()
    return cards


def redeem_card(user_id: int, code: str) -> dict:
    code = _format_code(_clean_code(code))
    if not code:
        raise ValueError("请输入积分卡号")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM credit_cards WHERE code = ?", (code,)).fetchone()
        if not row:
            raise ValueError("积分卡不存在")
        if row["status"] != "unused":
            raise ValueError("积分卡已被使用")
        now = _now()
        conn.execute(
            "UPDATE credit_cards SET status = 'redeemed', redeemed_by = ?, redeemed_at = ? WHERE code = ?",
            (user_id, now, code),
        )
        conn.execute(
            """INSERT INTO credit_ledger
                 (user_id, delta, reason, detail, card_code, created_at)
               VALUES (?,?,?,?,?,?)""",
            (user_id, int(row["points"]), "card_redeem", row["note"] or "积分卡充值", code, now),
        )
        conn.commit()
    finally:
        conn.close()
    return balance(user_id)


def list_cards(limit: int = 80) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.code, c.points, c.status, c.note, c.created_at, c.redeemed_at,
                      u.email AS redeemed_email
                 FROM credit_cards c
                 LEFT JOIN users u ON u.id = c.redeemed_by
                ORDER BY c.created_at DESC LIMIT ?""",
            (max(1, min(500, int(limit or 80))),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _format_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _new_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return _format_code(raw)


__all__ = [
    "credits_enabled",
    "default_signup_points",
    "tokens_per_point",
    "study_reward_points",
    "study_reward_daily_limit",
    "points_for_tokens",
    "ensure_signup_grant",
    "grant_points",
    "spend_for_tokens",
    "reward_study_completion",
    "balance",
    "balances_map",
    "create_cards",
    "redeem_card",
    "list_cards",
]
