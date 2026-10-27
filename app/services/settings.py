"""运行时设置：存库 key-value，带默认值、类型转换与备份配置读取。

设计原则：
- **只放用户可改的非框架配置**（备份路径 / 频率 / 保留份数），密钥等仍走环境变量；
- 读取时若表里没有该 key，返回硬编码默认值（不写库，避免污染）；
- 保存时 UPSERT 单条。

调用方一般直接 `get_backup_config(db)` 拿类型化后的 dict。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppSetting, now_iso

# ------------------------------------------------------------- 默认值
def _default_backup_dir() -> str:
    """默认备份目录跟随 `DATA_DIR`（容器内 = /data/backups）。

    以前这里写死 "/data/backups"：当 DATA_DIR 不是 /data（本地开发、换卷部署）时，
    备份页会因为往 /data 建目录失败而 500。改为跟随配置，写死值只作最后兜底。
    """
    try:
        from ..config import get_settings

        return str(get_settings().backup_dir)
    except Exception:  # pragma: no cover - 配置不可用时兜底
        return "/data/backups"


_STATIC_DEFAULTS: dict[str, str] = {
    # 备份保存目录（默认跟随 DATA_DIR；NAS 上 = /vol1/1000/Docker/Hvaccrm/data/backups）
    # 保留最近 N 份 zip
    "backup.keep": "30",
    # 每日自动备份
    "backup.daily.enabled": "1",
    "backup.daily.hour": "3",     # 0-23
    # 每周自动备份
    "backup.weekly.enabled": "1",
    "backup.weekly.dow": "0",     # 0=周一 … 6=周日（APScheduler day_of_week）
    "backup.weekly.hour": "4",    # 0-23
}


def defaults() -> dict[str, str]:
    """静态默认值 + 跟随运行环境的 backup.dir。"""
    d = dict(_STATIC_DEFAULTS)
    d["backup.dir"] = _default_backup_dir()
    return d


def get(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(AppSetting, key)
    if row is not None and row.value is not None:
        return row.value
    if default is not None:
        return default
    return defaults().get(key)


def set_(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=str(value), updated_at=now_iso()))
    else:
        row.value = str(value)
        row.updated_at = now_iso()


def all_settings(db: Session) -> dict[str, str]:
    stored = {r.key: r.value for r in db.scalars(select(AppSetting)).all()}
    merged = {**defaults(), **{k: v for k, v in stored.items() if v is not None}}
    return merged


def save_backup_config(db: Session, cfg: dict[str, str]) -> None:
    for key, value in cfg.items():
        if key.startswith("backup."):
            set_(db, key, value)


# ------------------------------------------------------------- 类型化读取
def get_backup_config(db: Session) -> dict:
    raw = all_settings(db)

    def _int(key: str, lo: int, hi: int, fallback: int) -> int:
        try:
            v = int(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(lo, min(hi, v))

    return {
        "dir": (raw.get("backup.dir") or _default_backup_dir()).strip(),
        "keep": _int("backup.keep", 1, 999, 30),
        "daily_enabled": raw.get("backup.daily.enabled", "1") == "1",
        "daily_hour": _int("backup.daily.hour", 0, 23, 3),
        "weekly_enabled": raw.get("backup.weekly.enabled", "1") == "1",
        "weekly_dow": _int("backup.weekly.dow", 0, 6, 0),
        "weekly_hour": _int("backup.weekly.hour", 0, 23, 4),
    }
