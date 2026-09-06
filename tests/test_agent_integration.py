"""飞书/OpenClaw 多用户绑定、短 token 与提醒队列闭环测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import pytest

from core.agent_integration import (
    AgentIntegrationError,
    create_binding_code,
    get_active_binding,
    issue_agent_token,
    redeem_binding_code,
    revoke_binding,
    verify_agent_token,
    verify_bridge_secret,
)
from core.auth import register_user
from core.db import get_conn, init_db
from core.reminders import (
    ReminderError,
    claim_pending_deliveries,
    complete_delivery,
    get_policy,
    queue_due_reminders,
    set_policy,
)


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("ITUTOR_AGENT_TOKEN_SECRET", "test-agent-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ITUTOR_OPENCLAW_BRIDGE_TOKEN", "test-bridge-token-0123456789-abcdefghijklmnopqrstuvwxyz")
    init_db()
    user_a = register_user("agent-a@example.com", "secret123", "Agent A")
    user_b = register_user("agent-b@example.com", "secret123", "Agent B")
    return user_a, user_b


def _bind(user_id: int, external_user_id: str, now: datetime) -> dict:
    issued = create_binding_code(user_id, now=now)
    return redeem_binding_code(issued["code"], external_user_id=external_user_id, now=now)


def test_binding_code_is_one_time_and_plaintext_is_not_stored(agent_env):
    user_a, _ = agent_env
    now = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)

    issued = create_binding_code(user_a.id, now=now)
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM channel_binding_codes WHERE user_id = ?", (user_a.id,)).fetchone()
        assert row["code_digest"] != issued["code"]
        assert issued["code"] not in "|".join(str(value) for value in dict(row).values())
    finally:
        conn.close()

    binding = redeem_binding_code(issued["code"], external_user_id="ou_feishu_a", now=now)
    assert binding["user_id"] == user_a.id
    assert get_active_binding(user_a.id)["external_user_id"] == "ou_feishu_a"
    with pytest.raises(AgentIntegrationError, match="已使用"):
        redeem_binding_code(issued["code"], external_user_id="ou_feishu_a", now=now)


def test_external_identity_cannot_bind_two_accounts(agent_env):
    user_a, user_b = agent_env
    now = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    _bind(user_a.id, "ou_same_feishu", now)
    code_b = create_binding_code(user_b.id, now=now)["code"]

    with pytest.raises(AgentIntegrationError, match="其他 LearnBuddy"):
        redeem_binding_code(code_b, external_user_id="ou_same_feishu", now=now)


def test_expired_binding_code_is_rejected(agent_env):
    user_a, _ = agent_env
    created = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    code = create_binding_code(user_a.id, ttl_seconds=120, now=created)["code"]

    with pytest.raises(AgentIntegrationError, match="已过期"):
        redeem_binding_code(code, external_user_id="ou_late", now=created + timedelta(minutes=3))


def test_agent_token_is_user_scoped_and_revocation_invalidates_it(agent_env):
    user_a, _ = agent_env
    now = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
    binding = _bind(user_a.id, "ou_token_a", now)
    token = issue_agent_token(binding, now=now)["access_token"]

    identity = verify_agent_token(token, now=now + timedelta(seconds=30))
    assert identity["user_id"] == user_a.id
    revoke_binding(user_a.id, now=now + timedelta(seconds=40))
    with pytest.raises(AgentIntegrationError, match="绑定已失效"):
        verify_agent_token(token, now=now + timedelta(seconds=50))


def test_bridge_secret_and_mcp_never_accept_model_supplied_user_id(agent_env):
    user_a, user_b = agent_env
    verify_bridge_secret("test-bridge-token-0123456789-abcdefghijklmnopqrstuvwxyz")
    with pytest.raises(AgentIntegrationError, match="凭证无效"):
        verify_bridge_secret("wrong")

    from mcp_server import handle

    listed = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, user_a.id)
    assert any(tool["name"] == "get_reminder_policy" for tool in listed["result"]["tools"])
    spoofed = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_instances", "arguments": {"user_id": user_b.id}},
        },
        user_a.id,
    )
    assert spoofed["result"]["isError"] is True
    assert "服务端身份注入" in spoofed["result"]["content"][0]["text"]


def test_due_reminder_is_idempotent_claimed_and_completed(agent_env):
    user_a, _ = agent_env
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)  # 周一 08:00 Asia/Shanghai
    _bind(user_a.id, "ou_reminder_a", now)
    policy = set_policy(
        user_a.id,
        timezone_name="Asia/Shanghai",
        local_time="08:00",
        weekdays=[1, 2, 3, 4, 5],
        now=now,
    )
    assert policy["enabled"] is True
    assert get_policy(user_a.id)["weekdays"] == [1, 2, 3, 4, 5]

    assert queue_due_reminders(now=now)["queued"] == 1
    assert queue_due_reminders(now=now)["queued"] == 0
    claimed = claim_pending_deliveries(now=now)
    assert len(claimed) == 1
    assert claimed[0]["external_user_id"] == "ou_reminder_a"
    assert claim_pending_deliveries(now=now) == []

    with pytest.raises(ReminderError, match="租约无效"):
        complete_delivery(claimed[0]["id"], claim_token="wrong", status="sent", now=now)
    completed = complete_delivery(
        claimed[0]["id"],
        claim_token=claimed[0]["claim_token"],
        status="sent",
        now=now,
    )
    assert completed["status"] == "sent"
    assert completed["sent_at"] is not None


def test_paused_or_quiet_policy_does_not_queue(agent_env):
    user_a, _ = agent_env
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    _bind(user_a.id, "ou_quiet_a", now)
    set_policy(
        user_a.id,
        local_time="08:00",
        quiet_start="22:00",
        quiet_end="09:00",
        enabled=True,
        now=now,
    )
    assert queue_due_reminders(now=now)["queued"] == 0

    set_policy(user_a.id, local_time="08:00", enabled=False, now=now)
    assert queue_due_reminders(now=now)["queued"] == 0


def test_reminder_queue_accepts_short_scheduler_delay(agent_env):
    user_a, _ = agent_env
    scheduled = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    _bind(user_a.id, "ou_delayed_a", scheduled)
    set_policy(user_a.id, local_time="08:00", now=scheduled)

    assert queue_due_reminders(now=scheduled + timedelta(minutes=3))["queued"] == 1
    assert queue_due_reminders(now=scheduled + timedelta(minutes=6))["queued"] == 0


def test_configured_feishu_sender_claims_and_sends_pending_delivery(agent_env, monkeypatch):
    user_a, _ = agent_env
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    _bind(user_a.id, "feishu:ou_sender_a", now)
    set_policy(user_a.id, local_time="08:00", now=now)
    assert queue_due_reminders(now=now)["queued"] == 1
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setenv("ITUTOR_BASE_URL", "https://learn.example.com")

    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("tenant_access_token/internal"):
                return FakeResponse({"code": 0, "tenant_access_token": "tenant-token", "expire": 7200})
            return FakeResponse({"code": 0, "msg": "ok"})

    import core.feishu_sender as sender

    sender._TOKEN_CACHE.update(token="", expires_at=0.0, app_id="")
    monkeypatch.setattr(sender.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    result = asyncio.run(sender.send_pending_reminders())

    assert result == {"configured": True, "claimed": 1, "sent": 1, "failed": 0}
    message_call = calls[-1]
    assert message_call[1]["json"]["receive_id"] == "ou_sender_a"
    assert "LearnBuddy 学习提醒" in message_call[1]["json"]["content"]
