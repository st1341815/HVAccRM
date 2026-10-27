"""分享访问统计：新增 share_visits 表。

记录对外案例分享页的匿名访问（时间 / 来源 / 设备 / 停留时长 / 图片曝光），
用于「我发出去的案例，客户到底看了没」这类统计；不含任何客户隐私。

Revision ID: 0016_share_visits
Revises: 0015_app_settings
Create Date: 2026-10-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_share_visits"
down_revision = "0015_app_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "share_visits" in set(insp.get_table_names()):
        return
    op.create_table(
        "share_visits",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("visitor_id", sa.String(length=24), nullable=True),
        sa.Column("visited_at", sa.String(), nullable=False),
        sa.Column("src", sa.String(length=16), nullable=True),
        sa.Column("referer_host", sa.String(length=120), nullable=True),
        sa.Column("ip_hash", sa.String(length=16), nullable=True),
        sa.Column("is_wechat", sa.Integer(), nullable=True),
        sa.Column("device", sa.String(length=12), nullable=True),
        sa.Column("os_name", sa.String(length=20), nullable=True),
        sa.Column("ua", sa.String(length=300), nullable=True),
        sa.Column("dwell_ms", sa.Integer(), nullable=True),
        sa.Column("viewed_images", sa.Integer(), nullable=True),
        sa.Column("image_total", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_share_visits_case", "share_visits", ["case_id"])
    op.create_index("ix_share_visits_time", "share_visits", ["visited_at"])
    op.create_index("ix_share_visits_visitor", "share_visits", ["visitor_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "share_visits" in set(insp.get_table_names()):
        op.drop_table("share_visits")
