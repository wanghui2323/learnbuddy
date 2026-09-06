"""工具注册层单测（Batch L）：注册表完整性 + 确定性 handler + 访问控制。

只覆盖不调 LLM 的路径（list/kb_search/analytics/access），LLM 型工具
（generate_lesson/coach_reply/get_graph）只验访问守卫。纯本地 sqlite。
"""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", tempfile.mktemp(suffix=".db"))
    monkeypatch.delenv("ITUTOR_EMBED_API_KEY", raising=False)
    from core.db import get_conn, init_db

    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('a','b','t')")
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('c','d','t')")
    conn.commit()
    rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    conn.close()

    tmpdir = Path(tempfile.mkdtemp())
    import core.tools as tools

    monkeypatch.setattr(tools, "INSTANCES_DIR", tmpdir)
    return {"uid1": rows[0]["id"], "uid2": rows[1]["id"], "dir": tmpdir}


def _make_instance(d: Path, iid: str, owner):
    inst = d / iid
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "meta.json").write_text(
        json.dumps({"user_id": owner, "domain": "测试域", "target": "测试目标"}),
        encoding="utf-8",
    )
    return inst


def test_registry_integrity():
    from core.tools import TOOLS, TOOLS_BY_NAME, list_tools

    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names)), "工具名必须唯一"
    assert set(names) == set(TOOLS_BY_NAME)
    for t in TOOLS:
        assert callable(t.handler)
        assert t.description
        assert isinstance(t.params, dict)
    # 写数据的工具集合（agent 权限收窄用）
    assert {t.name for t in TOOLS if t.mutates} == {
        "record_answer", "submit_feedback", "remember", "set_reminder_policy",
    }
    # schema 必须可 JSON 序列化（供 /api/tools 与 MCP）
    json.dumps(list_tools(), ensure_ascii=False)


def test_call_unknown_tool(env):
    from core.tools import ToolError, call_tool

    with pytest.raises(ToolError):
        call_tool("not_a_tool", user_id=env["uid1"], args={})


def test_call_missing_required_param(env):
    from core.tools import ToolError, call_tool

    with pytest.raises(ToolError):
        call_tool("get_graph", user_id=env["uid1"], args={})


def test_list_instances_empty(env):
    from core.tools import call_tool

    out = call_tool("list_instances", user_id=env["uid1"], args={})
    assert out == {"count": 0, "instances": []}


def test_access_guard_blocks_other_user(env):
    from core.tools import ToolError, call_tool

    _make_instance(env["dir"], "inst-x", owner=env["uid2"])
    with pytest.raises(ToolError):
        call_tool("kb_search", user_id=env["uid1"], args={"instance_id": "inst-x", "query": "hi"})


def test_kb_search_empty_kb(env):
    from core.tools import call_tool

    _make_instance(env["dir"], "inst-1", owner=env["uid1"])
    out = call_tool("kb_search", user_id=env["uid1"], args={"instance_id": "inst-1", "query": "随便"})
    assert out["mode"] == "none"
    assert out["hits"] == []


def test_get_analytics_returns_dict(env):
    from core.tools import call_tool

    _make_instance(env["dir"], "inst-1", owner=env["uid1"])
    out = call_tool("get_analytics", user_id=env["uid1"], args={"instance_id": "inst-1"})
    assert isinstance(out, dict)


def test_unowned_instance_is_not_exposed_to_agent(env):
    from core.tools import ToolError, call_tool

    # meta 无 user_id → 历史无归属实例，对所有人开放
    inst = env["dir"] / "legacy"
    inst.mkdir(parents=True)
    (inst / "meta.json").write_text(json.dumps({"domain": "d", "target": "t"}), encoding="utf-8")
    with pytest.raises(ToolError, match="无权访问"):
        call_tool("kb_search", user_id=env["uid1"], args={"instance_id": "legacy", "query": "q"})


def test_user_id_cannot_be_supplied_by_agent(env):
    from core.tools import ToolError, call_tool

    with pytest.raises(ToolError, match="服务端身份注入"):
        call_tool("list_instances", user_id=env["uid1"], args={"user_id": env["uid2"]})
