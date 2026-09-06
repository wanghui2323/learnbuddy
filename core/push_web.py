"""Web Push 发送（轨道 A）+ 未配置优雅降级。

严格对齐 core.embeddings 的降级模式（ADR-009）：未配 VAPID 时不发、不报错、
返回 skipped 信号让调用方（scheduler / admin）可感知，而不是抛异常。

pywebpush 是同步阻塞调用（内部用 requests）→ 调用方用 asyncio.to_thread 包，
不要在 async 路径里直接调。
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("itutor.push_web")


def is_configured() -> bool:
    """VAPID 私钥 + subject 都配了才 True。"""
    return bool(os.getenv("ITUTOR_VAPID_PRIVATE_KEY") and os.getenv("ITUTOR_VAPID_SUBJECT"))


def _private_key_bytes() -> bytes | None:
    """env 里的私钥转 bytes。支持 DER base64 单行（推荐）或 PEM。"""
    raw = os.getenv("ITUTOR_VAPID_PRIVATE_KEY", "").strip()
    if not raw:
        return None
    if "BEGIN" in raw:  # PEM 多行
        return raw.encode()
    import base64

    try:
        return base64.b64decode(raw)  # DER base64 单行
    except Exception:  # noqa: BLE001
        return raw.encode()


async def send_to_user(user_id: int, payload: dict) -> dict:
    """给该用户所有订阅设备发推送。

    未配 VAPID → {skipped:'no_vapid'}（不抛异常）。
    无订阅 → {skipped:'no_sub'}。
    否则 {sent, failed, reasons[]}。单 sub 失败不影响其他。
    """
    import asyncio

    from core.push_subs import list_subscriptions

    if not is_configured():
        log.info("push skipped: VAPID not configured")
        return {"skipped": "no_vapid"}

    subs = list_subscriptions(user_id)
    if not subs:
        return {"skipped": "no_sub"}

    payload_json = json.dumps(payload, ensure_ascii=False)
    key = _private_key_bytes()
    subject = os.getenv("ITUTOR_VAPID_SUBJECT")
    sent = failed = 0
    reasons: list[str] = []
    for sub in subs:
        ok, reason = await asyncio.to_thread(_send_one, sub, payload_json, key, subject)
        if ok:
            sent += 1
        else:
            failed += 1
            if reason:
                reasons.append(reason)
    return {"sent": sent, "failed": failed, "reasons": reasons}


def _send_one(sub: dict, payload_json: str, key: bytes, subject: str) -> tuple[bool, str | None]:
    """同步发一条。返回 (ok, reason)。410/404 → 触发删订阅。"""
    from core.push_subs import _delete_stale_subscription

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # noqa: BLE001 — 依赖未装时降级，不崩
        log.error("pywebpush 未安装；运行 pip install pywebpush")
        return False, "no_lib"

    subscription = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
    }
    try:
        webpush(
            subscription_info=subscription,
            data=payload_json,
            vapid_private_key=key,
            vapid_claims={"sub": subject},
            ttl=86400,  # 推送服务保留 24h，离线也能收到
        )
        return True, None
    except WebPushException as e:
        code = _status_code(e)
        if code in (404, 410):
            _delete_stale_subscription(
                subscription_id=sub["id"],
                user_id=sub["user_id"],
                endpoint=sub["endpoint"],
            )
            return False, f"gone({code})"
        if code == 429:
            return False, "rate_limited"
        log.warning("push failed (code=%s): %s", code, e)
        return False, f"error({code or '?'})"
    except Exception as e:  # noqa: BLE001 — 网络/库异常不拖垮整个发送循环
        log.warning("push error: %s", e)
        return False, "exception"


def _status_code(e: Exception) -> int | None:
    resp = getattr(e, "response", None)
    if resp is None:
        return None
    return getattr(resp, "status_code", None)


__all__ = ["is_configured", "send_to_user"]
