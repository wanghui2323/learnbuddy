"""Web 登录态 → 飞书绑定 → OpenClaw token → HTTP MCP 闭环。"""

from __future__ import annotations

from fastapi.testclient import TestClient

import server
from core.auth import create_session, register_user
from core.db import init_db


def test_feishu_binding_to_remote_mcp_and_unbind(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "agent-api.db"))
    monkeypatch.setenv("ITUTOR_AGENT_TOKEN_SECRET", "test-agent-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ITUTOR_OPENCLAW_BRIDGE_TOKEN", "test-bridge-token-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ITUTOR_FEISHU_ACCOUNT_ID", "default")
    monkeypatch.setenv("ITUTOR_RATE_LIMIT_ENABLED", "0")
    init_db()
    user = register_user("api-agent@example.com", "secret123", "API Agent")
    session = create_session(user.id)
    client = TestClient(server.app)
    client.cookies.set(server.COOKIE_NAME, session)

    status = client.get("/api/integrations/feishu")
    assert status.status_code == 200
    assert status.json()["binding"] is None

    issued = client.post("/api/integrations/feishu/binding-code")
    assert issued.status_code == 200
    bridge_headers = {"Authorization": "Bearer test-bridge-token-0123456789-abcdefghijklmnopqrstuvwxyz"}
    bound = client.post(
        "/api/integrations/openclaw/bind",
        headers=bridge_headers,
        json={
            "code": issued.json()["code"],
            "requester_sender_id": "feishu:ou_api_agent",
            "account_id": "default",
        },
    )
    assert bound.status_code == 200
    assert bound.json()["binding"]["user_id"] == user.id

    token_response = client.post(
        "/api/integrations/openclaw/token",
        headers=bridge_headers,
        json={"requester_sender_id": "feishu:ou_api_agent", "account_id": "default"},
    )
    assert token_response.status_code == 200
    agent_token = token_response.json()["access_token"]
    tools = client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert tools.status_code == 200
    assert any(tool["name"] == "set_reminder_policy" for tool in tools.json()["result"]["tools"])

    reminder = client.put(
        "/api/reminders/policy",
        json={"local_time": "20:30", "weekdays": [1, 2, 3, 4, 5], "enabled": True},
    )
    assert reminder.status_code == 200
    assert reminder.json()["policy"]["local_time"] == "20:30"

    unbound = client.delete("/api/integrations/feishu/binding")
    assert unbound.status_code == 200
    rejected = client.post(
        "/api/mcp",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    assert rejected.status_code == 401


def test_openclaw_bridge_rejects_missing_or_wrong_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "bridge-api.db"))
    monkeypatch.setenv("ITUTOR_AGENT_TOKEN_SECRET", "test-agent-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setenv("ITUTOR_OPENCLAW_BRIDGE_TOKEN", "test-bridge-token-0123456789-abcdefghijklmnopqrstuvwxyz")
    init_db()
    client = TestClient(server.app)

    assert client.post("/api/integrations/openclaw/token", json={}).status_code == 401
    assert client.post(
        "/api/integrations/openclaw/token",
        headers={"Authorization": "Bearer wrong"},
        json={},
    ).status_code == 401
