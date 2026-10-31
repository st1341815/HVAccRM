"""案例公众号草稿同步：cases 加 3 列。

- wx_draft_status：none / running / done / failed
- wx_draft_at：最近一次成功同步时间
- wx_draft_note：失败原因或提示（成功时为空）

全新库走 0001 的 create_all 时列已由模型建出 → 这里 inspect 幂等守卫
（与 0002/0014/0017 一致）。

Revision ID: 0018_wxmp_draft
Revises: 0017_sms_login
Create Date: 2026-10-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_wxmp_draft"
down_revision = "0017_sms_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("cases")}
    if "wx_draft_status" not in cols:
        op.add_column(
            "cases",
            sa.Column("wx_draft_status", sa.String(), nullable=True, server_default="none"),
        )
        # 老数据回填成 none，避免模板里出现 None
        op.execute("UPDATE cases SET wx_draft_status = 'none' WHERE wx_draft_status IS NULL")
    if "wx_draft_at" not in cols:
        op.add_column("cases", sa.Column("wx_draft_at", sa.String(), nullable=True))
    if "wx_draft_note" not in cols:
        op.add_column("cases", sa.Column("wx_draft_note", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("cases")}
    for name in ("wx_draft_note", "wx_draft_at", "wx_draft_status"):
        if name in cols:
            op.drop_column("cases", name)
