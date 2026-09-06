"""SQLite 滑动窗口限流；适合当前单机部署且跨 worker/重启一致。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from core.db import get_conn


def actor_digest(actor: str) -> str:
    """只持久化 actor 的不可逆摘要，避免保存 IP/邮箱明文。"""
    return hashlib.sha256((actor or "anonymous").encode("utf-8")).hexdigest()


def check_rate_limit(
    *,
    actor: str,
    scope: str,
    limit: int,
    window_seconds: int = 60,
    now: datetime | None = None,
) -> dict:
    """消费一次额度，返回 allowed/remaining/retry_after。"""
    safe_limit = max(1, int(limit or 1))
    safe_window = max(1, int(window_seconds or 60))
    current = now or datetime.now()
    cutoff = current - timedelta(seconds=safe_window)
    digest = actor_digest(actor)
    safe_scope = (scope or "default")[:80]
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM rate_limit_events WHERE created_at < ?",
            (cutoff.isoformat(timespec="microseconds"),),
        )
        rows = conn.execute(
            """SELECT created_at FROM rate_limit_events
                WHERE actor = ? AND scope = ? AND created_at >= ?
                ORDER BY created_at ASC""",
            (digest, safe_scope, cutoff.isoformat(timespec="microseconds")),
        ).fetchall()
        if len(rows) >= safe_limit:
            oldest = datetime.fromisoformat(rows[0]["created_at"])
            retry_after = max(1, int((oldest + timedelta(seconds=safe_window) - current).total_seconds()) + 1)
            conn.commit()
            return {"allowed": False, "remaining": 0, "retry_after": retry_after}
        conn.execute(
            "INSERT INTO rate_limit_events (actor, scope, created_at) VALUES (?,?,?)",
            (digest, safe_scope, current.isoformat(timespec="microseconds")),
        )
        conn.commit()
        return {
            "allowed": True,
            "remaining": max(0, safe_limit - len(rows) - 1),
            "retry_after": 0,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["actor_digest", "check_rate_limit"]
