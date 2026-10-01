"""初始 Schema：用户/权限、楼盘房号、客户、合同收款、施工、照片 + FTS5 全文索引。

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01
"""
from __future__ import annotations

from alembic import op

from app.models import FTS_DDL, Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # 全部表结构来自 ORM 元数据，保证「代码即 Schema」单一事实来源
    Base.metadata.create_all(bind=bind)
    for stmt in FTS_DDL:
        op.execute(stmt)
    try:
        op.execute("INSERT INTO customers_fts(customers_fts) VALUES('rebuild')")
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("customers_fts_au", "customers_fts_ad", "customers_fts_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS customers_fts")
    Base.metadata.drop_all(bind=bind)
