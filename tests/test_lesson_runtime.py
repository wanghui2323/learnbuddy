import asyncio
import json

import server
from core.auth import User
from core.lesson_runtime import load_lesson_runtime, merge_lesson_runtime, save_lesson_runtime


def test_merge_lesson_runtime_keeps_user_progress_and_text():
    runtime = merge_lesson_runtime(
        "AI Agent",
        {},
        {
            "current_step": 2,
            "furthest_step": 3,
            "completed_steps": ["prime-1", "explain-2"],
            "selected_options": {"prime": "先看问题"},
            "free_inputs": {"prime": "我想理解工具调用"},
            "outputs": {"produce": {"content": "我的复述"}},
        },
    )

    runtime = merge_lesson_runtime(
        "AI Agent",
        runtime,
        {
            "current_step": 1,
            "completed_steps": ["explain-2", "produce-3"],
            "selected_options": {"self_rate": "with_help", "cards": {"card-2": "B"}},
            "free_inputs": {"prime": ""},
        },
    )

    runtime = merge_lesson_runtime(
        "AI Agent",
        runtime,
        {
            "selected_options": {"cards": {"card-3": "C"}},
            "outputs": {"produce": {"checked": True}},
        },
    )

    assert runtime["current_step"] == 1
    assert runtime["furthest_step"] == 3
    assert runtime["completed_steps"] == ["prime-1", "explain-2", "produce-3"]
    assert runtime["selected_options"]["prime"] == "先看问题"
    assert runtime["selected_options"]["self_rate"] == "with_help"
    assert runtime["selected_options"]["cards"] == {"card-2": "B", "card-3": "C"}
    assert runtime["free_inputs"]["prime"] == "我想理解工具调用"
    assert runtime["outputs"]["produce"]["content"] == "我的复述"
    assert runtime["outputs"]["produce"]["checked"] is True


def test_save_and_load_lesson_runtime(tmp_path):
    saved = save_lesson_runtime(
        tmp_path,
        "记忆系统",
        {
            "current_step": 4,
            "furthest_step": 4,
            "completed_steps": ["prime-1", "explain-2"],
            "status": "in_progress",
        },
    )
    loaded = load_lesson_runtime(tmp_path, "记忆系统")

    assert saved["schema_version"] == 1
    assert loaded["topic"] == "记忆系统"
    assert loaded["current_step"] == 4
    assert loaded["completed_steps"] == ["prime-1", "explain-2"]


class _FakeRequest:
    def __init__(self, *, topic="", body=None):
        self.query_params = {"topic": topic} if topic else {}
        self._body = body or {}

    async def json(self):
        return self._body


def test_server_lesson_runtime_api_persists_separate_user_state(tmp_path, monkeypatch):
    instance_id = "inst-runtime"
    inst_dir = tmp_path / instance_id
    inst_dir.mkdir(parents=True)
    (inst_dir / "meta.json").write_text(
        json.dumps({"id": instance_id, "user_id": 7}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(
        server,
        "require_user",
        lambda _request: User(id=7, email="u@example.com", name="u"),
    )

    saved = asyncio.run(
        server.api_save_lesson_runtime(
            instance_id,
            _FakeRequest(
                body={
                    "topic": "记忆系统全景图",
                    "runtime": {
                        "current_step": 2,
                        "furthest_step": 2,
                        "selected_options": {"cards": {"card-1": "分层存储"}},
                        "free_inputs": {"prime": "我会区分工作记忆和情景记忆"},
                    },
                }
            ),
        )
    )
    loaded = asyncio.run(
        server.api_lesson_runtime(
            instance_id, _FakeRequest(topic="记忆系统全景图")
        )
    )

    assert saved["current_step"] == 2
    assert loaded["selected_options"]["cards"]["card-1"] == "分层存储"
    assert loaded["free_inputs"]["prime"] == "我会区分工作记忆和情景记忆"
    assert (inst_dir / "lesson_runtime").is_dir()
    meta = json.loads((inst_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"id": instance_id, "user_id": 7}


def _write_path_instance(inst_dir, instance_id="inst-path"):
    inst_dir.mkdir(parents=True)
    (inst_dir / "meta.json").write_text(
        json.dumps({"id": instance_id, "user_id": 7, "domain": "AI Agent"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (inst_dir / "master.json").write_text(
        json.dumps({"north_star": {"domain": "AI Agent"}, "weeks": [{"week": 1, "title": "记忆系统"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (inst_dir / "W1.json").write_text(
        json.dumps(
            {
                "week": 1,
                "title": "记忆系统",
                "days": [
                    {"day": 1, "date": "2026-06-22", "slots": [{"task": "推进新内容：三层记忆", "output": "结构图"}]},
                    {"day": 2, "date": "2026-06-23", "slots": [{"task": "轻量消化：检索策略", "output": "对比表"}]},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_server_path_loop_api_returns_current_unit(tmp_path, monkeypatch):
    instance_id = "inst-path"
    inst_dir = tmp_path / instance_id
    _write_path_instance(inst_dir, instance_id)
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(server, "require_user", lambda _request: User(id=7, email="u@example.com", name="u"))
    monkeypatch.setattr(server, "studied_topics", lambda _uid, _iid: ["记忆系统"])

    state = asyncio.run(server.api_path_loop(instance_id, _FakeRequest()))

    assert state["current_unit"]["unit_id"] == "w1-d2"
    assert state["completed_unit_ids"] == ["w1-d1"]


def test_server_record_event_returns_path_loop_state(tmp_path, monkeypatch):
    instance_id = "inst-path"
    inst_dir = tmp_path / instance_id
    _write_path_instance(inst_dir, instance_id)
    monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
    monkeypatch.setattr(server, "require_user", lambda _request: User(id=7, email="u@example.com", name="u"))
    monkeypatch.setattr(server, "studied_topics", lambda _uid, _iid: [])
    monkeypatch.setattr(server, "record_lesson_result", lambda **_kwargs: {"correct": 1, "total": 1, "self_rating": "independent"})
    monkeypatch.setattr(server, "reward_study_completion", lambda *_args, **_kwargs: {"awarded": False})
    monkeypatch.setattr(server, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_maybe_start_next_pregeneration", lambda **kwargs: kwargs["state"])

    result = asyncio.run(
        server.api_record_event(
            instance_id,
            _FakeRequest(
                body={
                    "topic": "记忆系统",
                    "week": 1,
                    "answers": [{"question": "q", "correct": True}],
                    "self_rating": "independent",
                }
            ),
        )
    )

    assert result["ok"] is True
    assert result["path_loop"]["current_unit"]["unit_id"] == "w1-d2"
    assert result["path_loop"]["events"][0]["kind"] == "lesson_completed"
