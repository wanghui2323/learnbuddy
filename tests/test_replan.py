"""动态重规划三触发器单测（Batch L · 环 A 收尾）：卡壳/超额/中断。纯本地 sqlite。"""

import tempfile
import json
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


def _detect(env):
    from core.replan import detect

    return detect(user_id=env["uid"], instance_id="i1", inst_dir=env["dir"], master=MASTER)


def test_no_history_no_triggers(env):
    out = _detect(env)
    assert out["has_history"] is False and out["triggers"] == []


def test_stuck(env):
    from core.learning import record_lesson_result

    for _ in range(2):  # 学两次仍 cant → 卡壳
        record_lesson_result(
            user_id=env["uid"], instance_id="i1", topic="助词は", week=1,
            answers=[{"question": "q", "correct": 0, "answer_index": 0, "chosen_index": 1}],
            self_rating="cant",
        )
    trigs = _detect(env)["triggers"]
    kinds = {t["kind"] for t in trigs}
    assert "stuck" in kinds
    stuck = next(t for t in trigs if t["kind"] == "stuck")
    assert any(a["type"] == "slow_down" for a in stuck["actions"])


def test_stuck_with_prereq_from_graph(env):
    import json

    from core.learning import record_lesson_result

    # 落一个含前置的图谱
    (env["dir"] / "concepts.json").write_text(json.dumps({"concepts": [
        {"id": "c1", "name": "五十音", "week": 1, "difficulty": 1, "prerequisites": []},
        {"id": "c2", "name": "助词は", "week": 2, "difficulty": 4, "prerequisites": ["c1"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    for _ in range(2):
        record_lesson_result(
            user_id=env["uid"], instance_id="i1", topic="助词は", week=2,
            answers=[{"question": "q", "correct": 0, "answer_index": 0, "chosen_index": 1}],
            self_rating="cant",
        )
    stuck = next(t for t in _detect(env)["triggers"] if t["kind"] == "stuck")
    assert any(a["type"] == "review_prereq" and a["topic"] == "五十音" for a in stuck["actions"])


def test_idle(env):
    from core.db import get_conn

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
    trigs = _detect(env)["triggers"]
    idle = next(t for t in trigs if t["kind"] == "idle")
    assert idle["severity"] == "mid"  # 10 天 < 14 → mid


def test_ahead(env):
    from core.learning import record_lesson_result

    for i in range(3):  # 三个知识点都独立 + 满分 → 超额
        record_lesson_result(
            user_id=env["uid"], instance_id="i1", topic=f"主题{i}", week=1,
            answers=[{"question": "q", "correct": 1, "answer_index": 0, "chosen_index": 0}],
            self_rating="independent",
        )
    kinds = {t["kind"] for t in _detect(env)["triggers"]}
    assert "ahead" in kinds
    assert "stuck" not in kinds  # 都掌握了，不该判卡壳


def _seed_stuck(env):
    from core.learning import record_lesson_result

    for _ in range(2):
        record_lesson_result(
            user_id=env["uid"], instance_id="i1", topic="助词は", week=1,
            answers=[{"question": "q", "correct": 0, "answer_index": 0, "chosen_index": 1}],
            self_rating="cant",
        )


def test_proposal_requires_confirm_and_supports_undo(env):
    from core.replan import create_proposal, decide_proposal

    _seed_stuck(env)
    before = {
        **MASTER,
        "weeks": [{"week": 1, "title": "五十音", "status": "active", "key_outcomes": ["完成练习"]}],
    }
    (env["dir"] / "master.json").write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")

    proposal = create_proposal(
        user_id=env["uid"], instance_id="i1", inst_dir=env["dir"], master=before
    )

    assert proposal["status"] == "proposed"
    assert json.loads((env["dir"] / "master.json").read_text(encoding="utf-8")) == before

    applied = decide_proposal(
        user_id=env["uid"], instance_id="i1", proposal_id=proposal["id"],
        action="confirm", inst_dir=env["dir"],
    )
    after = json.loads((env["dir"] / "master.json").read_text(encoding="utf-8"))
    assert applied["status"] == "applied"
    assert after["_replan"]["active_adjustment"]["proposal_id"] == proposal["id"]
    assert len(after["weeks"][0]["key_outcomes"]) == 2

    undone = decide_proposal(
        user_id=env["uid"], instance_id="i1", proposal_id=proposal["id"],
        action="undo", inst_dir=env["dir"],
    )
    assert undone["status"] == "undone"
    assert json.loads((env["dir"] / "master.json").read_text(encoding="utf-8")) == before


def test_reject_keeps_master_unchanged_and_cross_user_is_blocked(env):
    from core.db import get_conn
    from core.replan import create_proposal, decide_proposal

    _seed_stuck(env)
    before = {**MASTER, "weeks": [{"week": 1, "title": "五十音", "status": "active"}]}
    (env["dir"] / "master.json").write_text(json.dumps(before, ensure_ascii=False), encoding="utf-8")
    proposal = create_proposal(
        user_id=env["uid"], instance_id="i1", inst_dir=env["dir"], master=before
    )
    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('other','b','t')")
    conn.commit()
    other = conn.execute("SELECT id FROM users WHERE email='other'").fetchone()["id"]
    conn.close()

    with pytest.raises(ValueError, match="不存在或不属于"):
        decide_proposal(
            user_id=other, instance_id="i1", proposal_id=proposal["id"],
            action="confirm", inst_dir=env["dir"],
        )

    rejected = decide_proposal(
        user_id=env["uid"], instance_id="i1", proposal_id=proposal["id"],
        action="reject", inst_dir=env["dir"],
    )
    assert rejected["status"] == "rejected"
    assert json.loads((env["dir"] / "master.json").read_text(encoding="utf-8")) == before
