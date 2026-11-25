"""电子合同模板表（contract_templates）。

全新库走 0001 的 create_all 时表已由模型建出 → 这里 inspect 幂等守卫
（与 0002/0014/0017/0018/0019 一致）。

Revision ID: 0020_contract_templates
Revises: 0019_contract_settled
Create Date: 2026-11-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_contract_templates"
down_revision = "0019_contract_settled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "contract_templates" in inspector.get_table_names():
        return
    op.create_table(
        "contract_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Integer(), server_default="0"),
        sa.Column("is_active", sa.Integer(), server_default="1"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "contract_templates" in inspector.get_table_names():
        op.drop_table("contract_templates")
