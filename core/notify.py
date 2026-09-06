"""推送 payload 构建（通道无关）。

把 core.recommend.daily_plan 的结果转成统一推送内容。通道（Web Push / 飞书 IM）
各自消费，换通道只换发送器，不改这里。

设计：纯函数、无 IO、好测。不调 LLM（plan 已是确定性结果）。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def build_payload(plan: dict, *, instance_id: str, base_url: str = "") -> dict:
    """plan → 推送 payload。

    返回 {title, body, url, tag, data}：
    - title：到期复习数 / 本周主题推进 / 默认招呼；
    - body：前 2-3 个 item 的 topic 拼，截断 ~80 字（Web Push 加密后 ≤4KB，余量充足）；
    - url：deep link → instance 页 today 视图；
    - tag：同 tag 新通知盖旧的，避免堆积；
    - data：给 service worker notificationclick 用（含 url + 首个 topic）。
    """
    counts = plan.get("counts") or {}
    due = counts.get("due", 0)
    theme = plan.get("theme") or "今天的内容"
    items = plan.get("items") or []

    if due > 0:
        title = f"今日聚焦 · {due} 个知识点到期复习"
    elif items:
        title = f"今日聚焦 · 推进「{_clip(theme, 14)}」"
    else:
        title = "今天来学一会儿吧"

    topics = [it.get("topic", "") for it in items[:3] if it.get("topic")]
    body = "；".join(topics) if topics else (plan.get("rationale") or "点击查看今天的学习安排")
    body = _clip(body, 80)

    url = f"{base_url}/instance/{instance_id}?view=today"
    first_topic = items[0].get("topic") if items else None

    return {
        "title": title,
        "body": body,
        "url": url,
        "tag": f"daily-{instance_id}",
        "data": {"instance_id": instance_id, "topic": first_topic, "url": url},
    }


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ============================================================================
# 6 关自检提醒（V0.27 联通 V0.23 6 关自检 + V0.26 Web Push）
# ============================================================================

SELFCHECK_REMIND_DAYS = 7   # 超过 7 天没做才提醒
SELFCHECK_FORCE_DAYS  = 14  # 超过 14 天强制提醒（streak 风险）


def should_remind_selfcheck(latest: dict | None, *, today: str | None = None) -> dict:
    """是否应推 6 关自检提醒。
    返回 {remind: bool, level: "first"|"weekly"|"force"|None, days_since: int|None}。
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    if not latest:
        return {"remind": True, "level": "first", "days_since": None}
    created = (latest.get("created_at") or "").strip()[:10]
    if not created or created in ("null", "None"):
        return {"remind": True, "level": "first", "days_since": None}
    try:
        d = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(created, "%Y-%m-%d")).days
    except ValueError:
        return {"remind": True, "level": "first", "days_since": None}
    if d >= SELFCHECK_FORCE_DAYS:
        return {"remind": True, "level": "force", "days_since": d}
    if d >= SELFCHECK_REMIND_DAYS:
        return {"remind": True, "level": "weekly", "days_since": d}
    return {"remind": False, "level": None, "days_since": d}


def build_selfcheck_reminder(latest: dict | None, *, instance_id: str, base_url: str = "") -> dict | None:
    """6 关自检提醒 payload。返回 None 表示不需要提醒。

    latest 形如 weekly_self_checks 表一行（latest_self_check() 返回）：
      {scores: [int]*6, passed_cnt, week, created_at, ...}
    """
    info = should_remind_selfcheck(latest)
    if not info["remind"]:
        return None
    level = info["level"]
    days = info["days_since"]
    passed = (latest or {}).get("passed_cnt", 0) if latest else 0
    if level == "first":
        title = "第一次 6 关自检 · 给你的学习打个基线"
        body = "6 个维度 3 分钟，看看你现在的强项和弱项"
    elif level == "force":
        title = f"6 关自检 {days} 天没做 · streak 告急"
        body = f"上次 {passed}/6 关，回归一下，1 分钟也够"
    else:  # weekly
        title = "本周 6 关自检 · 更新能力雷达图"
        body = f"上次 {passed}/6 关，花 3 分钟更新一下本周进度"

    url = f"{base_url}/self-check/{instance_id}"
    return {
        "title": title,
        "body": _clip(body, 80),
        "url": url,
        "tag": f"selfcheck-{instance_id}",
        "data": {
            "instance_id": instance_id,
            "url": url,
            "kind": "selfcheck",
            "level": level,
            "passed_cnt": passed,
        },
    }


__all__ = ["build_payload", "build_selfcheck_reminder", "should_remind_selfcheck"]
