"""飞书主动提醒发送器；未配应用凭证时明确降级，不领取队列。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from core.reminders import claim_pending_deliveries, complete_delivery


_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0, "app_id": ""}
_OPEN_ID = re.compile(r"(?:^|:)(ou_[A-Za-z0-9_-]+)$")


class FeishuSendError(RuntimeError):
    pass


def configured() -> bool:
    return bool((os.getenv("FEISHU_APP_ID") or "").strip() and (os.getenv("FEISHU_APP_SECRET") or "").strip())


def _receive_id(external_user_id: str) -> str:
    value = (external_user_id or "").strip()
    match = _OPEN_ID.search(value)
    if not match:
        raise FeishuSendError("绑定身份不包含可发送的飞书 open_id")
    return match.group(1)


async def _tenant_access_token(client: httpx.AsyncClient) -> str:
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise FeishuSendError("飞书应用凭证未配置")
    now = time.time()
    if (
        _TOKEN_CACHE["token"]
        and _TOKEN_CACHE["app_id"] == app_id
        and float(_TOKEN_CACHE["expires_at"]) > now + 60
    ):
        return str(_TOKEN_CACHE["token"])
    response = await client.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    data = response.json()
    if response.status_code >= 400 or int(data.get("code") or 0) != 0 or not data.get("tenant_access_token"):
        raise FeishuSendError(data.get("msg") or f"获取飞书 token 失败 ({response.status_code})")
    _TOKEN_CACHE.update(
        token=data["tenant_access_token"],
        expires_at=now + int(data.get("expire") or 7200),
        app_id=app_id,
    )
    return str(data["tenant_access_token"])


def _message_text(payload: dict[str, Any]) -> str:
    title = str(payload.get("title") or "LearnBuddy 学习提醒").strip()
    message = str(payload.get("message") or "今天也来推进一小步。").strip()
    parts = [title, message]
    instance_id = (payload.get("instance_id") or "").strip()
    base_url = (os.getenv("ITUTOR_BASE_URL") or "").strip().rstrip("/")
    if base_url and instance_id:
        parts.append(f"{base_url}/instance/{instance_id}")
    return "\n\n".join(parts)[:3500]


async def _send_one(client: httpx.AsyncClient, delivery: dict[str, Any]) -> None:
    token = await _tenant_access_token(client)
    response = await client.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "open_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": _receive_id(delivery["external_user_id"]),
            "msg_type": "text",
            "content": json.dumps({"text": _message_text(delivery.get("payload") or {})}, ensure_ascii=False),
        },
    )
    data = response.json()
    if response.status_code >= 400 or int(data.get("code") or 0) != 0:
        raise FeishuSendError(data.get("msg") or f"飞书发送失败 ({response.status_code})")


async def send_pending_reminders(*, limit: int = 20) -> dict[str, Any]:
    if not configured():
        return {"configured": False, "claimed": 0, "sent": 0, "failed": 0}
    account_id = (os.getenv("ITUTOR_FEISHU_ACCOUNT_ID") or "default").strip()
    deliveries = claim_pending_deliveries(account_id=account_id, limit=limit)
    sent = 0
    failed = 0
    async with httpx.AsyncClient(timeout=12.0) as client:
        for delivery in deliveries:
            try:
                await _send_one(client, delivery)
            except Exception as exc:  # noqa: BLE001 - 发送失败必须写入事实表供重试
                failed += 1
                complete_delivery(
                    delivery["id"],
                    claim_token=delivery["claim_token"],
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                sent += 1
                complete_delivery(
                    delivery["id"],
                    claim_token=delivery["claim_token"],
                    status="sent",
                )
    return {"configured": True, "claimed": len(deliveries), "sent": sent, "failed": failed}


__all__ = ["FeishuSendError", "configured", "send_pending_reminders"]
