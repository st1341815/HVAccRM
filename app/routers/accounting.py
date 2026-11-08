"""核算模块：利润核算（全部合同）+ 核算（仅已结算合同）。

两个页面的聚合与排序都走 services.overview，保证口径一致：
- 利润核算：日常经营视角，含在制合同
- 核算：关账视角，只统计已标记结算的合同
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User, today_str
from ..permissions import require
from ..services import overview as overview_svc
from ..services import settlement as settlement_svc
from ..templating import render

router = APIRouter(prefix="/accounting", tags=["accounting"])


def _common(
    request: Request,
    db: Session,
    user: User,
    *,
    settled_only: bool,
    template: str,
    base_path: str,
    date_from: str,
    date_to: str,
    customer_q: str,
    only_cost: str,
    sort: str,
    order: str,
    extra_sorts=(),
):
    contracts = overview_svc.visible_contracts(db, user)
    if settled_only:
        contracts = [c for c in contracts if settlement_svc.is_settled(c)]
    ctx = overview_svc.build_ctx(
        db,
        user,
        contracts,
        date_from=date_from,
        date_to=date_to,
        customer_q=customer_q,
        only_cost=only_cost,
        sort=sort,
        order=order,
        base_path=base_path,
        extra_sorts=extra_sorts,
    )
    return render(
        request,
        template,
        settled_only=settled_only,
        today=today_str(),
        **ctx,
    )


@router.get("")
def index():
    return RedirectResponse("/accounting/profit", status_code=302)


@router.get("/profit")
def profit_view(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    only_cost: str = "",
    sort: str = "sign_date",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("settlement:view")),
):
    return _common(
        request,
        db,
        user,
        settled_only=False,
        template="accounting/profit.html",
        base_path="/accounting/profit",
        date_from=date_from,
        date_to=date_to,
        customer_q=customer_q,
        only_cost=only_cost,
        sort=sort,
        order=order,
    )


@router.get("/settlement")
def settlement_view(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    only_cost: str = "",
    sort: str = "sign_date",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("settlement:view")),
):
    return _common(
        request,
        db,
        user,
        settled_only=True,
        template="accounting/settlement.html",
        base_path="/accounting/settlement",
        # 「结算日」只在已结算合同上有意义，做成页面私有的可排序列（利润核算页没有这列）
        extra_sorts=[("settled_at", "结算日")],
        date_from=date_from,
        date_to=date_to,
        customer_q=customer_q,
        only_cost=only_cost,
        sort=sort,
        order=order,
    )
