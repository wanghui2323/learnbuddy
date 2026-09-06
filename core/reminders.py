"""用户级学习提醒规则、幂等调度与发送队列。"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.db import get_conn


class ReminderError(ValueError):
    """可安全返回给 Web / Agent 的规则错误。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _valid_hhmm(value: str, label: str) -> str:
    normalized = (value or "").strip()
    try:
        datetime.strptime(normalized, "%H:%M")
    except ValueError as exc:
        raise ReminderError(f"{label} 必须是 HH:MM，例如 08:30") from exc
    return normalized


def _valid_timezone(value: str) -> str:
    normalized = (value or "Asia/Shanghai").strip()
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ReminderError("时区无效") from exc
    return normalized


def _valid_weekdays(value: Any) -> list[int]:
    if value in (None, ""):
        return [1, 2, 3, 4, 5, 6, 7]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReminderError("星期必须是 1-7 的数组") from exc
    try:
        weekdays = sorted({int(day) for day in value})
    except (TypeError, ValueError) as exc:
        raise ReminderError("星期必须是 1-7 的数组") from exc
    if not weekdays or any(day < 1 or day > 7 for day in weekdays):
        raise ReminderError("星期必须仅包含 1-7（周一到周日）")
    return weekdays


def _public_policy(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    try:
        data["weekdays"] = json.loads(data.pop("weekdays_json"))
    except (json.JSONDecodeError, TypeError):
        data["weekdays"] = [1, 2, 3, 4, 5, 6, 7]
        data.pop("weekdays_json", None)
    data["enabled"] = bool(data.get("enabled"))
    return data


def get_policy(user_id: int) -> dict[str, Any] | None:
    conn = get_conn()
    try:
        return _public_policy(
            conn.execute("SELECT * FROM reminder_policies WHERE user_id = ?", (int(user_id),)).fetchone()
        )
    finally:
        conn.close()


def set_policy(
    user_id: int,
    *,
    instance_id: str = "",
    timezone_name: str = "Asia/Shanghai",
    local_time: str = "08:00",
    weekdays: Any = None,
    quiet_start: str = "",
    quiet_end: str = "",
    enabled: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    timezone_name = _valid_timezone(timezone_name)
    local_time = _valid_hhmm(local_time, "提醒时间")
    normalized_weekdays = _valid_weekdays(weekdays)
    quiet_start = _valid_hhmm(quiet_start, "静默开始") if quiet_start else ""
    quiet_end = _valid_hhmm(quiet_end, "静默结束") if quiet_end else ""
    if bool(quiet_start) != bool(quiet_end):
        raise ReminderError("静默开始和结束必须同时设置")
    current_iso = _iso(now or _utc_now())
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO reminder_policies
               (user_id,instance_id,timezone,local_time,weekdays_json,quiet_start,quiet_end,
                enabled,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 instance_id=excluded.instance_id,
                 timezone=excluded.timezone,
                 local_time=excluded.local_time,
                 weekdays_json=excluded.weekdays_json,
                 quiet_start=excluded.quiet_start,
                 quiet_end=excluded.quiet_end,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (
                int(user_id), (instance_id or "").strip() or None, timezone_name, local_time,
                json.dumps(normalized_weekdays, separators=(",", ":")),
                quiet_start or None, quiet_end or None, int(bool(enabled)), current_iso, current_iso,
            ),
        )
        conn.commit()
        return get_policy(user_id) or {}
    finally:
        conn.close()


def _inside_quiet(local_hhmm: str, quiet_start: str | None, quiet_end: str | None) -> bool:
    if not quiet_start or not quiet_end or quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= local_hhmm < quiet_end
    return local_hhmm >= quiet_start or local_hhmm < quiet_end


def _payload_for(user_id: int, instance_id: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "LearnBuddy 学习提醒",
        "message": "今天也来推进一小步。回复我可以查看或调整你的学习安排。",
        "instance_id": instance_id,
    }
    if not instance_id:
        return payload
    try:
        from core.tools import call_tool

        plan = call_tool("recommend_today", user_id=int(user_id), args={"instance_id": instance_id})
        payload["plan"] = plan
        recommendation = plan.get("headline") or plan.get("reason") or plan.get("summary")
        if recommendation:
            payload["message"] = str(recommendation)[:500]
    except Exception as exc:  # noqa: BLE001 - 提醒不因计划计算失败而整体中断
        payload["plan_fallback"] = type(exc).__name__
    return payload


def queue_due_reminders(*, now: datetime | None = None) -> dict[str, int]:
    """扫描当前分钟到期的规则并幂等入队；本函数不调 LLM。"""
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    conn = get_conn()
    queued = 0
    scanned = 0
    try:
        rows = conn.execute(
            """SELECT p.*, b.id AS binding_id
               FROM reminder_policies p
               JOIN channel_bindings b
                 ON b.user_id = p.user_id AND b.provider = 'feishu' AND b.status = 'active'
               WHERE p.enabled = 1"""
        ).fetchall()
        for row in rows:
            scanned += 1
            local = current.astimezone(ZoneInfo(row["timezone"]))
            local_hhmm = local.strftime("%H:%M")
            try:
                weekdays = json.loads(row["weekdays_json"])
            except json.JSONDecodeError:
                weekdays = []
            reminder_hour, reminder_minute = (int(part) for part in row["local_time"].split(":"))
            scheduled_local = local.replace(
                hour=reminder_hour,
                minute=reminder_minute,
                second=0,
                microsecond=0,
            )
            lag = local - scheduled_local
            # 给 scheduler 抢占/短暂重启留 5 分钟容错；幂等键仍指向原定时间。
            if local.isoweekday() not in weekdays or lag < timedelta(0) or lag >= timedelta(minutes=5):
                continue
            if _inside_quiet(local_hhmm, row["quiet_start"], row["quiet_end"]):
                continue
            scheduled = scheduled_local.astimezone(timezone.utc)
            idem = f"reminder:{row['id']}:{row['binding_id']}:{scheduled.isoformat()}"
            payload = _payload_for(int(row["user_id"]), row["instance_id"])
            cursor = conn.execute(
                """INSERT OR IGNORE INTO notification_deliveries
                   (user_id,policy_id,binding_id,scheduled_for,idempotency_key,status,
                    payload_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,'pending',?,?,?)""",
                (
                    int(row["user_id"]), int(row["id"]), int(row["binding_id"]), _iso(scheduled), idem,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")), _iso(current), _iso(current),
                ),
            )
            queued += max(0, int(cursor.rowcount or 0))
        conn.commit()
        return {"scanned": scanned, "queued": queued}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_pending_deliveries(
    *,
    provider: str = "feishu",
    account_id: str = "default",
    limit: int = 20,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """为通道 worker 原子租约待发送项，防止并发 worker 同时取到。"""
    current = now or _utc_now()
    claim_token = secrets.token_urlsafe(18)
    claimed_until = _iso(current + timedelta(seconds=max(15, min(int(lease_seconds), 300))))
    safe_limit = max(1, min(int(limit), 100))
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT d.id
               FROM notification_deliveries d
               JOIN channel_bindings b ON b.id = d.binding_id
               WHERE d.status IN ('pending','failed')
                 AND d.attempts < 5
                 AND (d.claimed_until IS NULL OR d.claimed_until < ?)
                 AND b.provider = ? AND b.account_id = ? AND b.status = 'active'
               ORDER BY d.scheduled_for, d.id LIMIT ?""",
            (_iso(current), provider, account_id, safe_limit),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if not ids:
            conn.commit()
            return []
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""UPDATE notification_deliveries
                SET claim_token = ?, claimed_until = ?, attempts = attempts + 1, updated_at = ?
                WHERE id IN ({placeholders})""",
            (claim_token, claimed_until, _iso(current), *ids),
        )
        claimed = conn.execute(
            f"""SELECT d.*, b.external_user_id, b.account_id, b.provider
                FROM notification_deliveries d
                JOIN channel_bindings b ON b.id = d.binding_id
                WHERE d.id IN ({placeholders}) ORDER BY d.scheduled_for, d.id""",
            tuple(ids),
        ).fetchall()
        conn.commit()
        result = []
        for row in claimed:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except json.JSONDecodeError:
                item["payload"] = {}
                item.pop("payload_json", None)
            result.append(item)
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_delivery(
    delivery_id: int,
    *,
    claim_token: str,
    status: str,
    error: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_status = (status or "").strip().lower()
    if normalized_status not in {"sent", "failed", "skipped"}:
        raise ReminderError("发送结果必须是 sent / failed / skipped")
    current_iso = _iso(now or _utc_now())
    conn = get_conn()
    try:
        cursor = conn.execute(
            """UPDATE notification_deliveries
               SET status = ?, error = ?, sent_at = ?, claim_token = NULL,
                   claimed_until = NULL, updated_at = ?
               WHERE id = ? AND claim_token = ?""",
            (
                normalized_status, (error or "")[:1000] or None,
                current_iso if normalized_status == "sent" else None,
                current_iso, int(delivery_id), (claim_token or "").strip(),
            ),
        )
        conn.commit()
        if int(cursor.rowcount or 0) != 1:
            raise ReminderError("发送租约无效或已处理")
        row = conn.execute("SELECT * FROM notification_deliveries WHERE id = ?", (int(delivery_id),)).fetchone()
        return dict(row) if row else {"id": int(delivery_id), "status": normalized_status}
    finally:
        conn.close()


__all__ = [
    "ReminderError",
    "claim_pending_deliveries",
    "complete_delivery",
    "get_policy",
    "queue_due_reminders",
    "set_policy",
]
