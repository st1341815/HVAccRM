"""看板布局方案：CRUD + 角色预设（v1.16.0）。

决策（2026-11-26 确认）：**方案一律个人私有**，不做共享方案。
跨角色复用靠 `ROLE_PRESETS`——首次进看板时按角色实例化一份私有方案，
实例化之后归个人所有，谁也改不动别人。

为什么预设写在代码里而不是数据库：
- 它只是「初始值」，用户改一次就与它无关了；
- 放库里就得管版本、管迁移、管「管理员改了预设老用户要不要跟着变」——收益为零。

layout_json 结构（v=2，12 栅格）：
    {"v":2,"cols":12,"items":[{"id":"w1","metric":"sales.trend","kind":"line","span":12,"h":280,"opts":{}}]}
"""
from __future__ import annotations

import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DashboardView, User, now_iso
from . import metrics as M

LAYOUT_VERSION = 2
MAX_ITEMS = 30
MAX_VIEWS = 12
MAX_NAME = 20


# --------------------------------------------------------------- 角色预设
def _it(metric: str, kind: str, span: int, h: int = 0, **opts: Any) -> dict:
    return {"id": "w_" + secrets.token_hex(3), "metric": metric, "kind": kind,
            "span": span, "h": h, "opts": opts}


ROLE_PRESETS: dict[str, list[dict]] = {
    # 老板 / 管理员：经营驾驶舱（对应渲染图 A）
    "admin": [
        _it("kpi.sales", "kpi", 2), _it("kpi.received", "kpi", 2),
        _it("kpi.cost", "kpi", 2), _it("kpi.profit", "kpi", 2),
        _it("kpi.outstanding", "kpi", 2), _it("kpi.inprogress", "kpi", 2),
        _it("sales.trend", "line", 12, 280, months=12),
        _it("source.donut", "donut", 4, 240, days=30),
        _it("contract.hist", "hist", 4, 240),
        _it("product.bar", "bar", 4, 240),
        _it("debt.top", "list", 4, 260, n=5),
        _it("site.progress", "list", 4, 260, n=6),
        _it("task.open", "list", 4, 260, n=8),
    ],
    # 销售：自己的一亩三分地
    "sales": [
        _it("kpi.month_signed", "kpi", 3), _it("kpi.sales", "kpi", 3),
        _it("kpi.recovery", "kpi", 3), _it("kpi.inprogress", "kpi", 3),
        _it("source.donut", "donut", 4, 240, days=30),
        _it("contract.hist", "hist", 4, 240),
        _it("product.bar", "bar", 4, 240),
        _it("debt.top", "list", 6, 260, n=5),
        _it("task.open", "list", 6, 260, n=8),
    ],
    # 设计师：勘测 + 出图节奏
    "designer": [
        _it("kpi.inprogress", "kpi", 6), _it("kpi.open_tasks", "kpi", 6),
        _it("task.stage", "stack", 12, 260),
        _it("task.due", "list", 6, 260, days=7, n=8),
        _it("site.progress", "list", 6, 260, n=6),
    ],
    # 安装工：工地推进台（对应渲染图 B），无任何金额
    "installer": [
        _it("kpi.inprogress", "kpi", 6), _it("kpi.open_tasks", "kpi", 6),
        _it("site.heatmap", "heat", 8, 220, weeks=26),
        _it("task.delay", "donut", 4, 220, days=90),
        _it("staff.load", "bar", 4, 240),
        _it("site.progress", "list", 8, 260, n=8),
        _it("task.due", "list", 4, 260, days=7, n=8),
    ],
    # 财务：回款与利润
    "finance": [
        _it("kpi.received", "kpi", 3), _it("kpi.outstanding", "kpi", 3),
        _it("kpi.recovery", "kpi", 3), _it("kpi.profit", "kpi", 3),
        _it("sales.trend", "line", 8, 260, months=12),
        _it("debt.age", "bar", 4, 260),
        _it("debt.top", "list", 6, 260, n=6),
        _it("profit.trend", "bar", 6, 240, months=6),
    ],
}


# --------------------------------------------------------------- 校验
def sanitize_items(items: Any, user: User) -> list[dict]:
    """把前端传来的布局洗成合法结构：指标存在、图形支持、尺寸合规、权限够。

    宁可丢一个模块，也不要存进一个渲染不出来的配置——否则用户下次进看板
    会看到一张报错的卡片，还以为整个看板坏了。
    """
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen_ids: set[str] = set()
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("metric") or "")
        m = M.METRICS.get(key)
        if m is None or not M.allowed(user, m):
            continue
        kind = str(raw.get("kind") or m.kinds[0])
        if kind not in m.kinds:
            kind = m.kinds[0]
        try:
            span = int(raw.get("span") or 12)
        except (TypeError, ValueError):
            span = 12
        span = max(1, min(span, 12))
        try:
            h = int(raw.get("h") or 0)
        except (TypeError, ValueError):
            h = 0
        h = max(0, min(h, 600))
        wid = str(raw.get("id") or "")
        if not wid or wid in seen_ids:
            wid = "w_" + secrets.token_hex(3)
        seen_ids.add(wid)
        opts = raw.get("opts") if isinstance(raw.get("opts"), dict) else {}
        # 只留白名单键，值统一转 int，防止把任意字符串塞进查询
        safe_opts: dict[str, int] = {}
        for k in ("months", "days", "n", "weeks"):
            if k in opts:
                try:
                    safe_opts[k] = int(opts[k])
                except (TypeError, ValueError):
                    pass
        out.append({"id": wid, "metric": key, "kind": kind, "span": span,
                    "h": h, "opts": safe_opts})
    return out


