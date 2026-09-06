"""积分账户与积分卡测试。"""

from __future__ import annotations

import importlib


def _mods(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "credits.db"))
    monkeypatch.setenv("ITUTOR_CREDITS_ENABLED", "1")
    monkeypatch.setenv("ITUTOR_SIGNUP_POINTS", "1000")
    monkeypatch.setenv("ITUTOR_TOKENS_PER_POINT", "1000")
    import core.db as db
    import core.credits as credits
    import core.auth as auth
    import core.usage as usage

    importlib.reload(db)
    importlib.reload(credits)
    importlib.reload(auth)
    importlib.reload(usage)
    db.init_db()
    return auth, credits, usage


def test_signup_bonus_and_usage_spend(tmp_path, monkeypatch):
    auth, credits, usage = _mods(tmp_path, monkeypatch)
    u = auth.register_user("u@learnbuddy.test", "secret123")

    b = credits.balance(u.id)
    assert b["balance"] == 1000
    assert b["granted"] == 1000

    usage.record_usage(
        u.id,
        model="m",
        scene="lesson",
        prompt_tokens=1200,
        completion_tokens=300,
        total_tokens=1500,
    )
    b = credits.balance(u.id)
    assert b["spent"] == 2
    assert b["balance"] == 998
    q = usage.check_quota(u.id)
    assert q["allowed"] is True
    assert q["scope"] == "credits"


def test_admin_grant_and_card_redeem(tmp_path, monkeypatch):
    auth, credits, _usage = _mods(tmp_path, monkeypatch)
    admin = auth.register_user("admin@learnbuddy.test", "secret123")
    u = auth.register_user("card@learnbuddy.test", "secret123")

    credits.grant_points(u.id, 250, admin_id=admin.id, detail="测试赠送")
    assert credits.balance(u.id)["balance"] == 1250

    card = credits.create_cards(500, 1, admin_id=admin.id, note="测试卡")[0]
    b = credits.redeem_card(u.id, card["code"])
    assert b["balance"] == 1750

    cards = credits.list_cards()
    assert cards[0]["status"] == "redeemed"
    assert cards[0]["redeemed_email"] == "card@learnbuddy.test"


def test_credits_block_when_balance_empty(tmp_path, monkeypatch):
    auth, credits, usage = _mods(tmp_path, monkeypatch)
    monkeypatch.setenv("ITUTOR_SIGNUP_POINTS", "1")
    u = auth.register_user("empty@learnbuddy.test", "secret123")
    usage.record_usage(
        u.id,
        model="m",
        scene="generate",
        prompt_tokens=1000,
        completion_tokens=0,
        total_tokens=1000,
    )
    assert credits.balance(u.id)["balance"] == 0
    q = usage.check_quota(u.id)
    assert q["allowed"] is False
    assert q["scope"] == "credits"


def test_study_completion_reward_is_small_and_idempotent(tmp_path, monkeypatch):
    auth, credits, _usage = _mods(tmp_path, monkeypatch)
    u = auth.register_user("study@learnbuddy.test", "secret123")

    r1 = credits.reward_study_completion(u.id, instance_id="i1", topic="循环")
    assert r1["awarded"] is True
    assert r1["points"] == 20
    assert credits.balance(u.id)["balance"] == 1020

    r2 = credits.reward_study_completion(u.id, instance_id="i1", topic="循环")
    assert r2["awarded"] is False
    assert r2["reason"] == "already_rewarded"
    assert credits.balance(u.id)["balance"] == 1020


def test_study_reward_daily_limit(tmp_path, monkeypatch):
    auth, credits, _usage = _mods(tmp_path, monkeypatch)
    monkeypatch.setenv("ITUTOR_STUDY_REWARD_POINTS", "4")
    monkeypatch.setenv("ITUTOR_STUDY_REWARD_DAILY_LIMIT", "10")
    u = auth.register_user("limit@learnbuddy.test", "secret123")

    got = [
        credits.reward_study_completion(u.id, instance_id="i1", topic=f"T{i}")["points"]
        for i in range(4)
    ]
    assert got == [4, 4, 2, 0]
    assert credits.balance(u.id)["balance"] == 1010
