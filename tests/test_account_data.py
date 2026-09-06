"""账户导出、删除与保留期清理测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from core.account_data import cleanup_expired_data, delete_user_data, export_user_data
from core.agent_integration import create_binding_code, redeem_binding_code
from core.auth import AuthError, create_session, get_user_by_id, register_user
from core.conversations import claim_session, record_message
from core.db import get_conn, init_db
from core.reminders import set_policy


@pytest.fixture()
def account_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "account.db"))
    monkeypatch.setenv("ITUTOR_AGENT_TOKEN_SECRET", "test-agent-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    init_db()
    instances = tmp_path / "instances"
    instances.mkdir()
    owner = register_user("export@example.com", "secret123", "Export User")
    other = register_user("keep@example.com", "secret123", "Keep User")
    for user, slug in ((owner, "owner-path"), (other, "other-path")):
        path = instances / slug
        path.mkdir()
        (path / "meta.json").write_text(
            json.dumps({"id": slug, "user_id": user.id, "domain": "测试"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (path / "master.json").write_text(json.dumps({"north_star": {"target": slug}}), encoding="utf-8")
    (instances / "_index.json").write_text(
        json.dumps(
            {
                "instances": [
                    {"id": "owner-path", "user_id": owner.id},
                    {"id": "other-path", "user_id": other.id},
                ],
                "count": 2,
            }
        ),
        encoding="utf-8",
    )
    return owner, other, instances


def test_export_contains_owned_records_and_omits_security_secrets(account_env):
    owner, _, instances = account_env
    claim_session(owner.id, "sess-export")
    record_message(user_id=owner.id, session_id="sess-export", role="user", content="我的学习目标")
    create_session(owner.id)
    binding_code = create_binding_code(owner.id)["code"]
    redeem_binding_code(binding_code, external_user_id="ou_export_owner")
    set_policy(owner.id, instance_id="owner-path", local_time="08:30", weekdays=[1, 3, 5])

    exported = export_user_data(owner.id, instances)

    assert exported["account"]["email"] == "export@example.com"
    assert exported["schema_version"] == "learnbuddy-account-export-v3"
    assert "baseline_score_evidence" in exported["records"]
    assert "baseline_assist_messages" in exported["records"]
    assert "baseline_disputes" in exported["records"]
    assert [item["id"] for item in exported["instances"]] == ["owner-path"]
    assert exported["records"]["conversation_messages"][0]["content"] == "我的学习目标"
    assert exported["records"]["channel_bindings"][0]["external_user_id"] == "ou_export_owner"
    assert exported["records"]["reminder_policies"][0]["weekdays"] == [1, 3, 5]
    serialized = json.dumps(exported, ensure_ascii=False)
    assert "password_hash" not in exported["account"]
    assert "pbkdf2_sha256" not in serialized
    assert "session_tokens" in exported["omitted_for_security"]
    assert binding_code not in serialized
    assert "channel_binding_codes" not in exported["records"]
    assert all("claim_token" not in row for row in exported["records"]["notification_deliveries"])


def test_delete_requires_password_and_removes_only_owned_data(account_env):
    owner, other, instances = account_env
    claim_session(owner.id, "sess-delete")
    record_message(user_id=owner.id, session_id="sess-delete", role="user", content="待删除")
    code = create_binding_code(owner.id)["code"]
    redeem_binding_code(code, external_user_id="ou_delete_owner")
    set_policy(owner.id, instance_id="owner-path")

    with pytest.raises(AuthError):
        delete_user_data(owner.id, "wrong-password", instances)
    assert (instances / "owner-path").exists()

    result = delete_user_data(owner.id, "secret123", instances)

    assert result["instances_deleted"] == 1
    assert get_user_by_id(owner.id) is None
    assert get_user_by_id(other.id) is not None
    assert not (instances / "owner-path").exists()
    assert (instances / "other-path").exists()
    index = json.loads((instances / "_index.json").read_text(encoding="utf-8"))
    assert index["count"] == 1
    assert index["instances"][0]["id"] == "other-path"
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM channel_bindings WHERE user_id = ?", (owner.id,)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM reminder_policies WHERE user_id = ?", (owner.id,)).fetchone()["n"] == 0
    finally:
        conn.close()


def test_retention_cleanup_deletes_old_logs_but_keeps_learning_results(account_env):
    owner, _, _ = account_env
    old = datetime(2026, 1, 1, 10, 0, 0).isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO events (user_id, created_at, action, detail) VALUES (?,?,?,?)",
            (owner.id, old, "old-event", "expired"),
        )
        conn.execute(
            """INSERT INTO artifacts
                 (user_id, instance_id, topic, kind, content, created_at)
               VALUES (?,?,?,?,?,?)""",
            (owner.id, "owner-path", "主题", "note", "长期学习成果", old),
        )
        conn.commit()
    finally:
        conn.close()

    deleted = cleanup_expired_data(30, now=datetime(2026, 7, 10, 10, 0, 0))

    assert deleted["events"] == 1
    conn = get_conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM artifacts WHERE user_id = ?", (owner.id,)).fetchone()["n"] == 1
    finally:
        conn.close()
