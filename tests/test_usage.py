"""core.usage 单元测试（临时 SQLite，不需要 API key / 不打真实 LLM）。

跑法：
    pytest tests/test_usage.py -v
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    """独立临时 DB；指定管理员白名单。"""
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ITUTOR_ADMIN_EMAILS", "boss@itutor.com")
    monkeypatch.setenv("ITUTOR_CREDITS_ENABLED", "0")
    import core.db as db
    import core.auth as auth
    import core.usage as usage
    import core.credits as credits

    importlib.reload(db)
    importlib.reload(credits)
    importlib.reload(auth)
    importlib.reload(usage)
    db.init_db()
    return auth, usage


def test_public_auth_never_promotes_admin_from_email_config(mods):
    auth, _ = mods
    boss = auth.register_user("boss@itutor.com", "secret123")
    normal = auth.register_user("user@itutor.com", "secret123")
    assert boss.is_admin is False
    assert normal.is_admin is False

    # 即使历史环境仍配置邮箱白名单，普通登录也不能成为提权入口。
    assert auth.authenticate("boss@itutor.com", "secret123").is_admin is False

    # 已经由可信本机流程写入数据库的历史管理员仍应正常工作。
    from core.db import get_conn

    conn = get_conn()
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (boss.id,))
    conn.commit()
    conn.close()
    tok = auth.create_session(boss.id)
    assert auth.get_user_by_token(tok).is_admin is True


def test_record_and_user_summary(mods):
    auth, usage = mods
    u = auth.register_user("a@itutor.com", "secret123")
    usage.record_usage(u.id, model="deepseek-chat", scene="chat",
                        prompt_tokens=100, completion_tokens=50, total_tokens=150)
    usage.record_usage(u.id, model="deepseek-chat", scene="lesson",
                        prompt_tokens=200, completion_tokens=80, total_tokens=280)
    s = usage.user_summary(u.id)
    assert s["calls"] == 2
    assert s["total_tokens"] == 430
    assert s["prompt_tokens"] == 300
    assert s["est_cost"] > 0
    scenes = {r["scene"]: r["tt"] for r in s["by_scene"]}
    assert scenes == {"chat": 150, "lesson": 280}


def test_context_attribution(mods):
    """usage_context + 观察者：token 应落到上下文里的用户/场景。"""
    auth, usage = mods
    u = auth.register_user("ctx@itutor.com", "secret123")

    fake_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    with usage.usage_context(u.id, "generate", "inst-x"):
        usage.record_current_usage("deepseek-chat", fake_usage)

    rows = usage.user_recent_usage(u.id)
    assert len(rows) == 1
    assert rows[0]["scene"] == "generate"
    assert rows[0]["instance_id"] == "inst-x"
    assert rows[0]["total_tokens"] == 15

    usage.record_current_usage("deepseek-chat", fake_usage)
    assert len(usage.user_recent_usage(u.id)) == 1


def test_events_and_admin_overview(mods):
    auth, usage = mods
    auth.register_user("boss@itutor.com", "secret123")
    u = auth.register_user("b@itutor.com", "secret123")
    usage.log_event(u.id, "login", "b@itutor.com")
    usage.log_event(u.id, "generate", "日语 N3", "jp-n3")
    usage.record_usage(u.id, model="deepseek-chat", scene="chat",
                       prompt_tokens=1000, completion_tokens=500, total_tokens=1500)

    ov = usage.admin_overview()
    assert ov["totals"]["users"] == 2
    assert ov["totals"]["total_tokens"] == 1500
    top = ov["users"][0]
    assert top["id"] == u.id
    assert top["total_tokens"] == 1500
    assert top["events"] == 2

    detail = usage.admin_user_detail(u.id)
    assert detail["user"]["email"] == "b@itutor.com"
    assert detail["summary"]["total_tokens"] == 1500
    assert len(detail["recent_events"]) == 2
    assert usage.admin_user_detail(99999) == {}


def test_daily_trend(mods):
    auth, usage = mods
    u = auth.register_user("d@itutor.com", "secret123")
    usage.record_usage(u.id, model="m", scene="chat",
                       prompt_tokens=10, completion_tokens=5, total_tokens=15)
    days = usage.daily_usage(7)
    assert len(days) == 7
    assert days[-1]["date"] >= days[0]["date"]      # 升序
    assert days[-1]["total_tokens"] == 15           # 今天有 15
    assert sum(d["total_tokens"] for d in days) == 15


def test_quota_limit_blocks(mods):
    auth, usage = mods
    u = auth.register_user("q@itutor.com", "secret123")
    # 默认无限：允许
    assert usage.check_quota(u.id)["allowed"] is True

    usage.set_user_limit(u.id, 100)
    assert usage.get_user_limit(u.id) == 100

    usage.record_usage(u.id, model="m", scene="chat",
                       prompt_tokens=60, completion_tokens=30, total_tokens=90)
    q = usage.check_quota(u.id)
    assert q["allowed"] is True and q["remaining"] == 10

    usage.record_usage(u.id, model="m", scene="chat",
                       prompt_tokens=10, completion_tokens=10, total_tokens=20)
    q = usage.check_quota(u.id)
    assert q["allowed"] is False and q["scope"] == "user"

    # 设回 0 = 无限，应放行
    usage.set_user_limit(u.id, 0)
    assert usage.check_quota(u.id)["allowed"] is True


def test_global_quota(mods, monkeypatch):
    auth, usage = mods
    monkeypatch.setenv("ITUTOR_GLOBAL_TOKEN_LIMIT", "50")
    u = auth.register_user("g@itutor.com", "secret123")
    usage.record_usage(u.id, model="m", scene="chat",
                       prompt_tokens=40, completion_tokens=20, total_tokens=60)
    q = usage.check_quota(u.id)
    assert q["allowed"] is False and q["scope"] == "global"
