"""合同管理：明细、收款计划、增项、三数核对视图。"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import (
    COST_CATEGORIES,
    PRODUCT_OPTIONS,
    Case,
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
from ..services import cases as cases_svc
from ..services import costs as cost_svc
from ..services import numbering as numbering_svc
from ..services import photos as photo_svc
from ..services import settlement as settlement_svc
from ..services import tasks as task_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_float, parse_int, room_sort_key

router = APIRouter(prefix="/contracts", tags=["contracts"])

# 合同台账可排序列（顺序即表头顺序）。金额 / 结算两类按权限在路由里再过滤。
_BASE_SORTS: list[tuple[str, str]] = [
    ("room", "房号"),
    ("product_type", "产品"),
    ("sign_date", "签订日"),
]
_AMOUNT_SORTS: list[tuple[str, str]] = [
    ("total", "总额"),
    ("received", "已收"),
    ("outstanding", "未收"),
    ("recovery_rate", "回款率"),
]
_SETTLE_SORTS: list[tuple[str, str]] = [("settled", "结算")]


CONTRACT_PHOTO_KIND = "纸质合同"


def _back_target(back: str, fallback: str) -> str:
    """只接受站内相对路径，避免 back 被拿来做开放重定向。"""
    b = (back or "").strip()
    if b.startswith("/") and not b.startswith("//"):
        return b
    return fallback


def _settle_redirect(back: str, msg: str, level: str = "ok"):
    return redirect(_back_target(back, "/contracts"), msg, level)


def _frozen(contract: Contract, action: str, target: str):
    """已结算合同拦截写操作；返回 RedirectResponse 或 None。"""
    reason = settlement_svc.block_reason(contract, action)
    if reason:
        return redirect(target, reason, "err")
    return None


@router.get("")
def list_contracts(
    request: Request,
    customer_q: str = "",
    status: str = "",
    settled: str = "",
    sort: str = "sign_date",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("contract:view")),
):
    stmt = select(Contract).order_by(Contract.sign_date.desc(), Contract.id.desc())
    if customer_q and customer_q.strip():
        cids = search_svc.customer_ids_by_keyword(db, customer_q)
        stmt = stmt.where(Contract.customer_id.in_(cids or [-1]))
    if status:
        stmt = stmt.where(Contract.status == status)
    if settled == "1":
        stmt = stmt.where(Contract.settled == 1)
    elif settled == "0":
        # 存量数据在迁移前可能为 NULL，按未结算处理
        stmt = stmt.where(or_(Contract.settled == 0, Contract.settled.is_(None)))
    contracts = list(db.scalars(stmt).all())
    allowed = None
    from ..permissions import visible_customer_ids

    allowed = visible_customer_ids(db, user)
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    rows = [(c, finance_svc.contract_summary(db, c)) for c in contracts]

    # ---- 排序（全部在内存里做：已收 / 未收 / 回款率来自 finance 汇总，SQL 里没有现成列）----
    can_amount = can_see_amount(user)
    can_settle = has_perm(user, "settlement:edit")
    sort_options = list(_BASE_SORTS)
    if can_amount:
        sort_options += _AMOUNT_SORTS
    if can_settle:
        sort_options += _SETTLE_SORTS

    def _room(r):
        c = r[0]
        if not c.customer:
            return room_sort_key("")
        return room_sort_key(c.customer.room_label_short or c.customer.name or "")

    sort_keys = {
        "room": _room,
        "product_type": lambda r: (r[0].product_type or ""),
        "sign_date": lambda r: (r[0].sign_date or ""),
        "total": lambda r: r[1].total,
        "received": lambda r: r[1].received,
        "outstanding": lambda r: r[1].outstanding,
        "recovery_rate": lambda r: r[1].recovery_rate,
        "settled": lambda r: (1 if r[0].is_settled else 0),
    }
    if sort not in dict(sort_options) or sort not in sort_keys:
        sort = "sign_date"
    order = "desc" if order == "desc" else "asc"
    rows.sort(key=sort_keys[sort], reverse=(order == "desc"))

    # 排序链接：带上现有筛选条件（点列头不丢筛选），首点 desc、再点 asc
    base_params = {
        k: v
        for k, v in {"customer_q": customer_q, "status": status, "settled": settled}.items()
        if v
    }
    sort_links = {}
    for key, _label in sort_options:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = "/contracts?" + urlencode({**base_params, "sort": key, "order": new_order})

    totals = {
        "total": round(sum(s.total for _, s in rows), 2),
        "received": round(sum(s.received for _, s in rows), 2),
        "outstanding": round(sum(s.outstanding for _, s in rows), 2),
        "overdue": round(sum(s.overdue for _, s in rows), 2),
    }
    # 当前筛选条件的查询串，供行内结算按钮回跳时保留筛选
    keep_qs = request.url.query
    return render(
        request,
        "contracts/list.html",
        rows=rows,
        totals=totals,
        customer_q=customer_q,
        status=status,
        settled=settled,
        keep_qs=keep_qs,
        sort=sort,
        order=order,
        sort_options=sort_options,
        sort_links=sort_links,
        can_amount=can_amount,
        can_settle=can_settle,
        # render() 会用 request.state.user 重算 can_amount 并覆盖同名 ctx；
        # 表头（sort_options）与行（金额/结算单元格）必须同源，否则会错列，故另起名。
        show_amount=can_amount,
        show_settle=can_settle,
        can_delete_contract=has_perm(user, "admin:setting"),
    )


@router.post("/{contract_id}/settle")
def settle_contract(
    request: Request,
    contract_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("settlement:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    ok, why = settlement_svc.can_settle(db, contract)
    if not ok:
        return _settle_redirect(back, why.rstrip("。"), "err")
    before, changed = settlement_svc.set_settled(db, contract, True, user)
    if not changed:
        commit_retry(db)
        return _settle_redirect(back, "该合同已是结算状态", "err")
    log_action(db, user, "settle", "contracts", contract.id, old=before, new=contract, ip=client_ip(request))
    commit_retry(db)
    return _settle_redirect(back, f"合同 {contract.no or ('#' + str(contract.id))} 已标记结算，收款/费用/施工新增已冻结")


@router.post("/{contract_id}/unsettle")
def unsettle_contract(
    request: Request,
    contract_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("settlement:edit")),
):
    contract = get_or_404(db, Contract, contract_id, "合同")
    before, changed = settlement_svc.set_settled(db, contract, False, user)
    if not changed:
        commit_retry(db)
        return _settle_redirect(back, "该合同不是结算状态", "err")
    log_action(db, user, "unsettle", "contracts", contract.id, old=before, new=contract, ip=client_ip(request))
    commit_retry(db)
    return _settle_redirect(back, f"合同 {contract.no or ('#' + str(contract.id))} 已取消结算，可继续录入")


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
    # 已锁定客户（任一合同「前期施工」已完工）从新建合同下拉中过滤，降低误选概率
    customers = [c for c in customers if not c.locked]
    # 客户选择改为可搜索下拉：把可见客户序列化供前端本地过滤（含姓名/房号/手机号）
    customer_options = [
        {"id": c.id, "name": c.name, "room": c.room_label or "", "phone": c.phone or ""}
        for c in customers
    ]
    return render(
        request,
        "contracts/form.html",
        contract=None,
        customers=customers,
        customer_options=customer_options,
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
    # 每份合同独立生成自己的施工工序（前期施工 → 后期施工 → 调试验收）
    created_tasks = task_svc.generate_contract_tasks(db, contract)
    log_action(db, user, "create", "contracts", contract.id, new=contract, ip=client_ip(request))

    # 合同创建成功 → 客户状态同步为「已成交」
    # 幂等：本来就是「已成交」时不重复写审计；状态变化单独留一条 customers 审计
    prev_status = customer.status or "active"
    customer.status = "won"
    status_changed = prev_status != customer.status
    if status_changed:
        log_action(
            db, user, "update", "customers", customer.id,
            old={"status": prev_status}, new={"status": customer.status},
            ip=client_ip(request),
        )

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
    if status_changed:
        message += "；客户状态已更新为「已成交」"
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
        can_settle=has_perm(user, "settlement:edit"),
        can_pay=has_perm(user, "payment:edit"),
        can_refund=has_perm(user, "payment:refund"),
        can_delete_photo=has_perm(user, "photo:delete"),
        can_delete_contract=has_perm(user, "admin:setting"),
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
    # 已结算：金额相关字段冻结，仅允许改备注（备注不进核算口径）
    if settlement_svc.is_settled(contract):
        ptype_new = (product_type or "").strip() or None
        frozen_changes = []
        if abs(parse_float(total_amount) - (contract.total_amount or 0)) > 0.005:
            frozen_changes.append("合同额")
        if sign_date and sign_date != (contract.sign_date or ""):
            frozen_changes.append("签订日")
        if ptype_new != (contract.product_type or None):
            frozen_changes.append("产品类型")
        if (status or "active") != (contract.status or "active"):
            frozen_changes.append("状态")
        if no != contract.no:
            frozen_changes.append("合同号")
        if frozen_changes:
            return redirect(
                f"/contracts/{contract_id}",
                f"该合同已结算，{'、'.join(frozen_changes)}不可修改；如需调整请先取消结算",
                "err",
            )
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
    if (frozen := _frozen(contract, "edit", f"/contracts/{contract_id}")):
        return frozen
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
    if item.contract and (frozen := _frozen(item.contract, "edit", f"/contracts/{cid}")):
        return frozen
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
    if (frozen := _frozen(contract, "edit", f"/contracts/{contract_id}")):
        return frozen
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
    if plan.contract and (frozen := _frozen(plan.contract, "edit", f"/contracts/{cid}")):
        return frozen
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
    if (frozen := _frozen(contract, "edit", f"/contracts/{contract_id}")):
        return frozen
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
    if co.contract and (frozen := _frozen(co.contract, "edit", f"/contracts/{cid}")):
        return frozen
    log_action(db, user, "delete", "change_orders", co.id, old=co, ip=client_ip(request))
    db.delete(co)
    commit_retry(db)
    return redirect(f"/contracts/{cid}", "增项已删除")


@router.post("/{contract_id}/delete")
def delete_contract(
    request: Request,
    contract_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    """删除合同（破坏性，仅管理员）：连同其收款、成本、施工工序、对外案例与照片一并清除。"""
    contract = get_or_404(db, Contract, contract_id, "合同")
    if settlement_svc.is_settled(contract):
        return redirect(
            f"/contracts/{contract.id}",
            "该合同已结算，不能删除；如需删除请先取消结算",
            "err",
        )
    no = contract.no or f"#{contract.id}"

    payment_ids = [p.id for p in contract.payments]
    cost_ids = [c.id for c in contract.costs]
    task_ids = [t.id for t in contract.tasks]

    # 1) 清理所有关联照片（纸质合同 / 收款截图 / 成本凭证 / 施工照片），避免外键约束阻挡删除
    photo_conds = [Photo.contract_id == contract.id]
    if payment_ids:
        photo_conds.append(Photo.payment_id.in_(payment_ids))
    if cost_ids:
        photo_conds.append(Photo.cost_id.in_(cost_ids))
    if task_ids:
        photo_conds.append(Photo.task_id.in_(task_ids))
    for ph in db.scalars(select(Photo).where(or_(*photo_conds))).all():
        photo_svc.delete_photo(db, ph)

    # 2) 删除该合同施工工序对应生成的对外案例（连同展示图与物理文件）
    if task_ids:
        for case in db.scalars(select(Case).where(Case.task_id.in_(task_ids))).all():
            cases_svc.delete_case(db, case)

    log_action(db, user, "delete", "contracts", contract.id, old=contract, ip=client_ip(request))
    db.delete(contract)  # cascade 删除合同明细/收款计划/成本/收款/增项/施工工序
    commit_retry(db)
    return redirect("/contracts", f"合同 {no} 已删除（连同其收款、成本、工序、案例与照片）")