def parse_layout(view: DashboardView) -> dict:
    try:
        data = json.loads(view.layout_json or "{}")
    except (ValueError, TypeError):
        data = {}
    items = data.get("items") if isinstance(data, dict) else None
    return {"v": data.get("v", LAYOUT_VERSION) if isinstance(data, dict) else LAYOUT_VERSION,
            "cols": 12, "items": items if isinstance(items, list) else []}


def dump_layout(items: list[dict]) -> str:
    return json.dumps({"v": LAYOUT_VERSION, "cols": 12, "items": items}, ensure_ascii=False)


# --------------------------------------------------------------- CRUD
def list_views(db: Session, user: User) -> list[DashboardView]:
    return list(db.scalars(
        select(DashboardView)
        .where(DashboardView.user_id == user.id)
        .order_by(DashboardView.is_default.desc(), DashboardView.id)
    ).all())


def get_view(db: Session, user: User, view_id: int) -> DashboardView | None:
    v = db.get(DashboardView, view_id)
    if v is None or v.user_id != user.id:   # 别人的方案一律当不存在
        return None
    return v


def default_view(db: Session, user: User) -> DashboardView | None:
    return db.scalar(select(DashboardView).where(
        DashboardView.user_id == user.id, DashboardView.is_default == 1))


def ensure_default_view(db: Session, user: User) -> DashboardView:
    """首次进看板时按角色实例化一份私有方案；已有方案则原样返回。

    权限过滤只在这里做一次（生成阶段剔除），运行期权限被收回时改用灰色占位
    ——两个时点语义不同，别混。
    """
    cur = default_view(db, user)
    if cur is not None:
        return cur
    preset = ROLE_PRESETS.get(user.role) or ROLE_PRESETS["sales"]
    items = sanitize_items(preset, user)
    if not items:                      # 极端情况：角色啥权限都没有
        items = sanitize_items([_it("kpi.inprogress", "kpi", 12)], user)
    view = DashboardView(
        user_id=user.id, name="我的看板", layout_json=dump_layout(items),
        is_default=1, created_at=now_iso(), updated_at=now_iso(),
    )
    db.add(view)
    db.commit()
    return view


def _clear_other_defaults(db: Session, user: User, keep_id: int | None = None) -> None:
    for v in list_views(db, user):
        if v.is_default and (keep_id is None or v.id != keep_id):
            v.is_default = 0
            v.updated_at = now_iso()
    db.flush()


def create_view(db: Session, user: User, name: str, items: list[dict],
                make_default: bool = False) -> DashboardView:
    views = list_views(db, user)
    if len(views) >= MAX_VIEWS:
        raise ValueError(f"方案数量已达上限（{MAX_VIEWS} 个）")
    clean_name = (name or "").strip()[:MAX_NAME] or "新方案"
    if any(v.name == clean_name for v in views):
        clean_name = (clean_name[:MAX_NAME - 4] + " " + secrets.token_hex(1))[:MAX_NAME]
    is_default = 1 if (make_default or not views) else 0
    if is_default:
        _clear_other_defaults(db, user)
    view = DashboardView(
        user_id=user.id, name=clean_name, layout_json=dump_layout(sanitize_items(items, user)),
        is_default=is_default, created_at=now_iso(), updated_at=now_iso(),
    )
    db.add(view)
    db.commit()
    return view


def update_view(db: Session, user: User, view_id: int, *,
                name: str | None = None, items: list[dict] | None = None,
                is_default: bool | None = None) -> DashboardView | None:
    view = get_view(db, user, view_id)
    if view is None:
        return None
    if name is not None:
        clean = name.strip()[:MAX_NAME]
        if clean and clean != view.name:
            if any(v.name == clean and v.id != view.id for v in list_views(db, user)):
                raise ValueError("方案名已存在")
            view.name = clean
    if items is not None:
        view.layout_json = dump_layout(sanitize_items(items, user))
    if is_default:
        _clear_other_defaults(db, user, keep_id=view.id)
        view.is_default = 1
    view.updated_at = now_iso()
    db.commit()
    return view


def delete_view(db: Session, user: User, view_id: int) -> bool:
    view = get_view(db, user, view_id)
    if view is None:
        return False
    was_default = bool(view.is_default)
    db.delete(view)
    db.commit()
    if was_default:
        rest = list_views(db, user)
        if rest:                       # 默认被删 → 顶第一个上来
            rest[0].is_default = 1
            db.commit()
        else:                          # 一个都不剩 → 重新按角色预设生成
            ensure_default_view(db, user)
    return True


def duplicate_view(db: Session, user: User, view_id: int, name: str | None = None) -> DashboardView | None:
    src = get_view(db, user, view_id)
    if src is None:
        return None
    items = parse_layout(src)["items"]
    return create_view(db, user, name or f"{src.name} 副本", items)
