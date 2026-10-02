"""楼栋标准化：新增 buildings 表（按楼盘预录、去重），并从现有房号回填。

Revision ID: 0008_building
Revises: 0007_photo_cost
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_building"
down_revision = "0007_photo_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "buildings" not in tables:
        op.create_table(
            "buildings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("''")),
            sa.UniqueConstraint("project_id", "name", name="uq_building"),
        )
        op.create_index("ix_buildings_project", "buildings", ["project_id"])
    # 幂等回填：把现有房号的楼栋去重后写入 buildings（已存在的不重复插入）
    op.execute(
        "INSERT OR IGNORE INTO buildings (project_id, name, created_at) "
        "SELECT DISTINCT project_id, building, '' FROM rooms "
        "WHERE building IS NOT NULL AND building != ''"
    )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "buildings" in tables:
        op.drop_table("buildings")
