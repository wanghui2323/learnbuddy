"""RAG 单测（Batch RAG）：分块 / BM25 排序 / 入库检索 —— 纯逻辑 + 本地 sqlite，不调 embedding API。"""

import os
import tempfile

import pytest


@pytest.fixture()
def db(monkeypatch):
    path = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("ITUTOR_DB_PATH", path)
    # 确保未配 embedding → 走 BM25
    monkeypatch.delenv("ITUTOR_EMBED_API_KEY", raising=False)
    from core.db import init_db, get_conn
    init_db()
    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('a','b','t')")
    conn.commit()
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    conn.close()
    yield uid


def test_chunk_text_basic():
    from core.rag import chunk_text
    text = "第一段内容。" * 60 + "\n\n" + "第二段内容。" * 60
    chunks = chunk_text(text, target=200, overlap=40)
    assert len(chunks) >= 2
    assert all(c.strip() for c in chunks)


def test_chunk_text_empty():
    from core.rag import chunk_text
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_ingest_and_bm25_search(db, monkeypatch):
    monkeypatch.delenv("ITUTOR_EMBED_API_KEY", raising=False)
    from core.rag import ingest_text, search, list_sources, has_knowledge
    uid = db
    inst = "demo"
    assert has_knowledge(uid, inst) is False
    r1 = ingest_text(user_id=uid, instance_id=inst, title="被动态资料",
                     text="日语被动态由动词未然形加れる或られる构成。\n\n五段动词把词尾う段变あ段再加れる。")
    r2 = ingest_text(user_id=uid, instance_id=inst, title="敬语资料",
                     text="敬语分尊敬语和谦让语。\n\n尊敬语抬高对方，谦让语降低自己。")
    assert r1["ok"] and r2["ok"]
    assert r1["embedded"] is False  # 未配 key → BM25
    assert has_knowledge(uid, inst) is True
    assert len(list_sources(uid, inst)) == 2

    res = search(uid, inst, "被动态怎么变形", k=3)
    assert res["mode"] == "bm25"
    assert res["hits"], "应检索到结果"
    # 最相关的应来自"被动态资料"
    assert res["hits"][0]["source_title"] == "被动态资料"


def test_search_isolation_between_instances(db):
    from core.rag import ingest_text, search
    uid = db
    ingest_text(user_id=uid, instance_id="A", title="A源", text="苹果 香蕉 橙子 水果")
    ingest_text(user_id=uid, instance_id="B", title="B源", text="汽车 飞机 火车 交通")
    res = search(uid, "A", "水果", k=3)
    assert all(h["source_title"] == "A源" for h in res["hits"])


def test_search_empty_kb(db):
    from core.rag import search
    res = search(db, "empty", "任何问题", k=3)
    assert res == {"mode": "none", "hits": []}


def test_search_user_knowledge_spans_own_instances_without_cross_user_leak(db):
    from core.db import get_conn
    from core.rag import ingest_text, search_user_knowledge

    conn = get_conn()
    conn.execute("INSERT INTO users (email,password_hash,created_at) VALUES ('b','b','t')")
    conn.commit()
    other_uid = conn.execute("SELECT id FROM users WHERE email='b'").fetchone()["id"]
    conn.close()
    ingest_text(
        user_id=db,
        instance_id="path-a",
        title="项目验收清单",
        text="路径必须包含失败回归、可观察产出和上线验收。",
    )
    ingest_text(
        user_id=db,
        instance_id="path-b",
        title="部署笔记",
        text="上线前需要权限隔离、日志观测和回滚策略。",
    )
    ingest_text(
        user_id=other_uid,
        instance_id="private-path",
        title="其他用户私密资料",
        text="这是绝不能被当前用户检索到的上线验收资料。",
    )

    result = search_user_knowledge(db, "上线 验收", k=6)

    assert result["scope"] == "user"
    assert {hit["instance_id"] for hit in result["hits"]} == {"path-a", "path-b"}
    assert all(hit["source_title"] != "其他用户私密资料" for hit in result["hits"])
