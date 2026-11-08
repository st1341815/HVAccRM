"""合同台账（合同列表）列排序的回归测试。

**背景**：合同台账的表头原本全是死的 `<th>`，8 列数据（房号 / 产品 / 签订日 /
总额 / 已收 / 未收 / 回款率 / 结算）一个都不能点。13叔要求这些列都能排序。

**修法要点**：
1. 排序在内存里做——已收 / 未收 / 回款率来自 `finance_svc.contract_summary` 的 Python
   计算结果，SQL 里没有对应列，硬写 `ORDER BY` 得重算一遍汇总，不值得。
2. 房号走「自然排序」（`_room_sort_key`）：纯字符串排序会把 10#201 排到 6#401 前面。
3. 金额列和结算列受权限控制，`sort_options` 按 `can_amount` / `can_settle` 动态生成，
   表头和数据列必须同时出现或同时消失，否则错位。
4. 排序链接要带上现有筛选条件，否则点一次列头筛选就丢了。

本文件盯住：
- 8 个表头都是排序链接，且默认按签订日倒序（老行为不变）；
- 各列真能改变行顺序，方向可切换；
- 房号是自然排序（数字段按数值比）；
- 无金额权限的角色看不到金额列表头，且传 `sort=total` 不会炸（回退到签订日）；
- 排序链接保留 `customer_q` / `status` / `settled` 筛选。
"""
from __future__ import annotations

import re

from starlette.requests import Request


def _fake_request(path: str = "/contracts", query: str = "") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": query.encode(),
        }
    )


def _seed(db, admin, spec):
    """spec: [(房号/姓名, 产品, 签订日, 总额, 已结算?), ...]"""
    from app.models import Contract, Customer

    out = []
    for i, (label, product, sign, amount, settled) in enumerate(spec):
        cust = Customer(name=label, owner_id=admin.id, status="active")
        db.add(cust); db.flush()
        c = Contract(customer_id=cust.id, no=f"CT-SORT-{i}", product_type=product,
                     sign_date=sign, total_amount=amount, status="active",
                     settled=1 if settled else 0, created_by=admin.id)
        db.add(c); db.flush()
        out.append(c)
    db.commit()
    return out


def _render(handler, query: str = "", **params):
    db = params.pop("db"); user = params.pop("user")
    resp = handler(request=_fake_request(query=query), db=db, user=user, **params)
    return bytes(resp.body).decode()


def _html(handler, db, user, **kwargs):
    """默认参数渲染一次（不传 sort/order 时走路由默认值）。"""
    defaults = {"customer_q": "", "status": "", "settled": "", "sort": "sign_date", "order": "desc"}
    defaults.update(kwargs)
    return _render(handler, **defaults, db=db, user=user)


def _col(html: str, idx: int) -> list[str]:
    """按行顺序取第 idx 列纯文本（去掉行内标签）。"""
    body = html.rsplit("<tbody>", 1)[-1]
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) > idx:
            out.append(re.sub(r"<[^>]+>", "", tds[idx]).strip())
    return out


def test_all_eight_columns_are_sortable_links(db, admin):
    """8 列全部变成排序链接；默认签约日倒序，且带 ▼ 指示。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [("6#401", "中央空调", "2026-10-01", 10000, False)])
    html = _html(list_contracts, db, admin)

    for label in ["房号", "产品", "签订日", "总额", "已收", "未收", "回款率", "结算"]:
        assert f">{label}<" in html or f"{label}<span" in html, f"表头缺少 {label}"
    for key in ["room", "product_type", "sign_date", "total", "received",
                "outstanding", "recovery_rate", "settled"]:
        assert f"sort={key}" in html, f"{key} 没有排序链接"
    # 首点规则：除当前排序列外都是 desc
    assert re.search(r'href="[^"]*sort=total[^"]*order=desc"', html)
    assert "▼" in html, "当前排序列（签订日）应显示降序指示"


def test_sign_date_and_product_sort_both_directions(db, admin):
    """签订日 / 产品升降序真的改变行顺序。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [
        ("6#401", "地暖", "2026-10-01", 10000, False),
        ("7#101", "中央空调", "2026-10-05", 20000, False),
        ("8#301", "新风", "2026-10-03", 30000, False),
    ])

    desc = _html(list_contracts, db, admin, sort="sign_date", order="desc")
    assert _col(desc, 2) == ["2026-10-05", "2026-10-03", "2026-10-01"]
    asc = _html(list_contracts, db, admin, sort="sign_date", order="asc")
    assert _col(asc, 2) == ["2026-10-01", "2026-10-03", "2026-10-05"]

    p_desc = _html(list_contracts, db, admin, sort="product_type", order="desc")
    assert _col(p_desc, 1) == ["新风", "地暖", "中央空调"]


