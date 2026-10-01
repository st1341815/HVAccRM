"""客户表新增：手机号、微信、意向产品（多选）。

- phone / wechat：客户级联系方式（与 contacts 表的联系人电话分开）
- products：意向产品多选，编码为 "|锅炉|地暖|"
- 表单已移除的字段（industry / decor_stage / is_showroom / address）保留列不删：
  FTS5 索引与搜索仍引用 industry，历史数据结构不动更安全

Revision ID: 0002_customer_contact
Revises: 0001_initial
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_customer_contact"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

NEW_COLUMNS = (
    ("phone", sa.String()),
    ("wechat", sa.String()),
    ("products", sa.String()),
)
INDEX_NAME = "ix_customers_phone"

# 一次性回填：老客户的手机/微信此前只存在主联系人上，搬一份到客户级字段（为空时才填）
BACKFILL_SQL = (
    """UPDATE customers SET phone = (
           SELECT c.phone FROM contacts c
           WHERE c.customer_id = customers.id AND c.is_primary = 1 AND c.phone IS NOT NULL
           LIMIT 1)
       WHERE phone IS NULL AND EXISTS (
           SELECT 1 FROM contacts c
           WHERE c.customer_id = customers.id AND c.is_primary = 1 AND c.phone IS NOT NULL)""",
    """UPDATE customers SET wechat = (
           SELECT c.wechat FROM contacts c
           WHERE c.customer_id = customers.id AND c.is_primary = 1 AND c.wechat IS NOT NULL
           LIMIT 1)
       WHERE wechat IS NULL AND EXISTS (
           SELECT 1 FROM contacts c
           WHERE c.customer_id = customers.id AND c.is_primary = 1 AND c.wechat IS NOT NULL)""",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("customers")}
    for name, coltype in NEW_COLUMNS:
        if name not in existing:
            op.add_column("customers", sa.Column(name, coltype, nullable=True))
    indexes = {i["name"] for i in inspector.get_indexes("customers")}
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, "customers", ["phone"])
    for stmt in BACKFILL_SQL:
        op.execute(stmt)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("customers")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="customers")
    existing = {c["name"] for c in inspector.get_columns("customers")}
    for name, _ in NEW_COLUMNS:
        if name in existing:
            op.drop_column("customers", name)
