"""每周 6 关自检（WeeklySelfCheck · 6 关自检交互页核心数据）。

设计（SPEC §1.1 + 02-技术方案 WeeklySelfCheck）：
- 用户每周日完成 1 次 6 关自检，每关独立 0-5 评分 + 笔记
- 通过数 ≥ 4 关 = 本周通过，下周可进新内容
- 通过数 < 4 关 = 触发 R1（reduce intensity 或 repeat week），由 /coach 现场给反馈
- 数据回流 mastery：自检高分（≥4）→ 提升该维度的自评分
- 通过数 = 0 表示完全没学 → 不计入连续学习 streak

表 weekly_self_checks：
- scores_json : [int]*6  与 learning_methods.self_check_dimensions 同序
- notes_json  : [str]*6
- passed_cnt  : scores >= 4 的个数
- r1_action   : AI 反馈时给的 R 规则动作（advance/repeat/reduce/null）
- ai_feedback : AI 现场反馈文本（来自 /coach）

不做：评分修改/删除历史（用户自检是状态快照，不允许追溯篡改）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.db import get_conn


# 与学习手册 R1 规则对齐：≤ 4 关 = 不进新内容
PASS_SCORE_THRESHOLD = 4
PASS_COUNT_MIN = 4


def _ensure_table() -> None:
    """首次调用时建表（幂等）。"""
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_self_checks (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id     INTEGER NOT NULL,
              instance_id TEXT    NOT NULL,
              week        INTEGER NOT NULL,
              scores_json TEXT    NOT NULL,
              notes_json  TEXT    NOT NULL DEFAULT '[]',
              passed_cnt  INTEGER NOT NULL,
              r1_action   TEXT,
              ai_feedback TEXT,
              created_at  TEXT    NOT NULL,
              UNIQUE (user_id, instance_id, week)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_self_check_inst ON weekly_self_checks (instance_id, week)"
        )
        conn.commit()
    finally:
        conn.close()


def _validate_scores(scores: list) -> list[int]:
    if not isinstance(scores, list) or len(scores) != 6:
        raise ValueError("scores 必须是长度 6 的数组")
    out = []
    for s in scores:
        try:
            v = int(s)
        except (TypeError, ValueError):
            raise ValueError(f"score 必须是 0-5 整数，收到 {s!r}")
        if v < 0 or v > 5:
            raise ValueError(f"score 必须在 [0,5]，收到 {v}")
        out.append(v)
    return out


def _validate_notes(notes: list, scores: list) -> list[str]:
    if notes is None:
        return ["" for _ in scores]
    if not isinstance(notes, list):
        raise ValueError("notes 必须是数组")
    # 补齐到 6 项
    out = [str(n or "").strip()[:500] for n in notes]
    while len(out) < 6:
        out.append("")
    return out[:6]


def submit_self_check(
    *,
    user_id: int,
    instance_id: str,
    week: int,
    scores: list,
    notes: Optional[list] = None,
    ai_feedback: str = "",
    r1_action: str = "",
) -> dict:
    """落一条 6 关自检。同 (user, instance, week) 唯一 → 二次提交覆盖。"""
    _ensure_table()
    try:
        w = int(week)
    except (TypeError, ValueError):
        raise ValueError("week 必须是正整数")
    if w < 1:
        raise ValueError("week 必须 ≥ 1")
    scores_ok = _validate_scores(scores)
    notes_ok = _validate_notes(notes, scores_ok)
    passed = sum(1 for s in scores_ok if s >= PASS_SCORE_THRESHOLD)
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO weekly_self_checks
              (user_id, instance_id, week, scores_json, notes_json,
               passed_cnt, r1_action, ai_feedback, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, instance_id, week) DO UPDATE SET
              scores_json=excluded.scores_json,
              notes_json=excluded.notes_json,
              passed_cnt=excluded.passed_cnt,
              r1_action=excluded.r1_action,
              ai_feedback=excluded.ai_feedback,
              created_at=excluded.created_at
            """,
            (
                user_id, instance_id, w,
                json.dumps(scores_ok, ensure_ascii=False),
                json.dumps(notes_ok, ensure_ascii=False),
                passed,
                (r1_action or "").strip()[:50],
                (ai_feedback or "").strip()[:4000],
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            """SELECT id, created_at FROM weekly_self_checks
               WHERE user_id=? AND instance_id=? AND week=?""",
            (user_id, instance_id, w),
        ).fetchone()
        return {
            "id": row["id"] if row else None,
            "week": w,
            "created_at": now,
            "passed_cnt": passed,
            "passed": passed >= PASS_COUNT_MIN,
            "threshold_score": PASS_SCORE_THRESHOLD,
            "min_pass_count": PASS_COUNT_MIN,
            "r1_rule": _r1_rule(passed),
        }
    finally:
        conn.close()


