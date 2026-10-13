"""定时任务：逾期重算、备份（每日 / 每周，频率可后台改）。

施工计划不在系统内跟踪，故不做「延期扫描」。
备份频率读 app_settings（services.settings）；改设置后调 reload_backup_jobs() 即时生效。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_settings
from .db import session_scope
from .services import backup as backup_svc
from .services import finance as finance_svc
from .services import settings as settings_svc

log = logging.getLogger("crm.scheduler")
_scheduler: BackgroundScheduler | None = None

_FALLBACK_BACKUP_CFG = {
    "daily_enabled": True,
    "daily_hour": 3,
    "weekly_enabled": True,
    "weekly_dow": 0,
    "weekly_hour": 4,
}


def refresh_overdue_job() -> None:
    with session_scope() as db:
        stats = finance_svc.refresh_all(db)
    log.info("逾期重算完成：%s", stats)


def backup_job() -> None:
    try:
        with session_scope() as db:
            result = backup_svc.run_backup_job(db)
        log.info("自动备份完成：%s", result)
    except Exception as exc:  # pragma: no cover
        log.error("自动备份失败：%s", exc)


def _configure_backup_jobs(sched: BackgroundScheduler, db=None) -> None:
    """按当前设置（或回退默认）注册 daily / weekly 备份 job。"""
    for jid in ("daily_backup", "weekly_backup"):
        try:
            sched.remove_job(jid)
        except Exception:
            pass
    cfg = settings_svc.get_backup_config(db) if db is not None else dict(_FALLBACK_BACKUP_CFG)
    if cfg.get("daily_enabled"):
        sched.add_job(
            backup_job,
            CronTrigger(hour=cfg["daily_hour"], minute=0),
            id="daily_backup",
            replace_existing=True,
        )
    if cfg.get("weekly_enabled"):
        sched.add_job(
            backup_job,
            CronTrigger(day_of_week=cfg["weekly_dow"], hour=cfg["weekly_hour"], minute=0),
            id="weekly_backup",
            replace_existing=True,
        )


def reload_backup_jobs() -> None:
    """备份设置改动后调用：重排 daily / weekly 备份任务。"""
    if _scheduler and _scheduler.running:
        with session_scope() as db:
            _configure_backup_jobs(_scheduler, db)
        log.info("备份任务已按新设置重排：%s", [j.id for j in _scheduler.get_jobs()])


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    s = get_settings()
    sched = BackgroundScheduler(timezone=s.tz, job_defaults={"coalesce": True, "max_instances": 1})
    sched.add_job(
        refresh_overdue_job, CronTrigger(hour=0, minute=10),
        id="overdue_refresh", replace_existing=True,
    )
    with session_scope() as db:
        _configure_backup_jobs(sched, db)
    sched.start()
    _scheduler = sched
    log.info("APScheduler 已启动：%s", [j.id for j in sched.get_jobs()])
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
