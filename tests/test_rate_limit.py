"""关键接口滑动窗口限流测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.db import init_db
from core.rate_limit import actor_digest, check_rate_limit


def test_rate_limit_blocks_after_limit_and_does_not_store_plain_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "rate.db"))
    init_db()
    now = datetime(2026, 7, 10, 12, 0, 0)

    first = check_rate_limit(actor="ip:127.0.0.1|person@example.com", scope="login", limit=2, now=now)
    second = check_rate_limit(
        actor="ip:127.0.0.1|person@example.com",
        scope="login",
        limit=2,
        now=now + timedelta(seconds=1),
    )
    blocked = check_rate_limit(
        actor="ip:127.0.0.1|person@example.com",
        scope="login",
        limit=2,
        now=now + timedelta(seconds=2),
    )

    assert first == {"allowed": True, "remaining": 1, "retry_after": 0}
    assert second["remaining"] == 0
    assert blocked["allowed"] is False
    assert blocked["retry_after"] >= 58
    assert actor_digest("ip:127.0.0.1|person@example.com") != "person@example.com"


def test_rate_limit_window_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "rate-expire.db"))
    init_db()
    now = datetime(2026, 7, 10, 12, 0, 0)
    check_rate_limit(actor="user:1", scope="chat", limit=1, window_seconds=60, now=now)

    after_window = check_rate_limit(
        actor="user:1",
        scope="chat",
        limit=1,
        window_seconds=60,
        now=now + timedelta(seconds=61),
    )

    assert after_window["allowed"] is True
