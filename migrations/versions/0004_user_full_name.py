"""users 新增 full_name（姓名）：界面与选择器优先显示姓名，账号仅用于登录/审计。

Revision ID: 0004_user_full_name
Revises: 0003_contract_product_scan
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_user_full_name"
down_revision = "0003_contract_product_scan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "full_name" not in cols:
        op.add_column("users", sa.Column("full_name", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "full_name" in cols:
        op.drop_column("users", "full_name")
