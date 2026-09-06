"""认证逻辑：注册 / 登录 / 会话 token（V1 用户体系）。

安全取向（标准库实现，零额外依赖）：
- 密码用 PBKDF2-HMAC-SHA256 + 每用户随机盐，存为 `pbkdf2_sha256$迭代$盐$哈希`。
- 会话 token 用 secrets 生成，存 DB，带过期时间；登录态靠 httponly cookie 带 token。
- 比对用 hmac.compare_digest，防时序侧信道。

注意：本模块不碰 HTTP/cookie，只做"邮箱+密码 ↔ 用户/会话"的纯逻辑，方便单测。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.db import get_conn

_PBKDF2_ITERATIONS = 200_000
_SESSION_TTL_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """注册/登录类业务错误（邮箱占用、密码弱、凭证错等）。"""


class RegistrationClosedError(AuthError):
    """当前运行版本不再接受新账号。"""


@dataclass
class User:
    id: int
    email: str
    name: str
    is_admin: bool = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "is_admin": self.is_admin,
        }


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters)
        )
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


def _row_to_user(row: sqlite3.Row) -> User:
    keys = row.keys()
    is_admin = bool(row["is_admin"]) if "is_admin" in keys else False
    return User(
        id=row["id"], email=row["email"], name=row["name"] or "", is_admin=is_admin
    )


def get_user_by_email(email: str) -> User | None:
    """按邮箱取用户；不存在返回 None。"""
    email = (email or "").strip().lower()
    if not email:
        return None
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return _row_to_user(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> User | None:
    """按 id 取用户；不存在返回 None。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None
    finally:
        conn.close()


def register_user(
    email: str,
    password: str,
    name: str = "",
    *,
    require_first_user: bool = False,
) -> User:
    """注册新用户。邮箱已占用 / 格式错 / 密码过短 → AuthError。"""
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not _EMAIL_RE.match(email):
        raise AuthError("邮箱格式不正确")
    if len(password or "") < 6:
        raise AuthError("密码至少 6 位")

    password_hash = hash_password(password)
    conn = get_conn()
    try:
        if require_first_user:
            # 加写锁后再计数，避免两个首次请求并发创建两个 owner。
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            if int(count or 0) > 0:
                raise RegistrationClosedError(
                    "个人版已完成 owner 初始化，公开注册已关闭"
                )
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            raise AuthError("该邮箱已注册")
        now = datetime.now().isoformat(timespec="seconds")
        display = name or email.split("@")[0]
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, is_admin, created_at)"
            " VALUES (?,?,?,?,?)",
            (email, display, password_hash, 0, now),
        )
        conn.commit()
        user_id = cur.lastrowid
    finally:
        conn.close()
    from core.credits import ensure_signup_grant

    ensure_signup_grant(user_id)
    return User(id=user_id, email=email, name=display, is_admin=False)


def authenticate(email: str, password: str) -> User:
    """校验邮箱+密码，成功返回 User，失败抛 AuthError（不区分账号/密码错，防枚举）。"""
    email = (email or "").strip().lower()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise AuthError("邮箱或密码错误")
        return _row_to_user(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


def create_session(user_id: int) -> str:
    """为用户创建会话 token，返回 token。"""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (
                token,
                user_id,
                now.isoformat(timespec="seconds"),
                (now + timedelta(days=_SESSION_TTL_DAYS)).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_user_by_token(token: str) -> User | None:
    """凭 token 取用户；token 不存在或已过期 → None（过期顺手清理）。"""
    if not token:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS _exp
                 FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = ?""",
            (token,),
        ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["_exp"]) < datetime.now():
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        return _row_to_user(row)
    finally:
        conn.close()


def delete_session(token: str) -> None:
    if not token:
        return
    conn = get_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "AuthError",
    "RegistrationClosedError",
    "User",
    "hash_password",
    "verify_password",
    "register_user",
    "authenticate",
    "create_session",
    "get_user_by_token",
    "delete_session",
]
