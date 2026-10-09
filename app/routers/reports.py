"""报表：按月/年统计销售额、成本、利润等。"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Contract, ContractCost, Payment, User, today_str
from ..permissions import can_see_amount, require, visible_customer_ids
from ..templating import render

router = APIRouter(prefix="/reports", tags=["reports"])


def _visible_contracts(db: Session, user: User) -> list[Contract]:
    allowed = visible_customer_ids(db, user)
    contracts = list(db.scalars(select(Contract).order_by(Contract.sign_date, Contract.id)).all())
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    return contracts


def _contract_income(contract: Contract) -> float:
    """收入 = 合同额（减优惠）+ 增项合计。"""
    base = (contract.total_amount or 0) - (contract.discount or 0)
    change = sum(co.amount or 0 for co in contract.change_orders)
    return round(base + change, 2)


@router.get("")
def reports(
    request: Request,
    mode: str = "month",
    year: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("report:view")),
):
    if mode not in ("month", "year"):
        mode = "month"
    today = today_str()
    cur_year = today[:4]
    year = year if (len(year) == 4 and year.isdigit()) else cur_year

    contracts = _visible_contracts(db, user)
    visible_ids = {c.id for c in contracts}

    # 销售额 / 合同数：按签订日归集
    income_by = defaultdict(float)
    count_by = defaultdict(int)
    for c in contracts:
        if not c.sign_date:
            continue
        key = c.sign_date[:7] if mode == "month" else c.sign_date[:4]
        income_by[key] += _contract_income(c)
        count_by[key] += 1

    # 成本：按费用发生日归集
    cost_by = defaultdict(float)
    if visible_ids:
        for cost in db.scalars(select(ContractCost).where(ContractCost.contract_id.in_(visible_ids))).all():
            if not cost.spent_at:
                continue
            key = cost.spent_at[:7] if mode == "month" else cost.spent_at[:4]
            cost_by[key] += cost.amount or 0

    # 已收：按收款日归集
    received_by = defaultdict(float)
    if visible_ids:
        for p in db.scalars(select(Payment).where(Payment.contract_id.in_(visible_ids))).all():
            if not p.paid_at:
                continue
            key = p.paid_at[:7] if mode == "month" else p.paid_at[:4]
            received_by[key] += p.amount or 0

    # 生成周期列表
    if mode == "month":
        periods = [f"{year}-{m:02d}" for m in range(1, 13)]
    else:
        periods = sorted(set(income_by) | set(cost_by) | set(received_by))

    rows = []
    for key in periods:
        income = round(income_by.get(key, 0), 2)
        cost = round(cost_by.get(key, 0), 2)
        gross = round(income - cost, 2)
        received = round(received_by.get(key, 0), 2)
        rows.append(
            {
                "period": key,
                "label": f"{key} 年" if mode == "year" else f"{int(key[5:7])} 月",
                "income": income,
                "cost": cost,
                "gross": gross,
                "margin": round(gross / income * 100, 1) if income else 0.0,
                "received": received,
                "contracts": count_by.get(key, 0),
            }
        )

    total_income = round(sum(r["income"] for r in rows), 2)
    total_cost = round(sum(r["cost"] for r in rows), 2)
    total_gross = round(total_income - total_cost, 2)
    total_received = round(sum(r["received"] for r in rows), 2)

    years = sorted({(c.sign_date or "")[:4] for c in contracts if c.sign_date and len(c.sign_date) >= 4} | {cur_year})
    years = [y for y in years if len(y) == 4 and y.isdigit()]

    return render(
        request,
        "reports/index.html",
        mode=mode,
        year=year,
        years=years,
        rows=rows,
        totals={
            "income": total_income,
            "cost": total_cost,
            "gross": total_gross,
            "margin": round(total_gross / total_income * 100, 1) if total_income else 0.0,
            "received": total_received,
            "contracts": sum(count_by.values()),
        },
        show_money=can_see_amount(user),
        today=today_str(),
    )
