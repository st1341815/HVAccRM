"""工序与合同关联：tasks 增加 contract_id（空=上门勘测，非空=施工工序）。

- 上门勘测与客户一一对应、施工工序与合同一一对应；
- 用两个部分唯一索引取代旧的 (customer_id, stage) 唯一约束，允许同一客户多份合同各有同名施工工序；
- 老模型的施工工序是「客户级」的（未区分合同），此处按客户的合同把既有施工工序挂到合同上：
  * 客户有合同 → 挂到其（首份）合同；
  * 客户无合同 → 施工工序属孤儿（未成交不应进入施工），删除。

Revision ID: 0011_task_contract
Revises: 0010_case_share_images
Create Date: 2026-10-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_task_contract"
down_revision = "0010_case_share_images"
branch_labels = None
depends_on = None

CONSTRUCTION_STAGES = ("前期施工", "后期施工", "调试验收")


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("tasks")}
    if "contract_id" not in cols:
        # SQLite 的 UNIQUE 约束内联在 CREATE TABLE 里，无法单独 DROP，
        # 需用 batch 重建 tasks 表：新增 contract_id 列 + 删除旧唯一约束。
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.add_column(
                sa.Column(
                    "contract_id",
                    sa.Integer(),
                    sa.ForeignKey("contracts.id", ondelete="CASCADE", name="fk_tasks_contract"),
                    nullable=True,
                )
            )
            batch.drop_constraint("uq_task_customer_stage", type_="unique")

    stages = ",".join(f"'{s}'" for s in CONSTRUCTION_STAGES)
    # 1) 把老模型「客户级」的施工工序挂到该客户的首份合同上
    op.execute(
        f"""
        UPDATE tasks
        SET contract_id = (
            SELECT c.id FROM contracts c
            WHERE c.customer_id = tasks.customer_id
            ORDER BY c.id LIMIT 1
        )
        WHERE stage IN ({stages})
          AND contract_id IS NULL
          AND EXISTS (SELECT 1 FROM contracts c2 WHERE c2.customer_id = tasks.customer_id)
        """
    )
    # 2) 客户无合同（未成交）却存在的施工工序属孤儿，删除
    op.execute(
        f"DELETE FROM tasks WHERE stage IN ({stages}) AND contract_id IS NULL"
    )
    # 3) 案例的产品类型跟随其来源工序所属合同（老模型快照曾为空）
    op.execute(
        """
        UPDATE cases
        SET product_type = (
            SELECT c.product_type FROM tasks t JOIN contracts c ON t.contract_id = c.id
            WHERE t.id = cases.task_id
        )
        WHERE product_type IS NULL
          AND EXISTS (
            SELECT 1 FROM tasks t JOIN contracts c ON t.contract_id = c.id
            WHERE t.id = cases.task_id AND c.product_type IS NOT NULL
          )
        """
    )

    # 部分唯一索引（幂等）：上门勘测按客户唯一、施工工序按合同唯一
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_customer_survey "
        "ON tasks (customer_id, stage) WHERE contract_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_contract_stage "
        "ON tasks (contract_id, stage) WHERE contract_id IS NOT NULL"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP INDEX IF EXISTS uq_task_contract_stage")
    op.execute("DROP INDEX IF EXISTS uq_task_customer_survey")
    cols = {c["name"] for c in sa.inspect(bind).get_columns("tasks")}
    if "contract_id" in cols:
        with op.batch_alter_table("tasks", recreate="always") as batch:
            batch.drop_constraint("fk_tasks_contract", type_="foreignkey")
            batch.drop_column("contract_id")
            batch.create_unique_constraint("uq_task_customer_stage", ["customer_id", "stage"])

