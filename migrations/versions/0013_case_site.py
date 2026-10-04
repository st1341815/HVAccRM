"""案例：cases 增加 project_name / building（楼盘名称 + 楼栋号），供分享页「工地位置」。

Revision ID: 0013_case_site
Revises: 0012_case_image_source
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_case_site"
down_revision = "0012_case_image_source"
branch_labels = None
depends_on = None


def _add_column(op, table: str, name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
    if name not in cols:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column(op, "cases", "project_name", sa.Column("project_name", sa.String(), nullable=True))
    _add_column(op, "cases", "building", sa.Column("building", sa.String(), nullable=True))
    # 尽力回填既有案例：从 客户→房号→楼盘 取楼盘名与楼栋号（取不到则留空）
    op.execute(
        "UPDATE cases SET "
        "project_name = (SELECT p.name FROM customers c JOIN rooms r ON r.id = c.room_id "
        "                 JOIN projects p ON p.id = r.project_id WHERE c.id = cases.customer_id), "
        "building = (SELECT r.building FROM customers c JOIN rooms r ON r.id = c.room_id "
        "            WHERE c.id = cases.customer_id) "
        "WHERE project_name IS NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("cases")}
    for name in ("building", "project_name"):
        if name in cols:
            op.drop_column("cases", name)
