"""自检 API 必须在周完成后才解锁下一周。"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

import server
from core.auth import User
from core.path_loop import ordered_units


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def _write_instance(inst_dir):
    inst_dir.mkdir(parents=True)
    master = {
        "north_star": {"domain": "AI Agent", "target": "交付原型"},
        "schedule": {"start_date": "2026-07-06", "weekday_hours": 1, "weekend_hours": 2},
        "weeks": [
            {"week": 1, "title": "起步", "status": "active", "key_outcomes": ["产出 A", "产出 B"]},
            {"week": 2, "title": "进阶", "status": "pending", "key_outcomes": ["产出 C", "产出 D"]},
        ],
        "learning_methods": {"self_check_dimensions": ["理解", "实现", "评测", "观测", "复盘", "迁移"]},
    }
    (inst_dir / "master.json").write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")
    (inst_dir / "W1.json").write_text(
        json.dumps(
            {
                "week": 1,
                "title": "起步",
                "date_range": "2026-07-06 ~ 2026-07-12",
                "key_outcomes": ["产出 A", "产出 B"],
                "days": [
                    {"day": 1, "date": "2026-07-06", "weekday": "周一", "is_weekend": False, "planned_hours": 1, "slots": [{"time": "20:00", "task": "任务 A"}]},
                    {"day": 2, "date": "2026-07-07", "weekday": "周二", "is_weekend": False, "planned_hours": 1, "slots": [{"time": "20:00", "task": "任务 B"}]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_self_check_rejects_unfinished_week_then_builds_w2(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "api.db"))
    from core.db import init_db

    init_db()
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(server, "require_user", lambda _request: User(id=7, email="u@example.com", name="U"))
    monkeypatch.setattr(server, "_assert_can_access", lambda *_args: None)
    monkeypatch.setattr(server, "log_event", lambda *_args, **_kwargs: None)
    inst_dir = tmp_path / "path-1"
    _write_instance(inst_dir)
    body = {"week": 1, "scores": [4, 4, 4, 4, 3, 3], "notes": []}
    monkeypatch.setattr(server, "studied_topics", lambda *_args: [])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.api_self_check_submit("path-1", FakeRequest(body)))
    assert exc.value.status_code == 409
    assert not (inst_dir / "W2.json").exists()

    topics = [unit["topic"] for unit in ordered_units(inst_dir)]
    monkeypatch.setattr(server, "studied_topics", lambda *_args: topics)
    result = asyncio.run(server.api_self_check_submit("path-1", FakeRequest(body)))

    assert result["passed"] is True
    assert result["path_loop"]["current_unit"]["unit_id"] == "w2-d1"
    assert (inst_dir / "W2.json").exists()
