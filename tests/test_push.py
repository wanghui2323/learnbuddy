"""移动端推送触点单测（轨道 A）：payload / subs CRUD / 降级 / 去重 / scheduler 协程。

不依赖 apscheduler / pywebpush 真实安装——降级路径优雅处理 ImportError。
"""

import asyncio
import json
import sys
import tempfile
import types
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", tempfile.mktemp(suffix=".db"))
    # 降级测试默认清掉 VAPID（需要"已配置"的用例自己 setenv）
    monkeypatch.delenv("ITUTOR_VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ITUTOR_VAPID_SUBJECT", raising=False)
    from core.db import get_conn, init_db

    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('a','b','t')")
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('b','b','t')")
    conn.commit()
    rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    conn.close()
    return {"uid": rows[0]["id"], "other_uid": rows[1]["id"]}


def _add(env, ep="https://fcm/xyz"):
    from core.push_subs import add_subscription

    return add_subscription(
        user_id=env["uid"], endpoint=ep, p256dh="k1", auth="a1", instance_id="i1"
    )


# ---------- payload（纯函数）----------

def test_payload_title_due_count(env):
    from core.notify import build_payload

    p = build_payload(
        {
            "theme": "敬语",
            "counts": {"due": 3, "weak": 1, "frontier": 0},
            "items": [{"kind": "review", "topic": "动词变形"}],
            "rationale": "x",
        },
        instance_id="i1",
    )
    assert "3" in p["title"] and "到期" in p["title"]
    assert p["url"].endswith("/instance/i1?view=today")
    assert p["tag"] == "daily-i1"
    assert len(p["body"]) <= 80
    assert p["data"]["instance_id"] == "i1"


def test_payload_no_due_uses_theme(env):
    from core.notify import build_payload

    p = build_payload(
        {"theme": "敬语入门", "counts": {"due": 0}, "items": [{"topic": "敬语"}]},
        instance_id="i1",
    )
    assert "敬语" in p["title"]


def test_payload_empty_items(env):
    from core.notify import build_payload

    p = build_payload(
        {"theme": "", "counts": {}, "items": [], "rationale": "回来学吧"}, instance_id="i1"
    )
    assert p["title"] and p["body"]  # 有兜底


def test_payload_body_clipped(env):
    from core.notify import build_payload

    long_topic = "超" * 200
    p = build_payload(
        {"theme": "x", "counts": {}, "items": [{"topic": long_topic}]}, instance_id="i1"
    )
    assert len(p["body"]) <= 80


# ---------- push_subs CRUD ----------

def test_add_list_delete_subscription(env):
    from core.push_subs import delete_by_endpoint, list_subscriptions

    _add(env)
    subs = list_subscriptions(env["uid"])
    assert len(subs) == 1 and subs[0]["endpoint"].startswith("https://fcm")
    assert delete_by_endpoint("https://fcm/xyz", user_id=env["uid"]) == 1
    assert list_subscriptions(env["uid"]) == []


def test_endpoint_unique_idempotent(env):
    """同 endpoint 二次订阅不产生两行（upsert）。"""
    from core.push_subs import list_subscriptions

    first_id = _add(env)
    second_id = _add(env)
    assert first_id == second_id
    assert len(list_subscriptions(env["uid"])) == 1


def test_endpoint_cannot_be_rebound_or_deleted_by_another_user(env):
    """同一 endpoint 的归属不能被第二个用户接管，跨用户删除也无效。"""
    from core.push_subs import (
        SubscriptionOwnershipError,
        add_subscription,
        delete_by_endpoint,
        list_subscriptions,
    )

    original_id = _add(env)
    with pytest.raises(SubscriptionOwnershipError):
        add_subscription(
            user_id=env["other_uid"],
            endpoint="https://fcm/xyz",
            p256dh="attacker-key",
            auth="attacker-auth",
        )

    assert delete_by_endpoint("https://fcm/xyz", user_id=env["other_uid"]) == 0
    owner_subs = list_subscriptions(env["uid"])
    assert [(sub["id"], sub["p256dh"]) for sub in owner_subs] == [(original_id, "k1")]
    assert list_subscriptions(env["other_uid"]) == []


