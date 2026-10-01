"""定时任务：逾期重算、每日备份（低频，避免阻止硬盘休眠）。

施工计划不在系统内跟踪，故不再做「延期扫描」。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .db import session_scope
from .services import backup as backup_svc
from .services import finance as finance_svc
from .services import tasks as task_svc

log = logging.getLogger("crm.scheduler")
_scheduler: BackgroundScheduler | None = None


def refresh_overdue_job() -> None:
    with session_scope() as db:
        stats = finance_svc.refresh_all(db)
    log.info("逾期重算完成：%s", stats)


def backup_job() -> None:
    try:
        log.info("每日备份：%s", backup_svc.run_backup_job())
    except Exception as exc:  # pragma: no cover
        log.error("每日备份失败：%s", exc)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    s = get_settings()
    sched = BackgroundScheduler(timezone=s.tz, job_defaults={"coalesce": True, "max_instances": 1})
    sched.add_job(refresh_overdue_job, CronTrigger(hour=0, minute=10), id="overdue_refresh", replace_existing=True)
    sched.add_job(backup_job, CronTrigger(hour=3, minute=0), id="daily_backup", replace_existing=True)
    sched.start()
    _scheduler = sched
    log.info("APScheduler 已启动：%s", [j.id for j in sched.get_jobs()])
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
