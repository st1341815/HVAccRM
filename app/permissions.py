"""权限点、角色预设、数据范围（双重校验：权限点 + 数据范围）。"""
from __future__ import annotations

import json

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
    "cost:view",
    "cost:edit",
    "settlement:view",
    "settlement:edit",
    "task:view",
    "task:assign",
    "photo:upload",
    "photo:delete",
    "case:view",
    "case:edit",
    "report:view",
    "admin:user",
    "admin:setting",
)

# --------------------------------------------------------------- 权限点目录
# (key, 中文名, 分组, 一句话释义)
# 顺序即权限矩阵页面的展示顺序；分组按首次出现排序。
# 释义写给「不懂代码的店长」看：说清这个开关能让人做什么、不能做什么。
PERM_CATALOG: tuple[tuple[str, str, str, str], ...] = (
    ("customer:view", "查看客户", "客户",
     "能打开客户列表和客户详情。具体能看哪些客户，还要由该用户的「数据范围」再筛一层"),
    ("customer:edit", "编辑客户", "客户",
     "新建客户、修改客户资料（姓名电话、楼盘、装修阶段、意向产品等）"),
    ("customer:delete", "删除客户", "客户",
     "删除客户记录。合同、工序、照片不会被连带删除，但会失去归属客户"),
    ("contract:view", "查看合同", "合同与收款",
     "看合同列表、合同金额与优惠、电子合同正文"),
    ("contract:edit", "编辑合同", "合同与收款",
     "新建或修改合同、改金额与优惠、上传电子合同、作废合同"),
    ("payment:view", "查看收款", "合同与收款",
     "看每份合同收了多少、还差多少（回款进度）"),
    ("payment:edit", "登记收款", "合同与收款",
     "新增、修改收款记录（钱到账后录进来）"),
    ("payment:refund", "收款退款", "合同与收款",
     "对已收款项做退款。退款会冲减回款额，等于钱退回去"),
    ("cost:view", "查看成本", "成本与结算",
     "看成本明细和成本汇总（材料、人工、外包等支出）"),
    ("cost:edit", "编辑成本", "成本与结算",
     "新增、修改成本支出记录"),
    ("settlement:view", "查看结算", "成本与结算",
     "看结算单、结算进度与结算金额"),
    ("settlement:edit", "结算操作", "成本与结算",
     "生成结算单、确认结算。会改动账目，一般只给财务"),
    ("task:view", "查看工序", "施工与照片",
     "看工序列表、施工进度、工序模板内容"),
    ("task:assign", "指派工序", "施工与照片",
     "给工序安排负责人、调整计划工期、推进节点状态"),
    ("photo:upload", "上传照片", "施工与照片",
     "上传施工照片和现场资料"),
    ("photo:delete", "删除照片", "施工与照片",
     "删除已上传的照片。删掉后小程序端也看不到了"),
    ("case:view", "查看案例", "案例与报表",
     "打开案例库、看案例详情"),
    ("case:edit", "编辑案例", "案例与报表",
     "新增或修改案例、把案例发布到微信小程序"),
    ("report:view", "查看报表", "案例与报表",
     "看经营报表、财务汇总，并导出 Excel"),
    ("admin:user", "用户管理", "系统管理",
     "新增/停用用户、重置密码、设置角色与数据范围，以及打开这个权限矩阵页"),
    ("admin:setting", "系统设置", "系统管理",
     "备份、短信验证、外网入口、合同模板、小程序同步等全部系统设置"),
)

PERM_LABELS: dict[str, str] = {k: label for k, label, _g, _d in PERM_CATALOG}

# 权限矩阵页面上的展示分组：{"客户": [catalog item, ...], ...}
PERM_GROUPS: dict[str, list[dict[str, str]]] = {}
for _k, _label, _g, _d in PERM_CATALOG:
    PERM_GROUPS.setdefault(_g, []).append({"key": _k, "label": _label, "desc": _d})

# 可在页面上勾选的角色。admin 恒等于全量权限，不给改（改错了没人能进后台）。
EDITABLE_ROLES: tuple[str, ...] = ("sales", "designer", "installer", "finance")

PERM_MATRIX_KEY = "perm.matrix"

# 权限矩阵的自定义覆盖：{role: set(perm)}。None = 还没从库里加载过。
_overrides: dict[str, set[str]] | None = None


