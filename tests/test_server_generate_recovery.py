"""server.py 路径生成后台恢复契约测试。"""

from __future__ import annotations

from pathlib import Path


SERVER = Path(__file__).resolve().parent.parent / "server.py"


def test_path_generation_has_background_recovery_guard():
    source = SERVER.read_text(encoding="utf-8")

    assert "_GENERATE_JOBS" in source
    assert "def _generate_hard_timeout_seconds()" in source
    assert "ITUTOR_GENERATE_HARD_TIMEOUT" in source
    assert "def _run_generate_job_for_user" in source
    assert "_tag_instance_owner(inst_dir.name, user_id)" in source
    assert "@app.get(\"/api/generate/status\")" in source
    assert "用于 SSE 断开或刷新后的自动恢复" in source
    assert "asyncio.shield(task)" in source
    assert "progress_percent" in source
    assert "\"job_id\": job[\"id\"]" in source
    assert "save_background_job(" in source
    assert "_load_generate_job(user.id, safe_job_id)" in source
    assert "recover_inflight_jobs()" in source
