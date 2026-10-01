"""施工任务规则：自动建单、前置依赖、跳过原因、延期预警。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Customer, StageTemplate, Task, User, today_str

DONE_STATES = {"done", "skipped"}
CLOSED_STATES = {"done", "skipped"}


def active_stages(db: Session) -> list[StageTemplate]:
    return list(
        db.scalars(
            select(StageTemplate).where(StageTemplate.is_active == 1).order_by(StageTemplate.sort_order)
        ).all()
    )


def generate_tasks(db: Session, customer: Customer, assignee_id: int | None = None) -> list[Task]:
    """客户建单时按模板自动生成标准节点任务（幂等）。"""
    existing = {t.stage for t in db.scalars(select(Task).where(Task.customer_id == customer.id)).all()}
    created: list[Task] = []
    cursor = date.today()
    for tpl in active_stages(db):
        if tpl.name in existing:
            continue
        days = tpl.default_days or 1
        start = cursor
        end = start + timedelta(days=max(days - 1, 0))
        task = Task(
            customer_id=customer.id,
            stage=tpl.name,
            sort_order=tpl.sort_order,
            planned_start=start.isoformat(),
            planned_end=end.isoformat(),
            assignee_id=assignee_id or customer.owner_id,
            status="pending",
        )
        db.add(task)
        created.append(task)
        cursor = end + timedelta(days=1)
    db.flush()
    refresh_ready(db, customer.id)
    return created


def _ordered(db: Session, customer_id: int) -> list[Task]:
    return list(
        db.scalars(
            select(Task).where(Task.customer_id == customer_id).order_by(Task.sort_order, Task.id)
        ).all()
    )


def refresh_ready(db: Session, customer_id: int) -> None:
    """前置依赖：上一节点未完成，下一节点只能是 ready（禁止开工）。"""
    tasks = _ordered(db, customer_id)
    prev_done = True
    for t in tasks:
        if t.status in CLOSED_STATES:
            prev_done = True
            continue
        if prev_done and t.status == "pending":
            t.status = "ready"
        elif not prev_done and t.status == "ready":
            t.status = "pending"
        prev_done = False


def prerequisite_ok(db: Session, task: Task) -> tuple[bool, str]:
    tasks = _ordered(db, task.customer_id)
    for t in tasks:
        if t.sort_order >= task.sort_order:
            break
        if t.status not in CLOSED_STATES:
            return False, f"前置节点「{t.stage}」尚未完成"
    return True, ""


def start_task(db: Session, task: Task) -> tuple[bool, str]:
    ok, msg = prerequisite_ok(db, task)
    if not ok:
        return False, msg
    task.actual_start = task.actual_start or today_str()
    task.status = "doing"
    if not task.planned_start:
        task.planned_start = today_str()
    return True, "已开工"


def finish_task(db: Session, task: Task) -> tuple[bool, str]:
    if task.status == "pending":
        return False, "前置节点未完成，不能直接完工"
    task.actual_end = today_str()
    task.status = "done"
    refresh_ready(db, task.customer_id)
    return True, "已完工"


def skip_task(db: Session, task: Task, reason: str) -> tuple[bool, str]:
    if not (reason or "").strip():
        return False, "跳过节点必须填写原因"
    task.status = "skipped"
    task.skip_reason = reason.strip()
    task.actual_end = task.actual_end or today_str()
    refresh_ready(db, task.customer_id)
    return True, "已跳过"


def is_delayed(task: Task, today: str | None = None) -> bool:
    today = today or today_str()
    return bool(task.planned_end) and task.planned_end < today and task.status not in DONE_STATES


def delay_count(db: Session, tasks: list[Task]) -> int:
    return sum(1 for t in tasks if is_delayed(t))


def progress(tasks: list[Task]) -> int:
    if not tasks:
        return 0
    done = sum(1 for t in tasks if t.status in DONE_STATES)
    return round(done / len(tasks) * 100)


def due_soon(db: Session, start: str, end: str, scope=None) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.status.notin_(list(DONE_STATES)))
        .where(Task.planned_end.is_not(None))
        .where(Task.planned_end >= start)
        .where(Task.planned_end <= end)
        .order_by(Task.planned_end)
    )
    if scope is not None:
        stmt = stmt.where(scope)
    return list(db.scalars(stmt).all())


def delayed_tasks(db: Session, scope=None) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.status.notin_(list(DONE_STATES)))
        .where(Task.planned_end.is_not(None))
        .where(Task.planned_end < today_str())
        .order_by(Task.planned_end)
    )
    if scope is not None:
        stmt = stmt.where(scope)
    return list(db.scalars(stmt).all())


def default_assignee(db: Session, tasks: list[Task]) -> list[User]:
    ids = {t.assignee_id for t in tasks if t.assignee_id}
    if not ids:
        return []
    return list(db.scalars(select(User).where(User.id.in_(ids))).all())
