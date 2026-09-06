"""外部 Agent 身份绑定与用户级短期令牌。

信任边界：
- 网页登录态只负责生成一次性绑定码。
- OpenClaw 乔接用服务端 secret 证明自己，并传入通道插件观测到的 sender id。
- 模型从不传 user_id；远程 MCP 只接受本模块签发的短期 token。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import get_conn


DEFAULT_PROVIDER = "feishu"
DEFAULT_ACCOUNT_ID = "default"
DEFAULT_SCOPES = ("mcp",)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,160}$")


class AgentIntegrationError(ValueError):
    """可安全返回给调用方的集成错误。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _token_secret() -> bytes:
    secret = (os.getenv("ITUTOR_AGENT_TOKEN_SECRET") or "").strip()
    if len(secret) < 32:
        raise AgentIntegrationError("Agent 集成未配置：ITUTOR_AGENT_TOKEN_SECRET 至少 32 位")
    return secret.encode("utf-8")


def _validated_id(value: str, label: str, *, default: str = "") -> str:
    normalized = (value or default).strip()
    if not _SAFE_ID.fullmatch(normalized):
        raise AgentIntegrationError(f"{label} 格式不合法")
    return normalized


def configured() -> bool:
    """是否已配置签名秘钥；不返回秘钥本身。"""
    return len((os.getenv("ITUTOR_AGENT_TOKEN_SECRET") or "").strip()) >= 32


def verify_bridge_secret(candidate: str) -> None:
    expected = (os.getenv("ITUTOR_OPENCLAW_BRIDGE_TOKEN") or "").strip()
    if len(expected) < 24:
        raise AgentIntegrationError("OpenClaw 乔接未配置")
    if not hmac.compare_digest(expected, (candidate or "").strip()):
        raise AgentIntegrationError("OpenClaw 乔接凭证无效")


def _code_digest(code: str) -> str:
    normalized = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if not normalized:
        return ""
    return hmac.new(_token_secret(), normalized.encode("ascii", errors="ignore"), hashlib.sha256).hexdigest()


