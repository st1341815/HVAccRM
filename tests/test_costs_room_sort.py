"""成本明细「房号」列排序的回归测试。

**背景**：成本 → 成本明细的动态表里除「房号」「附图」外都能点表头排序，
房号那一列是静态 `<th>房号</th>`。想按房号把同一小区/同一栋的成本归到一起看，
只能靠肉眼扫。

**实现**：`sort_options` 补 `("room", "房号")`，`_key` 里按 `room_label_short` 走
`utils.room_sort_key`（自然排序，10#201 不能排在 6#401 前面）。
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from starlette.requests import Request


def _fake_request(path: str = "/costs", query: str = "") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": query.encode(),
        }
    )


def _seed(db, admin, spec):
    """spec: [(房号tuple, 费用项目, 金额, 日期), ...]"""
    from app.models import Contract, ContractCost, Customer, Project, Room

    project = Project(name="颐园清苑")
    db.add(project)
    db.flush()

    for i, (room_spec, category, amount, spent_at) in enumerate(spec):
        cust = Customer(name=f"成本排序客户{i}", owner_id=admin.id, status="active", source="自然到店")
        db.add(cust)
        db.flush()
        room = Room(project_id=project.id, building=room_spec[0], room_no=room_spec[1])
        db.add(room)
        db.flush()
        cust.room_id = room.id
        contract = Contract(customer_id=cust.id, no=f"CC{i}", sign_date="2026-10-01",
                            total_amount=100000, status="active", created_by=admin.id)
        db.add(contract)
        db.flush()
        db.add(ContractCost(contract_id=contract.id, category=category, amount=amount,
                            spent_at=spent_at, remark=f"备注{i}", created_by=admin.id))
    db.commit()


def _html(handler, db, user, query: str = "", **params):
    resp = handler(request=_fake_request(query=query), db=db, user=user, **params)
    return bytes(resp.body).decode()


def _col(html: str, index: int) -> list[str]:
    body = html.rsplit("<tbody>", 1)[-1]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    out = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) > index:
            out.append(re.sub(r"<[^>]+>", "", tds[index]).strip())
    return out


def test_room_header_is_sortable(db, admin):
    from app.routers.costs import entries

    _seed(db, admin, [(("6", "401"), "材料成本", 1000.0, "2026-10-01")])
    html = _html(entries, db, admin)
    assert "sort=room" in html, "房号表头没有排序链接"
    # 附图列仍然不可排序
    assert "sort=photo" not in html


def test_room_sorts_naturally_both_directions(db, admin):
    from app.routers.costs import entries

    _seed(db, admin, [
        (("10", "201"), "材料成本", 1000.0, "2026-10-01"),
        (("6", "401"), "材料成本", 2000.0, "2026-10-01"),
        (("2", "101"), "材料成本", 3000.0, "2026-10-01"),
    ])
    asc = _col(_html(entries, db, admin, sort="room", order="asc"), 1)
    assert [r.replace("颐园清苑", "").split("#")[0] for r in asc] == ["2", "6", "10"]

    desc = _col(_html(entries, db, admin, sort="room", order="desc"), 1)
    assert [r.replace("颐园清苑", "").split("#")[0] for r in desc] == ["10", "6", "2"]


def test_room_sort_keeps_filters(db, admin):
    """点房号排序不能把「费用项目」等筛选条件丢掉。"""
    from app.routers.costs import entries

    _seed(db, admin, [(("6", "401"), "材料成本", 1000.0, "2026-10-01")])
    # 成本页的 sort_links 是从 request.query_params 回拼的，所以测试也要给真实 query
    qs = urlencode({"category": "材料成本", "keyword": "备注"})
    html = _html(entries, db, admin, query=qs, category="材料成本", keyword="备注")
    m = re.search(r'href="([^"]*sort=room[^"]*)"', html)
    assert m, "找不到房号排序链接"
    link = m.group(1)
    assert "category=" in link and "keyword=" in link