def latest_self_check(user_id: int, instance_id: str, inst_dir: Path | None = None) -> Optional[dict]:
    """最近一次 6 关自检（含维度名 + 通过标志）。"""
    _ensure_table()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM weekly_self_checks
               WHERE user_id=? AND instance_id=?
               ORDER BY week DESC, id DESC LIMIT 1""",
            (user_id, instance_id),
        ).fetchone()
        return _hydrate(row, _self_check_dimensions(user_id, instance_id, inst_dir)) if row else None
    finally:
        conn.close()


def self_check_history(
    user_id: int,
    instance_id: str,
    limit: int = 12,
    inst_dir: Path | None = None,
) -> list[dict]:
    """历史自检（按周倒序）。"""
    _ensure_table()
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM weekly_self_checks
               WHERE user_id=? AND instance_id=?
               ORDER BY week DESC, id DESC LIMIT ?""",
            (user_id, instance_id, max(1, int(limit))),
        ).fetchall()
        dims = _self_check_dimensions(user_id, instance_id, inst_dir)
        return [_hydrate(r, dims) for r in rows]
    finally:
        conn.close()


def _hydrate(row, dims: list[str]) -> dict:
    scores = json.loads(row["scores_json"]) if row["scores_json"] else []
    notes = json.loads(row["notes_json"]) if row["notes_json"] else []
    passed = int(row["passed_cnt"])
    items = []
    for i in range(6):
        items.append({
            "index": i,
            "name": dims[i] if i < len(dims) else f"维度{i+1}",
            "score": scores[i] if i < len(scores) else 0,
            "passed": (scores[i] if i < len(scores) else 0) >= PASS_SCORE_THRESHOLD,
            "note": notes[i] if i < len(notes) else "",
        })
    return {
        "id": row["id"],
        "week": row["week"],
        "scores": scores,
        "notes": notes,
        "passed_cnt": passed,
        "passed": passed >= PASS_COUNT_MIN,
        "r1_action": row["r1_action"] or "",
        "ai_feedback": row["ai_feedback"] or "",
        "items": items,
        "created_at": row["created_at"],
    }


def self_check_for_week(
    user_id: int,
    instance_id: str,
    week: int,
    inst_dir: Path | None = None,
) -> Optional[dict]:
    """精确读取某周自检，避免前端用 latest+1 猜测周次。"""
    _ensure_table()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM weekly_self_checks
               WHERE user_id=? AND instance_id=? AND week=? LIMIT 1""",
            (user_id, instance_id, int(week)),
        ).fetchone()
        return _hydrate(row, _self_check_dimensions(user_id, instance_id, inst_dir)) if row else None
    finally:
        conn.close()


def _self_check_dimensions(user_id: int, instance_id: str, inst_dir: Path | None = None) -> list[str]:
    """从实例的 master.json 读 learning_methods.self_check_dimensions；缺失兜底。"""
    import json as _json
    # 尝试多种定位
    candidates = [
        (inst_dir / "master.json") if inst_dir is not None else None,
        Path("data/instances") / instance_id / "master.json",
        Path("data") / "instances" / instance_id / "master.json",
    ]
    for p in candidates:
        if p is not None and p.exists():
            try:
                m = _json.loads(p.read_text(encoding="utf-8"))
                dims = (m.get("learning_methods") or {}).get("self_check_dimensions") or []
                if isinstance(dims, list) and len(dims) >= 6:
                    return [str(x) for x in dims[:6]]
            except (OSError, ValueError):
                pass
    return ["听", "说", "读", "写", "翻译", "教别人"]


def _r1_rule(passed_cnt: int) -> dict:
    """R1 规则：≤ 4 关 = 不进新内容（repeat 或 reduce intensity）。"""
    if passed_cnt >= PASS_COUNT_MIN:
        return {"code": "advance", "text": "本周通过，下周进新内容"}
    if passed_cnt == 3:
        return {"code": "reduce", "text": "本周差 1 关，下周减量（强度下调一档）"}
    if passed_cnt in (1, 2):
        return {"code": "repeat", "text": "本周未通过，下周重复 + 强化弱项"}
    return {"code": "reset", "text": "本周几乎未推进，建议回到上一周重学"}


__all__ = [
    "submit_self_check",
    "latest_self_check",
    "self_check_history",
    "self_check_for_week",
    "PASS_SCORE_THRESHOLD",
    "PASS_COUNT_MIN",
]
