"""动态重规划（Batch L · 环 A 的"重规划"，ADR-011 收尾）。

监测学习状态，按**三触发器**给出路径调整建议——这是闭合用户学习闭环的最后一块
（决策的另一半：不只是"今天学什么"，还有"计划是不是该改了"）。

三触发器（**确定性规则**，不调 LLM；能用规则别用模型）：
1. **stuck 连续卡壳**：同一知识点学过 ≥2 次仍 cant / 客观答对率 <40% → 降速 + 补前置。
2. **ahead 超额达标**：学得又快又稳（多数独立且高分、无弱项无到期）→ 加压 / 跳级。
3. **idle 长期中断**：最近一次学练距今 ≥7 天 → 低门槛重启。

建议是"有判断力的一句话 + 可执行 action"（action 带 topic，前端可直接点开那一节）。
检测只读既有学情 + 已落盘的知识图谱（取前置），不触发昂贵生成。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from core import learning
from core.db import get_conn

IDLE_DAYS = 7          # 距上次学练多少天算"中断"
IDLE_DAYS_HARD = 14    # 超过则升级为高优先
STUCK_MIN_TIMES = 2    # 学过几次仍不会才算"卡壳"
STUCK_PCT = 40         # 客观答对率低于此算卡壳
AHEAD_MIN_TOPICS = 3   # 至少学过几个知识点才评估"超额"
AHEAD_PCT = 85         # 独立答对率达标线


def _to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return None


def _prereq_names(topic: str, inst_dir: Path) -> list[str]:
    """从已落盘的 concepts.json 取某知识点的前置名（无图谱/未命中→空）。"""
    cache = inst_dir / "concepts.json"
    if not cache.exists():
        return []
    try:
        import json
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    concepts = data.get("concepts") or []
    by_id = {c.get("id"): c for c in concepts}
    target = topic.strip()
    hit = next((c for c in concepts if (c.get("name") or "").strip() == target), None)
    if not hit:
        return []
    return [by_id[p]["name"] for p in (hit.get("prerequisites") or []) if p in by_id]


def detect(*, user_id: int, instance_id: str, inst_dir: Path, master: dict) -> dict:
    """检测三触发器，返回 {triggers:[...], checked_at, has_history}。

    trigger: {kind, severity(low/mid/high|good), title, message, actions:[{type, topic?}]}
    """
    a = learning.analytics_summary(user_id, instance_id)
    topics = a.get("topics") or []
    weak = a.get("weak_points") or []
    review = a.get("review_queue") or []
    has_history = a.get("topics_studied", 0) > 0
    today = date.today()
    triggers: list[dict] = []

    if not has_history:
        return {"triggers": [], "checked_at": today.isoformat(), "has_history": False}

    # --- idle：最近一次学练距今多久 ---
    last_dates = [d for d in (_to_date(t.get("last_studied")) for t in topics) if d]
    last = max(last_dates) if last_dates else None
    if last:
        days_idle = (today - last).days
        if days_idle >= IDLE_DAYS:
            sev = "high" if days_idle >= IDLE_DAYS_HARD else "mid"
            resume = review[0]["topic"] if review else (topics[0].get("topic") if topics else "")
            actions = [{"type": "restart_light"}]
            if resume:
                actions.append({"type": "review", "topic": resume})
            triggers.append({
                "kind": "idle",
                "severity": sev,
                "title": "你已经停了一阵子",
                "message": f"距上次学练已 {days_idle} 天。别想着补回全部——先用一节最熟的内容把状态找回来，比硬啃新课更重要。",
                "actions": actions,
            })

    # --- stuck：反复学仍不会 ---
    stuck = [
        t for t in topics
        if t.get("times_studied", 0) >= STUCK_MIN_TIMES
        and (t.get("self_rating") == "cant"
             or (t.get("objective_pct") is not None and t["objective_pct"] < STUCK_PCT))
    ]
    if stuck:
        first = stuck[0]
        topic = first.get("topic", "")
        prereqs = _prereq_names(topic, inst_dir)
        actions = [{"type": "slow_down", "topic": topic}]
        for p in prereqs[:2]:
            actions.append({"type": "review_prereq", "topic": p})
        if prereqs:
            msg = f"「{topic}」你练了 {first.get('times_studied')} 次还卡着——问题大概率不在它本身，而在前置「{prereqs[0]}」没真正打牢。先回去补前置，再回来它会顺很多。"
        else:
            msg = f"「{topic}」你练了 {first.get('times_studied')} 次还卡着。别硬刚——把它拆小、放慢，或换个角度（多看例子、少记规则）。"
        triggers.append({
            "kind": "stuck",
            "severity": "high" if len(stuck) >= 2 else "mid",
            "title": "有个知识点把你卡住了",
            "message": msg,
            "actions": actions,
        })

    # --- ahead：又快又稳，可以加压 ---
    if len(topics) >= AHEAD_MIN_TOPICS and not weak and not review:
        strong = [
            t for t in topics
            if t.get("self_rating") == "independent"
            and (t.get("objective_pct") or 0) >= AHEAD_PCT
        ]
        if len(strong) >= max(AHEAD_MIN_TOPICS, int(len(topics) * 0.8)):
            triggers.append({
                "kind": "ahead",
                "severity": "good",
                "title": "你明显学有余力",
                "message": "你最近这些都掌握得又快又稳，没有弱项也没有到期复习。可以加点难度或往前跳一跳，别让节奏拖住你。",
                "actions": [{"type": "level_up"}],
            })

    return {"triggers": triggers, "checked_at": today.isoformat(), "has_history": True}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_master(master: dict) -> str:
    raw = json.dumps(master, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_write_master(inst_dir: Path, master: dict) -> None:
    target = inst_dir / "master.json"
    temp = inst_dir / f".master.{secrets.token_hex(6)}.tmp"
    try:
        temp.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def _proposal_id() -> str:
    return "rp_" + secrets.token_hex(10)


def _pick_trigger(triggers: list[dict]) -> dict | None:
    rank = {"high": 4, "mid": 3, "good": 2, "low": 1}
    return max(triggers, key=lambda item: rank.get(str(item.get("severity")), 0), default=None)


def _proposal_from_trigger(trigger: dict) -> dict:
    kind = str(trigger.get("kind") or "")
    actions = trigger.get("actions") or []
    focus_topic = next((str(a.get("topic") or "") for a in actions if a.get("topic")), "")
    if kind == "stuck":
        summary = f"下一周先给「{focus_topic or '当前卡点'}」补前置、降低步长，再继续新内容。"
        outcome = f"把「{focus_topic or '当前卡点'}」拆成更小练习并完成一次补强验收"
        mode = "remediate"
    elif kind == "idle":
        summary = "不追补全部落后内容，先安排一次低门槛重启，恢复节奏后再推进。"
        outcome = "完成一次 30 分钟低门槛重启并写下下一个最小行动"
        mode = "restart_light"
    else:
        summary = "当前节奏明显有余力，在不跳过验收的前提下加一个高阶迁移任务。"
        outcome = "完成一个高阶迁移任务并记录适用边界"
        mode = "stretch"
    return {
        "kind": kind,
        "severity": trigger.get("severity"),
        "title": trigger.get("title") or "学习路径调整建议",
        "summary": summary,
        "mode": mode,
        "focus_topic": focus_topic,
        "next_week_outcome": outcome,
        "source_trigger": trigger,
        "changes": [
            {"field": "master._replan.active_adjustment", "description": summary},
            {"field": "next_week.key_outcomes", "description": outcome},
        ],
    }


def _row_payload(row) -> dict:
    proposal = json.loads(row["proposal_json"])
    return {
        "id": row["id"],
        "instance_id": row["instance_id"],
        "trigger_kind": row["trigger_kind"],
        "status": row["status"],
        "proposal": proposal,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "applied_at": row["applied_at"],
        "decided_at": row["decided_at"],
        "can_confirm": row["status"] == "proposed",
        "can_reject": row["status"] == "proposed",
        "can_undo": row["status"] == "applied",
    }


def current_proposal(user_id: int, instance_id: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT * FROM replan_proposals
               WHERE user_id=? AND instance_id=?
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, instance_id),
        ).fetchone()
        return _row_payload(row) if row is not None else None
    finally:
        conn.close()


def create_proposal(*, user_id: int, instance_id: str, inst_dir: Path, master: dict) -> dict:
    """显式创建提案；只读检测本身永远不修改 master。"""
    existing = current_proposal(user_id, instance_id)
    if existing and existing["status"] == "proposed":
        return existing
    result = detect(user_id=user_id, instance_id=instance_id, inst_dir=inst_dir, master=master)
    trigger = _pick_trigger(result.get("triggers") or [])
    if trigger is None:
        return {"status": "none", "proposal": None, "detection": result}
    proposal = _proposal_from_trigger(trigger)
    proposal_id = _proposal_id()
    ts = _now()
    snapshot = json.dumps(master, ensure_ascii=False)
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO replan_proposals
               (id,user_id,instance_id,trigger_kind,status,proposal_json,before_snapshot,before_hash,created_at,updated_at)
               VALUES (?,?,?,?,'proposed',?,?,?,?,?)""",
            (
                proposal_id,
                user_id,
                instance_id,
                proposal["kind"],
                json.dumps(proposal, ensure_ascii=False),
                snapshot,
                _hash_master(master),
                ts,
                ts,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM replan_proposals WHERE id=?", (proposal_id,)).fetchone()
        return _row_payload(row)
    finally:
        conn.close()


def _apply_to_master(master: dict, proposal: dict, proposal_id: str) -> dict:
    updated = deepcopy(master)
    replan = updated.get("_replan") if isinstance(updated.get("_replan"), dict) else {}
    history = replan.get("history") if isinstance(replan.get("history"), list) else []
    adjustment = {
        "proposal_id": proposal_id,
        "mode": proposal.get("mode"),
        "focus_topic": proposal.get("focus_topic"),
        "summary": proposal.get("summary"),
        "applied_at": _now(),
    }
    replan["active_adjustment"] = adjustment
    replan["history"] = [*history[-19:], adjustment]
    updated["_replan"] = replan

    weeks = updated.get("weeks") if isinstance(updated.get("weeks"), list) else []
    target = next((week for week in weeks if str(week.get("status")) in {"active", "pending"}), None)
    if target is not None:
        outcomes = [str(item) for item in (target.get("key_outcomes") or []) if str(item).strip()]
        addition = str(proposal.get("next_week_outcome") or "").strip()
        if addition and addition not in outcomes:
            target["key_outcomes"] = [addition, *outcomes][:4]
    updated["updated_at"] = _now()
    return updated


def decide_proposal(
    *,
    user_id: int,
    instance_id: str,
    proposal_id: str,
    action: str,
    inst_dir: Path,
) -> dict:
    action = str(action or "").strip().lower()
    if action not in {"confirm", "reject", "undo"}:
        raise ValueError("action 必须是 confirm / reject / undo。")
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM replan_proposals WHERE id=? AND user_id=? AND instance_id=?",
            (proposal_id, user_id, instance_id),
        ).fetchone()
        if row is None:
            raise ValueError("重规划提案不存在或不属于当前用户。")
        status = row["status"]
        now = _now()
        if action == "reject":
            if status != "proposed":
                raise ValueError("只有待确认提案可以拒绝。")
            conn.execute(
                "UPDATE replan_proposals SET status='rejected',updated_at=?,decided_at=? WHERE id=?",
                (now, now, proposal_id),
            )
        elif action == "confirm":
            if status != "proposed":
                raise ValueError("只有待确认提案可以应用。")
            current = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
            if _hash_master(current) != row["before_hash"]:
                raise ValueError("master 在提案后已发生变化，请重新检测后再确认。")
            proposal = json.loads(row["proposal_json"])
            updated = _apply_to_master(current, proposal, proposal_id)
            _atomic_write_master(inst_dir, updated)
            conn.execute(
                """UPDATE replan_proposals
                   SET status='applied',after_snapshot=?,after_hash=?,updated_at=?,applied_at=?,decided_at=?
                   WHERE id=?""",
                (
                    json.dumps(updated, ensure_ascii=False),
                    _hash_master(updated),
                    now,
                    now,
                    now,
                    proposal_id,
                ),
            )
        else:
            if status != "applied":
                raise ValueError("只有已应用且未撤销的提案可以撤销。")
            current = json.loads((inst_dir / "master.json").read_text(encoding="utf-8"))
            if _hash_master(current) != row["after_hash"]:
                raise ValueError("master 在应用后又被修改，为避免覆盖新变更，本次不能自动撤销。")
            before = json.loads(row["before_snapshot"])
            _atomic_write_master(inst_dir, before)
            conn.execute(
                "UPDATE replan_proposals SET status='undone',updated_at=?,decided_at=? WHERE id=?",
                (now, now, proposal_id),
            )
        conn.commit()
        updated_row = conn.execute("SELECT * FROM replan_proposals WHERE id=?", (proposal_id,)).fetchone()
        return _row_payload(updated_row)
    finally:
        conn.close()


__all__ = [
    "create_proposal",
    "current_proposal",
    "decide_proposal",
    "detect",
    "IDLE_DAYS",
]
