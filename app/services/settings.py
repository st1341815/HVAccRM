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
    # ---- 短信验证（外网登录双因素）----
    "sms.enabled": "0",           # 总开关：关 = 功能完全回到现状（内网外网都不要求验证码）
    "sms.provider": "console",    # console(调试模式) | zhenzi | tencent | aliyun
    "sms.external_domains": "crm.example.com",  # Host 兜底信号：这些域名视为外网（支持 *.xx 通配）
    "sms.code_ttl_seconds": "300",         # 验证码有效期
    "sms.send_cooldown": "60",             # 同一账号两条验证码最小间隔
    "sms.daily_limit_per_user": "20",      # 单账号日发送上限
    "sms.daily_limit_per_ip": "30",        # 单 IP 日发送上限
    "sms.daily_cap_total": "100",          # 全局日发送保险丝（防异常烧钱）
    # 服务商参数（非密钥部分；密钥类键 zhenzi_secret/tencent_skey/aliyun_sk 不设默认，且不入备份）
    "sms.zhenzi_appid": "",
    "sms.zhenzi_tpl": "",
    "sms.tencent_sid": "",
    "sms.tencent_appid": "",
    "sms.tencent_sign": "",
    "sms.tencent_tpl": "",
    "sms.aliyun_ak": "",
    "sms.aliyun_sign": "",
    "sms.aliyun_tpl": "",
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


def get_sms_config(db: Session) -> dict:
    """短信验证配置（类型化）。密钥类键原文返回，仅供发送路径使用；
    展示层一律用 sms.mask_secret 脱敏。"""
    raw = all_settings(db)

    def _int(key: str, lo: int, hi: int, fallback: int) -> int:
        try:
            v = int(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
        return max(lo, min(hi, v))

    def _str(key: str) -> str:
        return (raw.get(key) or "").strip()

    domains = [d for d in _str("sms.external_domains").replace(";", ",").split(",") if d.strip()]
    provider = _str("sms.provider") or "console"
    if provider not in ("console", "zhenzi", "tencent", "aliyun"):
        provider = "console"
    return {
        "enabled": raw.get("sms.enabled", "0") == "1",
        "provider": provider,
        "external_domains": domains,
        "code_ttl_seconds": _int("sms.code_ttl_seconds", 60, 3600, 300),
        "send_cooldown": _int("sms.send_cooldown", 30, 3600, 60),
        "daily_limit_per_user": _int("sms.daily_limit_per_user", 1, 1000, 20),
        "daily_limit_per_ip": _int("sms.daily_limit_per_ip", 1, 1000, 30),
        "daily_cap_total": _int("sms.daily_cap_total", 1, 10000, 100),
        # 服务商参数（密钥单独放一个子 dict，方便整体脱敏）
        "creds": {
            "zhenzi_appid": _str("sms.zhenzi_appid"),
            "zhenzi_secret": _str("sms.zhenzi_secret"),
            "zhenzi_tpl": _str("sms.zhenzi_tpl"),
            "tencent_sid": _str("sms.tencent_sid"),
            "tencent_skey": _str("sms.tencent_skey"),
            "tencent_appid": _str("sms.tencent_appid"),
            "tencent_sign": _str("sms.tencent_sign"),
            "tencent_tpl": _str("sms.tencent_tpl"),
            "aliyun_ak": _str("sms.aliyun_ak"),
            "aliyun_sk": _str("sms.aliyun_sk"),
            "aliyun_sign": _str("sms.aliyun_sign"),
            "aliyun_tpl": _str("sms.aliyun_tpl"),
        },
    }
