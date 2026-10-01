"""财务规则：三数核对（合同额 / 应收 / 已收）、逾期重算、超额拦截、收款分摊。"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChangeOrder, Contract, Customer, Payment, PaymentPlan, today_str

TOL = 0.005


@dataclass
class ContractSummary:
    base: float = 0.0
    change: float = 0.0
    total: float = 0.0
    received: float = 0.0
    plans_total: float = 0.0
    outstanding: float = 0.0
    overdue: float = 0.0
    recovery_rate: float = 0.0
    status: str = "正常"
    plans: list[PaymentPlan] = field(default_factory=list)

    @property
    def is_over(self) -> bool:
        return self.received > self.total + TOL

    @property
    def needs_plan(self) -> bool:
        return self.plans_total - self.total < -TOL


def contract_base_total(contract: Contract) -> float:
    return round((contract.total_amount or 0) - (contract.discount or 0), 2)


def contract_change_total(db: Session, contract: Contract) -> float:
    rows = db.scalars(select(ChangeOrder).where(ChangeOrder.contract_id == contract.id)).all()
    return round(sum(r.amount or 0 for r in rows), 2)


def contract_received(db: Session, contract: Contract) -> float:
    rows = db.scalars(select(Payment).where(Payment.contract_id == contract.id)).all()
    return round(sum(r.amount or 0 for r in rows), 2)


def allocate_plans(db: Session, contract: Contract) -> ContractSummary:
    """把已收款按 sort_order 分摊到应收计划，并刷新 status / paid_amount。"""
    plans = sorted(contract.plans, key=lambda p: (p.sort_order or 0, p.id or 0))
    received = contract_received(db, contract)
    remaining = received
    today = today_str()
    overdue = 0.0
    for p in plans:
        need = p.amount or 0
        if remaining >= need - TOL:
            p.paid_amount = round(need, 2)
            p.status = "paid"
            remaining = round(remaining - need, 2)
        elif remaining > TOL:
            p.paid_amount = round(remaining, 2)
            p.status = "overdue" if (p.due_date and p.due_date < today) else "partial"
            overdue += need - p.paid_amount
            remaining = 0.0
        else:
            p.paid_amount = 0.0
            p.status = "overdue" if (p.due_date and p.due_date < today) else "pending"
            if p.status == "overdue":
                overdue += need
    return summarize(contract, plans, received, overdue, db)


def summarize(
    contract: Contract,
    plans: list[PaymentPlan] | None = None,
    received: float | None = None,
    overdue: float | None = None,
    db: Session | None = None,
) -> ContractSummary:
    base = contract_base_total(contract)
    change = contract_change_total(db, contract) if db is not None else 0.0
    total = round(base + change, 2)
    if received is None:
        received = contract_received(db, contract) if db is not None else 0.0
    plans = plans if plans is not None else sorted(contract.plans, key=lambda p: (p.sort_order or 0, p.id or 0))
    plans_total = round(sum(p.amount or 0 for p in plans), 2)
    if overdue is None:
        overdue = round(
            sum((p.amount or 0) - (p.paid_amount or 0) for p in plans if p.status == "overdue"), 2
        )
    s = ContractSummary(
        base=base,
        change=change,
        total=total,
        received=round(received, 2),
        plans_total=plans_total,
        outstanding=round(total - received, 2),
        overdue=round(overdue, 2),
        recovery_rate=round(received / total * 100, 1) if total > TOL else 0.0,
        plans=plans,
    )
    if s.is_over:
        s.status = "超收"
    elif s.outstanding <= TOL and total > 0:
        s.status = "已结清"
    elif s.overdue > TOL:
        s.status = "有逾期"
    else:
        s.status = "正常"
    return s


def contract_summary(db: Session, contract: Contract) -> ContractSummary:
    return allocate_plans(db, contract)


def customer_summary(db: Session, customer: Customer) -> ContractSummary:
    """客户层面的汇总（多合同合并）。"""
    acc = ContractSummary()
    for c in customer.contracts:
        s = contract_summary(db, c)
        acc.base += s.base
        acc.change += s.change
        acc.total += s.total
        acc.received += s.received
        acc.plans_total += s.plans_total
        acc.overdue += s.overdue
    acc.base, acc.change, acc.total = round(acc.base, 2), round(acc.change, 2), round(acc.total, 2)
    acc.received, acc.plans_total = round(acc.received, 2), round(acc.plans_total, 2)
    acc.overdue = round(acc.overdue, 2)
    acc.outstanding = round(acc.total - acc.received, 2)
    acc.recovery_rate = round(acc.received / acc.total * 100, 1) if acc.total > TOL else 0.0
    return acc


def check_payment(contract: Contract, summary: ContractSummary, amount: float, is_refund: bool = False) -> str | None:
    """超额拦截 / 退款校验。返回错误信息或 None。"""
    if amount == 0:
        return "金额不能为 0"
    if not is_refund and amount < 0:
        return "退款请使用「退款登记」入口（需 payment:refund 权限）"
    if is_refund:
        if amount > 0:
            return "退款金额需为负数"
        if summary.received + amount < -TOL:
            return f"退款金额超过已收合计（已收 {summary.received:.2f}）"
        return None
    if summary.received + amount > summary.total + TOL:
        room = summary.total - summary.received
        return (
            f"超额拦截：合同总额 {summary.total:.2f}（含增项 {summary.change:.2f}），"
            f"已收 {summary.received:.2f}，本次剩余可收 {max(room, 0):.2f}"
        )
    return None


def refresh_all(db: Session, only_overdue: bool = True) -> dict[str, int]:
    """定时任务：扫描全部合同，重算计划和逾期。"""
    stats = {"contracts": 0, "overdue_plans": 0}
    for contract in db.scalars(select(Contract)).all():
        s = allocate_plans(db, contract)
        stats["contracts"] += 1
        stats["overdue_plans"] += sum(1 for p in s.plans if p.status == "overdue")
    db.commit()
    return stats


def top_debtors(db: Session, limit: int = 5) -> list[tuple[Customer, float]]:
    """欠款客户排行：按「未收金额」（合同额 + 增项 - 已收）排序。

    分期应收计划（payment_plans）已停用，逾期口径不再可用，故统一按未收金额。
    """
    rows: list[tuple[Customer, float]] = []
    for customer in db.scalars(select(Customer)).all():
        s = customer_summary(db, customer)
        if s.outstanding > TOL:
            rows.append((customer, s.outstanding))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:limit]


def upcoming_plans(db: Session, limit: int = 10) -> list[PaymentPlan]:
    today = today_str()
    stmt = (
        select(PaymentPlan)
        .where(PaymentPlan.status.in_(["pending", "partial", "overdue"]))
        .where(PaymentPlan.due_date.is_not(None))
        .where(PaymentPlan.due_date <= today)
        .order_by(PaymentPlan.due_date)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())