def _parse_matrix(raw: str) -> dict[str, set[str]]:
    """把库里存的 JSON 解析成 {role: set}，非法内容一律丢弃。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    valid = set(ALL_PERMISSIONS)
    out: dict[str, set[str]] = {}
    for role, perms in data.items():
        if role not in EDITABLE_ROLES or not isinstance(perms, list):
            continue
        out[role] = {p for p in perms if isinstance(p, str) and p in valid}
    return out


def load_overrides(db: Session) -> dict[str, set[str]]:
    """启动时 / 保存后调用：把库里的自定义矩阵读进进程缓存。

    uvicorn 跑 --workers 1，模块级缓存安全；多 worker 部署时这里需要改成
    每次请求读库或加版本号（当前部署形态用不到）。
    """
    global _overrides
    from .services import settings as settings_svc

    try:
        raw = settings_svc.get(db, PERM_MATRIX_KEY, "") or ""
    except Exception:  # pragma: no cover - 库还没建好时不要拖垮启动
        raw = ""
    _overrides = _parse_matrix(raw)
    return _overrides


def _loaded() -> dict[str, set[str]]:
    global _overrides
    if _overrides is None:
        # 还没人调 load_overrides（比如脚本里直接 import），退化成纯默认，不报错
        _overrides = {}
    return _overrides


def effective_role_perms() -> dict[str, set[str]]:
    """默认预设 + 库里的自定义覆盖 = 实际生效的矩阵。"""
    out = {role: set(perms) for role, perms in ROLE_PERMS.items()}
    for role, perms in _loaded().items():
        if role in out and role != "admin":
            out[role] = set(perms)
    return out


def is_custom(role: str) -> bool:
    return role in _loaded()


def save_matrix(db: Session, matrix: dict[str, object]) -> dict[str, set[str]]:
    """保存自定义矩阵。只存「与默认预设不同」的角色，其余回落到默认。"""
    from .services import settings as settings_svc

    keep: dict[str, list[str]] = {}
    for role in EDITABLE_ROLES:
        raw = matrix.get(role, [])
        if not isinstance(raw, list):
            raw = []
        perms = {p for p in raw if isinstance(p, str) and p in ALL_PERMISSIONS}
        if perms != ROLE_PERMS.get(role, set()):
            keep[role] = sorted(perms)
    settings_svc.set_(db, PERM_MATRIX_KEY, json.dumps(keep, ensure_ascii=False, sort_keys=True) if keep else "")
    # ⚠️ 必须显式 flush：SessionLocal 是 autoflush=False，首次保存时 key 还不存在，
    # 不 flush 的话紧接着的 load_overrides() 读库会读到「没有这一行」→ 缓存不更新 → 保存了不生效。
    db.flush()
    return load_overrides(db)


def reset_matrix(db: Session) -> None:
    from .services import settings as settings_svc

    settings_svc.set_(db, PERM_MATRIX_KEY, "")
    db.flush()
    load_overrides(db)


def reset_cache() -> None:
    """清掉进程内缓存（测试用；线上只有启动和保存两处会写它）。"""
    global _overrides
    _overrides = None


# 角色预设（文档 5.2）。installer 采用显式白名单，不做任何默认放行。
ROLE_PERMS: dict[str, set[str]] = {
    "admin": set(ALL_PERMISSIONS),
    "sales": {
        "customer:view",
        "customer:edit",
        "contract:view",
        "payment:view",
        "cost:view",
        "cost:edit",
        "settlement:view",
        "task:view",
        "photo:upload",
        "case:view",
        "case:edit",
        "report:view",
    },
    "designer": {"customer:view", "task:view", "photo:upload", "case:view"},
    "installer": {"task:view", "photo:upload", "case:view"},
    "finance": {
        "customer:view",
        "contract:view",
        "payment:view",
        "payment:edit",
        "payment:refund",
        "cost:view",
        "cost:edit",
        "settlement:view",
        "settlement:edit",
        "case:view",
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
    # admin 恒等于全量：后台一旦把管理员的用户管理权限关掉，就再也没人能改回来了
    if user.role == "admin":
        return set(ALL_PERMISSIONS)
    custom = _loaded().get(user.role)
    if custom is not None:
        return set(custom)
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
        # installer 白名单：以「被指派任务的客户」为主要可见性来源（与 owner 取并集）；
        # 之前用 &= 在 installer 不 own 任何客户时结果永远空集，这是 v1.0.0 测试发现的 bug
        ids |= set(
            db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all()
        )
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
