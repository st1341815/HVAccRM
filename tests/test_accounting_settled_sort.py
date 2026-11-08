"""核算（已结算）页「结算日」排序的回归测试。

**背景**：「核算 → 核算（已结算）」的明细表里，除「结算日」外每列都能点表头排序，
只有结算日是写死的静态表头。用户报「结算日不能排序」。

**修法**：`services/overview.build_ctx` 增加 `extra_sorts` 参数——页面可以追加
自己私有的可排序列，共用 `SORT_OPTIONS` 不动（否则利润核算页会凭空多出一列无数据的表头，
表头与行内容错位）。核算页传入 `[("settled_at", "结算日")]`，模板里删掉写死的 `<th>结算日</th>`。

本文件盯住三件事：
1. 核算页表头出现「结算日」且是排序链接（带 `sort=settled_at`）；
2. 点它能真的按 settled_at 升降序（行顺序随参数变化）；
3. 利润核算页**不受影响**，不会多出这一列。
"""
from __future__ import annotations

import re

from starlette.requests import Request


def _fake_request(path: str = "/accounting/settlement") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": b"",
        }
    )


def _seed(db, admin, spec):
    """spec: [(合同号, 签订日, 结算日), ...] —— settled=1 才会出现在核算页。"""
    from app.models import Contract, Customer

    out = []
    for i, (no, sign, settled_at) in enumerate(spec):
        cust = Customer(name=f"结算排序客户{i}", owner_id=admin.id, status="active")
        db.add(cust); db.flush()
        c = Contract(customer_id=cust.id, no=no, sign_date=sign, total_amount=10000,
                     status="done", settled=1, settled_at=settled_at, created_by=admin.id)
        db.add(c); db.flush()
        out.append(c)
    return out


def _render(handler, path="/accounting/settlement", **params):
    resp = handler(request=_fake_request(path), db=params.pop("db"), user=params.pop("user"), **params)
    return bytes(resp.body).decode()


def _settled_date_order(html: str) -> list[str]:
    """按出现顺序取出各行的结算日（行内第 10 个 td）。

    页面里有两张表（成本构成 + 明细），取**最后**一个 `<tbody>` 才是明细行。
    """
    body = html.rsplit("<tbody>", 1)[-1]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    out = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) >= 10:
            out.append(tds[9].strip())  # 第 10 个 td = 结算日
    return out


def test_settlement_header_exposes_sortable_settled_at(db, admin):
    """表头「结算日」是可排序链接，且首点默认升序（与其它列同一套交互）。"""
    from app.routers.accounting import settlement_view

    _seed(db, admin, [("ST-1", "2026-10-01", "2026-11-01")])
    html = _render(settlement_view, db=db, user=admin)

    assert "结算日" in html
    assert "sort=settled_at" in html, "结算日表头没有排序链接"
    # 首点统一给 desc（和「签订日」等其它列同一套规则：新→旧），再点才切 asc
    assert re.search(r'href="[^"]*sort=settled_at[^"]*order=desc"', html), (
        "结算日链接的首点排序方向应为 desc"
    )


def test_settled_at_sorts_both_directions(db, admin):
    """?sort=settled_at 真能改变行顺序：desc 新→旧，asc 旧→新。"""
    from app.routers.accounting import settlement_view

    # 签订日统一，避免它干扰结算日在 HTML 里的定位
    _seed(db, admin, [
        ("ST-A", "2026-10-01", "2026-11-01"),
        ("ST-B", "2026-10-01", "2026-11-05"),
        ("ST-C", "2026-10-01", "2026-11-03"),
    ])

    desc = _render(settlement_view, db=db, user=admin, sort="settled_at", order="desc")
    assert _settled_date_order(desc) == ["2026-11-05", "2026-11-03", "2026-11-01"]

    asc = _render(settlement_view, db=db, user=admin, sort="settled_at", order="asc")
    assert _settled_date_order(asc) == ["2026-11-01", "2026-11-03", "2026-11-05"]


def test_settlement_headline_mentions_settled_at(db, admin):
    """标题里的「按XX升/降序」跟随排序字段。"""
    from app.routers.accounting import settlement_view

    _seed(db, admin, [("ST-X", "2026-10-01", "2026-11-02")])
    html = _render(settlement_view, db=db, user=admin, sort="settled_at", order="desc")
    assert "按结算日降序" in html


def test_profit_page_has_no_settled_at_column(db, admin):
    """利润核算页不该被牵连多出一列（它没有结算日列，表头/行会错位）。"""
    from app.routers.accounting import profit_view

    _seed(db, admin, [("ST-P", "2026-10-01", "2026-11-01")])
    html = _render(profit_view, path="/accounting/profit", db=db, user=admin)
    assert "sort=settled_at" not in html, "利润核算页不该出现结算日排序列"
    assert "结算日" not in html


def test_unknown_sort_falls_back_to_sign_date(db, admin):
    """非法 sort 参数回退到签订日，不 500（旧链接/手改 URL 也不能炸）。"""
    from app.routers.accounting import settlement_view

    _seed(db, admin, [("ST-Y", "2026-10-01", "2026-11-02")])
    html = _render(settlement_view, db=db, user=admin, sort="not_a_column", order="asc")
    assert "按签订日升序" in html
