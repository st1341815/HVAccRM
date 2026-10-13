"""备份设置：新增 app_settings（key-value 运行时配置）表。

用于存放「备份保存路径 / 保留份数 / 每日与每周频率」等后台可改的配置，
避免为了改一个设置去动 .env 并重建容器。

Revision ID: 0015_app_settings
Revises: 0014_customer_lock
Create Date: 2026-10-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_app_settings"
down_revision = "0014_customer_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "app_settings" in tables:
        return
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), primary_key=True, nullable=False),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "app_settings" in tables:
        op.drop_table("app_settings")
