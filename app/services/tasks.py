"""施工任务规则。

业务关系（需求方定稿）：
- 「上门勘测」与客户一一对应：创建客户即生成唯一勘测工序，完工后**不**再自动生成后续工序。
- 「前期施工 / 后期施工 / 调试验收」与合同一一对应：创建合同后逐个生成并推进，
  每份合同独立记录各自的施工过程；后续工序的推进以合同为依据，与勘测完工无关。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contract, Customer, StageTemplate, Task, User, today_str

DONE_STATES = {"done", "skipped"}
CLOSED_STATES = {"done", "skipped"}

# 工序与业务对象的分界
SURVEY_STAGE = "上门勘测"
CONSTRUCTION_STAGES = ["前期施工", "后期施工", "调试验收"]


def active_stages(db: Session) -> list[StageTemplate]:
    return list(
        db.scalars(
            select(StageTemplate).where(StageTemplate.is_active == 1).order_by(StageTemplate.sort_order)
        ).all()
    )


def construction_templates(db: Session) -> list[StageTemplate]:
    """启用的施工工序模板（按 sort_order 排序）。"""
    return [s for s in active_stages(db) if s.name in CONSTRUCTION_STAGES]


def _survey_template(db: Session) -> StageTemplate | None:
    for s in active_stages(db):
        if s.name == SURVEY_STAGE:
            return s
    return None


def _existing_survey(db: Session, customer_id: int) -> Task | None:
    return db.scalars(
        select(Task)
        .where(Task.customer_id == customer_id)
        .where(Task.stage == SURVEY_STAGE)
        .where(Task.contract_id.is_(None))
    ).first()


def _existing_contract_stages(db: Session, contract_id: int) -> set[str]:
    return {t.stage for t in db.scalars(select(Task).where(Task.contract_id == contract_id)).all()}


def _open_contract_task(db: Session, contract_id: int) -> Task | None:
    return db.scalars(
        select(Task).where(Task.contract_id == contract_id).where(Task.status.notin_(CLOSED_STATES))
    ).first()


def generate_survey_task(db: Session, customer: Customer, assignee_id: int | None = None) -> Task | None:
    """创建客户后生成唯一的「上门勘测」工序（默认开工）。已存在则不重复生成。"""
    if _existing_survey(db, customer.id):
        return None
    tpl = _survey_template(db)
    task = Task(
        customer_id=customer.id,
        contract_id=None,
        stage=SURVEY_STAGE,
        sort_order=(tpl.sort_order if tpl else 1),
        assignee_id=assignee_id or customer.owner_id,
        status="doing",
        actual_start=today_str(),
    )
    db.add(task)
    db.flush()
    return task


def generate_contract_tasks(db: Session, contract: Contract) -> list[Task]:
    """创建合同后生成首个施工工序（前期施工），默认开工。

    已有进行中的节点则不再重复生成；否则生成第一个尚未有记录的施工节点。
    """
    if _open_contract_task(db, contract.id):
        return []
    return _generate_next_contract_task(db, contract)


def _generate_next_contract_task(db: Session, contract: Contract) -> list[Task]:
    """按「已有节点记录」生成第一个尚未有记录的施工节点（不按状态判断）。

    注意：会话 autoflush=False，完工/跳过后推进下一节点时，必须按「节点是否有记录」
    而非「状态」判断——否则刚置为 done 的节点还没刷库，会被误判为仍有进行中节点。
    """
    existing = _existing_contract_stages(db, contract.id)
    customer = db.get(Customer, contract.customer_id)
    assignee = customer.owner_id if customer else None
    for tpl in construction_templates(db):
        if tpl.name in existing:
            continue
        task = Task(
            customer_id=contract.customer_id,
            contract_id=contract.id,
            stage=tpl.name,
            sort_order=tpl.sort_order,
            assignee_id=assignee,
            status="doing",
            actual_start=today_str(),
        )
        db.add(task)
        db.flush()
        return [task]
    return []


def activate_next_contract_stage(db: Session, contract_id: int) -> Task | None:
    """施工节点完工/跳过时，生成同一合同的下一个施工节点（默认开工）。"""
    contract = db.get(Contract, contract_id)
    if not contract:
        return None
    rows = _generate_next_contract_task(db, contract)
    return rows[0] if rows else None


def _activate_after(db: Session, task: Task) -> None:
    """完工/跳过后推进。

    - 施工节点（contract_id 非空）：推进同一合同的下一个施工节点。
    - 上门勘测（contract_id 为空）：完工后**不**再自动生成后续工序（后续以合同为依据）。
    """
    if task.contract_id:
        activate_next_contract_stage(db, task.contract_id)


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
    _activate_after(db, task)
    # 前期施工 / 后期施工完工后自动生成对外展示案例（幂等）
    from . import cases as cases_svc

    cases_svc.create_case_for_task(db, task)
    # 「前期施工」完工后激活客户锁定：该客户已进入实际施工，从新建合同下拉中过滤
    if task.stage == "前期施工":
        customer = db.get(Customer, task.customer_id)
        if customer is not None and not customer.locked:
            customer.locked = 1
    return True, "已完工"


def skip_task(db: Session, task: Task, reason: str) -> tuple[bool, str]:
    if not (reason or "").strip():
        return False, "跳过节点必须填写原因"
    task.status = "skipped"
    task.skip_reason = reason.strip()
    task.actual_end = task.actual_end or today_str()
    _activate_after(db, task)
    return True, "已跳过"


def progress(tasks: list[Task]) -> int:
    """完工进度：已完成节点数 / 已生成节点数（而非模板总数）。

    新模型下并非所有节点都会一开始就生成（勘测 1 份/客户，施工按合同逐份生成），
    故分母取「已生成」的节点数。
    """
    if not tasks:
        return 0
    done = sum(1 for t in tasks if t.status in DONE_STATES)
    return round(done / len(tasks) * 100)


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
