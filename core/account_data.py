"""账户数据导出、彻底删除与保留期清理。"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.auth import AuthError, authenticate, get_user_by_id
from core.db import get_conn


EXPORT_TABLES = (
    "usage",
    "events",
    "learning_answers",
    "lesson_completions",
    "artifacts",
    "kb_sources",
    "kb_chunks",
    "feedback",
    "llm_traces",
    "user_memory",
    "push_send_log",
    "channel_bindings",
    "reminder_policies",
    "notification_deliveries",
    "credit_ledger",
    "conversation_messages",
    "baseline_assessments",
    "baseline_responses",
    "baseline_score_evidence",
    "baseline_profiles",
    "baseline_assist_messages",
    "baseline_disputes",
    "replan_proposals",
    "background_jobs",
)

DELETE_TABLES = (
    "notification_deliveries",
    "channel_binding_codes",
    "reminder_policies",
    "channel_bindings",
    "baseline_disputes",
    "baseline_assist_messages",
    "baseline_score_evidence",
    "baseline_profiles",
    "baseline_responses",
    "kb_chunks",
    "usage",
    "events",
    "learning_answers",
    "lesson_completions",
    "artifacts",
    "feedback",
    "llm_traces",
    "user_memory",
    "push_subs",
    "push_send_log",
    "credit_ledger",
    "conversation_messages",
    "conversation_sessions",
    "background_jobs",
    "baseline_assessments",
    "replan_proposals",
    "sessions",
    "kb_sources",
)

_INSTANCE_EXPORT_SUFFIXES = {".json", ".md", ".txt"}
_MAX_INSTANCE_EXPORT_BYTES = 10 * 1024 * 1024


def _safe_rows(conn, table: str, user_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(f'SELECT * FROM "{table}" WHERE user_id = ? ORDER BY rowid', (user_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item.pop("embedding", None)
        item.pop("claim_token", None)
        for key in (
            "payload_json", "result_json", "questions", "summary", "proposal_json",
            "blueprint_json", "rubric_scores_json", "flags_json", "profile_json", "assist_trace",
            "weekdays_json",
        ):
            if key not in item or not isinstance(item[key], str):
                continue
            try:
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            except json.JSONDecodeError:
                pass
        result.append(item)
    return result


def _owned_instance_dirs(instances_dir: Path, user_id: int) -> list[Path]:
    owned: list[Path] = []
    if not instances_dir.exists():
        return owned
    for path in instances_dir.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        meta_path = path / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if int(meta.get("user_id") or 0) == int(user_id):
                owned.append(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return owned


def _export_instance(path: Path) -> dict[str, Any]:
    files: dict[str, Any] = {}
    total = 0
    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in _INSTANCE_EXPORT_SUFFIXES:
            continue
        size = file_path.stat().st_size
        if size > 2 * 1024 * 1024 or total + size > _MAX_INSTANCE_EXPORT_BYTES:
            continue
        relative = file_path.relative_to(path).as_posix()
        raw = file_path.read_text(encoding="utf-8")
        total += size
        if file_path.suffix.lower() == ".json":
            try:
                files[relative] = json.loads(raw)
                continue
            except json.JSONDecodeError:
                pass
        files[relative] = raw
    return {"id": path.name, "files": files}


def export_user_data(user_id: int, instances_dir: Path) -> dict[str, Any]:
    """导出当前账号的结构化数据和学习路径文件，不包含密码/会话/推送密钥。"""
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("账号不存在")
    conn = get_conn()
    try:
        records = {table: _safe_rows(conn, table, user_id) for table in EXPORT_TABLES}
    finally:
        conn.close()
    instances = [_export_instance(path) for path in _owned_instance_dirs(Path(instances_dir), user_id)]
    return {
        "schema_version": "learnbuddy-account-export-v3",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "account": user.public(),
        "records": records,
        "instances": instances,
        "omitted_for_security": [
            "password_hash",
            "session_tokens",
            "push_subscription_keys",
            "binding_code_digests",
            "agent_tokens",
            "delivery_claim_tokens",
            "embeddings",
        ],
    }


def _rewrite_index_without_user(instances_dir: Path, user_id: int) -> bytes | None:
    index_path = instances_dir / "_index.json"
    if not index_path.exists():
        return None
    original = index_path.read_bytes()
    index = json.loads(original.decode("utf-8"))
    kept = [entry for entry in index.get("instances", []) if int(entry.get("user_id") or 0) != int(user_id)]
    index["instances"] = kept
    index["count"] = len(kept)
    index["_updated_at"] = datetime.now().isoformat(timespec="seconds")
    temp = index_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(index_path)
    return original


def delete_user_data(user_id: int, password: str, instances_dir: Path) -> dict[str, Any]:
    """密码复核后删除账号及归属路径；文件先隔离，DB 失败时可恢复。"""
    user = get_user_by_id(user_id)
    if user is None:
        raise AuthError("账号不存在")
    verified = authenticate(user.email, password or "")
    if verified.id != user_id:
        raise AuthError("密码确认失败")

    instances_dir = Path(instances_dir)
    owned = _owned_instance_dirs(instances_dir, user_id)
    quarantine = instances_dir.parent / f".account-delete-{uuid.uuid4().hex}"
    quarantine.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    original_index: bytes | None = None
    index_path = instances_dir / "_index.json"
    try:
        for source in owned:
            target = quarantine / source.name
            shutil.move(str(source), str(target))
            moved.append((source, target))
        original_index = _rewrite_index_without_user(instances_dir, user_id)

        conn = get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE credit_cards SET redeemed_by = NULL WHERE redeemed_by = ?", (user_id,))
            conn.execute("UPDATE credit_cards SET created_by = NULL WHERE created_by = ?", (user_id,))
            conn.execute("UPDATE credit_ledger SET admin_id = NULL WHERE admin_id = ?", (user_id,))
            for table in DELETE_TABLES:
                conn.execute(f'DELETE FROM "{table}" WHERE user_id = ?', (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception:
        if original_index is not None:
            index_path.write_bytes(original_index)
        for source, target in reversed(moved):
            if target.exists():
                shutil.move(str(target), str(source))
        shutil.rmtree(quarantine, ignore_errors=True)
        raise
    shutil.rmtree(quarantine, ignore_errors=True)
    return {"deleted": True, "user_id": user_id, "instances_deleted": len(owned)}


def cleanup_expired_data(days: int, *, now: datetime | None = None) -> dict[str, int]:
    """清理过期对话/任务/行为日志；学习成果与计费流水不自动删除。"""
    safe_days = max(1, int(days or 1))
    current = now or datetime.now()
    cutoff = (current - timedelta(days=safe_days)).isoformat(timespec="seconds")
    limits = {
        "conversation_messages": ("created_at", ""),
        "background_jobs": ("updated_at", " AND status NOT IN ('generating','running')"),
        "channel_binding_codes": ("expires_at", ""),
        "notification_deliveries": ("updated_at", " AND status IN ('sent','failed','skipped')"),
        "rate_limit_events": ("created_at", ""),
        "events": ("created_at", ""),
        "llm_traces": ("created_at", ""),
    }
    deleted: dict[str, int] = {}
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table, (column, extra) in limits.items():
            cursor = conn.execute(f'DELETE FROM "{table}" WHERE "{column}" < ?{extra}', (cutoff,))
            deleted[table] = max(0, int(cursor.rowcount or 0))
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (current.isoformat(timespec="seconds"),))
        deleted["sessions"] = max(0, int(cursor.rowcount or 0))
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["export_user_data", "delete_user_data", "cleanup_expired_data"]
