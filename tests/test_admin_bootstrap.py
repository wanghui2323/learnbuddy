"""Cloud 管理员只能通过部署机本地、密码验证后的显式初始化。"""

from __future__ import annotations

import pytest

from core.auth import authenticate, register_user
from core.db import init_db
from scripts.init_cloud_admin import AdminInitializationError, initialize_cloud_admin


@pytest.fixture()
def admin_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "admin.db"))
    monkeypatch.setenv("LEARNBUDDY_EDITION", "cloud")
    init_db()


def test_cloud_admin_initialization_requires_target_password_and_is_idempotent(admin_db):
    user = register_user("owner@example.com", "secret123", "Owner")
    assert user.is_admin is False

    with pytest.raises(AdminInitializationError, match="不存在或密码不正确"):
        initialize_cloud_admin("owner@example.com", "wrong-password")
    assert authenticate("owner@example.com", "secret123").is_admin is False

    first = initialize_cloud_admin("OWNER@example.com", "secret123")
    assert first == {
        "id": user.id,
        "email": "owner@example.com",
        "name": "Owner",
        "is_admin": True,
        "changed": True,
    }
    assert authenticate("owner@example.com", "secret123").is_admin is True

    second = initialize_cloud_admin("owner@example.com", "secret123")
    assert second["changed"] is False
    assert second["id"] == user.id


def test_cloud_admin_initialization_rejects_unknown_account(admin_db):
    with pytest.raises(AdminInitializationError, match="不存在或密码不正确"):
        initialize_cloud_admin("missing@example.com", "secret123")


def test_cloud_admin_initialization_rejects_personal_mode(admin_db, monkeypatch):
    register_user("owner@example.com", "secret123")
    monkeypatch.setenv("LEARNBUDDY_EDITION", "personal")

    with pytest.raises(AdminInitializationError, match="LEARNBUDDY_EDITION=cloud"):
        initialize_cloud_admin("owner@example.com", "secret123")
