"""案例：新增 cases 表（前期/后期施工完工后自动生成，对外展示不含客户隐私）。

Revision ID: 0009_cases
Revises: 0008_building
Create Date: 2026-10-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_cases"
down_revision = "0008_building"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "cases" not in tables:
        op.create_table(
            "cases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("room_label", sa.String(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("work_date", sa.String()),
            sa.Column("installer_id", sa.Integer()),
            sa.Column("installer_name", sa.String()),
            sa.Column("product_type", sa.String()),
            sa.Column("share_token", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("''")),
            sa.UniqueConstraint("task_id", name="uq_case_task"),
            sa.UniqueConstraint("share_token", name="uq_case_share_token"),
        )
        op.create_index("ix_cases_work_date", "cases", ["work_date"])
        op.create_index("ix_cases_installer", "cases", ["installer_id"])
        op.create_index("ix_cases_stage", "cases", ["stage"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "cases" in tables:
        op.drop_table("cases")
