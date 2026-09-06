"""学情数据层单测。"""

from __future__ import annotations

import importlib
import os
from datetime import date, timedelta

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "t.db"))
    import core.db as dbmod
    import core.learning as learnmod
    importlib.reload(dbmod)
    importlib.reload(learnmod)
    dbmod.init_db()
    # 建一个用户
    conn = dbmod.get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('u@x','h','2026-01-01')")
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.commit()
    conn.close()
    return learnmod, uid


def _ans(q, ai, ci, correct, why="w"):
    return {"question": q, "options": ["a", "b"], "answer_index": ai, "chosen_index": ci, "correct": correct, "why": why}


def test_record_and_summary(db):
    learning, uid = db
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="循环", week=1,
        answers=[_ans("Q1", 0, 0, True), _ans("Q2", 1, 0, False), _ans("Q3", 0, 0, True)],
        self_rating="with_help",
    )
    s = learning.analytics_summary(uid, "i1")
    assert s["lessons_done"] == 1
    assert s["topics_studied"] == 1
    assert s["answers_total"] == 3
    assert s["objective_pct"] == 67  # 2/3
    t = s["topics"][0]
    assert t["topic"] == "循环"
    assert t["objective_pct"] == 67
    assert t["self_rating"] == "with_help"
    assert t["self_score"] == 60


def test_weak_points(db):
    learning, uid = db
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="函数", week=2,
        answers=[_ans("F1", 0, 1, False)], self_rating="cant",
    )
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="变量", week=1,
        answers=[_ans("V1", 0, 0, True), _ans("V2", 1, 1, True)], self_rating="independent",
    )
    weak = learning.weak_points(uid, "i1")
    topics = [w["topic"] for w in weak]
    assert "函数" in topics       # 0% + cant
    assert "变量" not in topics    # 100% + independent


def test_wrong_questions_dedup_latest(db):
    learning, uid = db
    # 第一次答错
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="切片", week=1,
        answers=[_ans("S1", 0, 1, False)], self_rating="cant",
    )
    # 重做答对 → 应移出错题本
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="切片", week=1,
        answers=[_ans("S1", 0, 0, True)], self_rating="with_help",
    )
    wq = learning.wrong_questions(uid, "i1")
    assert all(w["question"] != "S1" for w in wq)


def test_review_due_for_cant(db):
    learning, uid = db
    learning.record_lesson_result(
        user_id=uid, instance_id="i1", topic="递归", week=3,
        answers=[_ans("R1", 0, 1, False)], self_rating="cant",
    )
    stats = {t["topic"]: t for t in learning.topic_stats(uid, "i1")}
    # cant → 间隔 1 天，到期日 = 今天 + 1
    assert stats["递归"]["next_due"] == (date.today() + timedelta(days=1)).isoformat()


def test_isolation_between_instances(db):
    learning, uid = db
    learning.record_lesson_result(user_id=uid, instance_id="i1", topic="A", week=1,
                                  answers=[_ans("Q", 0, 0, True)], self_rating="independent")
    s2 = learning.analytics_summary(uid, "i2")
    assert s2["topics_studied"] == 0
