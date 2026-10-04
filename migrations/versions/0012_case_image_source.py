"""案例：case_images 增加 source_photo_id（来源照片 id），用于「重新同步」去重。

Revision ID: 0012_case_image_source
Revises: 0011_task_contract
Create Date: 2026-10-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_case_image_source"
down_revision = "0011_task_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("case_images")}
    if "source_photo_id" not in cols:
        op.add_column("case_images", sa.Column("source_photo_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("case_images")}
    if "source_photo_id" in cols:
        op.drop_column("case_images", "source_photo_id")
