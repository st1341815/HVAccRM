"""报表：按月/年统计销售额、成本、利润；按客户查询利润。"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChangeOrder, Contract, ContractCost, Customer, Payment, User, today_str
from ..permissions import can_see_amount, require, visible_customer_ids
from ..services import search as search_svc
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


# --------------------------------------------------- 按客户查询利润
_CUSTOMER_SORT_OPTIONS = [
    ("customer", "客户"),
    ("contracts", "合同数"),
    ("income", "销售额"),
    ("cost", "成本"),
    ("gross", "毛利"),
    ("margin", "毛利率"),
    ("received", "已收"),
    ("outstanding", "未收"),
]

_CUSTOMER_SORT_KEYS = {k for k, _ in _CUSTOMER_SORT_OPTIONS}


@router.get("/customers")
def reports_customers(
    request: Request,
    q: str = "",
    sort: str = "gross",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("report:view")),
):
    """按客户查询利润：该客户名下所有合同的收入 / 成本 / 毛利合计。

    口径与「按月 / 按年」不同：这里不按日期拆分，而是把该客户全部合同作为一个整体聚合，
    便于直接回答「这个客户到底赚了多少」。
    """
    if sort not in _CUSTOMER_SORT_KEYS:
        sort = "gross"
    if order not in ("asc", "desc"):
        order = "desc"

    contracts = _visible_contracts(db, user)

    by_customer: dict[int, list[Contract]] = defaultdict(list)
    for c in contracts:
        by_customer[c.customer_id].append(c)

    # 关键词过滤：姓名 / 手机号 / 房号 / 楼盘（复用客户检索）
    if q.strip():
        matched = set(search_svc.customer_ids_by_keyword(db, q))
        by_customer = {cid: cs for cid, cs in by_customer.items() if cid in matched}

    visible_ids = {c.id for cs in by_customer.values() for c in cs}

    # 批量取成本 / 收款 / 增项（避免逐合同 N+1）
    cost_by_contract: dict[int, float] = defaultdict(float)
    received_by_contract: dict[int, float] = defaultdict(float)
    change_by_contract: dict[int, float] = defaultdict(float)
    if visible_ids:
        for cost in db.scalars(
            select(ContractCost).where(ContractCost.contract_id.in_(visible_ids))
        ).all():
            cost_by_contract[cost.contract_id] += cost.amount or 0
        for p in db.scalars(select(Payment).where(Payment.contract_id.in_(visible_ids))).all():
            received_by_contract[p.contract_id] += p.amount or 0
        for co in db.scalars(
            select(ChangeOrder).where(ChangeOrder.contract_id.in_(visible_ids))
        ).all():
            change_by_contract[co.contract_id] += co.amount or 0

    customer_ids = list(by_customer.keys())
    customers = (
        {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(customer_ids))).all()}
        if customer_ids
        else {}
    )
    owner_ids = {c.owner_id for c in customers.values() if c.owner_id}
    owners = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(owner_ids))).all()}
        if owner_ids
        else {}
    )

    rows = []
    for cid, cs in by_customer.items():
        cust = customers.get(cid)
        income = round(
            sum(
                (c.total_amount or 0) - (c.discount or 0) + change_by_contract.get(c.id, 0)
                for c in cs
            ),
            2,
        )
        cost = round(sum(cost_by_contract.get(c.id, 0) for c in cs), 2)
        received = round(sum(received_by_contract.get(c.id, 0) for c in cs), 2)
        gross = round(income - cost, 2)
        owner = owners.get(cust.owner_id) if cust and cust.owner_id else None
        rows.append(
            {
                "customer_id": cid,
                "customer_name": cust.name if cust else f"#{cid}",
                "room_label": cust.room_label_short if cust else "",
                "owner_name": owner.display_name if owner else "",
                "contracts": len(cs),
                "income": income,
                "cost": cost,
                "gross": gross,
                "margin": round(gross / income * 100, 1) if income else 0.0,
                "received": received,
                "outstanding": round(income - received, 2),
            }
        )

    sort_key = {
        "customer": lambda r: r["customer_name"],
        "contracts": lambda r: r["contracts"],
        "income": lambda r: r["income"],
        "cost": lambda r: r["cost"],
        "gross": lambda r: r["gross"],
        "margin": lambda r: r["margin"],
        "received": lambda r: r["received"],
        "outstanding": lambda r: r["outstanding"],
    }[sort]
    rows.sort(key=sort_key, reverse=(order == "desc"))

    # 排序链接（保留现有筛选参数，仅切换字段 / 升降序）
    from urllib.parse import urlencode

    base_params = {k: v for k, v in request.query_params.items() if k in ("q",) and v}
    sort_links = {}
    for key, _ in _CUSTOMER_SORT_OPTIONS:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = "/reports/customers?" + urlencode(
            {**base_params, "sort": key, "order": new_order}
        )

    total_income = round(sum(r["income"] for r in rows), 2)
    total_cost = round(sum(r["cost"] for r in rows), 2)
    total_gross = round(total_income - total_cost, 2)
    total_received = round(sum(r["received"] for r in rows), 2)

    return render(
        request,
        "reports/customers.html",
        q=q,
        sort=sort,
        order=order,
        sort_options=_CUSTOMER_SORT_OPTIONS,
        sort_links=sort_links,
        rows=rows,
        totals={
            "customers": len(rows),
            "contracts": sum(r["contracts"] for r in rows),
            "income": total_income,
            "cost": total_cost,
            "gross": total_gross,
            "margin": round(total_gross / total_income * 100, 1) if total_income else 0.0,
            "received": total_received,
            "outstanding": round(total_income - total_received, 2),
        },
        show_money=can_see_amount(user),
        today=today_str(),
    )
