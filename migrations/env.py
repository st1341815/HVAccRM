"""Alembic 环境：复用应用配置与模型元数据。"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # 应用内嵌运行迁移时，root logger 已由 app.bootstrap 配好：
    # 此时若再执行 fileConfig，Alembic 会按 alembic.ini 替换掉应用自身的日志 handler。
    import logging as _logging

    if not _logging.getLogger().handlers:
        try:
            fileConfig(config.config_file_name, disable_existing_loggers=False)
        except Exception:  # pragma: no cover - 容器内无 ini 时忽略
            pass

target_metadata = Base.metadata


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), connect_args={"check_same_thread": False}, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
