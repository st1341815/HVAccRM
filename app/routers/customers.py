"""客户管理：CRUD、联系人、共享、按数据范围过滤、FTS 搜索、自动建工序任务。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import (
    Contact,
    Customer,
    CustomerShare,
    Project,
    Room,
    User,
    encode_products,
    now_iso,
)
from ..permissions import (
    DEFAULT_SCOPE,
    can_edit_customer,
    can_see_amount,
    can_view_customer,
    customer_scope_conditions,
    has_perm,
    require,
    require_any,
    visible_customer_ids,
)
from ..services import costs as cost_svc
from ..services import finance as finance_svc
from ..services import photos as photo_svc
from ..services import search as search_svc
from ..services import tasks as task_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_int

router = APIRouter(prefix="/customers", tags=["customers"])

CUSTOMER_TYPES = ["家装业主", "工程客户", "经销商", "老客户转介"]
SOURCES = ["自然到店", "老客户转介", "楼盘扫楼", "设计师推荐", "装修公司", "线上咨询", "其他"]
LEVELS = ["A", "B", "C"]
STATUSES = ["active", "won", "paused", "lost"]


@router.get("")
def list_customers(
    request: Request,
    q: str = "",
    status: str = "",
    level: str = "",
    source: str = "",
    owner: str = "",
    project_id: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:view")),
):
    scope = customer_scope_conditions(db, user)
    customers = search_svc.search_customers(db, q, scope_cond=scope, limit=300)
    if status:
        customers = [c for c in customers if (c.status or "") == status]
    if level:
        customers = [c for c in customers if (c.level or "") == level]
    if source:
        customers = [c for c in customers if (c.source or "") == source]
    if owner:
        customers = [c for c in customers if str(c.owner_id) == owner]
    if project_id:
        customers = [c for c in customers if c.room and str(c.room.project_id) == project_id]

    owners = {u.id: u for u in db.scalars(select(User)).all()}
    projects = list(db.scalars(select(Project).order_by(Project.name)).all())
    show_money = has_perm(user, "payment:view") and can_see_amount(user)
    summaries = {c.id: finance_svc.customer_summary(db, c) for c in customers} if show_money else {}
    return render(
        request,
        "customers/list.html",
        customers=customers,
        q=q,
        status=status,
        level=level,
        source=source,
        owner=owner,
        project_id=project_id,
        owners=owners,
        projects=projects,
        summaries=summaries,
        show_money=show_money,
        sources=SOURCES,
        levels=LEVELS,
        statuses=STATUSES,
    )


@router.get("/search.json")
def search_json(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:view")),
):
    scope = customer_scope_conditions(db, user)
    rows = search_svc.search_customers(db, q, scope_cond=scope, limit=20)
    return JSONResponse(
        {
            "items": [
                {
                    "id": c.id,
                    "name": c.name,
                    "room": c.room_label,
                    "status": c.status,
                    "phone": (c.primary_contact.phone if c.primary_contact else None),
                }
                for c in rows
            ]
        }
    )


@router.get("/new")
def new_customer_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    return render(
        request,
        "customers/form.html",
        customer=None,
        projects=list(db.scalars(select(Project).order_by(Project.name)).all()),
        owners=list(db.scalars(select(User).where(User.is_active == 1)).all()),
        types=CUSTOMER_TYPES,
        sources=SOURCES,
        levels=LEVELS,
    )


@router.post("/new")
def create_customer(
    request: Request,
    name: str = Form(...),
    type: str = Form(""),
    phone: str = Form(""),
    wechat: str = Form(""),
    products: list[str] = Form([]),
    source: str = Form(""),
    level: str = Form(""),
    status: str = Form("active"),
    room_id: str = Form(""),
    notes: str = Form(""),
    owner_id: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    contact_wechat: str = Form(""),
    default_assignee: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    if not name.strip():
        return render(
            request,
            "customers/form.html",
            customer=None,
            error="客户姓名必填",
            projects=list(db.scalars(select(Project).order_by(Project.name)).all()),
            owners=list(db.scalars(select(User).where(User.is_active == 1)).all()),
            types=CUSTOMER_TYPES,
            sources=SOURCES,
            levels=LEVELS,
        )
    scope = user.data_scope or DEFAULT_SCOPE.get(user.role, "self")
    owner = parse_int(owner_id) or 0
    if scope != "all" or not owner:
        owner = user.id
    customer = Customer(
        owner_id=owner,
        name=name.strip(),
        type=type or None,
        phone=phone.strip() or None,
        wechat=wechat.strip() or None,
        products=encode_products(products),
        source=source or None,
        level=level or None,
        status=status or "active",
        room_id=parse_int(room_id),
        notes=notes.strip() or None,
        created_by=user.id,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    db.add(customer)
    db.flush()
    if contact_name.strip():
        db.add(
            Contact(
                customer_id=customer.id,
                name=contact_name.strip(),
                phone=contact_phone.strip() or None,
                wechat=contact_wechat.strip() or None,
                is_primary=1,
            )
        )
    assignee = parse_int(default_assignee) or customer.owner_id
    created_tasks = task_svc.generate_tasks(db, customer, assignee_id=assignee)
    log_action(db, user, "create", "customers", customer.id, new=customer, ip=client_ip(request))
    commit_retry(db)
    return redirect(
        f"/customers/{customer.id}",
        f"客户「{customer.name}」已创建，自动生成 {len(created_tasks)} 个工序任务",
    )


@router.get("/{customer_id}")
def customer_detail(
    request: Request,
    customer_id: int,
    tab: str = "overview",
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not can_view_customer(db, user, customer):
        return render(request, "403.html", status_code=403)

    tasks = sorted(customer.tasks, key=lambda t: t.sort_order)
    contacts = list(customer.contacts)
    contracts = list(customer.contracts)
    summary = finance_svc.customer_summary(db, customer) if has_perm(user, "contract:view") else None
    plans = []
    show_cost = has_perm(user, "cost:view") and can_see_amount(user)
    plan_rows = []
    for c in contracts:
        s = finance_svc.contract_summary(db, c)
        plan_rows.append((c, s, cost_svc.profit_summary(db, c) if show_cost else None))

    assignees = list(db.scalars(select(User).where(User.is_active == 1)).all())
    shares = list(db.scalars(select(CustomerShare).where(CustomerShare.customer_id == customer.id)).all())
    share_users = {u.id: u for u in db.scalars(select(User)).all()}
    return render(
        request,
        "customers/detail.html",
        customer=customer,
        tab=tab,
        tasks=tasks,
        contacts=contacts,
        contracts=contracts,
        summary=summary,
        plan_rows=plan_rows,
        show_cost=show_cost,
        assignees=assignees,
        shares=shares,
        share_users=share_users,
        progress=task_svc.progress(tasks),
        delayed=task_svc.delay_count(db, tasks),
        task_photos=photo_svc.photos_by_task(db, [t.id for t in tasks]),
        stage_requires_photo=task_svc.stage_photo_requirements(db),
        stage_kind=task_svc.STAGE_PHOTO_KIND,
        can_upload_photo=has_perm(user, "photo:upload"),
        back_path=str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""),
        can_edit=can_edit_customer(db, user, customer),
        can_delete=has_perm(user, "customer:delete"),
        sources=SOURCES,
    )


@router.get("/{customer_id}/edit")
def edit_customer_form(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not can_edit_customer(db, user, customer):
        return render(request, "403.html", status_code=403)
    return render(
        request,
        "customers/form.html",
        customer=customer,
        projects=list(db.scalars(select(Project).order_by(Project.name)).all()),
        owners=list(db.scalars(select(User).where(User.is_active == 1)).all()),
        types=CUSTOMER_TYPES,
        sources=SOURCES,
        levels=LEVELS,
    )


@router.post("/{customer_id}/edit")
def update_customer(
    request: Request,
    customer_id: int,
    name: str = Form(...),
    type: str = Form(""),
    phone: str = Form(""),
    wechat: str = Form(""),
    products: list[str] = Form([]),
    source: str = Form(""),
    level: str = Form(""),
    status: str = Form("active"),
    room_id: str = Form(""),
    notes: str = Form(""),
    owner_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not can_edit_customer(db, user, customer):
        return render(request, "403.html", status_code=403)
    before = {
        "name": customer.name,
        "type": customer.type,
        "phone": customer.phone,
        "wechat": customer.wechat,
        "products": customer.products,
        "source": customer.source,
        "level": customer.level,
        "status": customer.status,
        "room_id": customer.room_id,
        "owner_id": customer.owner_id,
        "notes": customer.notes,
    }
    customer.name = name.strip() or customer.name
    customer.type = type or None
    customer.phone = phone.strip() or None
    customer.wechat = wechat.strip() or None
    customer.products = encode_products(products)
    customer.source = source or None
    customer.level = level or None
    customer.status = status or "active"
    customer.room_id = parse_int(room_id)
    customer.notes = notes.strip() or None
    if (user.data_scope or "") == "all":
        customer.owner_id = parse_int(owner_id) or customer.owner_id
    customer.updated_at = now_iso()
    log_action(db, user, "update", "customers", customer.id, old=before, new=customer, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/customers/{customer.id}", "客户资料已更新")


@router.post("/{customer_id}/delete")
def delete_customer(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:delete")),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    # 先清理该客户的所有照片（含纸质合同 / 付款截图 / 成本凭证 / 施工照片），
    # 否则它们引用的合同、收款、成本记录被删除时会触发外键约束
    detached = photo_svc.delete_photos_for(db, customer_id=customer.id)
    log_action(db, user, "delete", "customers", customer.id, old=customer, ip=client_ip(request))
    db.delete(customer)
    commit_retry(db)
    suffix = f"（含 {detached} 张照片）" if detached else ""
    return redirect("/customers", f"客户「{customer.name}」及其关联数据已删除{suffix}")


@router.post("/{customer_id}/regenerate-tasks")
def regenerate_tasks(
    request: Request,
    customer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    created = task_svc.generate_tasks(db, customer)
    log_action(db, user, "generate_tasks", "tasks", customer.id, new={"count": len(created)}, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/customers/{customer_id}?tab=tasks", f"已补齐 {len(created)} 个工序任务")


# ------------------------------------------------------------------ 联系人
@router.post("/{customer_id}/contacts")
def add_contact(
    request: Request,
    customer_id: int,
    name: str = Form(...),
    title: str = Form(""),
    phone: str = Form(""),
    wechat: str = Form(""),
    email: str = Form(""),
    is_primary: str = Form(""),
    birthday: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not can_edit_customer(db, user, customer):
        return render(request, "403.html", status_code=403)
    if not name.strip():
        return redirect(f"/customers/{customer_id}?tab=contacts", "联系人姓名必填", "err")
    if is_primary:
        for c in customer.contacts:
            c.is_primary = 0
    contact = Contact(
        customer_id=customer_id,
        name=name.strip(),
        title=title.strip() or None,
        phone=phone.strip() or None,
        wechat=wechat.strip() or None,
        email=email.strip() or None,
        is_primary=1 if is_primary else 0,
        birthday=birthday.strip() or None,
    )
    db.add(contact)
    db.flush()
    if not customer.contacts:
        contact.is_primary = 1
    log_action(db, user, "create", "contacts", contact.id, new=contact, ip=client_ip(request))
    commit_retry(db)
    return redirect(f"/customers/{customer_id}?tab=contacts", "联系人已添加")


@router.post("/contacts/{contact_id}/delete")
def delete_contact(
    request: Request,
    contact_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("customer:edit")),
):
    contact = get_or_404(db, Contact, contact_id, "联系人")
    cid = contact.customer_id
    log_action(db, user, "delete", "contacts", contact.id, old=contact, ip=client_ip(request))
    db.delete(contact)
    commit_retry(db)
    return redirect(f"/customers/{cid}?tab=contacts", "联系人已删除")


# ---------------------------------------------------------------- 客户共享
@router.post("/{customer_id}/share")
def share_customer(
    request: Request,
    customer_id: int,
    user_id: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    can_share = has_perm(user, "admin:user") or can_edit_customer(db, user, customer)
    if not can_share:
        return render(request, "403.html", status_code=403)
    target = parse_int(user_id)
    if not target:
        return redirect(f"/customers/{customer_id}", "请选择要共享的用户", "err")
    exists = db.scalars(
        select(CustomerShare)
        .where(CustomerShare.customer_id == customer_id)
        .where(CustomerShare.user_id == target)
    ).first()
    if not exists:
        db.add(CustomerShare(customer_id=customer_id, user_id=target, granted_by=user.id))
        log_action(
            db,
            user,
            "share",
            "customer_shares",
            customer_id,
            new={"user_id": target},
            ip=client_ip(request),
        )
        commit_retry(db)
    return redirect(f"/customers/{customer_id}", "共享设置已更新")


@router.post("/{customer_id}/unshare")
def unshare_customer(
    request: Request,
    customer_id: int,
    user_id: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not (has_perm(user, "admin:user") or can_edit_customer(db, user, customer)):
        return render(request, "403.html", status_code=403)
    row = db.scalars(
        select(CustomerShare)
        .where(CustomerShare.customer_id == customer_id)
        .where(CustomerShare.user_id == parse_int(user_id))
    ).first()
    if row:
        log_action(db, user, "unshare", "customer_shares", customer_id, old=row, ip=client_ip(request))
        db.delete(row)
        commit_retry(db)
    return redirect(f"/customers/{customer_id}", "已取消共享")
