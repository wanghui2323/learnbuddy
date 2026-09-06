"""APScheduler 后台调度器（Web Push + 飞书 Agent 提醒公共时钟）。

每天定时（默认本地 8 点，env ITUTOR_PUSH_HOUR）对每个有订阅的用户算一次
今日推荐 → 构建推送 payload → 发送。确定性、不调 LLM。

集成：server.py startup 调 start_scheduler()，shutdown 调 stop_scheduler()。
多 worker：env ITUTOR_SCHEDULER_RUN=0 时本进程不起（生产 web worker 设 0，
用独立进程跑 scheduler，避免重复执行）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date

log = logging.getLogger("itutor.push_scheduler")

_scheduler = None  # 模块级单例，防重复 start


def _hour() -> int:
    try:
        h = int(os.getenv("ITUTOR_PUSH_HOUR", "8"))
    except ValueError:
        h = 8
    return max(0, min(23, h))


async def daily_push_all() -> None:
    """遍历有订阅的用户：算 plan → payload → 发 → 再发 6 关自检提醒（如果到期）。
    一用户一天可能两条 (daily + selfcheck)，用 kind 字段去重区分。
    """
    from core.generator import DEFAULT_INSTANCES_DIR
    from core.notify import build_payload, build_selfcheck_reminder
    from core.push_subs import (
        already_sent_today,
        last_active_instance,
        mark_sent,
        subscriber_user_ids,
    )
    from core.push_web import send_to_user
    from core.recommend import daily_plan

    base_url = os.getenv("ITUTOR_BASE_URL", "")
    today = date.today().isoformat()

    for uid in subscriber_user_ids():
        iid = last_active_instance(uid)
        if not iid:
            mark_sent(user_id=uid, instance_id=None, day=today, skipped="no_instance")
            log.info("push skip user=%s: no active instance", uid)
            continue
        inst_dir = DEFAULT_INSTANCES_DIR / iid

        # ---- 1) daily plan 推送 ----
        if not already_sent_today(uid, today, kind="daily"):
            master = {}
            mp = inst_dir / "master.json"
            if mp.exists():
                try:
                    master = json.loads(mp.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    master = {}
            try:
                plan = await asyncio.to_thread(
                    daily_plan, user_id=uid, instance_id=iid, inst_dir=inst_dir, master=master
                )
            except Exception as e:  # noqa: BLE001
                log.warning("plan failed user=%s: %s", uid, e)
                mark_sent(user_id=uid, instance_id=iid, day=today, skipped="plan_error", kind="daily")
            else:
                payload = build_payload(plan, instance_id=iid, base_url=base_url)
                try:
                    result = await send_to_user(uid, payload)
                except Exception as e:  # noqa: BLE001
                    log.warning("daily send failed user=%s: %s", uid, e)
                    mark_sent(user_id=uid, instance_id=iid, day=today, skipped="send_error", kind="daily")
                else:
                    if result.get("skipped"):
                        mark_sent(user_id=uid, instance_id=iid, day=today, skipped=result["skipped"], kind="daily")
                    else:
                        mark_sent(user_id=uid, instance_id=iid, day=today, sent_count=result.get("sent", 0), kind="daily")
                    log.info("push daily user=%s instance=%s result=%s", uid, iid, result)

        # ---- 2) 6 关自检提醒推送（V0.27 联通）----
        if not already_sent_today(uid, today, kind="selfcheck"):
            try:
                from core.self_check import latest_self_check
                latest = await asyncio.to_thread(latest_self_check, uid, iid)
                sc_payload = build_selfcheck_reminder(latest, instance_id=iid, base_url=base_url)
            except Exception as e:  # noqa: BLE001
                log.warning("selfcheck reminder build failed user=%s: %s", uid, e)
                sc_payload = None
            if sc_payload is None:
                # 不需要提醒 → 标记 skipped（避免日重算）
                mark_sent(user_id=uid, instance_id=iid, day=today, skipped="not_due", kind="selfcheck")
            else:
                try:
                    result = await send_to_user(uid, sc_payload)
                except Exception as e:  # noqa: BLE001
                    log.warning("selfcheck send failed user=%s: %s", uid, e)
                    mark_sent(user_id=uid, instance_id=iid, day=today, skipped="send_error", kind="selfcheck")
                else:
                    if result.get("skipped"):
                        mark_sent(user_id=uid, instance_id=iid, day=today, skipped=result["skipped"], kind="selfcheck")
                    else:
                        mark_sent(user_id=uid, instance_id=iid, day=today, sent_count=result.get("sent", 0), kind="selfcheck")
                    log.info("push selfcheck user=%s instance=%s result=%s", uid, iid, result)


async def queue_agent_reminders() -> None:
    """每分钟把到期规则幂等入队，已配飞书凭证时继续发送。"""
    from core.runtime import runtime_edition

    if runtime_edition() != "personal":
        log.info("agent reminders skipped: only available in personal edition")
        return
    from core.feishu_sender import send_pending_reminders
    from core.reminders import queue_due_reminders

    result = await asyncio.to_thread(queue_due_reminders)
    if result.get("queued"):
        log.info("agent reminders queued=%s scanned=%s", result["queued"], result["scanned"])
    send_result = await send_pending_reminders()
    if send_result.get("sent") or send_result.get("failed"):
        log.info("agent reminders delivery=%s", send_result)


def start_scheduler() -> None:
    """启动调度器（幂等）。两类推送均关闭或非 scheduler 进程时不启。"""
    global _scheduler
    if _scheduler is not None:
        return
    push_enabled = os.getenv("ITUTOR_PUSH_ENABLED", "1") != "0"
    from core.runtime import runtime_edition

    agent_enabled = (
        runtime_edition() == "personal"
        and os.getenv("ITUTOR_AGENT_REMINDERS_ENABLED", "1") != "0"
    )
    if not push_enabled and not agent_enabled:
        log.info("scheduler disabled: web push and agent reminders are both off")
        return
    if os.getenv("ITUTOR_SCHEDULER_RUN", "1") != "1":
        log.info("push scheduler skipped in this process (ITUTOR_SCHEDULER_RUN!=1)")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:  # noqa: BLE001
        log.warning("apscheduler 未安装；运行 pip install 'apscheduler>=3.10,<4'；后台推送未启用")
        return

    tz = os.getenv("ITUTOR_TZ", "Asia/Shanghai")
    sched = AsyncIOScheduler(timezone=tz)
    if push_enabled:
        sched.add_job(
            daily_push_all,
            CronTrigger(hour=_hour(), minute=0),
            id="daily_push",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
            replace_existing=True,
        )
    if agent_enabled:
        sched.add_job(
            queue_agent_reminders,
            IntervalTrigger(minutes=1),
            id="agent_reminder_queue",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60,
            replace_existing=True,
        )
    sched.start()
    _scheduler = sched
    log.info("push scheduler started, daily at %02d:00 (%s)", _hour(), tz)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("push scheduler stopped")


__all__ = ["daily_push_all", "queue_agent_reminders", "start_scheduler", "stop_scheduler"]
