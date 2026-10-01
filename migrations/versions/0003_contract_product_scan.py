"""合同新增：产品类型；照片新增：归属合同（纸质合同照片）。

保留列不删：contracts.discount（表单已移除，历史数据兼容）。

Revision ID: 0003_contract_product_scan
Revises: 0002_customer_contact
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_contract_product_scan"
down_revision = "0002_customer_contact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    contract_cols = {c["name"] for c in inspector.get_columns("contracts")}
    if "product_type" not in contract_cols:
        op.add_column("contracts", sa.Column("product_type", sa.String(), nullable=True))

    photo_cols = {c["name"] for c in inspector.get_columns("photos")}
    if "contract_id" not in photo_cols:
        op.add_column("photos", sa.Column("contract_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    photo_cols = {c["name"] for c in inspector.get_columns("photos")}
    if "contract_id" in photo_cols:
        op.drop_column("photos", "contract_id")
    contract_cols = {c["name"] for c in inspector.get_columns("contracts")}
    if "product_type" in contract_cols:
        op.drop_column("contracts", "product_type")
