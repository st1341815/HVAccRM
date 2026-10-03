"""案例：施工工序完工后自动生成对外展示记录。

只保存可公开字段（房号、工序、施工时间、师傅、产品类型），
绝不落客户姓名/手机/微信/合同金额/付款时间等隐私与商业信息。
"""
from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Case, Contract, Customer, Task, User, today_str


def product_type_for_customer(db: Session, customer: Customer) -> str | None:
    """对应合同的产品类型：取该客户最新一份（签订日期降序）填了产品类型的合同。"""
    if not customer:
        return None
    contracts = list(
        db.scalars(
            select(Contract)
            .where(Contract.customer_id == customer.id)
            .order_by(Contract.sign_date.desc(), Contract.id.desc())
        ).all()
    )
    for c in contracts:
        if c.product_type:
            return c.product_type
    return None


def _room_label(customer: Customer) -> str:
    """房号信息快照：楼盘名 + 楼栋-单元-房号（缺失段自动省略）。"""
    return (customer.room_label or "").strip() if customer else ""


def _new_token(db: Session) -> str:
    while True:
        token = secrets.token_urlsafe(16)
        if not db.scalars(select(Case).where(Case.share_token == token)).first():
            return token


def create_case_for_task(db: Session, task: Task) -> Case | None:
    """工序完工后生成案例（幂等：同一工序只生成一次）。"""
    from ..models import CASE_STAGES

    if not task or task.stage not in CASE_STAGES:
        return None
    existing = db.scalars(select(Case).where(Case.task_id == task.id)).first()
    if existing:
        return existing

    customer = db.get(Customer, task.customer_id)
    installer = db.get(User, task.assignee_id) if task.assignee_id else None

    case = Case(
        task_id=task.id,
        customer_id=task.customer_id,
        room_label=_room_label(customer) or "-",
        stage=task.stage,
        work_date=(task.actual_end or task.actual_start or today_str())[:10],
        installer_id=task.assignee_id,
        installer_name=(installer.display_name if installer else None),
        product_type=product_type_for_customer(db, customer),
        share_token=_new_token(db),
    )
    db.add(case)
    db.flush()
    return case
