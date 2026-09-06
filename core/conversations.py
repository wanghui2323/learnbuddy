"""持久对话会话与消息流水。

ConversationEngine 仍负责单次请求中的状态机；这里负责 user+session 归属、
服务重启后的历史恢复，以及需求/漏斗/学习卡点分析所需的事实消息。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.db import get_conn


MAX_CONTENT_CHARS = 8000
MAX_SESSION_ID_CHARS = 120


class ConversationSessionOwnershipError(ValueError):
    """session_id 已归属于其他用户。"""


def _safe_session_id(session_id: str) -> str:
    value = (session_id or "").strip()[:MAX_SESSION_ID_CHARS]
    if not value:
        raise ValueError("session_id 必填")
    return value


def claim_session(user_id: int, session_id: str) -> None:
    """原子认领会话；已被其他账号认领时拒绝复用。"""
    safe_session_id = _safe_session_id(session_id)
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO conversation_sessions
                 (session_id, user_id, created_at, updated_at)
               VALUES (?,?,?,?)""",
            (safe_session_id, user_id, now, now),
        )
        row = conn.execute(
            "SELECT user_id FROM conversation_sessions WHERE session_id = ?",
            (safe_session_id,),
        ).fetchone()
        if row is None or int(row["user_id"]) != int(user_id):
            conn.rollback()
            raise ConversationSessionOwnershipError("该对话属于另一个账号，请刷新后重试")
        conn.execute(
            "UPDATE conversation_sessions SET updated_at = ? WHERE session_id = ?",
            (now, safe_session_id),
        )
        conn.commit()
    finally:
        conn.close()


def session_messages(user_id: int, session_id: str, limit: int = 60) -> list[dict]:
    """按时间正序返回某个已归属会话最近的可恢复消息。"""
    safe_session_id = _safe_session_id(session_id)
    safe_limit = max(1, min(120, int(limit or 60)))
    conn = get_conn()
    try:
        owner = conn.execute(
            "SELECT user_id FROM conversation_sessions WHERE session_id = ?",
            (safe_session_id,),
        ).fetchone()
        if owner is None or int(owner["user_id"]) != int(user_id):
            raise ConversationSessionOwnershipError("该对话不属于当前账号")
        rows = conn.execute(
            """SELECT role, content, scene, created_at FROM (
                   SELECT id, role, content, scene, created_at
                     FROM conversation_messages
                    WHERE user_id = ? AND session_id = ?
                      AND role IN ('user', 'assistant')
                      AND scene IN ('chat', 'chat_anchor', 'chat_choice')
                    ORDER BY id DESC LIMIT ?
               ) ORDER BY id ASC""",
            (user_id, safe_session_id, safe_limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def clear_session(user_id: int, session_id: str) -> None:
    """清除当前用户的一段会话，供显式“重新开始”使用。"""
    safe_session_id = _safe_session_id(session_id)
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM conversation_messages WHERE user_id = ? AND session_id = ?",
            (user_id, safe_session_id),
        )
        conn.execute(
            "DELETE FROM conversation_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, safe_session_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_message(
    *,
    user_id: Optional[int],
    role: str,
    content: str,
    session_id: str = "",
    scene: str = "chat",
    instance_id: Optional[str] = None,
) -> None:
    content = (content or "").strip()
    if not content:
        return
    role = role if role in {"user", "assistant", "system"} else "user"
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO conversation_messages
                 (user_id, session_id, instance_id, scene, role, content, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                user_id,
                (session_id or "")[:MAX_SESSION_ID_CHARS],
                instance_id,
                (scene or "chat")[:40],
                role,
                content[:MAX_CONTENT_CHARS],
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_messages(user_id: int, limit: int = 80) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT created_at, session_id, instance_id, scene, role, content
                 FROM conversation_messages
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?""",
            (user_id, max(1, min(500, int(limit or 80)))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


__all__ = [
    "ConversationSessionOwnershipError",
    "claim_session",
    "session_messages",
    "clear_session",
    "record_message",
    "recent_messages",
    "MAX_CONTENT_CHARS",
    "MAX_SESSION_ID_CHARS",
]
