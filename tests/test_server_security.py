"""server.py 生产安全配置契约测试。"""

from __future__ import annotations

from starlette.responses import Response

import server


def test_secure_cookie_is_forced_in_production(monkeypatch):
    monkeypatch.delenv("ITUTOR_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("ITUTOR_ENV", "production")
    response = Response()

    server._set_session_cookie(response, "test-token")

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_secure_cookie_can_be_disabled_for_local_http(monkeypatch):
    monkeypatch.setenv("ITUTOR_COOKIE_SECURE", "0")
    monkeypatch.setenv("ITUTOR_ENV", "development")
    response = Response()

    server._set_session_cookie(response, "local-token")

    assert "Secure" not in response.headers["set-cookie"]


def test_production_cannot_disable_secure_cookie(monkeypatch):
    monkeypatch.setenv("ITUTOR_COOKIE_SECURE", "0")
    monkeypatch.setenv("ITUTOR_ENV", "production")

    assert server._secure_cookie_enabled() is True
