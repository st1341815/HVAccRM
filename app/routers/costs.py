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
    Customer,
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


@router.get("/overview")
def overview(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    only_cost: str = "",
    sort: str = "sign_date",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:view")),
):
    from urllib.parse import urlencode

    sort_options = [
        ("product_type", "产品类型"),
        ("sign_date", "签订日"),
        ("income", "收入"),
        ("cost", "成本"),
        ("gross_profit", "毛利"),
        ("gross_margin", "毛利率"),
        ("received", "已收"),
        ("outstanding", "未收"),
    ]
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

    # 排序：默认按签单日降序，点表头切换字段与升降序
    if sort not in [k for k, _ in sort_options]:
        sort = "sign_date"
    order = "desc" if order == "desc" else "asc"
    sort_key = {
        "product_type": lambda r: (r[0].product_type or ""),
        "sign_date": lambda r: (r[0].sign_date or ""),
        "income": lambda r: r[2].income,
        "cost": lambda r: r[2].cost,
        "gross_profit": lambda r: r[2].gross_profit,
        "gross_margin": lambda r: r[2].gross_margin,
        "received": lambda r: r[2].received,
        "outstanding": lambda r: r[1].outstanding,
    }[sort]
    rows.sort(key=sort_key, reverse=(order == "desc"))

    # 排序链接：保留现有筛选参数，仅切换字段/升降序
    base_params = {
        k: v
        for k, v in request.query_params.items()
        if k in ("date_from", "date_to", "customer_q", "only_cost") and v
    }
    sort_links = {}
    for key, _ in sort_options:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = "/costs/overview?" + urlencode({**base_params, "sort": key, "order": new_order})

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
        sort=sort,
        order=order,
        sort_options=sort_options,
        sort_links=sort_links,
        can_edit=has_perm(user, "cost:edit"),
        today=today_str(),
    )


