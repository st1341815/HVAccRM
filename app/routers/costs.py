"""成本模块：供应商维护、合同成本录入、利润核算与成本明细查询。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import (
    COST_CATEGORIES,
    COST_NEEDS_INSTALLER,
    COST_NEEDS_SUPPLIER,
    Contract,
    ContractCost,
    Supplier,
    User,
    now_iso,
    today_str,
)
from ..permissions import can_see_amount, customer_scope_conditions, has_perm, require, visible_customer_ids
from ..services import costs as cost_svc
from ..services import photos as photo_svc
from ..services import finance as finance_svc
from ..services import search as search_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_float, parse_int

router = APIRouter(prefix="/costs", tags=["costs"])
COST_PHOTO_KIND = "成本凭证"


def _visible_contracts(db: Session, user: User) -> list[Contract]:
    """按数据范围过滤可见合同（成本/利润只算自己有权看的客户）。"""
    allowed = visible_customer_ids(db, user)
    contracts = list(db.scalars(select(Contract).order_by(Contract.sign_date.desc(), Contract.id.desc())).all())
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    return contracts


def _money_ok(user: User) -> bool:
    return can_see_amount(user) and has_perm(user, "cost:view")


@router.get("")
def overview(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    only_cost: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:view")),
):
    contracts = _visible_contracts(db, user)
    if date_from:
        contracts = [c for c in contracts if (c.sign_date or "") >= date_from]
    if date_to:
        contracts = [c for c in contracts if (c.sign_date or "") <= date_to]
    if customer_q and customer_q.strip():
        cids = set(search_svc.customer_ids_by_keyword(db, customer_q))
        contracts = [c for c in contracts if c.customer_id in cids]

    rows = []
    for c in contracts:
        fin = finance_svc.contract_summary(db, c)
        p = cost_svc.profit_summary(db, c)
        if only_cost and p.cost <= 0:
            continue
        rows.append((c, fin, p))
    rows.sort(key=lambda r: r[2].gross_profit)

    totals = {
        "income": round(sum(p.income for _, _, p in rows), 2),
        "received": round(sum(p.received for _, _, p in rows), 2),
        "cost": round(sum(p.cost for _, _, p in rows), 2),
        "gross_profit": round(sum(p.gross_profit for _, _, p in rows), 2),
    }
    totals["gross_margin"] = (
        round(totals["gross_profit"] / totals["income"] * 100, 1) if totals["income"] > cost_svc.TOL else 0.0
    )
    return render(
        request,
        "costs/overview.html",
        rows=rows,
        totals=totals,
        categories=COST_CATEGORIES,
        category_totals=cost_svc.category_totals(db, [c.id for c, _, _ in rows]),
        filters={"date_from": date_from, "date_to": date_to, "customer_q": customer_q, "only_cost": only_cost},
        can_edit=has_perm(user, "cost:edit"),
        today=today_str(),
    )


@router.get("/entries")
def entries(
    request: Request,
    category: str = "",
    supplier_name: str = "",
    installer_name: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:view")),
):
    contracts = _visible_contracts(db, user)
    contract_map = {c.id: c for c in contracts}
    rows = cost_svc.query_costs(
        db,
        contract_ids=list(contract_map) or None,
        category=category,
        supplier_name=supplier_name,
        installer_name=installer_name,
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
    )
    suppliers = {s.id: s for s in db.scalars(select(Supplier)).all()}
    users = {u.id: u for u in db.scalars(select(User)).all()}
    return render(
        request,
        "costs/entries.html",
        rows=rows,
        cost_photos=photo_svc.photos_by_cost(db, [r.id for r in rows]),
        contract_map=contract_map,
        suppliers=suppliers,
        users=users,
        categories=COST_CATEGORIES,
        total=round(sum(r.amount or 0 for r in rows), 2),
        filters={
            "category": category,
            "supplier_name": supplier_name,
            "installer_name": installer_name,
            "date_from": date_from,
            "date_to": date_to,
            "keyword": keyword,
        },
        can_edit=has_perm(user, "cost:edit"),
        today=today_str(),
    )


@router.get("/suppliers")
def suppliers_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:view")),
):
    rows = list(db.scalars(select(Supplier).order_by(Supplier.is_active.desc(), Supplier.name)).all())
    used = {}
    for cost in db.scalars(select(ContractCost).where(ContractCost.supplier_id.is_not(None))).all():
        used[cost.supplier_id] = round(used.get(cost.supplier_id, 0.0) + (cost.amount or 0), 2)
    return render(
        request,
        "costs/suppliers.html",
        rows=rows,
        used=used,
        can_edit=has_perm(user, "cost:edit"),
    )


@router.post("/suppliers/new")
def create_supplier(
    request: Request,
    name: str = Form(...),
    contact: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    name = name.strip()
    if not name:
        return redirect("/costs/suppliers", "供应商名称必填", "err")
    if db.scalars(select(Supplier).where(Supplier.name == name)).first():
        return redirect("/costs/suppliers", f"供应商「{name}」已存在", "err")
    supplier = Supplier(
        name=name,
        contact=contact.strip() or None,
        phone=phone.strip() or None,
        notes=notes.strip() or None,
        is_active=1,
        created_at=now_iso(),
    )
    db.add(supplier)
    db.flush()
    log_action(db, user, "create", "suppliers", supplier.id, new=supplier, ip=client_ip(request))
    commit_retry(db)
    return redirect("/costs/suppliers", f"供应商「{name}」已添加")


@router.post("/suppliers/{supplier_id}/update")
def update_supplier(
    request: Request,
    supplier_id: int,
    contact: str = Form(""),
    phone: str = Form(""),
    notes: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    supplier = get_or_404(db, Supplier, supplier_id, "供应商")
    before = {"contact": supplier.contact, "phone": supplier.phone, "is_active": supplier.is_active}
    supplier.contact = contact.strip() or None
    supplier.phone = phone.strip() or None
    supplier.notes = notes.strip() or None
    supplier.is_active = 1 if is_active else 0
    log_action(db, user, "update", "suppliers", supplier.id, old=before, new=supplier, ip=client_ip(request))
    commit_retry(db)
    return redirect("/costs/suppliers", f"供应商「{supplier.name}」已更新")


@router.post("/new")
async def create_cost(
    request: Request,
    contract_id: str = Form(...),
    category: str = Form(...),
    amount: str = Form("0"),
    supplier_id: str = Form(""),
    installer_id: str = Form(""),
    remark: str = Form(""),
    spent_at: str = Form(""),
    back: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    contract = db.get(Contract, parse_int(contract_id))
    target = back or (f"/contracts/{contract_id}" if contract else "/costs")
    if not contract:
        return redirect("/costs", "合同不存在", "err")
    category = category.strip()
    if category not in COST_CATEGORIES:
        return redirect(target, "费用项目请从下拉中选择", "err")
    amt = parse_float(amount)
    if amt <= 0:
        return redirect(target, "费用金额需大于 0", "err")
    sid = parse_int(supplier_id)
    iid = parse_int(installer_id)
    if category == COST_NEEDS_SUPPLIER:
        if not sid or not db.get(Supplier, sid):
            return redirect(target, f"「{COST_NEEDS_SUPPLIER}」必须选择供应商", "err")
    if category == COST_NEEDS_INSTALLER:
        if not iid or not db.get(User, iid):
            return redirect(target, f"「{COST_NEEDS_INSTALLER}」必须选择安装师傅", "err")
    cost = ContractCost(
        contract_id=contract.id,
        category=category,
        amount=round(amt, 2),
        supplier_id=sid if category == COST_NEEDS_SUPPLIER else None,
        installer_id=iid if category == COST_NEEDS_INSTALLER else None,
        remark=remark.strip() or None,
        spent_at=(spent_at.strip() or today_str())[:10],
        created_by=user.id,
        created_at=now_iso(),
    )
    db.add(cost)
    db.flush()
    # 附图（发票/收据/对账单）：与成本记录绑定，便于后续查询核对
    saved, dedup, errors = 0, 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=contract.customer_id,
            kind=COST_PHOTO_KIND,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            contract_id=contract.id,
            cost_id=cost.id,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                dedup += 1
            else:
                saved += 1
    log_action(db, user, "create", "contract_costs", cost.id, new=cost, ip=client_ip(request))
    commit_retry(db)
    message = f"已登记{category} {amt:,.2f}"
    if saved or dedup:
        message += f"；附图新增 {saved} 张、去重 {dedup} 张"
    if errors:
        return redirect(target, message + "；附图失败：" + "；".join(errors), "err")
    return redirect(target, message)


@router.post("/{cost_id}/delete")
def delete_cost(
    request: Request,
    cost_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    cost = get_or_404(db, ContractCost, cost_id, "成本记录")
    contract_id = cost.contract_id
    detach = photo_svc.delete_photos_for(db, cost_id=cost.id)  # 先清附图，避免外键阻挡
    log_action(db, user, "delete", "contract_costs", cost.id, old=cost, ip=client_ip(request))
    db.delete(cost)
    commit_retry(db)
    suffix = f"（同时删除 {detach} 张附图）" if detach else ""
    return redirect(back or f"/contracts/{contract_id}", "成本记录已删除" + suffix)
