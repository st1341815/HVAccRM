"""Pytest configuration: import-path setup + shared DB fixtures for HVAccRM tests.

每个 test 用 function scope 的内存 SQLite 库；autoflush=False 与生产一致，
测试中需手动 db.flush() 取 id。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 `from app.xxx import ...` 工作
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", ":memory:")

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import Base, User, today_str  # noqa: E402


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """function-scoped Session，与生产配置对齐 (autoflush=False, expire_on_commit=False)。"""
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def admin(db):
    u = User(
        username="admin", full_name="管理员", password_hash="x",
        role="admin", data_scope="all", is_active=1,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def sales(db):
    u = User(
        username="sales1", full_name="销售员", password_hash="x",
        role="sales", data_scope="self", is_active=1,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def installer(db):
    u = User(
        username="worker1", full_name="师傅", password_hash="x",
        role="installer", data_scope="self", is_active=1,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def today():
    return today_str()
