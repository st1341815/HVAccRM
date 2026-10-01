"""应用配置：全部来自环境变量（pydantic-settings），不落盘敏感值。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "家居建材客户管理系统"
    app_secret_key: str = "dev-insecure-change-me"

    # 路径：配置 / 数据 / 缓存 / 日志分离（NAS 卷挂载）
    db_path: str = "/data/crm.db"
    data_dir: str = "/data"
    config_dir: str = "/config"

    admin_user: str = "admin"
    admin_pass: str = "admin123"
    admin_force_password_change: bool = False

    tz: str = "Asia/Shanghai"
    log_level: str = "INFO"

    session_cookie: str = "crm_session"
    session_max_age: int = 12 * 3600

    thumb_max_edge: int = 1280
    thumb_quality: int = 80

    backup_keep: int = 30
    login_max_attempts: int = 5
    login_lock_seconds: int = 300
    write_retry_attempts: int = 3

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def media_dir(self) -> Path:
        return self.data_path / "media"

    @property
    def backup_dir(self) -> Path:
        return self.data_path / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_path / "logs"

    @property
    def is_secret_default(self) -> bool:
        return self.app_secret_key in {"dev-insecure-change-me", "change-me-please", ""}

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.media_dir, self.backup_dir, self.log_dir):
            p.mkdir(parents=True, exist_ok=True)
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


@lru_cache
def get_settings() -> Settings:
    return Settings()


def set_timezone(tz: str) -> None:
    try:
        os.environ["TZ"] = tz
        import time

        time.tzset()
    except Exception:  # pragma: no cover - 仅影响日志显示时间
        pass
