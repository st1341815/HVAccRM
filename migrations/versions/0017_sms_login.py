"""外网短信验证码登录：users.mobile + sms_codes 表。

- users.mobile：可空；未绑定的账号在外网登录时被拒（提示联系管理员）。
- sms_codes：验证码只存 HMAC-SHA256 哈希，5 分钟有效、错 3 次作废、一次性。

全新库走 0001 的 create_all 时列/表已由模型建出 → 这里全部 inspect 幂等守卫
（与 0002/0014 一致：add_column 仅在列缺失时执行；建表仅在表缺失时执行）。

Revision ID: 0017_sms_login
Revises: 0016_share_visits
Create Date: 2026-10-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_sms_login"
down_revision = "0016_share_visits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("users")}
    if "mobile" not in cols:
        # 全新库（create_all 已建出列，可能无 server_default）无需回填：历史行 mobile 为 NULL 即「未绑定」
        op.add_column("users", sa.Column("mobile", sa.String(), nullable=True))

    tables = set(inspector.get_table_names())
    if "sms_codes" not in tables:
        op.create_table(
            "sms_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("mobile", sa.String(), nullable=False),
            sa.Column("code_hash", sa.String(), nullable=False),
            sa.Column("scene", sa.String(), nullable=False, server_default="login"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("used_at", sa.String(), nullable=True),
            sa.Column("expires_at", sa.String(), nullable=False),
            sa.Column("created_ip", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
        )
        op.create_index("ix_sms_codes_user_scene", "sms_codes", ["user_id", "scene"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "sms_codes" in tables:
        op.drop_index("ix_sms_codes_user_scene", table_name="sms_codes")
        op.drop_table("sms_codes")
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "mobile" in cols:
        with op.batch_alter_table("users") as batch:
            batch.drop_column("mobile")
