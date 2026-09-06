"""持久对话归属与恢复测试。"""

from __future__ import annotations

import pytest

from core.auth import register_user
from core.conversations import (
    claim_session,
    clear_session,
    record_message,
    session_messages,
)
from core.db import init_db


@pytest.fixture()
def users(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "conversation.db"))
    init_db()
    return (
        register_user("owner@example.com", "secret123", "Owner"),
        register_user("other@example.com", "secret123", "Other"),
    )


def test_session_is_owned_and_cannot_be_reused_across_accounts(users):
    owner, other = users
    claim_session(owner.id, "sess-shared")

    with pytest.raises(ValueError, match="另一个账号"):
        claim_session(other.id, "sess-shared")


def test_session_messages_restore_in_order_and_stay_isolated(users):
    owner, other = users
    claim_session(owner.id, "sess-owner")
    claim_session(other.id, "sess-other")
    record_message(user_id=owner.id, session_id="sess-owner", role="user", content="我想学日语")
    record_message(
        user_id=owner.id,
        session_id="sess-owner",
        role="assistant",
        content="先确认目标",
        scene="chat_choice",
    )
    record_message(user_id=other.id, session_id="sess-other", role="user", content="隔离消息")

    restored = session_messages(owner.id, "sess-owner")

    assert [(item["role"], item["content"]) for item in restored] == [
        ("user", "我想学日语"),
        ("assistant", "先确认目标"),
    ]
    assert all(item["content"] != "隔离消息" for item in restored)


def test_explicit_clear_removes_messages_and_allows_fresh_claim(users):
    owner, other = users
    claim_session(owner.id, "sess-reset")
    record_message(user_id=owner.id, session_id="sess-reset", role="user", content="旧消息")

    clear_session(owner.id, "sess-reset")
    claim_session(other.id, "sess-reset")

    assert session_messages(other.id, "sess-reset") == []
