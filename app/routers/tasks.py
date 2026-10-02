"""施工任务：看板、开工/完工/跳过（含前置依赖与原因校验）、指派、延期预警。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Customer, Photo, Project, Room, StageTemplate, Task, User, now_iso, today_str
from ..permissions import has_perm, require, task_scope_conditions, visible_customer_ids
from ..services import photos as photo_svc
from ..services import tasks as task_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_int, return_path

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


def _photo_ctx(db: Session, tasks: list[Task], user: User, request: Request) -> dict:
    """施工页照片上下文：每个节点的已有照片 + 是否需照片 + 上传权限 + 返回路径。"""
    # back_path 要指向「完整页面」而非 htmx 局部刷新地址（partial=1），
    # 否则上传后跳转会落回裸片段页、看不到完整布局。
    from urllib.parse import parse_qsl, urlencode

    query = urlencode([(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True) if k != "partial"])
    back_path = str(request.url.path) + (f"?{query}" if query else "")
    return {
        "task_photos": photo_svc.photos_by_task(db, [t.id for t in tasks]),
        "stage_requires_photo": task_svc.stage_photo_requirements(db),
        "stage_kind": task_svc.STAGE_PHOTO_KIND,
        "can_upload_photo": has_perm(user, "photo:upload"),
        "back_path": back_path,
    }


@router.get("")
def task_board(
    request: Request,
    assignee: str = "",
    stage: str = "",
    status: str = "",
    q: str = "",
    scope: str = "open",
    view: str = "tasks",
    partial: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require("task:view")),
):
    stmt = select(Task).order_by(Task.customer_id, Task.sort_order, Task.id)
    cond = task_scope_conditions(db, user)
    if cond is not None:
        stmt = stmt.where(cond)
    if assignee:
        stmt = stmt.where(Task.assignee_id == parse_int(assignee))
    if stage:
        stmt = stmt.where(Task.stage == stage)
    if status:
        stmt = stmt.where(Task.status == status)
    # 关键字筛选：客户姓名 / 手机号 / 房号（楼栋·单元·房号）/ 楼盘名
    if q and q.strip():
        kw = f"%{q.strip()}%"
        cids = list(
            db.scalars(
                select(Customer.id)
                .outerjoin(Room, Customer.room_id == Room.id)
                .outerjoin(Project, Room.project_id == Project.id)
                .where(
                    or_(
                        Customer.name.like(kw),
                        Customer.phone.like(kw),
                        Room.building.like(kw),
                        Room.unit.like(kw),
                        Room.room_no.like(kw),
                        Project.name.like(kw),
                    )
                )
            ).all()
        )
        stmt = stmt.where(Task.customer_id.in_(cids or [-1]))
    if scope == "open":
        stmt = stmt.where(Task.status.notin_(["done", "skipped"]))
    tasks = list(db.scalars(stmt.limit(500)).all())

    customers = {c.id: c for c in db.scalars(select(Customer)).all()}
    users = list(db.scalars(select(User).where(User.is_active == 1)).all())
    users_map = {u.id: u for u in users}
    stages = [s.name for s in task_svc.active_stages(db)]
    filters = {
        "assignee": assignee,
        "stage": stage,
        "status": status,
        "q": q,
        "scope": scope,
        "view": view,
    }
    # 按客户分组视图：先按筛选条件定位「有相关工序的客户」，再展示这些客户的全部节点
    groups = []
    if view == "customer" and tasks:
        cids = list(dict.fromkeys(t.customer_id for t in tasks))
        gstmt = select(Task).where(Task.customer_id.in_(cids)).order_by(Task.customer_id, Task.sort_order)
        if cond is not None:
            gstmt = gstmt.where(cond)
        all_tasks = list(db.scalars(gstmt).all())
        by_customer: dict[int, list[Task]] = {}
        for t in all_tasks:
            by_customer.setdefault(t.customer_id, []).append(t)
        for cid in cids:
            rows = sorted(by_customer.get(cid, []), key=lambda t: t.sort_order or 0)
            groups.append(
                {
                    "customer": customers.get(cid),
                    "tasks": rows,
                    "progress": task_svc.progress(rows),
                    "open": sum(1 for t in rows if t.status not in task_svc.DONE_STATES),
                }
            )
        groups.sort(key=lambda g: (-g["open"], g["customer"].name if g["customer"] else ""))

    def _qs(**over) -> str:
        params = {k: v for k, v in filters.items() if v and k != "view"}
        for k, v in over.items():
            if k == "view":
                continue
            if v:
                params[k] = v
            else:
                params.pop(k, None)  # 空值 = 清除该筛选（如「全部未完成」要清掉 assignee）
        params["view"] = over.get("view", view)
        return "/tasks?" + "&".join(f"{k}={v}" for k, v in params.items())

    ctx = dict(
        tasks=tasks,
        groups=groups,
        customers=customers,
        users=users,
        users_map=users_map,
        stages=stages,
        status_labels=STATUS_LABELS,
        filters=filters,
        view=view,
        quick_links={
            "all": _qs(scope="open", assignee="", view="tasks"),
            "mine": _qs(scope="open", assignee=user.id, view="tasks"),
            "view_tasks": _qs(view="tasks"),
            "view_customer": _qs(view="customer"),
        },
        can_assign=has_perm(user, "task:assign"),
        today=today_str(),
        **_photo_ctx(db, tasks if view != "customer" else [t for g in groups for t in g["tasks"]], user, request),
    )
    if partial:
        name = "_fragments/task_cards.html" if view == "customer" else "_fragments/task_rows.html"
        return render(request, name, **ctx)
    return render(request, "tasks/board.html", **ctx)



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
    return redirect(return_path(None, request.headers.get("referer"), "/tasks"), msg, "ok" if ok else "err")


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
        # 追加而非覆盖：保留原有备注（如计划备注/延期原因），带上完工日期便于追溯
        stamp = today_str()
        prev = (task.notes or "").strip()
        entry = f"[{stamp} 完工] {notes.strip()}"
        task.notes = f"{prev}\n{entry}" if prev else entry
    ok, msg = task_svc.finish_task(db, task)
    if ok and task_svc.stage_photo_requirements(db).get(task.stage):
        have = db.scalar(select(func.count(Photo.id)).where(Photo.task_id == task.id)) or 0
        if not have:
            msg += "（提示：该节点模板标记为「需照片」，建议先上传现场照片）"
    log_action(db, user, "task_done" if ok else "task_done_denied", "tasks", task.id, new={"status": task.status, "msg": msg}, ip=client_ip(request))
    commit_retry(db)
    return redirect(return_path(None, request.headers.get("referer"), "/tasks"), msg, "ok" if ok else "err")


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
    return redirect(return_path(None, request.headers.get("referer"), "/tasks"), msg, "ok" if ok else "err")


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
    return redirect(return_path(None, request.headers.get("referer"), "/tasks"), "已指派")


