"""案例分享限制 + 案例展示图：cases 加分享有效期/次数/失效开关，新增 case_images 表。

Revision ID: 0010_case_share_images
Revises: 0009_cases
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_case_share_images"
down_revision = "0009_cases"
branch_labels = None
depends_on = None


def _add_column(op, table: str, name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    if name not in cols:
        op.add_column(table, column)


def upgrade() -> None:
    # cases 新增分享限制相关列（幂等）
    _add_column(op, "cases", "share_expires_at", sa.Column("share_expires_at", sa.String(), nullable=True))
    _add_column(op, "cases", "share_duration", sa.Column("share_duration", sa.String(), nullable=True))
    _add_column(op, "cases", "share_max_views", sa.Column("share_max_views", sa.Integer(), nullable=True))
    _add_column(op, "cases", "share_views", sa.Column("share_views", sa.Integer(), nullable=True))
    _add_column(op, "cases", "share_disabled", sa.Column("share_disabled", sa.Integer(), nullable=True))

    # case_images 表（幂等）
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "case_images" not in tables:
        op.create_table(
            "case_images",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("thumb_path", sa.String()),
            sa.Column("sort_order", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("''")),
        )
        op.create_index("ix_case_images_case", "case_images", ["case_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "case_images" in tables:
        op.drop_table("case_images")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("cases")}
    for name in ("share_disabled", "share_views", "share_max_views", "share_duration", "share_expires_at"):
        if name in cols:
            op.drop_column("cases", name)
