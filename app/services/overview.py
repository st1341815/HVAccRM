"""利润 / 核算的共用聚合与排序。

为什么单独成模块：成本页的「利润核算」和核算页的「核算」如果各写一份毛利算法，
口径迟早会漂移（一个改了另一个没改，数字对不上还很难查）。两边都调这里。
"""
from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import COST_CATEGORIES, Contract, User
from ..permissions import can_see_amount, has_perm, visible_customer_ids
from . import costs as cost_svc
from . import finance as finance_svc
from . import search as search_svc

# (字段, 表头)
SORT_OPTIONS: list[tuple[str, str]] = [
    ("product_type", "产品类型"),
    ("sign_date", "签订日"),
    ("income", "收入"),
    ("cost", "成本"),
    ("gross_profit", "毛利"),
    ("gross_margin", "毛利率"),
    ("received", "已收"),
    ("outstanding", "未收"),
]


def visible_contracts(db: Session, user: User) -> list[Contract]:
    """按数据范围过滤可见合同（核算/成本只算自己有权看的客户）。"""
    allowed = visible_customer_ids(db, user)
    contracts = list(db.scalars(select(Contract).order_by(Contract.sign_date.desc(), Contract.id.desc())).all())
    if allowed is not None:
        contracts = [c for c in contracts if c.customer_id in allowed]
    return contracts


def apply_filters(
    db: Session,
    contracts: list[Contract],
    *,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
) -> list[Contract]:
    """按签订日区间 + 客户关键字（房号/姓名/手机号）过滤合同。"""
    if date_from:
        contracts = [c for c in contracts if (c.sign_date or "") >= date_from]
    if date_to:
        contracts = [c for c in contracts if (c.sign_date or "") <= date_to]
    if customer_q and customer_q.strip():
        cids = set(search_svc.customer_ids_by_keyword(db, customer_q))
        contracts = [c for c in contracts if c.customer_id in cids]
    return contracts


def build_ctx(
    db: Session,
    user: User,
    contracts: list[Contract],
    *,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    only_cost: str = "",
    sort: str = "sign_date",
    order: str = "desc",
    base_path: str = "/accounting/profit",
    extra_sorts: Sequence[tuple[str, str]] = (),
) -> dict:
    """构造利润/核算页面的完整上下文（KPI + 成本构成 + 明细行 + 排序链接）。"""
    contracts = apply_filters(db, contracts, date_from=date_from, date_to=date_to, customer_q=customer_q)

    rows = []
    for c in contracts:
        fin = finance_svc.contract_summary(db, c)
        p = cost_svc.profit_summary(db, c)
        if only_cost and p.cost <= 0:
            continue
        rows.append((c, fin, p))

    # 页面可追加自己的可排序列（如核算页的「结算日」只有已结算合同才有意义），
    # 共用 SORT_OPTIONS 保持两页口径一致，不因某一页的特殊列互相牵连。
    sort_options = list(SORT_OPTIONS) + [s for s in extra_sorts if s[0] not in dict(SORT_OPTIONS)]
    sort_keys = {
        "product_type": lambda r: (r[0].product_type or ""),
        "sign_date": lambda r: (r[0].sign_date or ""),
        "income": lambda r: r[2].income,
        "cost": lambda r: r[2].cost,
        "gross_profit": lambda r: r[2].gross_profit,
        "gross_margin": lambda r: r[2].gross_margin,
        "received": lambda r: r[2].received,
        "outstanding": lambda r: r[1].outstanding,
        "settled_at": lambda r: (r[0].settled_at or ""),
    }
    if sort not in sort_keys or sort not in [k for k, _ in sort_options]:
        sort = "sign_date"
    order = "desc" if order == "desc" else "asc"
    rows.sort(key=sort_keys[sort], reverse=(order == "desc"))

    base_params = {
        k: v
        for k, v in {
            "date_from": date_from,
            "date_to": date_to,
            "customer_q": customer_q,
            "only_cost": only_cost,
        }.items()
        if v
    }
    sort_links = {}
    for key, _ in sort_options:
        new_order = "asc" if (key == sort and order == "desc") else "desc"
        sort_links[key] = f"{base_path}?" + urlencode({**base_params, "sort": key, "order": new_order})

    totals = {
        "income": round(sum(p.income for _, _, p in rows), 2),
        "received": round(sum(p.received for _, _, p in rows), 2),
        "outstanding": round(sum(f.outstanding for _, f, _ in rows), 2),
        "cost": round(sum(p.cost for _, _, p in rows), 2),
        "gross_profit": round(sum(p.gross_profit for _, _, p in rows), 2),
    }
    totals["gross_margin"] = (
        round(totals["gross_profit"] / totals["income"] * 100, 1) if totals["income"] > cost_svc.TOL else 0.0
    )

    return {
        "rows": rows,
        "totals": totals,
        "categories": COST_CATEGORIES,
        "category_totals": cost_svc.category_totals(db, [c.id for c, _, _ in rows]),
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "customer_q": customer_q,
            "only_cost": only_cost,
        },
        "sort": sort,
        "order": order,
        "sort_options": sort_options,
        "sort_links": sort_links,
        "can_edit": has_perm(user, "cost:edit"),
        "can_amount": can_see_amount(user),
    }
