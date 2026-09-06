"""每日推荐编排器单测（Batch L · 环 A 决策）：复习/新课/弱项路由。纯本地 sqlite。"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

MASTER = {
    "north_star": {"domain": "日语", "weeks": 12},
    "schedule": {"start_date": "2026-06-01"},
    "weeks": [{"week": 1, "title": "五十音"}],
}


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", tempfile.mktemp(suffix=".db"))
    from core.db import get_conn, init_db

    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('a','b','t')")
    conn.commit()
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.close()
    return {"uid": uid, "dir": Path(tempfile.mkdtemp())}


def _plan(env):
    from core.recommend import daily_plan

    return daily_plan(user_id=env["uid"], instance_id="i1", inst_dir=env["dir"], master=MASTER)


def test_fresh_recommends_new(env):
    plan = _plan(env)
    assert plan["items"]
    assert any(it["kind"] == "new" for it in plan["items"])
    assert "还没有学练" in plan["rationale"]


def test_due_review_prioritized(env):
    from core.db import get_conn

    # 10 天前完成、自评 independent（间隔 4 天）→ 已到期
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    conn = get_conn()
    conn.execute(
        """INSERT INTO lesson_completions
             (user_id,instance_id,topic,week,correct_count,total,self_rating,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (env["uid"], "i1", "旧主题", 1, 3, 3, "independent", old),
    )
    conn.commit()
    conn.close()

    plan = _plan(env)
    kinds = [it["kind"] for it in plan["items"]]
    assert kinds and kinds[0] == "review"  # 抗遗忘：复习排第一
    assert plan["counts"]["due"] >= 1
    assert "复习" in plan["rationale"]


def test_weak_when_no_due(env):
    from core.learning import record_lesson_result

    record_lesson_result(
        user_id=env["uid"], instance_id="i1", topic="助词", week=1,
        answers=[{"question": "q", "correct": 0, "answer_index": 0, "chosen_index": 1}],
        self_rating="cant",
    )
    plan = _plan(env)
    assert any(it["kind"] == "weak" for it in plan["items"])
    assert plan["counts"]["weak"] >= 1
    assert "mix" in plan  # 混合复习：输出含 review/new/weak 配比


MASTER_MONTHS = {
    "north_star": {"domain": "日语", "weeks": 8},
    "schedule": {"start_date": "2026-06-01"},
    "weeks": [{"week": 1, "title": "五十音"}],
    "months": [
        {"id": "M1", "title": "基础", "weeks": [1, 2]},
        {"id": "M2", "title": "进阶", "weeks": [3, 4]},
    ],
}


def test_phase_gate_blocks_next_phase_new(env):
    import json

    from core.recommend import daily_plan

    # M1(week1) 与 M2(week3) 各一个无前置的前线概念；M1 未通关 → M2 应被闸门挡住
    (env["dir"] / "concepts.json").write_text(json.dumps({"concepts": [
        {"id": "c1", "name": "基础A", "week": 1, "difficulty": 1, "prerequisites": []},
        {"id": "c2", "name": "进阶B", "week": 3, "difficulty": 5, "prerequisites": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    plan = daily_plan(user_id=env["uid"], instance_id="i1", inst_dir=env["dir"], master=MASTER_MONTHS)
    new_topics = [it["topic"] for it in plan["items"] if it["kind"] == "new"]
    assert "基础A" in new_topics
    assert "进阶B" not in new_topics          # 阶段闸门：M2 未解锁，不越级推
    assert plan["phase"] and plan["phase"]["id"] == "M1"
