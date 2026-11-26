"""看板方案表 dashboard_views（v1.16.0 首页看板 2.0）。

Revision ID: 0021_dashboard_views
Revises: 0020_contract_templates
Create Date: 2026-11-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_dashboard_views"
down_revision = "0020_contract_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "dashboard_views" in tables:  # 幂等：全新库由 0001 create_all 建过就跳过
        return

    op.create_table(
        "dashboard_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("layout_json", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_dashboard_view_name"),
    )
    # 每人至多一个默认方案。SQLite 的部分唯一索引必须走裸 SQL（alembic 的
    # create_index 不支持 sqlite_where 之外的方言差异，这里库就是 SQLite）。
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_view_default "
        "ON dashboard_views(user_id) WHERE is_default = 1"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_dashboard_views_user ON dashboard_views(user_id)")


def downgrade() -> None:
    op.drop_table("dashboard_views")
