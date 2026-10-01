"""photos 新增 cost_id：成本凭证（发票/收据）归属到成本记录。

Revision ID: 0007_photo_cost
Revises: 0006_costs
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_photo_cost"
down_revision = "0006_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("photos")}
    if "cost_id" not in cols:
        op.add_column("photos", sa.Column("cost_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("photos")}
    if "cost_id" in cols:
        op.drop_column("photos", "cost_id")
