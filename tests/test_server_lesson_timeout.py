"""server.py 学练单元后台生成契约测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import server


SERVER = Path(__file__).resolve().parent.parent / "server.py"


def test_lesson_generation_has_background_recovery_guard():
    source = SERVER.read_text(encoding="utf-8")

    assert "def _lesson_timeout_seconds()" in source
    assert "def _lesson_hard_timeout_seconds()" in source
    assert "ITUTOR_LESSON_TIMEOUT" in source
    assert "ITUTOR_LESSON_HARD_TIMEOUT" in source
    assert "_LESSON_JOBS" in source
    assert "_LESSON_STAGE_DEFS" in source
    assert "def _build_lesson_and_save" in source
    assert "progress=lambda stage_id: _set_lesson_job_stage(job, stage_id)" in source
    assert "asyncio.shield(job[\"task\"])" in source
    assert "status_code=202" in source
    assert "@app.get(\"/api/instance/{instance_id}/lesson/status\")" in source
    assert "用于请求断开后的自动恢复" in source
    assert "asyncio.wait_for(" in source
    assert "generate_lesson_reviewed" in source
    assert "def _recover_lesson_job_from_cache" in source
    assert "recovered_from_cache" in source
    assert "can_retry" in source
    assert "progress_percent" in source
    assert "@app.get(\"/api/instance/{instance_id}/lesson/runtime\")" in source
    assert "@app.post(\"/api/instance/{instance_id}/lesson/runtime\")" in source
    assert "load_lesson_runtime" in source
    assert "save_lesson_runtime" in source
    assert "\"teaching-content-loop-v1\"" in source
    assert "deadline_exceeded" in source
    assert "_job_task_running(job)" in source
    assert "job.pop(\"deadline_detail\", None)" in source
    assert "stage_trace" in source
    assert "duration_seconds" in source
    assert "_persist_lesson_job(job)" in source
    assert "_load_lesson_job(user.id, resolved_job_id)" in source


def test_lesson_timeout_defaults_to_fast_background_polling(monkeypatch):
    monkeypatch.delenv("ITUTOR_LESSON_TIMEOUT", raising=False)
    monkeypatch.delenv("ITUTOR_LESSON_HARD_TIMEOUT", raising=False)

    assert server._lesson_timeout_seconds() == 45.0
    assert server._lesson_hard_timeout_seconds() == 300.0


def test_lesson_cache_requires_current_version_and_quality_pass():
    passed = {"_quality": {"passed": True}}
    failed = {"_quality": {"passed": False}}

    assert server._lesson_cache_metadata({}, passed)["cache_status"] == "stale_version"
    assert server._lesson_cache_metadata(
        {"cache_version": server.LESSON_CACHE_VERSION}, failed
    )["cache_status"] == "stale_quality"
    assert server._lesson_cache_metadata(
        {"cache_version": server.LESSON_CACHE_VERSION}, passed
    ) == {
        "cache_status": "current",
        "cache_version": server.LESSON_CACHE_VERSION,
        "cache_current": True,
        "quality_passed": True,
    }


def test_legacy_lesson_cache_is_visible_as_stale_not_current(tmp_path, monkeypatch):
    topic = "测试主题"
    cache_path = server._lesson_cache_path(tmp_path, topic)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps({"topic": topic, "lesson": {"_quality": {"passed": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "ensure_lesson_cards", lambda _lesson: None)
    monkeypatch.setattr(server, "_prepare_lesson_runtime", lambda lesson, **_kwargs: lesson)

    lesson = server._load_cached_lesson(tmp_path, topic=topic, domain="学习", target="目标")

    assert lesson is not None
    assert lesson["_cache_status"] == "stale_version"
    assert lesson["_cache_version"] == "legacy"
    assert lesson["_cache_current"] is False


def test_atomic_lesson_cache_failure_preserves_previous_file(tmp_path, monkeypatch):
    cache_path = tmp_path / "lessons" / "lesson.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text('{"old": true}', encoding="utf-8")
    original_replace = Path.replace

    def fail_target_replace(path, target):
        if Path(target) == cache_path:
            raise OSError("simulated replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_target_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        server._write_lesson_cache_atomic(cache_path, {"new": True})

    assert cache_path.read_text(encoding="utf-8") == '{"old": true}'
    assert not list(cache_path.parent.glob("*.tmp"))


def test_lesson_api_upgrades_stale_cache_instead_of_returning_it(tmp_path, monkeypatch):
    class Request:
        async def json(self):
            return {"topic": "测试主题"}

    async def run_case() -> None:
        instance_id = "stale-cache-instance"
        inst_dir = tmp_path / instance_id
        cache_path = server._lesson_cache_path(inst_dir, "测试主题")
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text('{"legacy": true}', encoding="utf-8")
        built = []

        monkeypatch.setattr(server, "INSTANCES_DIR", tmp_path)
        monkeypatch.setattr(
            server,
            "require_user",
            lambda _request: server.User(id=7, email="u@example.com", name="u"),
        )
        monkeypatch.setattr(server, "_assert_can_access", lambda _instance_id, _user: None)
        monkeypatch.setattr(server, "check_quota", lambda _user_id: {"allowed": True})
        monkeypatch.setattr(
            server,
            "_load_cached_lesson",
            lambda *_args, **_kwargs: {
                "topic": "旧内容",
                "_cache_current": False,
                "_cache_status": "stale_version",
            },
        )

        def build_current(**_kwargs):
            built.append(True)
            return {
                "topic": "新内容",
                "_cache_current": True,
                "_cache_status": "current",
            }

        monkeypatch.setattr(server, "_build_lesson_and_save", build_current)
        job_key = server._lesson_job_key(instance_id, "测试主题", "")
        server._LESSON_JOBS.pop(job_key, None)
        try:
            result = await server.api_lesson(instance_id, Request())
            await asyncio.sleep(0)

            assert result["topic"] == "新内容"
            assert built == [True]
            assert server._LESSON_JOBS[job_key]["previous_cache_status"] == "stale_version"
            assert server._LESSON_JOBS[job_key]["cache_status"] == "current"
        finally:
            server._LESSON_JOBS.pop(job_key, None)

    asyncio.run(run_case())


def test_lesson_session_contract_uses_collected_daily_hours():
    contract = server._lesson_session_contract(
        meta={},
        master={"schedule": {"weekday_hours": 1.5, "weekend_hours": 1.5, "weekly_total_hours": 10.5}},
    )

    assert contract["source"] == "onboarding_schedule"
    assert contract["label"] == "1–2 小时"
    assert contract["target_minutes"] == 90
    assert contract["mode"] == "full_unit"
    assert "worked_example" in contract["required_activity_types"]


def test_lesson_timeout_keeps_running_task_recoverable(monkeypatch):
    async def run_case() -> None:
        job = server._start_lesson_job(key="lesson-timeout-running", topic="测试主题")
        fut = asyncio.get_running_loop().create_future()
        server._attach_lesson_job_task(job, fut)
        job["started_at"] = datetime.now() - timedelta(seconds=10)
        monkeypatch.setattr(server, "_lesson_hard_timeout_seconds", lambda: 1)

        try:
            payload = server._lesson_job_payload(job)

            assert payload["status"] == "generating"
            assert payload["deadline_exceeded"] is True
            assert payload["can_retry"] is True
            assert "后台任务仍在运行" in payload["detail"]
        finally:
            fut.cancel()
            server._LESSON_JOBS.pop("lesson-timeout-running", None)

    asyncio.run(run_case())


def test_lesson_stage_trace_records_step_durations():
    job = server._start_lesson_job(key="lesson-trace", topic="测试主题")
    try:
        server._set_lesson_job_stage(job, "kb")
        payload = server._lesson_job_payload(job)

        assert payload["stage_trace"][0]["id"] == "context"
        assert payload["stage_trace"][0]["status"] == "done"
        assert payload["stage_trace"][0]["duration_seconds"] >= 0
        assert payload["stage_trace"][1]["id"] == "kb"
        assert payload["stage_trace"][1]["status"] == "active"
        assert payload["steps"][1]["duration_seconds"] >= 0
        assert payload["steps"][2]["status"] == "active"
    finally:
        server._LESSON_JOBS.pop("lesson-trace", None)


def test_lesson_done_after_timeout_marks_ready():
    async def run_case() -> None:
        job = server._start_lesson_job(key="lesson-timeout-ready", topic="测试主题")
        fut = asyncio.get_running_loop().create_future()
        server._attach_lesson_job_task(job, fut)
        job["timed_out"] = True
        job["deadline_exceeded"] = True
        job["can_retry"] = True
        job["deadline_detail"] = "后台仍在运行"

        try:
            fut.set_result({"topic": "测试主题", "cards": []})
            await asyncio.sleep(0)

            assert job["status"] == "ready"
            assert job["can_retry"] is False
            assert job["timed_out"] is False
            assert job["deadline_exceeded"] is False
            assert "deadline_detail" not in job
            assert job["stage_trace"][-1]["id"] == "quality_gate"
            assert job["stage_trace"][-1]["status"] == "done"
        finally:
            server._LESSON_JOBS.pop("lesson-timeout-ready", None)

    asyncio.run(run_case())


def test_generate_timeout_keeps_running_task_recoverable(monkeypatch):
    async def run_case() -> None:
        job = server._start_generate_job(user_id=7, domain="AI", target="测试目标")
        fut = asyncio.get_running_loop().create_future()
        server._attach_generate_job_task(job, fut)
        job["started_at"] = datetime.now() - timedelta(seconds=10)
        monkeypatch.setattr(server, "_generate_hard_timeout_seconds", lambda: 1)

        try:
            payload = server._generate_job_payload(job)

            assert payload["status"] == "generating"
            assert payload["deadline_exceeded"] is True
            assert payload["can_retry"] is True
            assert "后台任务仍在运行" in payload["message"]
        finally:
            fut.cancel()
            server._GENERATE_JOBS.pop(job["id"], None)

    asyncio.run(run_case())
