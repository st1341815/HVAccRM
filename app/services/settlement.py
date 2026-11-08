"""合同结算：标记、解冻判断与未完成工序校验。

设计原则：**冻结规则只有一个出口**。所有写入口（收款 / 费用 / 施工 / 合同编辑）
统一调用本模块，不允许在路由里各写各的 `if contract.settled`——新增入口时漏拦
的代价是核算数字失真，且很难事后发现。

结算标记与 Contract.status 正交：
- status 表示业务流转（active / done / void）
- settled 表示财务关账（0 / 1）
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Contract, Task, User, today_str

# 与 services/tasks.py 的 DONE_STATES 保持一致（此处不 import 以避免循环依赖）
TERMINAL_TASK_STATES = {"done", "skipped"}

# 动作 → 提示语前缀（统一口径，便于前端提示与测试断言）
ACTION_LABELS = {
    "payment": "登记收款",
    "cost": "登记费用",
    "task": "操作施工工序",
    "edit": "修改合同内容",
}


def is_settled(contract: Contract | None) -> bool:
    """None 视为未结算（新建合同尚未 flush 的场景）。"""
    return bool(getattr(contract, "settled", 0))


def block_reason(contract: Contract | None, action: str) -> str | None:
    """已结算合同返回拦截原因；未结算返回 None。

    action 见 ACTION_LABELS，未知 action 统一按「修改合同内容」提示。
    """
    if not is_settled(contract):
        return None
    label = ACTION_LABELS.get(action, ACTION_LABELS["edit"])
    no = getattr(contract, "no", None) or f"#{getattr(contract, 'id', '?')}"
    return f"合同 {no} 已结算，不能{label}；如需继续录入，请先在合同台账取消结算。"


def unsettled_task_count(db: Session, contract: Contract) -> int:
    """该合同下仍未完成的施工工序数量（pending / doing 等非终态）。"""
    rows = db.scalars(select(Task).where(Task.contract_id == contract.id)).all()
    return sum(1 for t in rows if (t.status or "") not in TERMINAL_TASK_STATES)


def can_settle(db: Session, contract: Contract) -> tuple[bool, str]:
    """结算前置校验：作废合同不可结算；有未完成工序时硬拦截。

    返回 (是否可以, 拒绝原因)。
    """
    if is_settled(contract):
        return False, f"合同 {contract.no or '#' + str(contract.id)} 已是结算状态。"
    if (contract.status or "") == "void":
        return False, "作废合同不能标记结算。"
    pending = unsettled_task_count(db, contract)
    if pending:
        return False, f"还有 {pending} 道施工工序未完成，请先完成或跳过后再结算。"
    return True, ""


def set_settled(db: Session, contract: Contract, on: bool, user: User | None) -> tuple[dict, bool]:
    """写入结算标记，返回 (变更前快照, 是否发生实际变更)。

    幂等：重复设置同一值不改 settled_at、不重复记审计。
    """
    before = {
        "settled": contract.settled,
        "settled_at": contract.settled_at,
        "settled_by": contract.settled_by,
    }
    target = 1 if on else 0
    if int(contract.settled or 0) == target:
        return before, False
    contract.settled = target
    if target:
        contract.settled_at = today_str()
        contract.settled_by = getattr(user, "id", None)
    else:
        contract.settled_at = None
        contract.settled_by = None
    return before, True


def settled_label(contract: Contract) -> str:
    """台账/详情展示用文案。"""
    if not is_settled(contract):
        return "未结算"
    return f"已结算 {contract.settled_at}" if contract.settled_at else "已结算"
