"""合同管理：明细、收款计划、增项、三数核对视图。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import (
    COST_CATEGORIES,
    PRODUCT_OPTIONS,
    ChangeOrder,
    Contract,
    ContractItem,
    Customer,
    PaymentPlan,
    Photo,
    User,
    now_iso,
    today_str,
)
from ..permissions import (
    can_see_amount,
    customer_scope_conditions,
    has_perm,
    require,
)
from ..services import finance as finance_svc
from ..services import search as search_svc
from ..services import costs as cost_svc
from ..services import numbering as numbering_svc
from ..services import photos as photo_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_float, parse_int

router = APIRouter(prefix="/contracts", tags=["contracts"])
CONTRACT_PHOTO_KIND = "纸质合同"


@router.get("")
def list_contracts(
    request: Request,
    customer_q: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:view")),
):
    stmt = select(Contract).order_by(Contract.sign_date.desc(), Contract.id.desc())
    if customer_q and customer_q.strip():
        cids = search_svc.customer_ids_by_keyword(db, customer_q)
        stmt = stmt.where(Contract.customer_id.in_(cids or [-1]))
    if status:
        stmt = stmt.where(Contract.status == status)
    contracts = list(db.scalars(stmt).all())
    allowed = None
    from ..permissions import visible_customer_ids

    allowed = visible_customer_ids(db, user)
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    rows = [(c, finance_svc.contract_summary(db, c)) for c in contracts]
    totals = {
        "total": round(sum(s.total for _, s in rows), 2),
        "received": round(sum(s.received for _, s in rows), 2),
        "outstanding": round(sum(s.outstanding for _, s in rows), 2),
        "overdue": round(sum(s.overdue for _, s in rows), 2),
    }
    return render(
        request,
        "contracts/list.html",
        rows=rows,
        totals=totals,
        customer_q=customer_q,
        status=status,
        can_amount=can_see_amount(user),
    )


@router.get("/new")
def new_contract_form(
    request: Request,
    customer_id: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    customers = list(db.scalars(select(Customer).order_by(Customer.name)).all())
    allowed = None
    from ..permissions import visible_customer_ids

    allowed = visible_customer_ids(db, user)
    if allowed is not None:
        customers = [c for c in customers if c.id in allowed]
    return render(
        request,
        "contracts/form.html",
        contract=None,
        customers=customers,
        customer_id=customer_id,
        today=today_str(),
        next_no=numbering_svc.next_contract_no(db),
        product_types=PRODUCT_OPTIONS,
    )


@router.post("/new")
async def create_contract(
    request: Request,
    customer_id: str = Form(...),
    sign_date: str = Form(...),
    total_amount: str = Form("0"),
    product_type: str = Form(""),
    notes: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    cid = parse_int(customer_id)
    customer = db.get(Customer, cid) if cid else None
    if not customer:
        return redirect("/contracts", "请选择有效客户", "err")
    ptype = product_type.strip()
    if ptype and ptype not in PRODUCT_OPTIONS:
        return redirect(f"/contracts/new?customer_id={customer.id}", "产品类型请从下拉中选择", "err")
    # 合同号由服务端生成：ONE + 8 位年月日 + 4 位顺数（按年重置），前端不可改写
    no = numbering_svc.reserve_contract_no(db)
    contract = Contract(
        customer_id=customer.id,
        no=no,
        sign_date=sign_date or today_str(),
        total_amount=parse_float(total_amount),
        product_type=ptype or None,
        discount=0,
        status="active",
        notes=notes.strip() or None,
        created_by=user.id,
        created_at=now_iso(),
    )
    db.add(contract)
    db.flush()
    log_action(db, user, "create", "contracts", contract.id, new=contract, ip=client_ip(request))

    saved, dedup, errors = 0, 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=customer.id,
            kind=CONTRACT_PHOTO_KIND,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            contract_id=contract.id,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                dedup += 1
            else:
                saved += 1
    commit_retry(db)
    message = f"合同 {contract.no} 已创建"
    if saved or dedup:
        message += f"；纸质合同图片新增 {saved} 张、去重 {dedup} 张"
    if errors:
        return redirect(f"/contracts/{contract.id}", message + "；失败：" + "；".join(errors), "err")
    return redirect(f"/contracts/{contract.id}", message)


@router.post("/{contract_id}/photos")
async def upload_contract_photos(
    request: Request,
    contract_id: int,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    """给已有合同补传纸质合同照片。"""
    contract = get_or_404(db, Contract, contract_id, "合同")
    saved, dedup, errors = 0, 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=contract.customer_id,
            kind=CONTRACT_PHOTO_KIND,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            contract_id=contract.id,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                dedup += 1
            else:
                saved += 1
            log_action(
                db,
                user,
                "upload",
                "photos",
                None,
                new={"file": f.filename, "kind": CONTRACT_PHOTO_KIND, "contract": contract.id},
                ip=client_ip(request),
            )
    commit_retry(db)
    message = f"纸质合同图片：新增 {saved} 张，去重 {dedup} 张"
    if errors:
        message += "；失败 " + "；".join(errors)
    return redirect(f"/contracts/{contract.id}", message, "err" if errors else "ok")


@router.get("/{contract_id}")
def contract_detail(
    request: Request,
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:view")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    from ..permissions import visible_customer_ids

    allowed = visible_customer_ids(db, user)
    if allowed is not None and contract.customer_id not in allowed:
        return render(request, "403.html", status_code=403)
    summary = finance_svc.contract_summary(db, contract)
    items = list(contract.items)
    payments = sorted(contract.payments, key=lambda p: p.paid_at or "", reverse=True)
    changes = list(contract.change_orders)
    users = {u.id: u for u in db.scalars(select(User)).all()}
    contract_photos = list(
        db.scalars(
            select(Photo)
            .where(Photo.contract_id == contract.id)
            .where(Photo.payment_id.is_(None))  # 收款截图单独归到各自收款行，不混进纸质合同区
            .order_by(Photo.created_at.desc(), Photo.id.desc())
        ).all()
    )
    payment_photos = photo_svc.photos_by_payment(db, [p.id for p in payments])
    # 成本与利润：需 cost:view 且不属金额隔离角色
    can_cost = has_perm(user, "cost:view") and can_see_amount(user)
    costs = list(contract.costs) if can_cost else []
    profit = cost_svc.profit_summary(db, contract) if can_cost else None
    cost_photos = photo_svc.photos_by_cost(db, [c.id for c in costs]) if can_cost else {}
    installers, other_users = cost_svc.contract_installers(db) if can_cost else ([], [])
    return render(
        request,
        "contracts/detail.html",
        contract=contract,
        summary=summary,
        items=items,
        payments=payments,
        changes=changes,
        users=users,
        contract_photos=contract_photos,
        payment_photos=payment_photos,
        product_types=PRODUCT_OPTIONS,
        costs=costs,
        cost_photos=cost_photos,
        profit=profit,
        cost_categories=COST_CATEGORIES,
        suppliers=cost_svc.active_suppliers(db) if can_cost else [],
        installers=installers,
        other_users=other_users,
        cost_users={u.id: u for u in users.values()},
        can_cost=can_cost,
        can_amount=can_see_amount(user),
        can_edit=has_perm(user, "contract:edit"),
        can_pay=has_perm(user, "payment:edit"),
        can_refund=has_perm(user, "payment:refund"),
        can_delete_photo=has_perm(user, "photo:delete"),
        today=today_str(),
    )


@router.post("/{contract_id}/edit")
def update_contract(
    request: Request,
    contract_id: int,
    no: str = Form(""),
    sign_date: str = Form(""),
    total_amount: str = Form("0"),
    product_type: str = Form(""),
    status: str = Form("active"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    no = no.strip() or None
    if no:
        dup = db.scalars(
            select(Contract).where(Contract.no == no).where(Contract.id != contract.id)
        ).first()
        if dup:
            return redirect(f"/contracts/{contract_id}", f"合同号 {no} 已被其他合同占用", "err")
    before = {
        "no": contract.no,
        "sign_date": contract.sign_date,
        "total_amount": contract.total_amount,
        "product_type": contract.product_type,
        "status": contract.status,
        "notes": contract.notes,
    }
    contract.no = no
    contract.sign_date = sign_date or contract.sign_date
    contract.total_amount = parse_float(total_amount)
    ptype = (product_type or "").strip()
    if ptype and ptype not in PRODUCT_OPTIONS:
        return redirect(f"/contracts/{contract_id}", "产品类型请从下拉中选择", "err")
    contract.product_type = ptype or None
    contract.status = status or "active"
    contract.notes = notes.strip() or None
    log_action(db, user, "update", "contracts", contract.id, old=before, new=contract, ip=client_ip(request))
    finance_svc.allocate_plans(db, contract)
    commit_retry(db)
    return redirect(f"/contracts/{contract_id}", "合同已更新")


@router.post("/{contract_id}/items")
def add_item(
    request: Request,
    contract_id: int,
    product_name: str = Form(...),
    spec: str = Form(""),
    qty: str = Form("0"),
    unit: str = Form(""),
    unit_price: str = Form("0"),
    amount: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    q = parse_float(qty)
    up = parse_float(unit_price)
    amt = parse_float(amount) if amount.strip() else round(q * up, 2)
    item = ContractItem(
        contract_id=contract_id,
        product_name=product_name.strip(),
        spec=spec.strip() or None,
        qty=q,
        unit=unit.strip() or None,
        unit_price=up,
        amount=amt,
        notes=notes.strip() or None,
    )
    db.add(item)
    db.flush()
    log_action(db, user, "create", "contract_items", item.id, new=item, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/contracts/{contract_id}", "合同明细已添加")


@router.post("/items/{item_id}/delete")
def delete_item(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    item = get_or_404(db, ContractItem, item_id, "合同明细")
    cid = item.contract_id
    log_action(db, user, "delete", "contract_items", item.id, old=item, ip=client_ip(request))
    db.delete(item)
    commit_retry(db)
    return redirect(f"/contracts/{cid}", "合同明细已删除")


@router.post("/{contract_id}/plans")
def add_plan(
    request: Request,
    contract_id: int,
    label: str = Form(...),
    amount: str = Form("0"),
    due_date: str = Form(""),
    sort_order: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    order = parse_int(sort_order) or (max([p.sort_order or 0 for p in contract.plans], default=0) + 1)
    plan = PaymentPlan(
        contract_id=contract_id,
        label=label.strip(),
        amount=parse_float(amount),
        due_date=due_date.strip() or None,
        sort_order=order,
        status="pending",
    )
    db.add(plan)
    db.flush()
    log_action(db, user, "create", "payment_plans", plan.id, new=plan, ip=client_ip(request))
    finance_svc.allocate_plans(db, contract)
    commit_retry(db)
    return redirect(f"/contracts/{contract_id}", "应收计划已添加")


@router.post("/plans/{plan_id}/delete")
def delete_plan(
    request: Request,
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    plan = get_or_404(db, PaymentPlan, plan_id, "收款计划")
    cid = plan.contract_id
    log_action(db, user, "delete", "payment_plans", plan.id, old=plan, ip=client_ip(request))
    db.delete(plan)
    commit_retry(db)
    return redirect(f"/contracts/{cid}", "应收计划已删除")


@router.post("/{contract_id}/change-orders")
def add_change_order(
    request: Request,
    contract_id: int,
    reason: str = Form(""),
    amount: str = Form("0"),
    due_date: str = Form(""),
    sync_plan: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    amt = parse_float(amount)
    co = ChangeOrder(
        contract_id=contract_id,
        reason=reason.strip() or "增项",
        amount=amt,
        approved_at=now_iso(),
        approved_by=user.id,
        created_at=now_iso(),
    )
    db.add(co)
    db.flush()
    if sync_plan and amt:
        order = max([p.sort_order or 0 for p in contract.plans], default=0) + 1
        db.add(
            PaymentPlan(
                contract_id=contract_id,
                label=f"增项-{co.reason}",
                amount=amt,
                due_date=due_date.strip() or None,
                sort_order=order,
                status="pending",
            )
        )
    log_action(db, user, "create", "change_orders", co.id, new=co, ip=client_ip(request))
    finance_svc.allocate_plans(db, contract)
    commit_retry(db)
    return redirect(f"/contracts/{contract_id}", f"增项 {amt:,.2f} 已登记")


@router.post("/change-orders/{co_id}/delete")
def delete_change_order(
    request: Request,
    co_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:edit")),
):
    co = get_or_404(db, ChangeOrder, co_id, "增项")
    cid = co.contract_id
    log_action(db, user, "delete", "change_orders", co.id, old=co, ip=client_ip(request))
    db.delete(co)
    commit_retry(db)
    return redirect(f"/contracts/{cid}", "增项已删除")
