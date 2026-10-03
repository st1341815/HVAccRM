"""施工任务规则：逐个生成、逐个推进（默认开工）。"""
from __future__ import annotations

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


def _existing_stages(db: Session, customer_id: int) -> set[str]:
    return {t.stage for t in db.scalars(select(Task).where(Task.customer_id == customer_id)).all()}


def _open_task(db: Session, customer_id: int) -> Task | None:
    return db.scalars(
        select(Task).where(Task.customer_id == customer_id).where(Task.status.notin_(CLOSED_STATES))
    ).first()


def generate_tasks(db: Session, customer: Customer, assignee_id: int | None = None) -> list[Task]:
    """生成「当前」工序节点（逐个生成，默认开工）。

    已有进行中的节点则不再重复生成；否则生成第一个尚未有记录的节点。
    """
    if _open_task(db, customer.id):
        return []
    existing = _existing_stages(db, customer.id)
    for tpl in active_stages(db):
        if tpl.name in existing:
            continue
        task = Task(
            customer_id=customer.id,
            stage=tpl.name,
            sort_order=tpl.sort_order,
            assignee_id=assignee_id or customer.owner_id,
            status="doing",
            actual_start=today_str(),
        )
        db.add(task)
        db.flush()
        return [task]
    return []


def activate_next_stage(db: Session, customer_id: int) -> Task | None:
    """当前节点完工/跳过时，生成下一个节点（默认开工）。"""
    existing = _existing_stages(db, customer_id)
    owner_id = db.scalar(select(Customer.owner_id).where(Customer.id == customer_id))
    for tpl in active_stages(db):
        if tpl.name in existing:
            continue
        task = Task(
            customer_id=customer_id,
            stage=tpl.name,
            sort_order=tpl.sort_order,
            assignee_id=owner_id,
            status="doing",
            actual_start=today_str(),
        )
        db.add(task)
        db.flush()
        return task
    return None


def start_task(db: Session, task: Task) -> tuple[bool, str]:
    """兜底：把节点置为开工（新流程节点生成即开工，一般无需手动开工）。"""
    task.actual_start = task.actual_start or today_str()
    task.status = "doing"
    return True, "已开工"


def finish_task(db: Session, task: Task, done_date: str | None = None) -> tuple[bool, str]:
    """完工。done_date 为实际施工日期（每个工序 1 天），缺省今天。

    补录场景下「上传/操作时间」可能≠「实际施工时间」，故由调用方传入施工日期；
    该日期同时作为 actual_start 与 actual_end（单日工序）。
    """
    if task.status == "pending":
        return False, "前置节点未完成，不能直接完工"
    d = (done_date or "").strip()[:10] or today_str()
    task.actual_start = d
    task.actual_end = d
    task.status = "done"
    activate_next_stage(db, task.customer_id)
    # 前期施工 / 后期施工完工后自动生成对外展示案例（幂等）
    from . import cases as cases_svc

    cases_svc.create_case_for_task(db, task)
    return True, "已完工"


def skip_task(db: Session, task: Task, reason: str) -> tuple[bool, str]:
    if not (reason or "").strip():
        return False, "跳过节点必须填写原因"
    task.status = "skipped"
    task.skip_reason = reason.strip()
    task.actual_end = task.actual_end or today_str()
    activate_next_stage(db, task.customer_id)
    return True, "已跳过"


def progress(db: Session, tasks: list[Task]) -> int:
    """完工进度：已完成节点数 / 总工序节点数（按模板总数，而非已生成数）。"""
    total = len(active_stages(db))
    if not total:
        return 0
    done = sum(1 for t in tasks if t.status in DONE_STATES)
    return round(done / total * 100)


def default_assignee(db: Session, tasks: list[Task]) -> list[User]:
    ids = {t.assignee_id for t in tasks if t.assignee_id}
    if not ids:
        return []
    return list(db.scalars(select(User).where(User.id.in_(ids))).all())


# 工序节点 → 相册分类（上传照片时自动归类）
STAGE_PHOTO_KIND: dict[str, str] = {
    "上门勘测": "现场",
    "前期施工": "施工",
    "后期施工": "施工",
    "调试验收": "验收",
}


def stage_default_kind(stage: str | None) -> str:
    return STAGE_PHOTO_KIND.get(stage or "", "其他")


def stage_photo_requirements(db: Session) -> dict[str, bool]:
    """节点是否需要照片（取自工序模板的 require_photo 开关）。"""
    return {s.name: bool(s.require_photo) for s in db.scalars(select(StageTemplate)).all()}
