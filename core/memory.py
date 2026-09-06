"""个人记忆系统（Batch L · 环 A 的"记忆"）。

让系统"越用越懂你"：把跨会话稳定的事实沉淀成可注入的记忆，喂给教练 / 推荐 / 讲解，
逐步提供更个性化的学习。

两类记忆：
- **派生记忆**（source=derived）：从学情信号自动提炼（弱项/强项/学习节奏），
  用 (user_id,instance_id,kind,mkey) 幂等 upsert，随学练刷新。
- **手写记忆**（source=user/feedback）：用户显式告诉系统的偏好/目标（"我喜欢例句多""怕语法术语"）。

设计：只依赖 db；derive 时**惰性** import learning（避免与 learning 顶层循环依赖）。
记忆是"信号的浓缩"，不是流水——刻意保持少而稳（每空间派生 ≤ ~6 条）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.db import get_conn

VALID_KINDS = {"struggle", "strength", "preference", "goal", "pace", "fact"}

_KIND_LABEL = {
    "struggle": "易错/薄弱",
    "strength": "已扎实",
    "preference": "学习偏好",
    "goal": "目标",
    "pace": "学习节奏",
    "fact": "其他",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_memory(
    *,
    user_id: int,
    content: str,
    kind: str = "fact",
    instance_id: Optional[str] = None,
    mkey: Optional[str] = None,
    weight: float = 1.0,
    source: str = "user",
) -> dict:
    """写入/更新一条记忆。

    - 派生记忆传 mkey（非空）→ 按 (user,instance,kind,mkey) 幂等 upsert（更新内容/权重/时间）。
    - 手写记忆不传 mkey → 每次新增一条。
    返回 {id, created} （created=True 表示新建）。
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    kind = (kind or "fact").strip()
    if kind not in VALID_KINDS:
        raise ValueError(f"非法记忆类型：{kind}")
    now = _now()
    conn = get_conn()
    try:
        if mkey:
            row = conn.execute(
                """SELECT id FROM user_memory
                   WHERE user_id=? AND IFNULL(instance_id,'')=IFNULL(?,'')
                     AND kind=? AND mkey=?""",
                (user_id, instance_id, kind, mkey),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE user_memory SET content=?, weight=?, source=?, updated_at=? WHERE id=?",
                    (content, weight, source, now, row["id"]),
                )
                conn.commit()
                return {"id": row["id"], "created": False}
        cur = conn.execute(
            """INSERT INTO user_memory
                 (user_id, instance_id, kind, mkey, content, weight, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, instance_id, kind, mkey, content, weight, source, now, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "created": True}
    finally:
        conn.close()


def recall(user_id: int, instance_id: Optional[str] = None, limit: int = 8) -> list[dict]:
    """取与当前情境相关的记忆：全局记忆（instance 为空）+ 该空间记忆，按权重/新鲜度排序。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, instance_id, kind, content, weight, source, updated_at
               FROM user_memory
               WHERE user_id=? AND (instance_id IS NULL OR instance_id=?)
               ORDER BY weight DESC, updated_at DESC
               LIMIT ?""",
            (user_id, instance_id, max(1, int(limit))),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_memory(user_id: int, memory_id: int) -> bool:
    """删除一条记忆（仅本人）。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM user_memory WHERE id=? AND user_id=?", (memory_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def format_for_prompt(mems: list[dict]) -> str:
    """把记忆压成 LLM 易读的紧凑文本块（供教练/讲解注入）。空→空串。"""
    mems = [m for m in (mems or []) if (m.get("content") or "").strip()]
    if not mems:
        return ""
    lines = []
    for m in mems:
        label = _KIND_LABEL.get(m.get("kind"), "其他")
        lines.append(f"- [{label}] {m['content'].strip()}")
    return "\n".join(lines)


# ----------------------------- 派生：从学情信号提炼记忆 -----------------------------

def refresh_derived(user_id: int, instance_id: str) -> dict:
    """从该空间的学情聚合提炼派生记忆（幂等 upsert）。

    规则（确定性，不调 LLM）：
    - struggle：弱项 topic（独立答对<60% 或自评 cant）→ 取前 3。
    - strength：已扎实 topic（独立答对≥85% 且自评 independent）→ 取前 2。
    - pace：根据近 30 天活跃天数给一句节奏画像。
    返回 {written, removed}。
    """
    from core.learning import analytics_summary  # 惰性，避免顶层循环依赖

    a = analytics_summary(user_id, instance_id)
    written = 0

    weak = a.get("weak_points") or []
    fresh_keys: set[str] = set()
    for w in weak[:3]:
        topic = (w.get("topic") or "").strip()
        if not topic:
            continue
        pct = w.get("objective_pct")
        desc = "还不会" if w.get("self_rating") == "cant" else (f"独立答对率仅 {pct}%" if pct is not None else "尚不稳")
        key = f"struggle:{topic}"
        fresh_keys.add(key)
        write_memory(
            user_id=user_id, instance_id=instance_id, kind="struggle", mkey=key,
            content=f"「{topic}」是薄弱点（{desc}），讲解时要更耐心、多举例、放慢。",
            weight=2.0, source="derived",
        )
        written += 1

    for t in (a.get("topics") or []):
        topic = (t.get("topic") or "").strip()
        if not topic:
            continue
        if t.get("objective_pct") is not None and t["objective_pct"] >= 85 and t.get("self_rating") == "independent":
            key = f"strength:{topic}"
            fresh_keys.add(key)
            write_memory(
                user_id=user_id, instance_id=instance_id, kind="strength", mkey=key,
                content=f"「{topic}」已扎实（独立答对 {t['objective_pct']}%），可往更难的延伸。",
                weight=1.2, source="derived",
            )
            written += 1
        if len([k for k in fresh_keys if k.startswith("strength:")]) >= 2:
            break

    trend = a.get("trend") or []
    active_days = len([d for d in trend if d.get("answered")])
    if a.get("topics_studied", 0) > 0:
        if active_days >= 12:
            pace = "学习很规律（近 30 天大部分日子都有练），节奏可适当加压。"
        elif active_days >= 4:
            pace = "学习时断时续（近 30 天约 %d 天有练），适合短小高频的安排。" % active_days
        else:
            pace = "最近练得很少，先用低门槛任务把习惯重新带起来。"
        key = "pace:overall"
        fresh_keys.add(key)
        write_memory(
            user_id=user_id, instance_id=instance_id, kind="pace", mkey=key,
            content=pace, weight=1.0, source="derived",
        )
        written += 1

    # 清理已不再成立的旧派生记忆（弱项已补强 / 强项已退化）
    removed = _prune_stale_derived(user_id, instance_id, fresh_keys)
    return {"written": written, "removed": removed}


def _prune_stale_derived(user_id: int, instance_id: str, fresh_keys: set[str]) -> int:
    """删掉本轮没再生成的派生记忆（信号已变化），保持记忆少而准。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, mkey FROM user_memory
               WHERE user_id=? AND instance_id=? AND source='derived' AND mkey IS NOT NULL""",
            (user_id, instance_id),
        ).fetchall()
        stale = [r["id"] for r in rows if r["mkey"] not in fresh_keys]
        for mid in stale:
            conn.execute("DELETE FROM user_memory WHERE id=?", (mid,))
        conn.commit()
        return len(stale)
    finally:
        conn.close()


__all__ = [
    "write_memory",
    "recall",
    "delete_memory",
    "format_for_prompt",
    "refresh_derived",
    "VALID_KINDS",
]
