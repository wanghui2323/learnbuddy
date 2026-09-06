"""个人记忆单测（Batch L · 环 A）：写入/召回/幂等派生/清理/格式化。纯本地 sqlite。"""

import tempfile

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


def test_write_and_recall(env):
    from core.memory import recall, write_memory

    r = write_memory(user_id=env, content="喜欢例句多", kind="preference", instance_id="i1")
    assert r["created"] is True
    mems = recall(env, "i1")
    assert len(mems) == 1 and mems[0]["content"] == "喜欢例句多"


def test_global_and_instance_recall(env):
    from core.memory import recall, write_memory

    write_memory(user_id=env, content="全局偏好", kind="preference", instance_id=None)
    write_memory(user_id=env, content="i1 记忆", kind="fact", instance_id="i1")
    write_memory(user_id=env, content="i2 记忆", kind="fact", instance_id="i2")
    contents = {m["content"] for m in recall(env, "i1")}
    assert "全局偏好" in contents and "i1 记忆" in contents
    assert "i2 记忆" not in contents  # 别的空间记忆不串场


def test_invalid_inputs(env):
    from core.memory import write_memory

    with pytest.raises(ValueError):
        write_memory(user_id=env, content="", kind="fact")
    with pytest.raises(ValueError):
        write_memory(user_id=env, content="x", kind="weird")


def test_derived_upsert_idempotent(env):
    from core.memory import recall, write_memory

    a = write_memory(user_id=env, content="A", kind="struggle", mkey="struggle:X",
                     instance_id="i1", source="derived", weight=2.0)
    b = write_memory(user_id=env, content="A改", kind="struggle", mkey="struggle:X",
                     instance_id="i1", source="derived", weight=2.0)
    assert a["created"] is True and b["created"] is False
    strugg = [m for m in recall(env, "i1") if m["kind"] == "struggle"]
    assert len(strugg) == 1 and strugg[0]["content"] == "A改"


def test_format_for_prompt(env):
    from core.memory import format_for_prompt

    assert format_for_prompt([]) == ""
    s = format_for_prompt([{"kind": "struggle", "content": "X 弱"}])
    assert "X 弱" in s and "易错" in s


def test_refresh_derived_from_signals(env):
    from core.learning import record_lesson_result
    from core.memory import recall, refresh_derived

    record_lesson_result(
        user_id=env, instance_id="i1", topic="助词は", week=1,
        answers=[{"question": "q", "correct": 0, "answer_index": 0, "chosen_index": 1}],
        self_rating="cant",
    )
    res = refresh_derived(env, "i1")
    assert res["written"] >= 1
    mems = recall(env, "i1")
    kinds = {m["kind"] for m in mems}
    assert "struggle" in kinds  # 自评 cant → 弱项记忆
    assert any("助词は" in m["content"] for m in mems)


def test_refresh_prunes_stale(env):
    from core.memory import recall, refresh_derived, write_memory

    # 先塞一条"过时"的派生记忆，refresh 时它不再被生成 → 应被清理
    write_memory(user_id=env, content="旧弱项", kind="struggle", mkey="struggle:已不存在",
                 instance_id="i1", source="derived")
    refresh_derived(env, "i1")  # 无任何学练信号 → 不生成新派生 → 清理旧的
    assert [m for m in recall(env, "i1") if m["source"] == "derived"] == []


def test_delete(env):
    from core.memory import delete_memory, recall, write_memory

    r = write_memory(user_id=env, content="x", kind="fact", instance_id="i1")
    assert delete_memory(env, r["id"]) is True
    assert recall(env, "i1") == []
