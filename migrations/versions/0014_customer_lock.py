"""客户锁定：customers 增加 locked 列（0=未锁定，1=锁定）。

规则：
- 新客户默认 0（未锁定）。
- 任一合同的「前期施工」工序完工后自动置 1（由 services/tasks.finish_task 触发）。
- 管理员可手动解锁（/customers/{id}/toggle-lock）。
- 新建合同时，已锁定客户从客户下拉中被过滤，降低误选概率。

Revision ID: 0014_customer_lock
Revises: 0013_case_site
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_customer_lock"
down_revision = "0013_case_site"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("customers")}
    if "locked" not in cols:
        op.add_column("customers", sa.Column("locked", sa.Integer(), nullable=False, server_default="0"))
        # SQLite 会保留 server_default，无需回填；为兼容其它后端显式回填一次
        op.execute("UPDATE customers SET locked = 0 WHERE locked IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("customers")}
    if "locked" in cols:
        op.drop_column("customers", "locked")
