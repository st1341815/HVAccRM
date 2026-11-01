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

# 连接池配置（抽成常量便于测试断言，别在 create_engine 调用里写死）
POOL_SIZE = 15
MAX_OVERFLOW = 30
POOL_TIMEOUT = 10
POOL_RECYCLE = 1800


def _is_memory_sqlite(url: str) -> bool:
    """内存库不能传 pool_size/max_overflow（SQLAlchemy 会用 SingletonThreadPool）。

    测试与本地 `DB_PATH=:memory:` 走这条分支，生产是文件库走 QueuePool。
    """
    from sqlalchemy.engine import make_url

    try:
        u = make_url(url)
    except Exception:  # noqa: BLE001 - 解析失败就当作非内存库，保持原行为
        return False
    return u.get_backend_name() == "sqlite" and u.database in (None, "", ":memory:")


_POOL_KWARGS = (
    {}
    if _is_memory_sqlite(settings.database_url)
    else {
        # 连接池：默认 5+10=15 个连接在「图片并发」场景下会被打爆——
        # 一个页面同时请求 30+ 张缩略图时，排队 30s 后抛 QueuePool TimeoutError（2026-11-01 线上事故）。
        # SQLite 的读连接很轻（WAL 模式下并发读无锁），放宽到 15+30 足够；
        # pool_timeout 收到 10s，宁可让个别请求快速失败也不要让整页卡 30 秒。
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_timeout": POOL_TIMEOUT,
        "pool_recycle": POOL_RECYCLE,
    }
)

engine = create_engine(
    settings.database_url,
    future=True,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 15},
    **_POOL_KWARGS,
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