@pytest.mark.parametrize("status_code", [404, 410])
def test_stale_send_cleanup_deletes_exact_current_subscription(
    env, monkeypatch, status_code
):
    """Push Service 确认失效时，仍能清理当前 owner 的精确订阅。"""
    from core.push_subs import list_subscriptions
    from core.push_web import _send_one

    _add(env)
    sub = list_subscriptions(env["uid"])[0]

    class FakeWebPushException(Exception):
        def __init__(self):
            super().__init__("gone")
            self.response = types.SimpleNamespace(status_code=status_code)

    def fake_webpush(**_kwargs):
        raise FakeWebPushException()

    monkeypatch.setitem(
        sys.modules,
        "pywebpush",
        types.SimpleNamespace(WebPushException=FakeWebPushException, webpush=fake_webpush),
    )
    assert _send_one(sub, "{}", b"key", "mailto:test@example.com") == (
        False,
        f"gone({status_code})",
    )
    assert list_subscriptions(env["uid"]) == []


def test_stale_send_cleanup_requires_exact_owned_subscription(env, monkeypatch):
    """410 只清理本次发送读到的那条订阅，不会按 endpoint 误删新 owner。"""
    from core.push_subs import add_subscription, delete_by_endpoint, list_subscriptions
    from core.push_web import _send_one

    _add(env)
    stale = list_subscriptions(env["uid"])[0]
    assert delete_by_endpoint(stale["endpoint"], user_id=env["uid"]) == 1
    replacement_id = add_subscription(
        user_id=env["other_uid"],
        endpoint=stale["endpoint"],
        p256dh="replacement-key",
        auth="replacement-auth",
    )

    class FakeWebPushException(Exception):
        def __init__(self):
            super().__init__("gone")
            self.response = types.SimpleNamespace(status_code=410)

    def fake_webpush(**_kwargs):
        raise FakeWebPushException()

    monkeypatch.setitem(
        sys.modules,
        "pywebpush",
        types.SimpleNamespace(WebPushException=FakeWebPushException, webpush=fake_webpush),
    )
    assert _send_one(stale, "{}", b"key", "mailto:test@example.com") == (
        False,
        "gone(410)",
    )
    replacement = list_subscriptions(env["other_uid"])
    assert [sub["id"] for sub in replacement] == [replacement_id]


def test_push_api_enforces_endpoint_and_instance_ownership(env, tmp_path, monkeypatch):
    """API 必须同时隔离 endpoint 与 instance_id 两个用户输入。"""
    import server
    from core.auth import create_session
    from core.push_subs import list_subscriptions

    monkeypatch.setenv("ITUTOR_RATE_LIMIT_ENABLED", "0")
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    own_instance = tmp_path / "owner-path"
    own_instance.mkdir()
    (own_instance / "meta.json").write_text(
        json.dumps({"user_id": env["uid"]}), encoding="utf-8"
    )

    owner_client = TestClient(server.app)
    owner_client.cookies.set(server.COOKIE_NAME, create_session(env["uid"]))
    other_client = TestClient(server.app)
    other_client.cookies.set(server.COOKIE_NAME, create_session(env["other_uid"]))
    payload = {
        "endpoint": "https://fcm/shared",
        "keys": {"p256dh": "owner-key", "auth": "owner-auth"},
        "instance_id": "owner-path",
    }

    created = owner_client.post("/api/push/subscribe", json=payload)
    assert created.status_code == 200
    assert owner_client.post("/api/push/subscribe", json=payload).json()["sub_id"] == created.json()["sub_id"]

    foreign_instance = other_client.post(
        "/api/push/subscribe",
        json={**payload, "endpoint": "https://fcm/other-device"},
    )
    assert foreign_instance.status_code == 403
    assert list_subscriptions(env["other_uid"]) == []

    missing_instance = owner_client.post(
        "/api/push/subscribe",
        json={**payload, "endpoint": "https://fcm/missing", "instance_id": "missing"},
    )
    assert missing_instance.status_code == 404

    cross_user_rebind = other_client.post(
        "/api/push/subscribe",
        json={**payload, "instance_id": None},
    )
    assert cross_user_rebind.status_code == 409
    assert list_subscriptions(env["uid"])[0]["p256dh"] == "owner-key"

    assert other_client.request(
        "DELETE", "/api/push/subscribe", json={"endpoint": payload["endpoint"]}
    ).status_code == 200
    assert len(list_subscriptions(env["uid"])) == 1
    assert owner_client.request(
        "DELETE", "/api/push/subscribe", json={"endpoint": payload["endpoint"]}
    ).status_code == 200
    assert list_subscriptions(env["uid"]) == []


