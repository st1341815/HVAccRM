"""数据库引擎与会话：SQLite + WAL + 外键 + busy_timeout + 写重试。"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

log = logging.getLogger("crm.db")

settings = get_settings()

engine = create_engine(
    settings.database_url,
    future=True,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 15},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # pragma: no cover
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

T = TypeVar("T")


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """脚本/定时任务使用的事务上下文。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def is_locked_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower()


def write_with_retry(fn: Callable[[], T], attempts: int | None = None, base_delay: float = 0.25) -> T:
    """SQLite 并发写短暂锁等待时的退避重试（NAS 文档第十节要求）。"""
    attempts = attempts or settings.write_retry_attempts
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except OperationalError as exc:  # noqa: PERF203
            last = exc
            if not is_locked_error(exc) or i == attempts - 1:
                raise
            delay = base_delay * (2**i)
            log.warning("database is locked，%.2fs 后重试（第 %d/%d 次）", delay, i + 1, attempts)
            time.sleep(delay)
    raise last  # pragma: no cover


def commit_retry(db: Session, attempts: int | None = None) -> None:
    """带锁重试的提交。"""
    write_with_retry(db.commit, attempts)


def healthcheck() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover
        log.error("数据库健康检查失败: %s", exc)
        return False


def raw_exec(sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql))
