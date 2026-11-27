"""看板指标注册表（v1.16.0 首页看板 2.0）。

设计要点（为什么这么拆）：

1. **一个指标 = 一个函数**，函数只负责「算出数据」，不关心画成什么图。
   同一个 `sales.trend` 既能画折线、也能画柱状、也能画面积图——换图形不改后端。
2. **金额一律返回「元」**，`unit` 标记 `wan` 让前端统一除以 10000 显示。
   不在后端提前格式化，否则图表 tooltip 与列表口径容易对不上。
3. **权限声明在注册表里**（`perms`），接口层再校验一次：前端隐藏 ≠ 安全。
4. **口径与现有页面严格一致**：
   - 销售额 = 合同额 − 优惠 + 增项（按 `sign_date` 归集）
   - 收款按 `paid_at`、成本按 `spent_at`
   - 利润只认已结算合同（`settled=1`），未关账不确认利润
   改口径先改这里，别在模板里另算一套。

聚合策略：数据量在本机 NAS 上是「客户几千 / 合同几百」，一次取数 + 内存聚合
比逐条查询快一个数量级（原 dashboard 的 N+1 就栽在这里）。所有函数都只做
**常数次查询**，禁止在循环里 `db.scalars(...)`。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ChangeOrder,
    Contract,
    ContractCost,
    Customer,
    Payment,
    PaymentPlan,
    Project,
    Room,
    Task,
    User,
    today_str,
)
from ..permissions import can_see_amount, customer_scope_conditions, has_perm, task_scope_conditions

WAN = 10000.0
TOL = 0.005


# --------------------------------------------------------------- 注册表
@dataclass(frozen=True)
class Metric:
    key: str
    title: str
    group: str            # 经营 / 客户 / 施工 / 财务
    kinds: tuple[str, ...]  # kpi line bar area donut hist list heat stack
    perms: tuple[str, ...]
    money: bool           # 是否涉及金额（决定 can_see_amount 校验与单位）
    fn: Callable[..., dict]
    desc: str = ""


METRICS: dict[str, Metric] = {}


def metric(key: str, title: str, group: str, kinds: tuple[str, ...],
           perms: tuple[str, ...] = (), money: bool = False, desc: str = ""):
    def deco(fn: Callable[..., dict]):
        METRICS[key] = Metric(key, title, group, tuple(kinds), tuple(perms), money, fn, desc)
        return fn
    return deco


def allowed(user: User, m: Metric) -> bool:
    """当前用户能不能看这个指标：权限点全满足 + 涉及金额时还要能看金额。"""
    if any(not has_perm(user, p) for p in m.perms):
        return False
    if m.money and not can_see_amount(user):
        return False
    return True


def compute(db: Session, user: User, key: str, opts: dict[str, Any] | None = None) -> dict:
    m = METRICS.get(key)
    if m is None:
        raise KeyError(key)
    if not allowed(user, m):
        raise PermissionError(key)
    data = m.fn(db, user, **(opts or {}))
    data.setdefault("unit", "wan" if m.money else "")
    return data


# --------------------------------------------------------------- 通用取数
# 一个看板页要算十几个指标，每个指标都查一遍全表是浪费。
# 用 Session.info 做「请求级」缓存：同一次请求里共享一次查询结果，请求结束自动失效
# （Session 是请求级的，不用手工清理；跨请求也不会串数据）。
def _cache(db: Session, key: str, loader):
    store = db.info.setdefault("dashboard_metric_cache", {})
    if key not in store:
        store[key] = loader()
    return store[key]


def _sig(cids: list[int]) -> str:
    """cids 的参数指纹。

    ⚠️ 缓存 key **必须**带上它。踩过的坑（v1.16.0 上线当天）：key 写死成
    "costs"，于是「按全部合同算年成本」和「按已结算合同算年利润」共用同一份
    缓存 —— 谁先跑谁的结果被另一方复用。页面上年利润因此拿到了全公司成本
    59,164 而不是那 1 份已结算合同的 6,211，从 +2,289 变成 −50,664。
    这类 bug 单看任何一个函数都发现不了，因为每个函数单独算都是对的。
    """
    return str(hash(frozenset(cids)))


def visible_customer_ids(db: Session, user: User) -> set[int] | None:
    """None = 不限（管理员/全局范围）。"""
    def load():
        cond = customer_scope_conditions(db, user)
        if cond is None:
            return None
        return set(db.scalars(select(Customer.id).where(cond)).all())
    return _cache(db, "cust_ids", load)


def scoped_contracts(db: Session, user: User) -> list[Contract]:
    def load():
        stmt = select(Contract)
        ids = visible_customer_ids(db, user)
        if ids is not None:
            if not ids:
                return []
            stmt = stmt.where(Contract.customer_id.in_(ids))
        return list(db.scalars(stmt).all())
    return _cache(db, "contracts", load)


def scoped_tasks(db: Session, user: User, only_open: bool = False) -> list[Task]:
    def load():
        stmt = select(Task)
        if only_open:
            stmt = stmt.where(Task.status.notin_(["done", "skipped"]))
        cond = task_scope_conditions(db, user)
        if cond is not None:
            stmt = stmt.where(cond)
        return list(db.scalars(stmt.order_by(Task.customer_id, Task.sort_order, Task.id)).all())
    return _cache(db, "tasks_open" if only_open else "tasks_all", load)


def changes_by_contract(db: Session, cids: list[int]) -> dict[int, float]:
    if not cids:
        return {}
    def load():
        out: dict[int, float] = {}
        for co in db.scalars(select(ChangeOrder).where(ChangeOrder.contract_id.in_(cids))).all():
            out[co.contract_id] = out.get(co.contract_id, 0.0) + (co.amount or 0.0)
        return out
    return _cache(db, "changes:" + _sig(cids), load)


def contract_sales(c: Contract, changes: dict[int, float]) -> float:
    """销售额口径：合同额 − 优惠 + 增项（与 /reports、旧看板一致）。"""
    return (c.total_amount or 0.0) - (c.discount or 0.0) + changes.get(c.id, 0.0)


def payments_by_contract(db: Session, cids: list[int]) -> dict[int, list[Payment]]:
    if not cids:
        return {}
    def load():
        out: dict[int, list[Payment]] = {}
        for p in db.scalars(select(Payment).where(Payment.contract_id.in_(cids))).all():
            out.setdefault(p.contract_id, []).append(p)
        return out
    return _cache(db, "payments:" + _sig(cids), load)


def costs_by_contract(db: Session, cids: list[int]) -> dict[int, list[ContractCost]]:
    if not cids:
        return {}
    def load():
        out: dict[int, list[ContractCost]] = {}
        for r in db.scalars(select(ContractCost).where(ContractCost.contract_id.in_(cids))).all():
            out.setdefault(r.contract_id, []).append(r)
        return out
    return _cache(db, "costs:" + _sig(cids), load)


def month_labels(months: int) -> list[str]:
    """最近 N 个月（含本月），升序，格式 YYYY-MM。"""
    months = max(1, min(int(months or 12), 36))
    today = date.today()
    y, m = today.year, today.month
    out: list[str] = []
    for i in range(months - 1, -1, -1):
        mm, yy = m - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append(f"{yy}-{mm:02d}")
    return out


def _pct(part: float, total: float) -> float:
    return round(part / total * 100, 1) if abs(total) > TOL else 0.0


def _short_month(labels: list[str]) -> list[str]:
    return [f"{int(x[5:7])}月" for x in labels]


# =============================================================== KPI 指标
@metric("kpi.sales", "年销售额", "经营", ("kpi",), ("contract:view",), money=True,
        desc="当年签约合同额 − 优惠 + 增项")
def _kpi_sales(db: Session, user: User) -> dict:
    year = today_str()[:4]
    cs = [c for c in scoped_contracts(db, user) if (c.sign_date or "").startswith(year)]
    ch = changes_by_contract(db, [c.id for c in cs])
    total = sum(contract_sales(c, ch) for c in cs)
    prev_year = str(int(year) - 1)
    pcs = [c for c in scoped_contracts(db, user) if (c.sign_date or "").startswith(prev_year)]
    pch = changes_by_contract(db, [c.id for c in pcs])
    prev = sum(contract_sales(c, pch) for c in pcs)
    return {"value": round(total, 2), "delta": _pct(total - prev, prev), "label": f"{year} 年销售额"}


@metric("kpi.received", "年收款", "财务", ("kpi",), ("payment:view",), money=True)
def _kpi_received(db: Session, user: User) -> dict:
    year = today_str()[:4]
    cids = [c.id for c in scoped_contracts(db, user)]
    total = sum(
        (p.amount or 0.0) for ps in payments_by_contract(db, cids).values()
        for p in ps if (p.paid_at or "").startswith(year)
    )
    return {"value": round(total, 2), "label": f"{year} 年收款"}


@metric("kpi.cost", "年成本", "财务", ("kpi",), ("cost:view",), money=True)
def _kpi_cost(db: Session, user: User) -> dict:
    year = today_str()[:4]
    cids = [c.id for c in scoped_contracts(db, user)]
    total = sum(
        (r.amount or 0.0) for rs in costs_by_contract(db, cids).values()
        for r in rs if (r.spent_at or "").startswith(year)
    )
    return {"value": round(total, 2), "label": f"{year} 年成本"}


@metric("kpi.profit", "年利润", "财务", ("kpi",), ("cost:view", "settlement:view"), money=True,
        desc="只认已结算合同，未关账不确认利润")
def _kpi_profit(db: Session, user: User) -> dict:
    year = today_str()[:4]
    cs = [c for c in scoped_contracts(db, user)
          if c.settled and (c.sign_date or "").startswith(year)]
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    sales = sum(contract_sales(c, ch) for c in cs)
    cost = sum(
        (r.amount or 0.0) for rs in costs_by_contract(db, cids).values()
        for r in rs if (r.spent_at or "").startswith(year)
    )
    return {"value": round(sales - cost, 2), "label": f"{year} 年利润"}


@metric("kpi.outstanding", "未收总额", "财务", ("kpi",), ("payment:view",), money=True)
def _kpi_outstanding(db: Session, user: User) -> dict:
    cs = scoped_contracts(db, user)
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    pays = payments_by_contract(db, cids)
    total = 0.0
    for c in cs:
        received = sum((p.amount or 0.0) for p in pays.get(c.id, []))
        total += contract_sales(c, ch) - received
    return {"value": round(total, 2), "label": "未收总额"}


@metric("kpi.inprogress", "在施工地", "施工", ("kpi",), ("task:view",))
def _kpi_inprogress(db: Session, user: User) -> dict:
    open_tasks = scoped_tasks(db, user, only_open=True)
    return {"value": len({t.customer_id for t in open_tasks}), "label": "在施工地"}


@metric("kpi.open_tasks", "未完成工序", "施工", ("kpi",), ("task:view",))
def _kpi_open_tasks(db: Session, user: User) -> dict:
    return {"value": len(scoped_tasks(db, user, only_open=True)), "label": "未完成工序"}


@metric("kpi.month_signed", "本月签约", "经营", ("kpi",), ("contract:view",), money=True)
def _kpi_month_signed(db: Session, user: User) -> dict:
    month = today_str()[:7]
    cs = [c for c in scoped_contracts(db, user) if (c.sign_date or "").startswith(month)]
    ch = changes_by_contract(db, [c.id for c in cs])
    return {"value": round(sum(contract_sales(c, ch) for c in cs), 2), "label": f"{month} 签约额"}


@metric("kpi.recovery", "回款率", "财务", ("kpi",), ("payment:view",))
def _kpi_recovery(db: Session, user: User) -> dict:
    cs = scoped_contracts(db, user)
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    pays = payments_by_contract(db, cids)
    total = sum(contract_sales(c, ch) for c in cs)
    received = sum((p.amount or 0.0) for ps in pays.values() for p in ps)
    return {"value": _pct(received, total), "label": "回款率（已收 / 签约）"}


# =============================================================== 趋势与分布
@metric("sales.trend", "签约 · 收款 · 成本趋势", "经营",
        ("line", "area", "bar"), ("payment:view",), money=True,
        desc="近 N 个月三条曲线，口径与报表页一致")
def _sales_trend(db: Session, user: User, months: int = 12) -> dict:
    labels = month_labels(months)
    idx = {m: i for i, m in enumerate(labels)}
    n = len(labels)
    sales = [0.0] * n
    received = [0.0] * n
    cost = [0.0] * n

    cs = scoped_contracts(db, user)
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    for c in cs:
        k = (c.sign_date or "")[:7]
        if k in idx:
            sales[idx[k]] += contract_sales(c, ch)
    for ps in payments_by_contract(db, cids).values():
        for p in ps:
            k = (p.paid_at or "")[:7]
            if k in idx:
                received[idx[k]] += p.amount or 0.0
    for rs in costs_by_contract(db, cids).values():
        for r in rs:
            k = (r.spent_at or "")[:7]
            if k in idx:
                cost[idx[k]] += r.amount or 0.0

    return {
        "labels": _short_month(labels),
        "raw_labels": labels,
        "series": [
            {"name": "签约额", "data": [round(v, 2) for v in sales]},
            {"name": "收款", "data": [round(v, 2) for v in received]},
            {"name": "成本", "data": [round(v, 2) for v in cost]},
        ],
    }


@metric("profit.trend", "月度毛利", "财务", ("bar", "line"), ("cost:view",), money=True,
        desc="只认已结算合同：已结算销售额 − 该合同当年成本")
def _profit_trend(db: Session, user: User, months: int = 6) -> dict:
    labels = month_labels(months)
    idx = {m: i for i, m in enumerate(labels)}
    n = len(labels)
    sales = [0.0] * n
    cost = [0.0] * n
    cs = [c for c in scoped_contracts(db, user) if c.settled]
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    for c in cs:
        k = (c.sign_date or "")[:7]
        if k in idx:
            sales[idx[k]] += contract_sales(c, ch)
    for rs in costs_by_contract(db, cids).values():
        for r in rs:
            k = (r.spent_at or "")[:7]
            if k in idx:
                cost[idx[k]] += r.amount or 0.0
    return {
        "labels": _short_month(labels),
        "raw_labels": labels,
        "series": [
            {"name": "毛利", "data": [round(sales[i] - cost[i], 2) for i in range(n)]},
        ],
    }


@metric("source.donut", "客户来源占比", "客户", ("donut", "bar"), ("customer:view",),
        desc="近 N 天新增客户按来源分组")
def _source_donut(db: Session, user: User, days: int = 30) -> dict:
    since = (date.today() - timedelta(days=max(1, min(int(days or 30), 365)))).isoformat()
    ids = visible_customer_ids(db, user)
    stmt = select(Customer)
    if ids is not None:
        if not ids:
            return {"labels": [], "series": [], "total": 0}
        stmt = stmt.where(Customer.id.in_(ids))
    rows = [c for c in db.scalars(stmt).all() if (c.created_at or "") >= since]
    dist: dict[str, int] = {}
    for c in rows:
        k = (c.source or "").strip() or "未填写"
        dist[k] = dist.get(k, 0) + 1
    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:6]
    return {
        "labels": [k for k, _ in top],
        "series": [{"name": "新增客户", "data": [v for _, v in top]}],
        "total": len(rows),
    }


@metric("contract.hist", "合同金额分布", "经营", ("hist", "bar"), ("contract:view",), money=True,
        desc="按金额区间统计合同份数（直方图）")
def _contract_hist(db: Session, user: User, year_only: bool = True) -> dict:
    year = today_str()[:4]
    cs = scoped_contracts(db, user)
    if year_only:
        cs = [c for c in cs if (c.sign_date or "").startswith(year)]
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    buckets = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 10**9)]
    names = ["<5万", "5-10万", "10-15万", "15-20万", "20-30万", ">30万"]
    counts = [0] * len(buckets)
    for c in cs:
        v = contract_sales(c, ch) / WAN
        for i, (lo, hi) in enumerate(buckets):
            if lo <= v < hi:
                counts[i] += 1
                break
    return {"labels": names, "series": [{"name": "合同份数", "data": counts}],
            "total": len(cs)}


@metric("product.bar", "产品意向分布", "客户", ("bar", "donut"), ("customer:view",),
        desc="客户意向产品（多选，按出现次数统计）")
def _product_bar(db: Session, user: User, limit: int = 8) -> dict:
    ids = visible_customer_ids(db, user)
    stmt = select(Customer)
    if ids is not None:
        if not ids:
            return {"labels": [], "series": []}
        stmt = stmt.where(Customer.id.in_(ids))
    dist: dict[str, int] = {}
    for c in db.scalars(stmt).all():
        for p in c.product_list:
            dist[p] = dist.get(p, 0) + 1
    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[: max(1, min(int(limit or 8), 12))]
    return {"labels": [k for k, _ in top], "series": [{"name": "意向客户数", "data": [v for _, v in top]}]}


@metric("project.bar", "楼盘客户数 Top", "客户", ("bar",), ("customer:view",),
        desc="按楼盘统计客户数，取前 N")
def _project_bar(db: Session, user: User, limit: int = 10) -> dict:
    ids = visible_customer_ids(db, user)
    stmt = (
        select(Project.name, Room.project_id, Room.id, Customer.id)
        .join(Room, Customer.room_id == Room.id)
        .join(Project, Room.project_id == Project.id)
    )
    if ids is not None:
        if not ids:
            return {"labels": [], "series": []}
        stmt = stmt.where(Customer.id.in_(ids))
    dist: dict[str, int] = {}
    for name, _pid, _rid, _cid in db.execute(stmt).all():
        dist[name] = dist.get(name, 0) + 1
    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[: max(1, min(int(limit or 10), 15))]
    return {"labels": [k for k, _ in top], "series": [{"name": "客户数", "data": [v for _, v in top]}]}


@metric("debt.age", "欠款账龄", "财务", ("bar", "hist"), ("payment:view",), money=True,
        desc="按应收计划逾期天数分桶（元）")
def _debt_age(db: Session, user: User) -> dict:
    cs = scoped_contracts(db, user)
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    pays = payments_by_contract(db, cids)
    today = today_str()
    buckets = [(0, 30), (31, 60), (61, 90), (91, 3650)]
    names = ["1-30 天", "31-60 天", "61-90 天", "90 天以上"]
    sums = [0.0] * 4
    for c in cs:
        total = contract_sales(c, ch)
        received = sum((p.amount or 0.0) for p in pays.get(c.id, []))
        gap = total - received
        if gap <= TOL:
            continue
        # 用最早未付清的计划到期日算账龄；没有计划就按签约日
        plans = sorted([p for p in c.plans if (p.amount or 0) > (p.paid_amount or 0)],
                       key=lambda p: (p.sort_order or 0, p.id or 0))
        due = plans[0].due_date if plans and plans[0].due_date else (c.sign_date or today)
        try:
            d0 = date.fromisoformat((due or today)[:10])
            days = (date.today() - d0).days
        except ValueError:
            days = 0
        if days <= 0:
            continue
        for i, (lo, hi) in enumerate(buckets):
            if lo <= days <= hi:
                sums[i] += gap
                break
    return {"labels": names, "series": [{"name": "未收金额", "data": [round(v, 2) for v in sums]}]}


# =============================================================== 施工类
@metric("task.stage", "工序阶段分布", "施工", ("stack", "bar"), ("task:view",),
        desc="每个工序节点上未完成 / 施工中 / 待开工的数量")
def _task_stage(db: Session, user: User) -> dict:
    tasks = scoped_tasks(db, user, only_open=True)
    order = {s: i for i, s in enumerate(
        ["上门勘测", "前期施工", "后期施工", "调试验收"])}
    stages = sorted({t.stage for t in tasks}, key=lambda s: (order.get(s, 99), s))
    if not stages:
        return {"labels": [], "series": [], "rows": []}
    status_names = [("pending", "未就绪"), ("ready", "待开工"), ("doing", "施工中")]
    matrix: dict[str, dict[str, int]] = {s: {k: 0 for k, _ in status_names} for s in stages}
    for t in tasks:
        if t.stage in matrix and t.status in matrix[t.stage]:
            matrix[t.stage][t.status] += 1
    return {
        "labels": stages,
        "series": [{"name": label, "data": [matrix[s][key] for s in stages]}
                   for key, label in status_names],
    }


@metric("task.delay", "延误原因", "施工", ("donut", "bar"), ("task:view",),
        desc="近 N 天记录延误原因的工序，按原因分组")
def _task_delay(db: Session, user: User, days: int = 90) -> dict:
    since = (date.today() - timedelta(days=max(1, min(int(days or 90), 365)))).isoformat()
    tasks = [t for t in scoped_tasks(db, user) if (t.updated_at or "") >= since and t.delay_reason]
    dist: dict[str, int] = {}
    for t in tasks:
        k = (t.delay_reason or "").strip().replace("\n", " ")[:10] or "未填写"
        dist[k] = dist.get(k, 0) + 1
    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:6]
    return {"labels": [k for k, _ in top], "series": [{"name": "延误条数", "data": [v for _, v in top]}],
            "total": len(tasks)}


@metric("staff.load", "人员负荷", "施工", ("bar",), ("task:view",),
        desc="未完成工序按负责人统计")
def _staff_load(db: Session, user: User) -> dict:
    tasks = scoped_tasks(db, user, only_open=True)
    dist: dict[str, int] = {}
    for t in tasks:
        name = t.assignee.display_name if t.assignee else "未指派"
        dist[name] = dist.get(name, 0) + 1
    top = sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return {"labels": [k for k, _ in top], "series": [{"name": "未完成工序", "data": [v for _, v in top]}]}


@metric("site.heatmap", "施工量热力", "施工", ("heat",), ("task:view",),
        desc="近 N 周每天的完工工序数（GitHub 贡献图样式）")
def _site_heatmap(db: Session, user: User, weeks: int = 26) -> dict:
    weeks = max(4, min(int(weeks or 26), 52))
    today = date.today()
    # 以本周日结尾，向前推 weeks 周
    end = today
    start = end - timedelta(days=weeks * 7 - 1)
    tasks = scoped_tasks(db, user)
    counts: dict[str, int] = {}
    for t in tasks:
        for key in (t.actual_start, t.actual_end):
            if key and start.isoformat() <= key[:10] <= end.isoformat():
                counts[key[:10]] = counts.get(key[:10], 0) + 1
    cells: list[dict] = []
    d = start
    while d <= end:
        cells.append({"d": d.isoformat(), "v": counts.get(d.isoformat(), 0)})
        d += timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat(), "cells": cells,
            "max": max(counts.values()) if counts else 0}


@metric("task.due", "即将到期工序", "施工", ("list",), ("task:view",),
        desc="未来 N 天内到期的未完成工序")
def _task_due(db: Session, user: User, days: int = 7, n: int = 6) -> dict:
    days = max(1, min(int(days or 7), 60))
    end = (date.today() + timedelta(days=days)).isoformat()
    today = today_str()
    rows = []
    for t in scoped_tasks(db, user, only_open=True):
        if t.planned_end and today <= t.planned_end[:10] <= end:
            rows.append(t)
    rows.sort(key=lambda t: t.planned_end or "")
    out = []
    for t in rows[: max(1, min(int(n or 6), 20))]:
        cust = t.customer
        out.append({
            "label": (cust.room_label_short if cust else "") or f"#{t.customer_id}",
            "value": t.planned_end or "",
            "meta": t.stage,
            "status": t.status,
            "href": f"/customers/{t.customer_id}?tab=tasks",
        })
    return {"rows": out, "columns": ["房号", "计划完成"], "total": len(rows)}


# =============================================================== 列表类
@metric("debt.top", "Top 欠款客户", "财务", ("list",), ("payment:view",), money=True,
        desc="未收金额最高的 N 户")
def _debt_top(db: Session, user: User, n: int = 5) -> dict:
    cs = scoped_contracts(db, user)
    cids = [c.id for c in cs]
    ch = changes_by_contract(db, cids)
    pays = payments_by_contract(db, cids)
    per_cust: dict[int, float] = {}
    cust_obj: dict[int, Customer] = {}
    for c in cs:
        received = sum((p.amount or 0.0) for p in pays.get(c.id, []))
        gap = contract_sales(c, ch) - received
        if gap > TOL:
            per_cust[c.customer_id] = per_cust.get(c.customer_id, 0.0) + gap
            cust_obj[c.customer_id] = c.customer
    top = sorted(per_cust.items(), key=lambda kv: kv[1], reverse=True)[: max(1, min(int(n or 5), 20))]
    rows = [{
        "label": (cust_obj[cid].room_label_short if cust_obj.get(cid) else "") or f"#{cid}",
        "value": round(v, 2),
        "meta": "",
        "href": f"/customers/{cid}?tab=finance",
    } for cid, v in top]
    return {"rows": rows, "columns": ["房号", "未收"], "total": len(per_cust)}


@metric("task.open", "未完成工序", "施工", ("list",), ("task:view",),
        desc="按客户归组的未完成工序")
def _task_open(db: Session, user: User, n: int = 8) -> dict:
    tasks = scoped_tasks(db, user, only_open=True)[: max(1, min(int(n or 8), 30))]
    rows = []
    for t in tasks:
        cust = t.customer
        rows.append({
            "label": (cust.room_label_short if cust else "") or f"#{t.customer_id}",
            "value": t.stage,
            "meta": t.status,
            "href": f"/customers/{t.customer_id}?tab=tasks",
        })
    return {"rows": rows, "columns": ["房号", "工序"], "total": len(tasks)}


@metric("site.progress", "在施工地进度", "施工", ("list",), ("task:view",),
        desc="每个在建工地已完成工序 / 总工序 + 当前节点")
def _site_progress(db: Session, user: User, n: int = 6) -> dict:
    all_tasks = scoped_tasks(db, user)
    by_cust: dict[int, list[Task]] = {}
    for t in all_tasks:
        by_cust.setdefault(t.customer_id, []).append(t)
    rows = []
    for cid, ts in by_cust.items():
        done = sum(1 for t in ts if t.status in ("done", "skipped"))
        total = len(ts)
        if done >= total:
            continue
        cur = next((t for t in ts if t.status == "doing"), None) or \
              next((t for t in ts if t.status == "ready"), None) or ts[0]
        cust = cur.customer
        rows.append({
            "label": (cust.room_label_short if cust else "") or f"#{cid}",
            "value": f"{done}/{total}",
            "meta": cur.stage,
            "ratio": round(done / total * 100, 1) if total else 0.0,
            "href": f"/customers/{cid}?tab=tasks",
        })
    rows.sort(key=lambda r: r["ratio"])
    return {"rows": rows[: max(1, min(int(n or 6), 20))], "columns": ["房号", "进度"], "total": len(rows)}


@metric("customer.new", "新增客户", "客户", ("kpi", "line"), ("customer:view",),
        desc="近 N 天新增客户数")
def _customer_new(db: Session, user: User, days: int = 30) -> dict:
    since = (date.today() - timedelta(days=max(1, min(int(days or 30), 365)))).isoformat()
    ids = visible_customer_ids(db, user)
    stmt = select(Customer)
    if ids is not None:
        if not ids:
            return {"value": 0, "label": f"近 {days} 天新增"}
        stmt = stmt.where(Customer.id.in_(ids))
    n = sum(1 for c in db.scalars(stmt).all() if (c.created_at or "") >= since)
    return {"value": n, "label": f"近 {days} 天新增"}


# --------------------------------------------------------------- 目录（模块库）
def catalog(user: User) -> list[dict]:
    """给前端模块库用的指标目录：只列当前用户有权限的，按 group 分好。"""
    out = []
    for m in METRICS.values():
        if not allowed(user, m):
            continue
        out.append({
            "key": m.key,
            "title": m.title,
            "group": m.group,
            "kinds": list(m.kinds),
            "desc": m.desc,
        })
    return out
