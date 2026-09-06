"""Web Push 订阅持久化 + 最近活跃 instance 选取 + 当天去重。

复用 core.db（SQLite 现开现关）。push_subs 表一个用户多设备；
push_send_log 做"一用户一天一条"去重（防多 worker 重复 / 同日重发）。

最近活跃 instance 查 events 表（用户×实例×时间的事实表）——比 _index.json 更准：
_index.json 的实例条目没有 user_id 字段，无法归属到具体用户。
"""

from __future__ import annotations

from datetime import date, datetime

from core.db import get_conn


class SubscriptionOwnershipError(ValueError):
    """endpoint 已属于其他用户，禁止跨账户重绑。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_subscription(
    *,
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
    instance_id: str | None = None,
    expiration_time: int | None = None,
) -> int:
    """订阅入库。同一 owner + endpoint 幂等 upsert，返回 sub_id。

    endpoint 是 Push Service 给设备生成的全局唯一凭据，不能因为另一个
    已登录用户提交相同字符串就转移归属。用 BEGIN IMMEDIATE 串行化
    查询与 upsert，避免并发请求绕过 owner 检查。
    """
    now = _now()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id, user_id FROM push_subs WHERE endpoint = ?", (endpoint,)
        ).fetchone()
        if existing:
            if int(existing["user_id"]) != int(user_id):
                raise SubscriptionOwnershipError("该推送订阅已绑定其他账户")
            conn.execute(
                """UPDATE push_subs
                   SET instance_id=?, p256dh=?, auth=?, expiration_time=?, created_at=?
                   WHERE endpoint=? AND user_id=?""",
                (instance_id, p256dh, auth, expiration_time, now, endpoint, user_id),
            )
            sub_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO push_subs
                     (user_id, instance_id, endpoint, p256dh, auth, expiration_time, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (user_id, instance_id, endpoint, p256dh, auth, expiration_time, now),
            )
            sub_id = cur.lastrowid
        conn.commit()
        return sub_id
    finally:
        conn.close()


def list_subscriptions(user_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM push_subs WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_by_endpoint(endpoint: str, *, user_id: int) -> int:
    """仅删除当前 owner 的 endpoint；跨用户请求返回 0。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?",
            (endpoint, user_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _delete_stale_subscription(
    *, subscription_id: int, user_id: int, endpoint: str
) -> int:
    """410/404 发送回执的内部清理通道。

    调用方必须传入 ``list_subscriptions(user_id)`` 返回的完整行标识。
    同时约束 id + owner + endpoint，防止发送中订阅被删除、endpoint 又被
    新用户创建后，旧请求的失效回执误删新订阅。
    """
    conn = get_conn()
    try:
        cur = conn.execute(
            """DELETE FROM push_subs
               WHERE id = ? AND user_id = ? AND endpoint = ?""",
            (subscription_id, user_id, endpoint),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def subscriber_user_ids() -> list[int]:
    """所有至少有一条订阅的用户（去重）。scheduler 遍历用。"""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT DISTINCT user_id FROM push_subs").fetchall()
        return [r["user_id"] for r in rows]
    finally:
        conn.close()


def last_active_instance(user_id: int) -> str | None:
    """该用户最近活跃的学习路径 id（查 events 表，按 created_at 倒序）。无则 None。"""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT instance_id FROM events
               WHERE user_id = ? AND instance_id IS NOT NULL AND instance_id != ''
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return row["instance_id"] if row else None
    finally:
        conn.close()


def already_sent_today(user_id: int, day: str | None = None, kind: str = "daily") -> bool:
    """当日是否已发指定 kind。day 默认今天；kind 默认 daily。

    V0.27 扩展：用 (user_id, day) UNIQUE INDEX 不变，把不同 kind 用 `day+kind` 复合 key
    落到同一索引里，避免改 schema。这样 daily + selfcheck 同日可分别发。
    """
    base_day = day or date.today().isoformat()
    composite = f"{base_day}:{kind}"
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM push_send_log WHERE user_id = ? AND day = ?", (user_id, composite)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_sent(
    *,
    user_id: int,
    instance_id: str | None,
    day: str | None = None,
    sent_count: int = 0,
    skipped: str | None = None,
    kind: str = "daily",
) -> None:
    """记录当天已发指定 kind（幂等：UNIQUE(user_id,day) → INSERT OR REPLACE）。
    kind: "daily" (默认) / "selfcheck" / "test" — 用 day 字段的复合 key 区分。
    """
    base_day = day or date.today().isoformat()
    composite = f"{base_day}:{kind}"
    now = _now()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO push_send_log
                 (user_id, instance_id, day, sent_count, skipped, created_at)
               VALUES (?,?,?,?,?,?)""",
            (user_id, instance_id, composite, sent_count, skipped, now),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "SubscriptionOwnershipError",
    "add_subscription",
    "list_subscriptions",
    "delete_by_endpoint",
    "subscriber_user_ids",
    "last_active_instance",
    "already_sent_today",
    "mark_sent",
]
