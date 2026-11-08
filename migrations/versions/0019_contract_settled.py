"""合同结算标记：contracts 加 3 列。

- settled：0/1 结算开关（与 status 正交，status 仍表示 active/done/void）
- settled_at：最近一次标记结算的时间（YYYY-MM-DD）
- settled_by：执行结算的用户 id（仅记录，不建外键，便于 SQLite 幂等加列）

全新库走 0001 的 create_all 时列已由模型建出 → 这里 inspect 幂等守卫
（与 0002/0014/0017/0018 一致）。

Revision ID: 0019_contract_settled
Revises: 0018_wxmp_draft
Create Date: 2026-11-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_contract_settled"
down_revision = "0018_wxmp_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("contracts")}
    if "settled" not in cols:
        op.add_column(
            "contracts",
            sa.Column("settled", sa.Integer(), nullable=True, server_default="0"),
        )
        # 存量合同一律回填未结算，避免模板里出现 None
        op.execute("UPDATE contracts SET settled = 0 WHERE settled IS NULL")
    if "settled_at" not in cols:
        op.add_column("contracts", sa.Column("settled_at", sa.String(), nullable=True))
    if "settled_by" not in cols:
        op.add_column("contracts", sa.Column("settled_by", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("contracts")}
    for name in ("settled_by", "settled_at", "settled"):
        if name in cols:
            op.drop_column("contracts", name)