def test_list_returns_multi_devices(env):
    from core.push_subs import list_subscriptions

    _add(env, ep="https://fcm/a")
    _add(env, ep="https://fcm/b")
    assert len(list_subscriptions(env["uid"])) == 2


def test_subscriber_user_ids(env):
    from core.push_subs import subscriber_user_ids

    _add(env, ep="https://fcm/a")
    assert env["uid"] in subscriber_user_ids()


def test_last_active_instance_from_events(env):
    from core.db import get_conn
    from core.push_subs import last_active_instance

    conn = get_conn()
    conn.execute(
        "INSERT INTO events (user_id, action, instance_id, created_at) VALUES (?,?,?,?)",
        (env["uid"], "lesson", "inst-latest", "2026-06-19T10:00:00"),
    )
    conn.execute(
        "INSERT INTO events (user_id, action, instance_id, created_at) VALUES (?,?,?,?)",
        (env["uid"], "lesson", "inst-old", "2026-06-10T10:00:00"),
    )
    conn.commit()
    conn.close()
    assert last_active_instance(env["uid"]) == "inst-latest"


def test_last_active_instance_none(env):
    from core.push_subs import last_active_instance

    assert last_active_instance(env["uid"]) is None


# ---------- 降级（对齐 ADR-009，最关键）----------

def test_send_skipped_when_no_vapid(env):
    from core.push_web import send_to_user

    _add(env, ep="https://fcm/a")  # 有订阅但无 VAPID
    r = asyncio.run(send_to_user(env["uid"], {"title": "x", "body": "y"}))
    assert r["skipped"] == "no_vapid"


def test_send_skipped_when_no_sub(env, monkeypatch):
    from core.push_web import send_to_user

    monkeypatch.setenv("ITUTOR_VAPID_PRIVATE_KEY", "fake")
    monkeypatch.setenv("ITUTOR_VAPID_SUBJECT", "mailto:t@e.com")
    r = asyncio.run(send_to_user(env["uid"], {"title": "x"}))  # 配了但无订阅
    assert r["skipped"] == "no_sub"


def test_is_configured_flags(env, monkeypatch):
    from core.push_web import is_configured

    assert is_configured() is False
    monkeypatch.setenv("ITUTOR_VAPID_PRIVATE_KEY", "fake")
    monkeypatch.setenv("ITUTOR_VAPID_SUBJECT", "mailto:t@e.com")
    assert is_configured() is True


# ---------- 去重 ----------

def test_dedup_mark_and_check(env):
    from core.push_subs import already_sent_today, mark_sent

    today = date.today().isoformat()
    assert already_sent_today(env["uid"], today) is False
    mark_sent(user_id=env["uid"], instance_id="i1", day=today, sent_count=2)
    assert already_sent_today(env["uid"], today) is True


# ---------- scheduler 协程 ----------

async def _fake_send_sent(uid, payload, sink):
    sink.append(uid)
    return {"sent": 1}


def test_daily_push_skips_user_without_instance(env, monkeypatch):
    """有订阅但无活跃 instance → 标记 no_instance，不调 send。"""
    sink: list = []
    monkeypatch.setattr(
        "core.push_web.send_to_user", lambda uid, payload: _fake_send_sent(uid, payload, sink)
    )
    _add(env, ep="https://fcm/a")
    from core.push_subs import already_sent_today
    from core.push_scheduler import daily_push_all

    asyncio.run(daily_push_all())
    assert sink == []  # 没 active instance，没尝试发
    assert already_sent_today(env["uid"]) is True  # 但标记了当天已处理


def test_daily_push_dedup_second_run(env, monkeypatch):
    """第二次跑同一天应被去重（不重发）。"""
    from core.db import get_conn

    sink: list = []
    monkeypatch.setattr(
        "core.push_web.send_to_user", lambda uid, payload: _fake_send_sent(uid, payload, sink)
    )
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (user_id, action, instance_id, created_at) VALUES (?,?,?,?)",
        (env["uid"], "lesson", "i1", "2026-06-19T10:00:00"),
    )
    conn.commit()
    conn.close()
    _add(env, ep="https://fcm/a")

    from core.push_scheduler import daily_push_all

    asyncio.run(daily_push_all())
    first_count = len(sink)
    assert first_count in (1, 2)
    asyncio.run(daily_push_all())  # 第二次当天去重
    assert len(sink) == first_count
