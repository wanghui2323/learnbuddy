import asyncio
import json

import pytest
from fastapi import HTTPException

import server
from core.auth import User


def _write_index(root, rows):
    (root / "_index.json").write_text(
        json.dumps({"instances": rows, "count": len(rows)}, ensure_ascii=False),
        encoding="utf-8",
    )


def _row(i, *, user_id=7, status="active"):
    return {"id": f"inst-{i}", "user_id": user_id, "status": status}


def _write_meta(root, instance_id, *, user_id=7):
    inst = root / instance_id
    inst.mkdir(parents=True, exist_ok=True)
    (inst / "meta.json").write_text(
        json.dumps({"id": instance_id, "user_id": user_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_path(root, instance_id):
    inst = root / instance_id
    (inst / "master.json").write_text(
        json.dumps({"weeks": [{"week": 1, "title": "第一周"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (inst / "W1.json").write_text(
        json.dumps(
            {
                "days": [
                    {"day": 1, "title": "起步"},
                    {"day": 2, "title": "练习"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_instance_limits_count_only_current_user_active_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    _write_index(
        tmp_path,
        [
            _row(1, status="active"),
            _row(2, status="draft"),
            _row(3, status="paused"),
            _row(4, status="done"),
            _row(5, status="archived"),
            _row(6, user_id=99, status="active"),
        ],
    )

    limits = server._instance_limits(7)

    assert limits == {"max": 5, "current": 3, "remaining": 2}


def test_assert_can_create_instance_rejects_when_limit_reached(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    _write_index(tmp_path, [_row(i) for i in range(5)])

    with pytest.raises(HTTPException) as exc:
        server._assert_can_create_instance(7)

    assert exc.value.status_code == 409
    assert "5/5" in exc.value.detail


def test_instance_limits_fall_back_to_meta_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    _write_meta(tmp_path, "inst-missing-owner", user_id=7)
    _write_index(
        tmp_path,
        [
            {"id": "inst-missing-owner", "status": "active"},
            _row(2, user_id=99, status="active"),
        ],
    )

    limits = server._instance_limits(7)

    assert limits == {"max": 5, "current": 1, "remaining": 4}


def test_api_instances_repairs_missing_owner_from_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    _write_meta(tmp_path, "inst-missing-owner", user_id=7)
    _write_index(
        tmp_path,
        [
            {"id": "inst-missing-owner", "status": "draft"},
            _row(2, user_id=99, status="active"),
        ],
    )
    monkeypatch.setattr(
        server,
        "require_user",
        lambda _request: User(id=7, email="u@example.com", name="u"),
    )

    result = asyncio.run(server.api_list_instances(object()))

    assert [e["id"] for e in result["instances"]] == ["inst-missing-owner"]
    repaired = json.loads((tmp_path / "_index.json").read_text(encoding="utf-8"))
    assert repaired["instances"][0]["user_id"] == 7


def test_api_instances_returns_completion_based_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    _write_meta(tmp_path, "inst-progress", user_id=7)
    _write_path(tmp_path, "inst-progress")
    _write_index(tmp_path, [_row("progress") | {"id": "inst-progress"}])
    monkeypatch.setattr(server, "studied_topics", lambda _user_id, _instance_id: [])
    monkeypatch.setattr(
        server,
        "require_user",
        lambda _request: User(id=7, email="u@example.com", name="u"),
    )

    result = asyncio.run(server.api_list_instances(object()))

    progress = result["instances"][0]["progress"]
    assert progress == {
        "source": "path_loop",
        "status": "active",
        "current_week": 1,
        "current_day": 1,
        "completed_units": 0,
        "total_units": 2,
        "progress_percent": 0,
    }
