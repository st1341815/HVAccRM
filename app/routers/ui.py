"""首页看板与 HTMX 片段。"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user_or_redirect
from ..db import get_db
from ..models import ChangeOrder, Contract, ContractCost, Customer, Payment, Task, User, today_str
from ..permissions import can_see_amount, customer_scope_conditions, has_perm, task_scope_conditions
from ..services import dashboard_layout as layout_svc
from ..services import finance as finance_svc
from ..services import metrics as metrics_svc
from ..services import search as search_svc
from ..services import tasks as task_svc
from ..templating import render

router = APIRouter(tags=["ui"])


@router.get("/")
def dashboard(
    request: Request,
    view: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    """首页看板：按用户自己的布局方案渲染。

    v1.16.0 起看板是「用户自己拼的仪表盘」：
    - 布局来自 dashboard_views（个人私有，首次访问按角色预设实例化）；
    - 每个模块的数据在这里算好内联进页面（首屏一次请求，不搞 N 个 fetch）；
    - 无 JS 时 KPI 与列表仍然可见（服务端直出），图表类才需要 JS。

    性能注意：原实现对每份合同调一次 contract_summary（N+1），已改为
    「一次取全量 + 内存聚合」，查询次数与合同数量无关。
    """
    show_money = has_perm(user, "payment:view") and can_see_amount(user)
    t_scope = task_scope_conditions(db, user)
    today = today_str()
    year = today[:4]
    month = today[:7]

    # ---- 工序（一次查询）
    open_tasks: list[Task] = []
    open_count = 0
    in_progress = 0
    if has_perm(user, "task:view"):
        stmt = select(Task).where(Task.status.notin_(["done", "skipped"]))
        if t_scope is not None:
            stmt = stmt.where(t_scope)
        open_tasks = list(db.scalars(
            stmt.order_by(Task.customer_id, Task.sort_order, Task.id)).all())
        open_count = len(open_tasks)
        in_progress = len({t.customer_id for t in open_tasks})

    # ---- 客户（一次查询）
    cust_stmt = select(Customer)
    c_scope = customer_scope_conditions(db, user)
    if c_scope is not None:
        cust_stmt = cust_stmt.where(c_scope)
    customers = list(db.scalars(cust_stmt).all())

    # ---- 财务汇总（批量聚合，不再逐合同查询）
    finance = None
    year_stats = None
    if show_money:
        contracts = metrics_svc.scoped_contracts(db, user)
        cids = [c.id for c in contracts]
        changes = metrics_svc.changes_by_contract(db, cids)
        pays = metrics_svc.payments_by_contract(db, cids)
        costs = metrics_svc.costs_by_contract(db, cids)

        def received_of(cid: int) -> float:
            return sum((p.amount or 0.0) for p in pays.get(cid, []))

        signed = round(sum(
            (c.total_amount or 0) for c in contracts
            if (c.sign_date or "").startswith(month)), 2)
        received_month = sum(
            (p.amount or 0.0) for ps in pays.values() for p in ps
            if (p.paid_at or "").startswith(month))
        outstanding = sum(
            metrics_svc.contract_sales(c, changes) - received_of(c.id) for c in contracts)

        # 当年经营数据（口径与 /reports 一致：销售额 = 合同额 − 优惠 + 增项；
        # 成本按费用发生日、收款按收款日归集）
        year_sales = sum(
            metrics_svc.contract_sales(c, changes) for c in contracts
            if (c.sign_date or "").startswith(year))
        # 年利润口径：**只认已标记结算的合同**（settled=1 且当年签约），未结算的不计入。
        # 销售额 / 收款 / 成本三个 KPI 仍是全口径，所以年利润会小于「年销售额 − 年成本」
        # —— 这是刻意的：钱没关账之前不确认利润，避免看板虚高。
        settled_ids = {
            c.id for c in contracts if c.settled and (c.sign_date or "").startswith(year)}
        settled_sales = sum(
            metrics_svc.contract_sales(c, changes) for c in contracts if c.id in settled_ids)
        year_received = sum(
            (p.amount or 0.0) for ps in pays.values() for p in ps
            if (p.paid_at or "").startswith(year))
        year_cost = sum(
            (r.amount or 0.0) for rs in costs.values() for r in rs
            if (r.spent_at or "").startswith(year))
        settled_cost = sum(
            (r.amount or 0.0) for rs in costs.values() for r in rs
            if (r.spent_at or "").startswith(year) and r.contract_id in settled_ids)

        finance = {
            "signed_month": signed,
            "received_month": round(received_month, 2),
            "rate": round(received_month / signed * 100, 1) if signed else 0.0,
            "outstanding_total": round(outstanding, 2),
            "debtors": _debtor_rows(db, user),
        }
        year_stats = {
            "sales": round(year_sales, 2),
            "received": round(year_received, 2),
            "cost": round(year_cost, 2),
            "settled_count": len(settled_ids),
            "settled_sales": round(settled_sales, 2),
            "settled_cost": round(settled_cost, 2),
            "profit": round(settled_sales - settled_cost, 2),
        }

    # ---- 客户来源（近 30 天，一次查询）
    since = (date.today() - timedelta(days=30)).isoformat()
    source_dist: dict[str, int] = {}
    for c in customers:
        if (c.created_at or "") >= since:
            source_dist[c.source or "未填写"] = source_dist.get(c.source or "未填写", 0) + 1

    # ---- 布局与模块数据
    # ?view=N 指定方案（页头下拉切换走这个参数）；无效或不属于自己则回落到默认方案
    current = layout_svc.get_view(db, user, view) if view else None
    current = current or layout_svc.ensure_default_view(db, user)
    layout = layout_svc.parse_layout(current)
    widgets = []
    for item in layout["items"]:
        m = metrics_svc.METRICS.get(str(item.get("metric")))
        entry = {
            "id": item.get("id"), "metric": item.get("metric"),
            "kind": item.get("kind"), "span": item.get("span") or 12,
            "h": item.get("h") or 0, "opts": item.get("opts") or {},
            "title": m.title if m else "未知模块", "data": None, "error": None,
        }
        if m is None or not metrics_svc.allowed(user, m):
            entry["error"] = "forbidden"
        else:
            try:
                entry["data"] = metrics_svc.compute(
                    db, user, m.key, entry["opts"])
            except Exception:  # 单个模块算挂了不能拖垮整页
                entry["error"] = "error"
        widgets.append(entry)

    return render(
        request,
        "dashboard.html",
        today=today,
        year=year,
        open_count=open_count,
        open_tasks=open_tasks[:10],
        finance=finance,
        year_stats=year_stats,
        show_money=show_money,
        in_progress=in_progress,
        source_dist=sorted(source_dist.items(), key=lambda kv: kv[1], reverse=True),
        customers_map={c.id: c for c in customers},
        show_task_widgets=has_perm(user, "task:view"),
        # 看板 2.0
        view={"id": current.id, "name": current.name},
        views=[{"id": v.id, "name": v.name, "is_default": bool(v.is_default)}
               for v in layout_svc.list_views(db, user)],
        widgets=widgets,
        catalog=metrics_svc.catalog(user),
        max_views=layout_svc.MAX_VIEWS,
    )


def _debtor_rows(db: Session, user: User) -> list[dict]:
    """Top 欠款客户（批量算，避免再走一遍 N+1）。"""
    try:
        return metrics_svc.compute(db, user, "debt.top", {"n": 5}).get("rows", [])
    except (PermissionError, KeyError):
        return []


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
    stmt = select(Task).where(Task.status.notin_(["done", "skipped"])).order_by(Task.customer_id, Task.sort_order)
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
