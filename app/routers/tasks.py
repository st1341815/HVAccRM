"""施工任务：看板、开工/完工/跳过（含前置依赖与原因校验）、指派、延期预警。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Customer, Task, User, now_iso, today_str
from ..permissions import has_perm, require, task_scope_conditions, visible_customer_ids
from ..services import tasks as task_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_int

router = APIRouter(prefix="/tasks", tags=["tasks"])

STATUS_LABELS = {
    "pending": "未就绪",
    "ready": "待开工",
    "doing": "施工中",
    "done": "已完成",
    "skipped": "已跳过",
}


def _can_touch(db: Session, user: User, task: Task) -> bool:
    if user.role == "admin":
        return True
    if task.assignee_id == user.id:
        return True
    if has_perm(user, "task:assign"):
        return True
    allowed = visible_customer_ids(db, user)
    return allowed is None or task.customer_id in allowed


@router.get("")
def task_board(
    request: Request,
    assignee: str = "",
    stage: str = "",
    status: str = "",
    customer_id: str = "",
    scope: str = "open",
    partial: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("task:view")),
):
    stmt = select(Task).order_by(Task.planned_end, Task.sort_order)
    cond = task_scope_conditions(db, user)
    if cond is not None:
        stmt = stmt.where(cond)
    if assignee:
        stmt = stmt.where(Task.assignee_id == parse_int(assignee))
    if stage:
        stmt = stmt.where(Task.stage == stage)
    if status:
        stmt = stmt.where(Task.status == status)
    if customer_id:
        stmt = stmt.where(Task.customer_id == parse_int(customer_id))
    if scope == "open":
        stmt = stmt.where(Task.status.notin_(["done", "skipped"]))
    tasks = list(db.scalars(stmt.limit(500)).all())

    customers = {c.id: c for c in db.scalars(select(Customer)).all()}
    users = list(db.scalars(select(User).where(User.is_active == 1)).all())
    users_map = {u.id: u for u in users}
    stages = [s.name for s in task_svc.active_stages(db)]
    delayed = [t for t in tasks if task_svc.is_delayed(t)]
    week_end = _plus_days(7)
    due_week = [
        t for t in tasks if t.planned_end and today_str() <= t.planned_end <= week_end and t.status not in task_svc.DONE_STATES
    ]
    ctx = dict(
        tasks=tasks,
        customers=customers,
        users=users,
        users_map=users_map,
        stages=stages,
        delayed=delayed,
        due_week=due_week,
        status_labels=STATUS_LABELS,
        filters={"assignee": assignee, "stage": stage, "status": status, "customer_id": customer_id, "scope": scope},
        can_assign=has_perm(user, "task:assign"),
        today=today_str(),
    )
    if partial:
        return render(request, "_fragments/task_rows.html", **ctx)
    return render(request, "tasks/board.html", **ctx)


def _plus_days(days: int) -> str:
    from datetime import date, timedelta

    return (date.today() + timedelta(days=days)).isoformat()


@router.post("/{task_id}/start")
def start(
    request: Request,
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require("task:view")),
):
    task = get_or_404(db, Task, task_id, "工序任务")
    if not _can_touch(db, user, task):
        return render(request, "403.html", status_code=403)
    ok, msg = task_svc.start_task(db, task)
    log_action(db, user, "task_start" if ok else "task_start_denied", "tasks", task.id, new={"status": task.status, "msg": msg}, ip=client_ip(request))
    commit_retry(db)
    return redirect(request.headers.get("referer") or "/tasks", msg, "ok" if ok else "err")


@router.post("/{task_id}/done")
def done(
    request: Request,
    task_id: int,
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("task:view")),
):
    task = get_or_404(db, Task, task_id, "工序任务")
    if not _can_touch(db, user, task):
        return render(request, "403.html", status_code=403)
    if notes.strip():
        task.notes = notes.strip()
    ok, msg = task_svc.finish_task(db, task)
    log_action(db, user, "task_done" if ok else "task_done_denied", "tasks", task.id, new={"status": task.status, "msg": msg}, ip=client_ip(request))
    commit_retry(db)
    return redirect(request.headers.get("referer") or "/tasks", msg, "ok" if ok else "err")


@router.post("/{task_id}/skip")
def skip(
    request: Request,
    task_id: int,
    skip_reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("task:view")),
):
    task = get_or_404(db, Task, task_id, "工序任务")
    if not _can_touch(db, user, task):
        return render(request, "403.html", status_code=403)
    ok, msg = task_svc.skip_task(db, task, skip_reason)
    log_action(db, user, "task_skip" if ok else "task_skip_denied", "tasks", task.id, new={"status": task.status, "reason": skip_reason}, ip=client_ip(request))
    commit_retry(db)
    return redirect(request.headers.get("referer") or "/tasks", msg, "ok" if ok else "err")


@router.post("/{task_id}/assign")
def assign(
    request: Request,
    task_id: int,
    assignee_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("task:assign")),
):
    task = get_or_404(db, Task, task_id, "工序任务")
    before = {"assignee_id": task.assignee_id}
    task.assignee_id = parse_int(assignee_id)
    task.updated_at = now_iso()
    log_action(db, user, "task_assign", "tasks", task.id, old=before, new={"assignee_id": task.assignee_id}, ip=client_ip(request))
    commit_retry(db)
    return redirect(request.headers.get("referer") or "/tasks", "已指派")


@router.post("/{task_id}/plan")
def plan(
    request: Request,
    task_id: int,
    planned_start: str = Form(""),
    planned_end: str = Form(""),
    notes: str = Form(""),
    delay_reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("task:assign")),
):
    task = get_or_404(db, Task, task_id, "工序任务")
    before = {"planned_start": task.planned_start, "planned_end": task.planned_end}
    task.planned_start = planned_start.strip() or None
    task.planned_end = planned_end.strip() or None
    task.notes = notes.strip() or task.notes
    task.delay_reason = delay_reason.strip() or None
    task.updated_at = now_iso()
    log_action(db, user, "task_plan", "tasks", task.id, old=before, new={"planned_start": task.planned_start, "planned_end": task.planned_end}, ip=client_ip(request))
    commit_retry(db)
    return redirect(request.headers.get("referer") or "/tasks", "工期已更新")
