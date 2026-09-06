"""生成类后台任务的持久事实层。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.db import get_conn


ACTIVE_STATUSES = ("generating", "running")
RECOVERY_ERROR = "服务已重启，原后台任务无法继续。已保存的结果仍可直接使用；否则请重新发起。"


def _iso(value: datetime | str | None, *, fallback: datetime | None = None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return fallback.isoformat(timespec="seconds") if fallback else None


def _json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=lambda item: _iso(item) or str(item))


def save_job(
    *,
    job_id: str,
    user_id: int,
    kind: str,
    status: str,
    instance_id: str = "",
    stage_index: int = 0,
    stage_id: str = "",
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str = "",
    started_at: datetime | str | None = None,
    finished_at: datetime | str | None = None,
) -> None:
    """幂等保存任务快照；同 id 重试会覆盖旧状态。"""
    now = datetime.now()
    safe_id = (job_id or "").strip()[:120]
    safe_kind = (kind or "").strip()[:30]
    if not safe_id or not safe_kind or int(user_id) <= 0:
        raise ValueError("后台任务缺少合法 id、kind 或 user_id")
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO background_jobs
                 (id, user_id, kind, instance_id, status, stage_index, stage_id,
                  payload_json, result_json, error, started_at, updated_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 user_id=excluded.user_id,
                 kind=excluded.kind,
                 instance_id=excluded.instance_id,
                 status=excluded.status,
                 stage_index=excluded.stage_index,
                 stage_id=excluded.stage_id,
                 payload_json=excluded.payload_json,
                 result_json=excluded.result_json,
                 error=excluded.error,
                 started_at=excluded.started_at,
                 updated_at=excluded.updated_at,
                 finished_at=excluded.finished_at""",
            (
                safe_id,
                int(user_id),
                safe_kind,
                (instance_id or "")[:160] or None,
                (status or "error")[:30],
                max(0, int(stage_index or 0)),
                (stage_id or "")[:60] or None,
                _json(payload),
                _json(result),
                (error or "")[:2000] or None,
                _iso(started_at, fallback=now),
                _iso(now),
                _iso(finished_at),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_job(user_id: int, job_id: str, *, kind: str = "") -> dict[str, Any] | None:
    """只按当前用户读取任务；结果合并进快照供运行层恢复。"""
    conn = get_conn()
    try:
        sql = "SELECT * FROM background_jobs WHERE id = ? AND user_id = ?"
        args: list[Any] = [(job_id or "").strip(), int(user_id)]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        row = conn.execute(sql, args).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            result = {}
        data = payload if isinstance(payload, dict) else {}
        if isinstance(result, dict):
            data.update(result)
        data.update(
            {
                "id": row["id"],
                "user_id": int(row["user_id"]),
                "kind": row["kind"],
                "instance_id": row["instance_id"] or data.get("instance_id"),
                "status": row["status"],
                "stage_index": int(row["stage_index"] or 0),
                "stage_id": row["stage_id"] or data.get("stage_id"),
                "error": row["error"] or data.get("error"),
            }
        )
        for key in ("started_at", "updated_at", "finished_at"):
            raw = row[key]
            if raw:
                try:
                    data[key] = datetime.fromisoformat(raw)
                except ValueError:
                    data[key] = raw
        if data.get("status") == "error":
            data["can_retry"] = True
        return data
    finally:
        conn.close()


def recover_inflight_jobs(*, error: str = RECOVERY_ERROR) -> int:
    """启动时把上个进程遗留的进行中任务转为显式可重试错误态。"""
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        cursor = conn.execute(
            """UPDATE background_jobs
                  SET status = 'error', error = ?, updated_at = ?, finished_at = ?
                WHERE status IN ('generating', 'running')""",
            ((error or RECOVERY_ERROR)[:2000], now, now),
        )
        conn.commit()
        return max(0, int(cursor.rowcount or 0))
    finally:
        conn.close()


__all__ = ["ACTIVE_STATUSES", "RECOVERY_ERROR", "save_job", "get_job", "recover_inflight_jobs"]
