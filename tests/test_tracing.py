"""LLM Tracing 单测（Batch L）：成功/兜底/异常落 llm_traces；默认关闭不写库。"""

import tempfile

import pytest


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", tempfile.mktemp(suffix=".db"))
    from core.db import get_conn, init_db

    init_db()
    return get_conn


def _count(get_conn):
    conn = get_conn()
    try:
        return conn.execute("SELECT COUNT(*) n FROM llm_traces").fetchone()["n"]
    finally:
        conn.close()


def _rows(get_conn):
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM llm_traces ORDER BY id").fetchall()]
    finally:
        conn.close()


def test_disabled_no_write(db, monkeypatch):
    import core.tracing as tr

    monkeypatch.setattr(tr, "_enabled", False)
    with tr.trace_llm(scene="lesson"):
        pass
    assert _count(db) == 0


def test_success_trace(db, monkeypatch):
    import core.tracing as tr

    monkeypatch.setattr(tr, "_enabled", True)
    with tr.trace_llm(scene="lesson", instance_id="i1"):
        pass
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["ok"] == 1 and rows[0]["fallback"] == 0
    assert rows[0]["scene"] == "lesson" and rows[0]["instance_id"] == "i1"


def test_fallback_marked(db, monkeypatch):
    import core.tracing as tr

    monkeypatch.setattr(tr, "_enabled", True)
    with tr.trace_llm(scene="coach"):
        tr.mark_fallback()
    rows = _rows(db)
    assert rows[0]["fallback"] == 1 and rows[0]["ok"] == 1


def test_exception_records_then_reraises(db, monkeypatch):
    import core.tracing as tr

    monkeypatch.setattr(tr, "_enabled", True)
    with pytest.raises(RuntimeError):
        with tr.trace_llm(scene="graph"):
            raise RuntimeError("boom")
    rows = _rows(db)
    assert rows[0]["ok"] == 0
    assert "RuntimeError" in (rows[0]["error"] or "")


def test_fallback_isolated_between_calls(db, monkeypatch):
    import core.tracing as tr

    monkeypatch.setattr(tr, "_enabled", True)
    with tr.trace_llm(scene="lesson"):
        tr.mark_fallback()
    with tr.trace_llm(scene="lesson"):
        pass
    rows = _rows(db)
    assert rows[0]["fallback"] == 1 and rows[1]["fallback"] == 0
