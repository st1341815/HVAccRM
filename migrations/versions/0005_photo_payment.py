"""photos 新增 payment_id：收款/退款截图归属到具体收款记录。

Revision ID: 0005_photo_payment
Revises: 0004_user_full_name
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_photo_payment"
down_revision = "0004_user_full_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("photos")}
    if "payment_id" not in cols:
        op.add_column("photos", sa.Column("payment_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("photos")}
    if "payment_id" in cols:
        op.drop_column("photos", "payment_id")
