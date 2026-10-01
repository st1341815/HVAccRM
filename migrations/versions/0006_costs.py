"""成本模块：新增 suppliers（供应商）与 contract_costs（合同成本）两张表。

Revision ID: 0006_costs
Revises: 0005_photo_payment
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_costs"
down_revision = "0005_photo_payment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "suppliers" not in tables:
        op.create_table(
            "suppliers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False, unique=True),
            sa.Column("contact", sa.String()),
            sa.Column("phone", sa.String()),
            sa.Column("notes", sa.Text()),
            sa.Column("is_active", sa.Integer(), server_default="1"),
            sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("''")),
        )
    if "contract_costs" not in tables:
        op.create_table(
            "contract_costs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id")),
            sa.Column("installer_id", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("remark", sa.Text()),
            sa.Column("spent_at", sa.String()),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
            sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("''")),
        )
        op.create_index("ix_contract_costs_contract", "contract_costs", ["contract_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "contract_costs" in tables:
        op.drop_table("contract_costs")
    if "suppliers" in tables:
        op.drop_table("suppliers")
