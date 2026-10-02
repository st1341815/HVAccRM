"""成本与利润核算：按合同归集成本，计算毛利与毛利率。

口径：合同收入 = 合同额（含优惠冲减）+ 增项合计；成本 = 该合同下全部成本费用；
毛利 = 收入 - 成本；毛利率 = 毛利 / 收入。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import COST_CATEGORIES, Contract, ContractCost, Supplier, User
from . import finance as finance_svc

TOL = 0.005


@dataclass
class ProfitSummary:
    income: float = 0.0  # 应收总额（合同额 + 增项）
    received: float = 0.0
    cost: float = 0.0
    gross_profit: float = 0.0
    gross_margin: float = 0.0
    by_category: dict[str, float] = field(default_factory=dict)

    @property
    def is_loss(self) -> bool:
        return self.gross_profit < -TOL


def contract_cost_total(db: Session, contract_id: int) -> float:
    rows = db.scalars(select(ContractCost).where(ContractCost.contract_id == contract_id)).all()
    return round(sum(r.amount or 0 for r in rows), 2)


def cost_by_category(db: Session, contract_id: int) -> dict[str, float]:
    rows = db.scalars(select(ContractCost).where(ContractCost.contract_id == contract_id)).all()
    out: dict[str, float] = {}
    for r in rows:
        out[r.category] = round(out.get(r.category, 0.0) + (r.amount or 0), 2)
    return out


def profit_summary(db: Session, contract: Contract) -> ProfitSummary:
    fin = finance_svc.contract_summary(db, contract)
    income = fin.total
    cost = contract_cost_total(db, contract.id)
    gross = round(income - cost, 2)
    return ProfitSummary(
        income=income,
        received=fin.received,
        cost=cost,
        gross_profit=gross,
        gross_margin=round(gross / income * 100, 1) if income > TOL else 0.0,
        by_category=cost_by_category(db, contract.id),
    )


def contract_installers(db: Session) -> tuple[list[User], list[User]]:
    """返回 (安装工角色的启用账号, 其他启用账号) —— 施工费用优先选安装工。"""
    users = list(db.scalars(select(User).where(User.is_active == 1).order_by(User.id)).all())
    installers = [u for u in users if u.role == "installer"]
    others = [u for u in users if u.role != "installer"]
    return installers, others


def active_suppliers(db: Session) -> list[Supplier]:
    return list(db.scalars(select(Supplier).where(Supplier.is_active == 1).order_by(Supplier.name)).all())


def query_costs(
    db: Session,
    contract_ids: list[int] | None = None,
    category: str = "",
    supplier_name: str = "",
    installer_name: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    limit: int = 500,
) -> list[ContractCost]:
    stmt = select(ContractCost).order_by(ContractCost.spent_at.desc(), ContractCost.id.desc())
    if contract_ids is not None:
        if not contract_ids:
            return []
        stmt = stmt.where(ContractCost.contract_id.in_(contract_ids))
    if category:
        stmt = stmt.where(ContractCost.category == category)
    if supplier_name.strip():
        kw = f"%{supplier_name.strip()}%"
        sids = list(db.scalars(select(Supplier.id).where(Supplier.name.like(kw))).all())
        stmt = stmt.where(ContractCost.supplier_id.in_(sids or [-1]))
    if installer_name.strip():
        kw = f"%{installer_name.strip()}%"
        uids = list(
            db.scalars(select(User.id).where(or_(User.full_name.like(kw), User.username.like(kw)))).all()
        )
        stmt = stmt.where(ContractCost.installer_id.in_(uids or [-1]))
    if date_from:
        stmt = stmt.where(ContractCost.spent_at >= date_from)
    if date_to:
        stmt = stmt.where(ContractCost.spent_at <= date_to)
    if keyword.strip():
        stmt = stmt.where(ContractCost.remark.like(f"%{keyword.strip()}%"))
    return list(db.scalars(stmt.limit(limit)).all())


def category_totals(db: Session, contract_ids: list[int] | None = None) -> dict[str, float]:
    rows = query_costs(db, contract_ids=contract_ids, limit=100000)
    out = {c: 0.0 for c in COST_CATEGORIES}
    for r in rows:
        out[r.category] = round(out.get(r.category, 0.0) + (r.amount or 0), 2)
    return out