@router.get("")
def entries(
    request: Request,
    category: str = "",
    supplier_id: str = "",
    installer_id: str = "",
    room: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    sort: str = "spent_at",
    order: str = "desc",
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:view")),
):
    from urllib.parse import urlencode

    sort_options = [
        ("spent_at", "日期"),
        ("category", "费用项目"),
        ("amount", "金额"),
        ("supplier", "供应商"),
        ("installer", "安装师傅"),
        ("remark", "备注"),
        ("created_by", "录入人"),
    ]
    contracts = _visible_contracts(db, user)
    # 房号筛选：匹配完整房号展示串（楼盘名+楼栋#房号 / 楼栋#房号 / 楼栋-单元-房号 / 楼盘名），
    # 兼容用户粘贴「颐园清苑6#401」这类完整房号（对应 room_label_short）。
    if room.strip():
        kw = room.strip()
        cids = {c.customer_id for c in contracts}
        matched: set[int] = set()
        if cids:
            for cust in db.scalars(select(Customer).where(Customer.id.in_(cids))).all():
                if not cust.room:
                    continue
                r = cust.room
                pname = r.project.name if r.project else ""
                variants = (cust.room_label_short, r.short_label, r.label, cust.room_label, pname)
                if any(kw in (v or "") for v in variants):
                    matched.add(cust.id)
        contracts = [c for c in contracts if c.customer_id in matched]
    contract_map = {c.id: c for c in contracts}
    rows = cost_svc.query_costs(
        db,
        contract_ids=list(contract_map),
        category=category,
        supplier_id=parse_int(supplier_id),
        installer_id=parse_int(installer_id),
        date_from=date_from,
        date_to=date_to,
        keyword=keyword,
    )
    suppliers = {s.id: s for s in db.scalars(select(Supplier)).all()}
    users = {u.id: u for u in db.scalars(select(User)).all()}
    # 下拉选项：供应商（启用）、安装师傅（安装工角色优先 + 其他启用账号）
    supplier_options = cost_svc.active_suppliers(db)
    installer_list, other_users = cost_svc.contract_installers(db)
    installer_options = installer_list + other_users

    # 排序：默认按日期降序，点表头切换字段与升降序
    if sort not in [k for k, _ in sort_options]:
        sort = "spent_at"
    order = "desc" if order == "desc" else "asc"

    def _key(r):
        if sort == "spent_at":
            return r.spent_at or ""
        if sort == "category":
            return r.category or ""
        if sort == "amount":
            return r.amount or 0
        if sort == "supplier":
            s = suppliers.get(r.supplier_id) if r.supplier_id else None
            return s.name if s else ""
        if sort == "installer":
            u = users.get(r.installer_id) if r.installer_id else None
            return u.display_name if u else ""
        if sort == "remark":
            return r.remark or ""
        if sort == "created_by":
            u = users.get(r.created_by) if r.created_by else None
            return u.display_name if u else ""
        return ""

    rows.sort(key=_key, reverse=(order == "desc"))

    # 排序链接：保留现有筛选参数，仅切换字段/升降序
    base_params = {
        k: v
        for k, v in request.query_params.items()
        if k in ("category", "supplier_id", "installer_id", "room", "date_from", "date_to", "keyword") and v
    }
    sort_links = {}
    for key, _ in sort_options:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = "/costs?" + urlencode({**base_params, "sort": key, "order": new_order})

    return render(
        request,
        "costs/entries.html",
        rows=rows,
        cost_photos=photo_svc.photos_by_cost(db, [r.id for r in rows]),
        contract_map=contract_map,
        suppliers=suppliers,
        users=users,
        supplier_options=supplier_options,
        installer_options=installer_options,
        categories=COST_CATEGORIES,
        total=round(sum(r.amount or 0 for r in rows), 2),
        filters={
            "category": category,
            "supplier_id": supplier_id,
            "installer_id": installer_id,
            "room": room,
            "date_from": date_from,
            "date_to": date_to,
            "keyword": keyword,
        },
        sort=sort,
        order=order,
        sort_options=sort_options,
        sort_links=sort_links,
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


@router.get("/new")
def new_cost_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    contracts = _visible_contracts(db, user)
    # 合同选择改为可搜索下拉：序列化供前端本地过滤（合同号/房号/客户名）
    contract_options = [
        {
            "id": c.id,
            "no": c.no or f"#{c.id}",
            "room": c.customer.room_label_short if c.customer else "",
            "name": c.customer.name if c.customer else "",
            "product_type": c.product_type or "",
        }
        for c in contracts
    ]
    suppliers = cost_svc.active_suppliers(db)
    installers, other_users = cost_svc.contract_installers(db)
    return render(
        request,
        "costs/new.html",
        contract_options=contract_options,
        suppliers=suppliers,
        installers=installers,
        other_users=other_users,
        categories=COST_CATEGORIES,
    )


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
    if amt == 0:
        return redirect(target, "费用金额不能为 0（退款请填负数）", "err")
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


@router.get("/{cost_id}/edit")
def edit_cost_form(
    request: Request,
    cost_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    cost = get_or_404(db, ContractCost, cost_id, "成本记录")
    contract = db.get(Contract, cost.contract_id)
    suppliers = cost_svc.active_suppliers(db)
    # 若当前关联的供应商已停用，仍加入下拉以便回显
    if cost.supplier_id and not any(s.id == cost.supplier_id for s in suppliers):
        cur = db.get(Supplier, cost.supplier_id)
        if cur:
            suppliers.append(cur)
    installers, other_users = cost_svc.contract_installers(db)
    # 若当前关联的安装师傅已停用，仍加入下拉以便回显
    if cost.installer_id and not any(u.id == cost.installer_id for u in installers + other_users):
        cur = db.get(User, cost.installer_id)
        if cur:
            other_users.append(cur)
    photos = photo_svc.photos_by_cost(db, [cost.id]).get(cost.id, [])
    return render(
        request,
        "costs/edit.html",
        cost=cost,
        contract=contract,
        suppliers=suppliers,
        installers=installers,
        other_users=other_users,
        photos=photos,
        categories=COST_CATEGORIES,
    )


@router.post("/{cost_id}/edit")
async def update_cost(
    request: Request,
    cost_id: int,
    category: str = Form(...),
    amount: str = Form("0"),
    supplier_id: str = Form(""),
    installer_id: str = Form(""),
    remark: str = Form(""),
    spent_at: str = Form(""),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require("cost:edit")),
):
    cost = get_or_404(db, ContractCost, cost_id, "成本记录")
    contract = db.get(Contract, cost.contract_id)
    if not contract:
        return redirect("/costs", "合同不存在", "err")
    category = category.strip()
    if category not in COST_CATEGORIES:
        return redirect(f"/costs/{cost.id}/edit", "费用项目请从下拉中选择", "err")
    amt = parse_float(amount)
    if amt == 0:
        return redirect(f"/costs/{cost.id}/edit", "费用金额不能为 0（退款请填负数）", "err")
    sid = parse_int(supplier_id)
    iid = parse_int(installer_id)
    if category == COST_NEEDS_SUPPLIER:
        if not sid or not db.get(Supplier, sid):
            return redirect(f"/costs/{cost.id}/edit", f"「{COST_NEEDS_SUPPLIER}」必须选择供应商", "err")
    if category == COST_NEEDS_INSTALLER:
        if not iid or not db.get(User, iid):
            return redirect(f"/costs/{cost.id}/edit", f"「{COST_NEEDS_INSTALLER}」必须选择安装师傅", "err")

    old_vals = {
        "category": cost.category,
        "amount": cost.amount,
        "supplier_id": cost.supplier_id,
        "installer_id": cost.installer_id,
        "remark": cost.remark,
        "spent_at": cost.spent_at,
    }
    cost.category = category
    cost.amount = round(amt, 2)
    cost.supplier_id = sid if category == COST_NEEDS_SUPPLIER else None
    cost.installer_id = iid if category == COST_NEEDS_INSTALLER else None
    cost.remark = remark.strip() or None
    cost.spent_at = (spent_at.strip() or today_str())[:10]

    # 新增附图（已有附图的删除走 /photos/{id}/delete）
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

    log_action(db, user, "update", "contract_costs", cost.id, old=old_vals, new=cost, ip=client_ip(request))
    commit_retry(db)
    message = "成本已修改"
    if saved or dedup:
        message += f"；附图新增 {saved} 张、去重 {dedup} 张"
    if errors:
        message += "；附图失败：" + "；".join(errors)
    return redirect("/costs", message, "err" if errors else "ok")
