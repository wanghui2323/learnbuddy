"""server.py 对话重启恢复与 user+session 隔离测试。"""

from __future__ import annotations

import pytest

import server
from core.auth import register_user
from core.conversations import claim_session, record_message
from core.db import init_db


def test_server_rebuilds_engine_from_persisted_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "server-conversation.db"))
    init_db()
    user = register_user("recover@example.com", "secret123", "Recover")
    claim_session(user.id, "sess-recover")
    record_message(user_id=user.id, session_id="sess-recover", role="user", content="第一轮问题")
    record_message(user_id=user.id, session_id="sess-recover", role="assistant", content="第一轮回答")

    class FakeEngine:
        def __init__(self):
            self.restored = []

        def restore(self, messages):
            self.restored = list(messages)

    monkeypatch.setattr(server, "ConversationEngine", FakeEngine)
    server._sessions.clear()  # 模拟服务进程重启后内存丢失

    engine = server._get_or_create_session(user.id, "sess-recover")

    assert [item["content"] for item in engine.restored] == ["第一轮问题", "第一轮回答"]


def test_server_rejects_another_user_reusing_owned_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "server-isolation.db"))
    init_db()
    owner = register_user("first@example.com", "secret123")
    other = register_user("second@example.com", "secret123")
    claim_session(owner.id, "sess-owned")
    server._sessions.clear()

    with pytest.raises(ValueError):
        server._get_or_create_session(other.id, "sess-owned")
