"""LearnBuddy 版本发布闭环。

V0.53 固定顺序：
  human_review -> local_regression -> clean_commit
  -> production_backup -> deploy -> online_regression

用法：
  .venv/bin/python scripts/release_loop.py status
  .venv/bin/python scripts/release_loop.py verify-local
  .venv/bin/python scripts/release_loop.py release --confirm V0.53

`status` 只读；`verify-local` 只运行本地检查；只有带正确确认串的 `release`
才可能连接生产环境。任一前置门失败都会在产生生产副作用前停止。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.review_assessment_eval import calculate_review


VERSION = "V0.53"
EXPECTED_REVIEW_CASES = 18
DEFAULT_REVIEW_PATH = ROOT / "tests" / "fixtures" / "assessment_open_human_review.csv"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_to_ecs.sh"


@dataclass(frozen=True)
class ReleaseConfig:
    ssh_alias: str = "itutor"
    remote_dir: str = "/opt/itutor"
    backup_root: str = "/opt/itutor-backups"
    service: str = "itutor"
    public_url: str = "https://learnbuddy.top"


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """统一子进程入口，便于测试证明阻断时没有生产副作用。"""
    return subprocess.run(list(command), check=False, text=True, **kwargs)


def _gate(name: str, status: str, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "evidence": evidence}


def _result(
    *,
    state: str,
    current_stage: str,
    next_action: str,
    gates: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "version": VERSION,
        "state": state,
        "current_stage": current_stage,
        "next_action": next_action,
        "gates": gates,
        **extra,
    }


def inspect_human_review(path: Path = DEFAULT_REVIEW_PATH) -> dict[str, Any]:
    """读取部分或完整评分表；评分未齐时返回可行动状态而不是抛异常。"""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        return _result(
            state="blocked",
            current_stage="human_review",
            next_action=f"恢复或重新生成评分表：{path}",
            gates=[_gate("human_review", "blocked", error=str(exc), path=str(path))],
        )

    completed_cases = sum(
        bool(str(row.get("rater_1_score") or "").strip())
        and bool(str(row.get("rater_2_score") or "").strip())
        for row in rows
    )
    missing_ratings = sum(
        not bool(str(row.get(field) or "").strip())
        for row in rows
        for field in ("rater_1_score", "rater_2_score")
    )
    base_evidence = {
        "path": str(path),
        "expected_cases": EXPECTED_REVIEW_CASES,
        "cases": len(rows),
        "completed_cases": completed_cases,
        "missing_ratings": missing_ratings,
    }
    if len(rows) != EXPECTED_REVIEW_CASES:
        return _result(
            state="blocked",
            current_stage="human_review",
            next_action=f"恢复冻结的 {EXPECTED_REVIEW_CASES} 条开放题评分集",
            gates=[_gate("human_review", "blocked", **base_evidence)],
        )
    if missing_ratings:
        return _result(
            state="blocked",
            current_stage="human_review",
            next_action="请两位评审独立完成全部 18 条评分，再运行 status",
            gates=[_gate("human_review", "blocked", **base_evidence)],
        )

    try:
        metrics = calculate_review(rows)
    except (TypeError, ValueError) as exc:
        return _result(
            state="blocked",
            current_stage="human_review",
            next_action="修正评分表中的空值、非数字或越界分数",
            gates=[_gate("human_review", "blocked", **base_evidence, error=str(exc))],
        )

    evidence = {
        **base_evidence,
        "human_band_agreement": metrics["human_band_agreement"],
        "evaluator_band_agreement": metrics["evaluator_band_agreement"],
        "mean_absolute_error": metrics["mean_absolute_error"],
    }
    if not metrics["passed"]:
        return _result(
            state="failed",
            current_stage="human_review",
            next_action="只修评分合同并重跑冻结 Eval；人工门通过前不得部署或启动 V0.54",
            gates=[_gate("human_review", "failed", **evidence)],
        )
    return _result(
        state="ready",
        current_stage="local_regression",
        next_action="运行 verify-local，执行全量 pytest 与提交状态检查",
        gates=[_gate("human_review", "passed", **evidence)],
    )


def _command_evidence(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = str(result.stdout or "")
    stderr = str(result.stderr or "")
    return {
        "returncode": result.returncode,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
    }


def verify_local(
    review_path: Path = DEFAULT_REVIEW_PATH,
    *,
    runner: Runner = run_command,
) -> dict[str, Any]:
    """人工门通过后运行全量回归，并要求所有 tracked 变化已形成提交。"""
    review = inspect_human_review(review_path)
    gates = list(review["gates"])
    if review["state"] != "ready":
        return {**review, "gates": gates}

    pytest_command = [sys.executable, "-m", "pytest", "-q"]
    regression = runner(pytest_command, cwd=ROOT, capture_output=True)
    regression_evidence = {"command": pytest_command, **_command_evidence(regression)}
    if regression.returncode != 0:
        gates.append(_gate("local_regression", "failed", **regression_evidence))
        return _result(
            state="failed",
            current_stage="local_regression",
            next_action="修复失败测试并重新运行 verify-local",
            gates=gates,
        )
    gates.append(_gate("local_regression", "passed", **regression_evidence))

    worktree = runner(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
    )
    tracked_changes = [line for line in str(worktree.stdout or "").splitlines() if line.strip()]
    if worktree.returncode != 0 or tracked_changes:
        gates.append(
            _gate(
                "clean_commit",
                "failed",
                returncode=worktree.returncode,
                tracked_changes=tracked_changes,
                untracked_files_ignored=True,
            )
        )
        return _result(
            state="blocked",
            current_stage="clean_commit",
            next_action="检查并提交本版本的 tracked 变化；不要自动加入用户的未跟踪输出",
            gates=gates,
        )

    commit = runner(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True)
    commit_hash = str(commit.stdout or "").strip()
    if commit.returncode != 0 or not commit_hash:
        gates.append(_gate("clean_commit", "failed", **_command_evidence(commit)))
        return _result(
            state="failed",
            current_stage="clean_commit",
            next_action="恢复 Git 提交真相源后重新运行 verify-local",
            gates=gates,
        )
    gates.append(
        _gate(
            "clean_commit",
            "passed",
            commit=commit_hash,
            untracked_files_ignored=True,
        )
    )
    return _result(
        state="ready",
        current_stage="production_backup",
        next_action=f"确认发布提交 {commit_hash[:12]} 后运行 release --confirm {VERSION}",
        gates=gates,
        commit=commit_hash,
    )


REMOTE_BACKUP_SCRIPT = r'''set -euo pipefail
remote_dir="$1"
backup_root="$2"
service="$3"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${backup_root}/${stamp}"
db="${remote_dir}/data/itutor.db"
python="${remote_dir}/.venv/bin/python"

test -d "$remote_dir"
test -f "$db"
test -x "$python"
mkdir -p "$backup_dir"

"$python" - "$db" "$backup_dir/itutor.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY

live_integrity="$("$python" - "$db" <<'PY'
import sqlite3
import sys
con = sqlite3.connect(sys.argv[1])
try:
    print(con.execute('PRAGMA integrity_check').fetchone()[0])
finally:
    con.close()
PY
)"
backup_integrity="$("$python" - "$backup_dir/itutor.db" <<'PY'
import sqlite3
import sys
con = sqlite3.connect(sys.argv[1])
try:
    print(con.execute('PRAGMA integrity_check').fetchone()[0])
finally:
    con.close()
PY
)"
test "$live_integrity" = "ok"
test "$backup_integrity" = "ok"

if test -d "$remote_dir/data/instances"; then
  tar czf "$backup_dir/instances.tgz" -C "$remote_dir/data" instances
fi
tar --exclude=.git --exclude=.venv --exclude=data --exclude=.env \
  -czf "$backup_dir/code.tgz" -C "$remote_dir" .
test ! -f "$remote_dir/.env" || cp -p "$remote_dir/.env" "$backup_dir/.env"
systemctl cat "$service" > "$backup_dir/systemd-unit.txt"
if command -v nginx >/dev/null 2>&1; then
  nginx -T > "$backup_dir/nginx-config.txt" 2>&1
fi
sha256sum "$backup_dir/itutor.db" "$backup_dir/code.tgz" > "$backup_dir/SHA256SUMS"
printf '{"backup_dir":"%s","live_integrity":"%s","backup_integrity":"%s"}\n' \
  "$backup_dir" "$live_integrity" "$backup_integrity"
'''


REMOTE_VERIFY_SCRIPT = r'''set -euo pipefail
remote_dir="$1"
service="$2"
db="${remote_dir}/data/itutor.db"
python="${remote_dir}/.venv/bin/python"
service_state="$(systemctl is-active "$service")"
local_http="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8000/)"
integrity="$("$python" - "$db" <<'PY'
import sqlite3
import sys
con = sqlite3.connect(sys.argv[1])
try:
    print(con.execute('PRAGMA integrity_check').fetchone()[0])
finally:
    con.close()
PY
)"
server_sha="$(sha256sum "$remote_dir/server.py" | awk '{print $1}')"
baseline_sha="$(sha256sum "$remote_dir/core/baseline.py" | awk '{print $1}')"
web_sha="$(sha256sum "$remote_dir/web/index.html" | awk '{print $1}')"
release_commit="$(cat "$remote_dir/.release-commit")"
printf '{"service":"%s","local_http":"%s","db_integrity":"%s","server_sha256":"%s","baseline_sha256":"%s","web_sha256":"%s","release_commit":"%s"}\n' \
  "$service_state" "$local_http" "$integrity" "$server_sha" "$baseline_sha" "$web_sha" "$release_commit"
'''


def _ssh_script(
    config: ReleaseConfig,
    script: str,
    args: Sequence[str],
    *,
    runner: Runner,
) -> subprocess.CompletedProcess[str]:
    remote_command = "bash -s -- " + " ".join(shlex.quote(arg) for arg in args)
    return runner(
        ["ssh", "-o", "BatchMode=yes", config.ssh_alias, remote_command],
        input=script,
        capture_output=True,
    )


def _last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(str(output or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("远端命令没有返回 JSON 证据")


def create_production_backup(
    config: ReleaseConfig,
    *,
    runner: Runner = run_command,
) -> dict[str, Any]:
    result = _ssh_script(
        config,
        REMOTE_BACKUP_SCRIPT,
        [config.remote_dir, config.backup_root, config.service],
        runner=runner,
    )
    evidence = _command_evidence(result)
    if result.returncode != 0:
        return {"passed": False, **evidence}
    try:
        remote = _last_json_line(str(result.stdout or ""))
    except ValueError as exc:
        return {"passed": False, **evidence, "error": str(exc)}
    passed = remote.get("live_integrity") == "ok" and remote.get("backup_integrity") == "ok"
    return {"passed": passed, **remote, **evidence}


def deploy(config: ReleaseConfig, *, runner: Runner = run_command) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "ITUTOR_RELEASE_LOOP": "1",
            "SSH_ALIAS": config.ssh_alias,
            "REMOTE_DIR": config.remote_dir,
            "SERVICE": config.service,
            "PUBLIC_URL": config.public_url,
        }
    )
    result = runner(["bash", str(DEPLOY_SCRIPT)], cwd=ROOT, capture_output=True, env=env)
    return {"passed": result.returncode == 0, **_command_evidence(result)}


def verify_online(
    config: ReleaseConfig,
    *,
    expected_commit: str = "",
    runner: Runner = run_command,
) -> dict[str, Any]:
    remote_result = _ssh_script(
        config,
        REMOTE_VERIFY_SCRIPT,
        [config.remote_dir, config.service],
        runner=runner,
    )
    remote_evidence = _command_evidence(remote_result)
    if remote_result.returncode != 0:
        return {"passed": False, "remote": remote_evidence}
    try:
        remote = _last_json_line(str(remote_result.stdout or ""))
    except ValueError as exc:
        return {"passed": False, "remote": remote_evidence, "error": str(exc)}

    public_result = runner(
        [
            "curl",
            "-sS",
            "-L",
            "--max-time",
            "20",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            f"{config.public_url.rstrip('/')}/",
        ],
        capture_output=True,
    )
    public_http = str(public_result.stdout or "").strip()
    local_sha = hashlib.sha256((ROOT / "server.py").read_bytes()).hexdigest()
    local_baseline_sha = hashlib.sha256((ROOT / "core" / "baseline.py").read_bytes()).hexdigest()
    local_web_sha = hashlib.sha256((ROOT / "web" / "index.html").read_bytes()).hexdigest()
    passed = (
        remote.get("service") == "active"
        and remote.get("local_http") in {"200", "302", "303", "307", "308"}
        and remote.get("db_integrity") == "ok"
        and remote.get("server_sha256") == local_sha
        and remote.get("baseline_sha256") == local_baseline_sha
        and remote.get("web_sha256") == local_web_sha
        and bool(expected_commit)
        and remote.get("release_commit") == expected_commit
        and public_result.returncode == 0
        and public_http == "200"
    )
    return {
        "passed": passed,
        "remote": remote,
        "public_url": config.public_url,
        "public_http": public_http,
        "local_server_sha256": local_sha,
        "local_baseline_sha256": local_baseline_sha,
        "local_web_sha256": local_web_sha,
        "expected_commit": expected_commit,
        "public_command": _command_evidence(public_result),
    }


def perform_release(
    *,
    confirm: str,
    review_path: Path = DEFAULT_REVIEW_PATH,
    config: ReleaseConfig | None = None,
    runner: Runner = run_command,
) -> dict[str, Any]:
    """执行正式发布；所有生产副作用都位于本地门之后。"""
    if confirm != VERSION:
        return _result(
            state="blocked",
            current_stage="production_confirmation",
            next_action=f"显式提供 --confirm {VERSION}；不要确认不匹配的版本",
            gates=[_gate("production_confirmation", "blocked", received=confirm)],
        )

    local = verify_local(review_path, runner=runner)
    gates = list(local["gates"])
    if local["state"] != "ready" or local["current_stage"] != "production_backup":
        return {**local, "gates": gates}

    effective_config = config or ReleaseConfig()
    backup = create_production_backup(effective_config, runner=runner)
    if not backup["passed"]:
        gates.append(_gate("production_backup", "failed", **backup))
        return _result(
            state="failed",
            current_stage="production_backup",
            next_action="修复备份或数据库完整性问题；禁止继续部署",
            gates=gates,
        )
    gates.append(_gate("production_backup", "passed", **backup))

    deployment = deploy(effective_config, runner=runner)
    if not deployment["passed"]:
        gates.append(_gate("deploy", "failed", **deployment))
        return _result(
            state="failed",
            current_stage="deploy",
            next_action=f"部署失败；生产备份位于 {backup['backup_dir']}，先诊断再恢复或重试",
            gates=gates,
            backup_dir=backup["backup_dir"],
        )
    gates.append(_gate("deploy", "passed", **deployment))

    online = verify_online(effective_config, expected_commit=str(local.get("commit") or ""), runner=runner)
    if not online["passed"]:
        gates.append(_gate("online_regression", "failed", **online))
        return _result(
            state="failed",
            current_stage="online_regression",
            next_action=f"线上回归失败；生产备份位于 {backup['backup_dir']}，不得更新已发布状态",
            gates=gates,
            backup_dir=backup["backup_dir"],
        )
    gates.append(_gate("online_regression", "passed", **online))
    return _result(
        state="released",
        current_stage="release_record",
        next_action="归档备份路径、提交和线上证据，再同步 SPEC/工作台/AGENTS 版本状态",
        gates=gates,
        commit=local.get("commit"),
        backup_dir=backup["backup_dir"],
    )


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _exit_code(result: dict[str, Any]) -> int:
    if result["state"] in {"ready", "released"}:
        return 0
    return 2 if result["state"] == "blocked" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="LearnBuddy V0.53 Release Loop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "verify-local"):
        child = subparsers.add_parser(name)
        child.add_argument("--review-file", type=Path, default=DEFAULT_REVIEW_PATH)

    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--review-file", type=Path, default=DEFAULT_REVIEW_PATH)
    release_parser.add_argument("--confirm", required=True)
    release_parser.add_argument("--ssh-alias", default=os.getenv("SSH_ALIAS", "itutor"))
    release_parser.add_argument("--remote-dir", default=os.getenv("REMOTE_DIR", "/opt/itutor"))
    release_parser.add_argument("--backup-root", default=os.getenv("BACKUP_ROOT", "/opt/itutor-backups"))
    release_parser.add_argument("--service", default=os.getenv("SERVICE", "itutor"))
    release_parser.add_argument("--public-url", default=os.getenv("PUBLIC_URL", "https://learnbuddy.top"))
    args = parser.parse_args()

    if args.command == "status":
        result = inspect_human_review(args.review_file)
    elif args.command == "verify-local":
        result = verify_local(args.review_file)
    else:
        result = perform_release(
            confirm=args.confirm,
            review_path=args.review_file,
            config=ReleaseConfig(
                ssh_alias=args.ssh_alias,
                remote_dir=args.remote_dir,
                backup_root=args.backup_root,
                service=args.service,
                public_url=args.public_url,
            ),
        )
    _print_result(result)
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
