"""V0.53 Release Loop 的门禁、顺序与无副作用阻断测试。"""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

from scripts import release_loop


def _write_review(path: Path, *, evaluator: float, rater_1: float | None, rater_2: float | None) -> None:
    fields = ("case_id", "evaluator_score", "rater_1_score", "rater_2_score")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(release_loop.EXPECTED_REVIEW_CASES):
            writer.writerow(
                {
                    "case_id": f"case-{index + 1}",
                    "evaluator_score": evaluator,
                    "rater_1_score": "" if rater_1 is None else rater_1,
                    "rater_2_score": "" if rater_2 is None else rater_2,
                }
            )


def test_status_reports_exact_missing_human_ratings(tmp_path):
    review = tmp_path / "review.csv"
    _write_review(review, evaluator=70, rater_1=None, rater_2=None)

    result = release_loop.inspect_human_review(review)

    assert result["state"] == "blocked"
    assert result["current_stage"] == "human_review"
    evidence = result["gates"][0]["evidence"]
    assert evidence["cases"] == 18
    assert evidence["completed_cases"] == 0
    assert evidence["missing_ratings"] == 36


def test_status_unlocks_local_regression_only_after_human_gate_passes(tmp_path):
    review = tmp_path / "review.csv"
    _write_review(review, evaluator=70, rater_1=68, rater_2=70)

    result = release_loop.inspect_human_review(review)

    assert result["state"] == "ready"
    assert result["current_stage"] == "local_regression"
    evidence = result["gates"][0]["evidence"]
    assert evidence["evaluator_band_agreement"] == 1
    assert evidence["mean_absolute_error"] == 1


def test_status_blocks_deploy_when_metrics_fail_even_with_complete_scores(tmp_path):
    review = tmp_path / "review.csv"
    _write_review(review, evaluator=58, rater_1=62, rater_2=62)

    result = release_loop.inspect_human_review(review)

    assert result["state"] == "failed"
    assert result["current_stage"] == "human_review"
    assert "不得部署" in result["next_action"]


def test_verify_local_runs_pytest_then_records_clean_commit(tmp_path):
    review = tmp_path / "review.csv"
    _write_review(review, evaluator=70, rater_1=68, rater_2=70)
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        command = list(command)
        calls.append(command)
        if command[:3] == [sys.executable, "-m", "pytest"]:
            return subprocess.CompletedProcess(command, 0, stdout="400 passed", stderr="")
        if command[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        raise AssertionError(command)

    result = release_loop.verify_local(review, runner=runner)

    assert result["state"] == "ready"
    assert result["current_stage"] == "production_backup"
    assert result["commit"] == "a" * 40
    assert calls[0][:3] == [sys.executable, "-m", "pytest"]
    assert calls[1] == ["git", "status", "--porcelain", "--untracked-files=no"]


def test_release_confirmation_and_human_gate_stop_before_any_command(tmp_path):
    review = tmp_path / "review.csv"
    _write_review(review, evaluator=70, rater_1=None, rater_2=None)
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        raise AssertionError("blocked release must not run subprocesses")

    wrong_version = release_loop.perform_release(confirm="V0.54", review_path=review, runner=runner)
    missing_humans = release_loop.perform_release(confirm="V0.53", review_path=review, runner=runner)

    assert wrong_version["current_stage"] == "production_confirmation"
    assert missing_humans["current_stage"] == "human_review"
    assert calls == []


def test_release_runs_backup_deploy_and_online_regression_in_order(monkeypatch):
    order: list[str] = []
    commit = "b" * 40

    monkeypatch.setattr(
        release_loop,
        "verify_local",
        lambda *_args, **_kwargs: {
            "state": "ready",
            "current_stage": "production_backup",
            "gates": [],
            "commit": commit,
        },
    )

    def backup(*_args, **_kwargs):
        order.append("backup")
        return {"passed": True, "backup_dir": "/opt/itutor-backups/now"}

    def deploy(*_args, **_kwargs):
        order.append("deploy")
        return {"passed": True}

    def verify(*_args, expected_commit="", **_kwargs):
        order.append(f"verify:{expected_commit}")
        return {"passed": True}

    monkeypatch.setattr(release_loop, "create_production_backup", backup)
    monkeypatch.setattr(release_loop, "deploy", deploy)
    monkeypatch.setattr(release_loop, "verify_online", verify)

    result = release_loop.perform_release(confirm="V0.53")

    assert result["state"] == "released"
    assert order == ["backup", "deploy", f"verify:{commit}"]


def test_transport_script_blocks_direct_restart_before_network():
    env = os.environ.copy()
    env.pop("ITUTOR_RELEASE_LOOP", None)
    env.pop("ITUTOR_ALLOW_DIRECT_DEPLOY", None)
    result = subprocess.run(
        ["bash", str(release_loop.DEPLOY_SCRIPT), "server.py"],
        cwd=release_loop.ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "release_loop.py release --confirm V0.53" in result.stdout