def _public_binding(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    external_id = data.get("external_user_id") or ""
    data["external_user_hint"] = (
        f"{external_id[:4]}…{external_id[-4:]}" if len(external_id) > 10 else external_id
    )
    return data


def get_active_binding(
    user_id: int,
    *,
    provider: str = DEFAULT_PROVIDER,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict[str, Any] | None:
    provider = _validated_id(provider, "provider")
    account_id = _validated_id(account_id, "account_id")
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM channel_bindings
               WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'active'
               ORDER BY id DESC LIMIT 1""",
            (int(user_id), provider, account_id),
        ).fetchone()
        return _public_binding(row)
    finally:
        conn.close()


def create_binding_code(
    user_id: int,
    *,
    provider: str = DEFAULT_PROVIDER,
    account_id: str = DEFAULT_ACCOUNT_ID,
    ttl_seconds: int = 600,
    now: datetime | None = None,
) -> dict[str, Any]:
    """生成一次绑定码。明文只在此次返回，库内仅保存 HMAC 摘要。"""
    provider = _validated_id(provider, "provider")
    account_id = _validated_id(account_id, "account_id")
    current = now or _utc_now()
    ttl = max(120, min(int(ttl_seconds), 1800))
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    code = f"LB-{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    digest = _code_digest(code)
    created_at = _iso(current)
    expires_at = _iso(current + timedelta(seconds=ttl))
    code_id = uuid.uuid4().hex
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE channel_binding_codes
               SET status = 'expired'
               WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'pending'""",
            (int(user_id), provider, account_id),
        )
        conn.execute(
            """INSERT INTO channel_binding_codes
               (id,user_id,provider,account_id,code_digest,status,expires_at,created_at)
               VALUES (?,?,?,?,?,'pending',?,?)""",
            (code_id, int(user_id), provider, account_id, digest, expires_at, created_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"code": code, "expires_at": expires_at, "provider": provider, "account_id": account_id}


def redeem_binding_code(
    code: str,
    *,
    external_user_id: str,
    provider: str = DEFAULT_PROVIDER,
    account_id: str = DEFAULT_ACCOUNT_ID,
    tenant_key: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """用可信通道 sender id 单次兑换绑定。"""
    provider = _validated_id(provider, "provider")
    account_id = _validated_id(account_id, "account_id")
    external_user_id = _validated_id(external_user_id, "external_user_id")
    tenant_key = _validated_id(tenant_key, "tenant_key", default="none") if tenant_key else ""
    digest = _code_digest(code)
    if not digest:
        raise AgentIntegrationError("绑定码无效")
    current = now or _utc_now()
    current_iso = _iso(current)
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT * FROM channel_binding_codes
               WHERE code_digest = ? AND provider = ? AND account_id = ? LIMIT 1""",
            (digest, provider, account_id),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise AgentIntegrationError("绑定码无效或已使用")
        if _parse_iso(row["expires_at"]) <= current:
            conn.execute(
                "UPDATE channel_binding_codes SET status = 'expired' WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            raise AgentIntegrationError("绑定码已过期，请在 LearnBuddy 重新生成")

        occupied = conn.execute(
            """SELECT user_id FROM channel_bindings
               WHERE provider = ? AND account_id = ? AND external_user_id = ?
                 AND status = 'active' LIMIT 1""",
            (provider, account_id, external_user_id),
        ).fetchone()
        if occupied is not None and int(occupied["user_id"]) != int(row["user_id"]):
            raise AgentIntegrationError("该飞书身份已绑定其他 LearnBuddy 账号")

        existing = conn.execute(
            """SELECT * FROM channel_bindings
               WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'active'
               LIMIT 1""",
            (int(row["user_id"]), provider, account_id),
        ).fetchone()
        if existing is not None and existing["external_user_id"] == external_user_id:
            binding_id = int(existing["id"])
            conn.execute(
                """UPDATE channel_bindings
                   SET tenant_key = ?, updated_at = ?, last_seen_at = ? WHERE id = ?""",
                (tenant_key or None, current_iso, current_iso, binding_id),
            )
        else:
            conn.execute(
                """UPDATE channel_bindings
                   SET status = 'revoked', revoked_at = ?, updated_at = ?
                   WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'active'""",
                (current_iso, current_iso, int(row["user_id"]), provider, account_id),
            )
            cursor = conn.execute(
                """INSERT INTO channel_bindings
                   (user_id,provider,account_id,tenant_key,external_user_id,status,
                    created_at,updated_at,last_seen_at)
                   VALUES (?,?,?,?,?,'active',?,?,?)""",
                (
                    int(row["user_id"]), provider, account_id, tenant_key or None,
                    external_user_id, current_iso, current_iso, current_iso,
                ),
            )
            binding_id = int(cursor.lastrowid)
        conn.execute(
            """UPDATE channel_binding_codes
               SET status = 'consumed', consumed_at = ? WHERE id = ? AND status = 'pending'""",
            (current_iso, row["id"]),
        )
        conn.commit()
        binding = conn.execute("SELECT * FROM channel_bindings WHERE id = ?", (binding_id,)).fetchone()
        return _public_binding(binding) or {}
    except AgentIntegrationError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_binding(
    user_id: int,
    *,
    provider: str = DEFAULT_PROVIDER,
    account_id: str = DEFAULT_ACCOUNT_ID,
    now: datetime | None = None,
) -> dict[str, Any]:
    provider = _validated_id(provider, "provider")
    account_id = _validated_id(account_id, "account_id")
    current_iso = _iso(now or _utc_now())
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """UPDATE channel_bindings
               SET status = 'revoked', revoked_at = ?, updated_at = ?
               WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'active'""",
            (current_iso, current_iso, int(user_id), provider, account_id),
        )
        conn.execute(
            """UPDATE channel_binding_codes SET status = 'expired'
               WHERE user_id = ? AND provider = ? AND account_id = ? AND status = 'pending'""",
            (int(user_id), provider, account_id),
        )
        conn.commit()
        return {"ok": True, "revoked": max(0, int(cursor.rowcount or 0))}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_active_binding(
    *,
    external_user_id: str,
    provider: str = DEFAULT_PROVIDER,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict[str, Any]:
    provider = _validated_id(provider, "provider")
    account_id = _validated_id(account_id, "account_id")
    external_user_id = _validated_id(external_user_id, "external_user_id")
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM channel_bindings
               WHERE provider = ? AND account_id = ? AND external_user_id = ?
                 AND status = 'active' LIMIT 1""",
            (provider, account_id, external_user_id),
        ).fetchone()
        if row is None:
            raise AgentIntegrationError("该飞书身份尚未绑定 LearnBuddy")
        return _public_binding(row) or {}
    finally:
        conn.close()


def issue_agent_token(
    binding: dict[str, Any],
    *,
    scopes: tuple[str, ...] | list[str] = DEFAULT_SCOPES,
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _utc_now()
    ttl = max(30, min(int(ttl_seconds), 600))
    payload = {
        "v": 1,
        "sub": int(binding["user_id"]),
        "binding_id": int(binding["id"]),
        "scopes": sorted({_validated_id(str(scope), "scope") for scope in scopes}),
        "iat": int(current.timestamp()),
        "exp": int((current + timedelta(seconds=ttl)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    encoded = _b64url(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64url(hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return {"access_token": f"{encoded}.{signature}", "token_type": "Bearer", "expires_in": ttl, "scopes": payload["scopes"]}


def verify_agent_token(
    token: str,
    *,
    required_scope: str = "mcp",
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        encoded, supplied = (token or "").split(".", 1)
        expected = _b64url(hmac.new(_token_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, supplied):
            raise AgentIntegrationError("Agent token 签名无效")
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except AgentIntegrationError:
        raise
    except Exception as exc:
        raise AgentIntegrationError("Agent token 格式无效") from exc

    current = now or _utc_now()
    if payload.get("v") != 1 or int(payload.get("exp") or 0) <= int(current.timestamp()):
        raise AgentIntegrationError("Agent token 已过期")
    if required_scope and required_scope not in (payload.get("scopes") or []):
        raise AgentIntegrationError("Agent token 缺少必要权限")

    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT id,user_id,status FROM channel_bindings
               WHERE id = ? AND user_id = ? AND status = 'active' LIMIT 1""",
            (int(payload.get("binding_id") or 0), int(payload.get("sub") or 0)),
        ).fetchone()
        if row is None:
            raise AgentIntegrationError("Agent token 对应的绑定已失效")
    finally:
        conn.close()
    return {**payload, "user_id": int(payload["sub"])}


__all__ = [
    "AgentIntegrationError",
    "configured",
    "create_binding_code",
    "get_active_binding",
    "issue_agent_token",
    "redeem_binding_code",
    "resolve_active_binding",
    "revoke_binding",
    "verify_agent_token",
    "verify_bridge_secret",
]
