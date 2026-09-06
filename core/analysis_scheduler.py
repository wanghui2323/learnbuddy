"""每日运营分析调度器。"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("itutor.analysis_scheduler")
_scheduler = None


def _hour() -> int:
    return max(0, min(23, int(os.getenv("ITUTOR_ANALYSIS_HOUR", "3") or 3)))


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if os.getenv("ITUTOR_ANALYSIS_ENABLED", "1") == "0":
        log.info("analysis scheduler disabled")
        return
    if os.getenv("ITUTOR_SCHEDULER_RUN", "1") != "1":
        log.info("analysis scheduler skipped in this process")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("apscheduler 未安装；每日分析调度未启用")
        return
    from core.admin_analysis import run_daily_analysis

    tz = os.getenv("ITUTOR_TIMEZONE", "Asia/Shanghai")
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(
        run_daily_analysis,
        trigger=CronTrigger(hour=_hour(), minute=15),
        id="daily_admin_analysis",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    sched.start()
    _scheduler = sched
    log.info("analysis scheduler started, daily at %02d:15 (%s)", _hour(), tz)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("analysis scheduler stopped")


__all__ = ["start_scheduler", "stop_scheduler"]
