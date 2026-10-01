"""权限点、角色预设、数据范围（双重校验：权限点 + 数据范围）。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Customer, CustomerShare, Task, User

ALL_PERMISSIONS: tuple[str, ...] = (
    "customer:view",
    "customer:edit",
    "customer:delete",
    "contract:view",
    "contract:edit",
    "payment:view",
    "payment:edit",
    "payment:refund",
    "task:view",
    "task:assign",
    "photo:upload",
    "photo:delete",
    "report:view",
    "admin:user",
    "admin:setting",
)

# 角色预设（文档 5.2）。installer 采用显式白名单，不做任何默认放行。
ROLE_PERMS: dict[str, set[str]] = {
    "admin": set(ALL_PERMISSIONS),
    "sales": {
        "customer:view",
        "customer:edit",
        "contract:view",
        "payment:view",
        "task:view",
        "photo:upload",
        "report:view",
    },
    "designer": {"customer:view", "task:view", "photo:upload"},
    "installer": {"task:view", "photo:upload"},
    "finance": {
        "customer:view",
        "contract:view",
        "payment:view",
        "payment:edit",
        "payment:refund",
        "report:view",
    },
}

ROLE_LABELS = {
    "admin": "管理员（店长）",
    "sales": "销售",
    "designer": "设计师",
    "installer": "安装工",
    "finance": "财务",
}

DEFAULT_SCOPE = {"admin": "all", "sales": "self", "designer": "shared", "installer": "self", "finance": "all"}

# 不可见金额的角色（文档 5.4 金额隔离）
NO_AMOUNT_ROLES = {"designer", "installer"}


def perms_for(user: User | None) -> set[str]:
    if not user:
        return set()
    return ROLE_PERMS.get(user.role, set())


def has_perm(user: User | None, perm: str) -> bool:
    return perm in perms_for(user)


def can_see_amount(user: User | None) -> bool:
    return bool(user) and user.role not in NO_AMOUNT_ROLES


def require(perm: str):
    """路由依赖：校验权限点。"""

    def _dep(request: Request):
        from .auth import current_user_or_redirect

        user = current_user_or_redirect(request)
        if perm not in perms_for(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限：{perm}")
        return user

    return _dep


def require_any(*perms: str):
    def _dep(request: Request):
        from .auth import current_user_or_redirect

        user = current_user_or_redirect(request)
        if not perms_for(user) & set(perms):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"缺少权限：{'/'.join(perms)}")
        return user

    return _dep


# ------------------------------------------------------------- 数据范围
def _shared_customer_ids(db: Session, user: User) -> set[int]:
    return set(db.scalars(select(CustomerShare.customer_id).where(CustomerShare.user_id == user.id)).all())


def visible_customer_ids(db: Session, user: User) -> set[int] | None:
    """返回 None 表示不限制（all）。"""
    scope = user.data_scope or DEFAULT_SCOPE.get(user.role, "self")
    if scope == "all":
        return None
    ids = set(db.scalars(select(Customer.id).where(Customer.owner_id == user.id)).all())
    if scope == "shared":
        ids |= _shared_customer_ids(db, user)
        # 设计师可见自己被指派任务的客户
        if user.role in {"designer", "installer"}:
            ids |= set(
                db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all()
            )
    if user.role == "installer":
        # installer 白名单：仅能看到自己被指派任务的客户
        ids &= set(db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all())
    return ids


def customer_scope_conditions(db: Session, user: User):
    ids = visible_customer_ids(db, user)
    if ids is None:
        return None
    if not ids:
        return Customer.id == -1
    conds = [Customer.id.in_(ids)]
    if user.role in {"sales", "admin"}:
        conds = [or_(Customer.owner_id == user.id, Customer.id.in_(ids))]
    return or_(*conds) if len(conds) > 1 else conds[0]


def can_view_customer(db: Session, user: User, customer: Customer) -> bool:
    if not has_perm(user, "customer:view") and user.role != "installer":
        return False
    ids = visible_customer_ids(db, user)
    return ids is None or customer.id in ids


def can_edit_customer(db: Session, user: User, customer: Customer) -> bool:
    if not has_perm(user, "customer:edit"):
        return False
    scope = user.data_scope or DEFAULT_SCOPE.get(user.role, "self")
    if scope == "all":
        return True
    return customer.owner_id == user.id


def task_scope_conditions(db: Session, user: User):
    scope = user.data_scope or DEFAULT_SCOPE.get(user.role, "self")
    if scope == "all":
        return None
    ids = visible_customer_ids(db, user) or set()
    ids |= set(db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all())
    if not ids:
        return Task.id == -1
    return Task.customer_id.in_(ids)


# ------------------------------------------------------- FastAPI 依赖别名
def current_user(request: Request):
    from .auth import current_user_or_redirect

    return current_user_or_redirect(request)


CurrentUser = Depends(current_user)