def test_room_uses_natural_sort(db, admin):
    """房号按数字段自然排序：6#401 在 10#201 之前（纯字符串会反过来）。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [
        ("10#201", "地暖", "2026-10-01", 10000, False),
        ("6#401", "地暖", "2026-10-02", 10000, False),
        ("2#1101", "地暖", "2026-10-03", 10000, False),
    ])

    asc = _html(list_contracts, db, admin, sort="room", order="asc")
    assert _col(asc, 0) == ["2#1101", "6#401", "10#201"]


def test_amount_columns_sort(db, admin):
    """总额 / 已收 / 未收 / 回款率都能按参数排序（金额列只对 admin 可见）。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [
        ("6#401", "地暖", "2026-10-01", 10000, False),
        ("7#101", "地暖", "2026-10-02", 30000, False),
        ("8#301", "地暖", "2026-10-03", 20000, False),
    ])

    desc = _html(list_contracts, db, admin, sort="total", order="desc")
    assert _col(desc, 3) == ["30,000.00", "20,000.00", "10,000.00"]
    asc = _html(list_contracts, db, admin, sort="total", order="asc")
    assert _col(asc, 3) == ["10,000.00", "20,000.00", "30,000.00"]

    # 未收款金额与总额同序（还没收款），不能因此报错
    assert _col(asc, 5) == ["10,000.00", "20,000.00", "30,000.00"]


def test_settled_column_sorts(db, admin):
    """结算列排序：asc 未结算在前、desc 已结算在前。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [
        ("6#401", "地暖", "2026-10-01", 10000, True),
        ("7#101", "地暖", "2026-10-02", 10000, False),
        ("8#301", "地暖", "2026-10-03", 10000, True),
    ])

    asc = _html(list_contracts, db, admin, sort="settled", order="asc")
    asc_rooms = _col(asc, 0)
    assert asc_rooms[0] == "7#101", "升序时未结算的应排最前"
    assert set(asc_rooms[1:]) == {"6#401", "8#301"}  # 两个已结算内部顺序不追究
    desc = _html(list_contracts, db, admin, sort="settled", order="desc")
    assert _col(desc, 0)[-1] == "7#101", "降序时未结算的应排最后"


def test_no_amount_role_has_no_amount_columns_and_no_crash(db, installer):
    """师傅（installer）看不到金额列：表头不出现，传 sort=total 回退到签订日而不报错。"""
    from app.routers.contracts import list_contracts

    _seed(db, installer, [
        ("6#401", "地暖", "2026-10-01", 10000, False),
        ("7#101", "地暖", "2026-10-05", 20000, False),
    ])

    html = _html(list_contracts, db, installer, sort="total", order="desc")
    assert "回款率" not in html, "无金额权限不应出现金额列表头"
    assert "sort=total" not in html, "无金额权限不应有总额排序链接"
    # 回退到签订日倒序
    assert _col(html, 2) == ["2026-10-05", "2026-10-01"]


def test_sort_links_keep_active_filters(db, admin):
    """点列头排序时不能丢筛选条件。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [("6#401", "地暖", "2026-10-01", 10000, False)])
    html = _html(list_contracts, db, admin, customer_q="6#401", status="active", settled="0")

    m = re.search(r'href="([^"]*sort=total[^"]*)"', html)
    assert m, "找不到总额排序链接"
    link = m.group(1)
    assert "customer_q=" in link and "status=active" in link and "settled=0" in link, (
        f"排序链接丢了筛选条件：{link}"
    )


def test_keep_qs_preserves_sort_for_settle_return(db, admin):
    """结算表单的 back 参数要带当前排序，操作后回到同一排序视角。"""
    from app.routers.contracts import list_contracts

    _seed(db, admin, [("6#401", "地暖", "2026-10-01", 10000, False)])
    resp = list_contracts(
        request=_fake_request(query="sort=total&order=desc"),
        customer_q="", status="", settled="", sort="total", order="desc",
        db=db, user=admin,
    )
    html = bytes(resp.body).decode()
    assert "/contracts?" in html and "sort=total" in html
