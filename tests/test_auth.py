"""core.auth / core.db 单元测试（用临时 SQLite，不碰真实库，不需要 API key）。

跑法：
    pytest tests/test_auth.py -v
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def auth(tmp_path, monkeypatch):
    """每个用例一个独立临时 DB。"""
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "test.db"))
    import core.db as db
    import core.auth as auth_mod

    importlib.reload(db)
    importlib.reload(auth_mod)
    db.init_db()
    return auth_mod


def test_password_hash_roundtrip(auth):
    h = auth.hash_password("hunter2x")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("hunter2x", h)
    assert not auth.verify_password("wrong", h)


def test_register_and_authenticate(auth):
    u = auth.register_user("Alice@Example.com", "secret123", "Alice")
    assert u.id > 0
    assert u.email == "alice@example.com"  # 归一化为小写
    assert u.name == "Alice"

    back = auth.authenticate("alice@example.com", "secret123")
    assert back.id == u.id


def test_register_rejects_dupe_and_bad_input(auth):
    auth.register_user("bob@example.com", "secret123")
    with pytest.raises(auth.AuthError):
        auth.register_user("bob@example.com", "secret123")  # 重复邮箱
    with pytest.raises(auth.AuthError):
        auth.register_user("not-an-email", "secret123")  # 邮箱格式
    with pytest.raises(auth.AuthError):
        auth.register_user("c@example.com", "123")  # 密码过短


def test_authenticate_wrong_password(auth):
    auth.register_user("dora@example.com", "secret123")
    with pytest.raises(auth.AuthError):
        auth.authenticate("dora@example.com", "nope")
    with pytest.raises(auth.AuthError):
        auth.authenticate("ghost@example.com", "whatever")  # 不存在的账号


def test_session_lifecycle(auth):
    u = auth.register_user("eve@example.com", "secret123")
    token = auth.create_session(u.id)
    assert token
    got = auth.get_user_by_token(token)
    assert got is not None and got.id == u.id

    auth.delete_session(token)
    assert auth.get_user_by_token(token) is None
    assert auth.get_user_by_token("garbage-token") is None
    assert auth.get_user_by_token("") is None
