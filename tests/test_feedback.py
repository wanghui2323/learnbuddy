"""反馈采集单测（Batch L · 环 B）：记录 / 聚合 / 非法 rating / 工具入口。纯本地 sqlite。"""

import json
import tempfile
from pathlib import Path

import pytest


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
    return uid


def test_record_and_summary(env):
    from core.feedback import feedback_summary, record_feedback

    record_feedback(user_id=env, instance_id="i1", scene="lesson", rating="up", topic="t1")
    record_feedback(user_id=env, instance_id="i1", scene="lesson", rating="down", topic="t2", reason="例句有错")
    record_feedback(user_id=env, instance_id="i1", scene="coach", rating="error", reason="算错了")

    s = feedback_summary()
    assert s["totals"]["total"] == 3
    assert s["totals"]["up"] == 1
    assert s["totals"]["neg"] == 2
    assert s["totals"]["neg_rate"] == round(2 / 3, 3)
    scenes = {r["scene"]: r for r in s["by_scene"]}
    assert scenes["lesson"]["n"] == 2 and scenes["coach"]["neg"] == 1
    # 只有带 reason 的负反馈进 recent_negative
    assert len(s["recent_negative"]) == 2


def test_invalid_rating(env):
    from core.feedback import record_feedback

    with pytest.raises(ValueError):
        record_feedback(user_id=env, instance_id="i1", scene="lesson", rating="meh")


def test_submit_feedback_tool(env, monkeypatch):
    import core.tools as tools

    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(tools, "INSTANCES_DIR", tmp)
    inst = tmp / "i1"
    inst.mkdir()
    (inst / "meta.json").write_text(json.dumps({"user_id": env}), encoding="utf-8")

    out = tools.call_tool("submit_feedback", user_id=env,
                          args={"instance_id": "i1", "rating": "down", "reason": "x"})
    assert out["ok"] is True
    from core.feedback import feedback_summary
    assert feedback_summary()["totals"]["down"] == 1

    with pytest.raises(tools.ToolError):
        tools.call_tool("submit_feedback", user_id=env, args={"instance_id": "i1", "rating": "bad"})
