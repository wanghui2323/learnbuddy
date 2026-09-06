"""后台任务持久化与重启恢复测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.auth import register_user
from core.db import init_db
from core.jobs import RECOVERY_ERROR, get_job, recover_inflight_jobs, save_job


@pytest.fixture()
def users(tmp_path, monkeypatch):
    monkeypatch.setenv("ITUTOR_DB_PATH", str(tmp_path / "jobs.db"))
    init_db()
    return (
        register_user("jobs@example.com", "secret123"),
        register_user("other-jobs@example.com", "secret123"),
    )


def test_job_snapshot_survives_memory_loss_and_is_owner_scoped(users):
    owner, other = users
    started = datetime.now()
    save_job(
        job_id="generate-1",
        user_id=owner.id,
        kind="generate",
        status="ready",
        instance_id="japanese-n3",
        stage_index=4,
        payload={"domain": "日语", "started_at": started},
        result={"redirect_url": "/instance/japanese-n3"},
        started_at=started,
        finished_at=datetime.now(),
    )

    restored = get_job(owner.id, "generate-1", kind="generate")

    assert restored is not None
    assert restored["status"] == "ready"
    assert restored["instance_id"] == "japanese-n3"
    assert restored["redirect_url"] == "/instance/japanese-n3"
    assert isinstance(restored["started_at"], datetime)
    assert get_job(other.id, "generate-1", kind="generate") is None


def test_startup_marks_orphaned_running_jobs_as_retryable_error(users):
    owner, _ = users
    save_job(
        job_id="lesson-running",
        user_id=owner.id,
        kind="lesson",
        status="generating",
        instance_id="path-1",
        stage_index=5,
        stage_id="lesson_design",
        payload={"topic": "语法基础"},
    )

    assert recover_inflight_jobs() == 1
    restored = get_job(owner.id, "lesson-running", kind="lesson")

    assert restored is not None
    assert restored["status"] == "error"
    assert restored["can_retry"] is True
    assert restored["error"] == RECOVERY_ERROR
    assert restored["topic"] == "语法基础"


def test_same_job_id_retry_overwrites_old_error(users):
    owner, _ = users
    save_job(job_id="retry-1", user_id=owner.id, kind="lesson", status="error", error="old")
    save_job(
        job_id="retry-1",
        user_id=owner.id,
        kind="lesson",
        status="generating",
        payload={"topic": "新任务"},
    )

    restored = get_job(owner.id, "retry-1", kind="lesson")

    assert restored is not None
    assert restored["status"] == "generating"
    assert restored["error"] is None
    assert restored["topic"] == "新任务"
