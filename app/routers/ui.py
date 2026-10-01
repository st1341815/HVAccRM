"""首页看板与 HTMX 片段。"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user_or_redirect
from ..db import get_db
from ..models import Contract, Customer, Photo, Project, Room, Task, User, today_str
from ..permissions import can_see_amount, customer_scope_conditions, has_perm, task_scope_conditions
from ..services import finance as finance_svc
from ..services import search as search_svc
from ..services import tasks as task_svc
from ..templating import render

router = APIRouter(tags=["ui"])


@router.get("/")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    show_money = has_perm(user, "payment:view") and can_see_amount(user)
    t_scope = task_scope_conditions(db, user)
    c_scope = customer_scope_conditions(db, user)
    today = today_str()
    week_end = (date.today() + timedelta(days=7)).isoformat()

    def _tasks(extra=None):
        stmt = select(Task).where(Task.status.notin_(["done", "skipped"]))
        if t_scope is not None:
            stmt = stmt.where(t_scope)
        if extra is not None:
            stmt = stmt.where(extra)
        return list(db.scalars(stmt.order_by(Task.planned_end)).all())

    open_tasks = _tasks()
    delayed = [t for t in open_tasks if task_svc.is_delayed(t, today)] if has_perm(user, "task:view") else []
    due_today = [t for t in open_tasks if t.planned_end == today] if has_perm(user, "task:view") else []
    due_week = (
        [t for t in open_tasks if t.planned_end and today < t.planned_end <= week_end]
        if has_perm(user, "task:view")
        else []
    )

    cust_stmt = select(Customer)
    if c_scope is not None:
        cust_stmt = cust_stmt.where(c_scope)
    customers = list(db.scalars(cust_stmt).all())

    month = today[:7]
    finance = None
    if show_money:
        contracts = list(db.scalars(select(Contract)).all())
        ids = {c.id for c in customers}
        contracts = [c for c in contracts if c.customer_id in ids] if c_scope is not None else contracts
        signed = round(sum(c.total_amount or 0 for c in contracts if (c.sign_date or "").startswith(month)), 2)
        received = 0.0
        overdue = 0.0
        for c in contracts:
            s = finance_svc.contract_summary(db, c)
            overdue += s.overdue
            for p in c.payments:
                if (p.paid_at or "").startswith(month):
                    received += p.amount or 0
        finance = {
            "signed_month": signed,
            "received_month": round(received, 2),
            "rate": round(received / signed * 100, 1) if signed else 0.0,
            "overdue_total": round(overdue, 2),
            "debtors": finance_svc.top_debtors(db, 5),
        }

    # 施工概况：有未完工工序的客户数
    site_ids = {t.customer_id for t in open_tasks}
    in_progress = len(site_ids) if has_perm(user, "task:view") else 0

    since = (date.today() - timedelta(days=30)).isoformat()
    new_customers = [c for c in customers if (c.created_at or "") >= since]
    source_dist: dict[str, int] = {}
    for c in new_customers:
        source_dist[c.source or "未填写"] = source_dist.get(c.source or "未填写", 0) + 1

    project_rows = []
    if has_perm(user, "customer:view"):
        proj_counts = dict(
            db.execute(
                select(Room.project_id, func.count(Customer.id))
                .join(Customer, Customer.room_id == Room.id)
                .group_by(Room.project_id)
            ).all()
        )
        names = {p.id: p.name for p in db.scalars(select(Project)).all()}
        total_units = dict(db.execute(select(Room.project_id, func.count(Room.id)).group_by(Room.project_id)).all())
        for pid, cnt in sorted(proj_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
            if c_scope is not None:
                cnt = sum(1 for c in customers if c.room and c.room.project_id == pid)
            units = total_units.get(pid) or 0
            project_rows.append(
                {
                    "name": names.get(pid, f"#{pid}"),
                    "customers": cnt,
                    "units": units,
                    "rate": round(cnt / units * 100, 1) if units else 0.0,
                }
            )

    recent_photos = []
    if customers:
        ids = [c.id for c in customers]
        recent_photos = list(
            db.scalars(select(Photo).where(Photo.customer_id.in_(ids)).order_by(Photo.id.desc()).limit(8)).all()
        )

    return render(
        request,
        "dashboard.html",
        today=today,
        delayed=delayed,
        due_today=due_today,
        due_week=due_week,
        finance=finance,
        show_money=show_money,
        in_progress=in_progress,
        customer_total=len(customers),
        new_customers=len(new_customers),
        source_dist=sorted(source_dist.items(), key=lambda kv: kv[1], reverse=True),
        project_rows=project_rows,
        recent_photos=recent_photos,
        customers_map={c.id: c for c in customers},
        show_task_widgets=has_perm(user, "task:view"),
    )


@router.get("/ui/customer-search")
def fragment_customer_search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    if not has_perm(user, "customer:view"):
        return render(request, "_fragments/customer_rows.html", customers=[], summaries={}, show_money=False)
    scope = customer_scope_conditions(db, user)
    customers = search_svc.search_customers(db, q, scope_cond=scope, limit=30)
    summaries = {c.id: finance_svc.customer_summary(db, c) for c in customers}
    return render(
        request,
        "_fragments/customer_rows.html",
        customers=customers,
        summaries=summaries,
        show_money=has_perm(user, "payment:view") and can_see_amount(user),
    )


@router.get("/ui/tasks")
def fragment_tasks(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    scope = task_scope_conditions(db, user)
    stmt = select(Task).where(Task.status.notin_(["done", "skipped"])).order_by(Task.planned_end)
    if scope is not None:
        stmt = stmt.where(scope)
    tasks = list(db.scalars(stmt.limit(50)).all())
    customers = {c.id: c for c in db.scalars(select(Customer)).all()}
    return render(
        request,
        "_fragments/dashboard_tasks.html",
        tasks=tasks,
        customers=customers,
        status_labels={"pending": "未就绪", "ready": "待开工", "doing": "施工中", "done": "已完成", "skipped": "已跳过"},
    )
