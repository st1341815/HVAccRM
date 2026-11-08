"""收款流程（收款 → 收款流水）7 列排序的回归测试。

**背景**：收款流水表每列都是静态表头，想「按金额从大到小看最近收了多少钱」「按房号把
同一个小区的收款归到一起」，只能自己数。

**实现**：`list_payments` 加 `sort`/`order`，全部在内存里排 —— 房号跨表取
`customer.room_label_short`（property）、收款人是 `users` 表里的中文名，
SQL 层没有对应列；凭证号走自然排序（`P10` 不能排到 `P6` 前面）。

本文件盯住：
1. 7 个目标列头都是排序链接，付款截图列不是；
2. 收款日期 / 金额 / 房号 / 方式 / 凭证号 / 收款人 / 备注 七个字段升降序都对；
3. 点列头排序时保留 `customer_q` / `date_from` / `method` 等筛选；
4. 默认（不传 sort）仍是收款日期倒序。
"""
from __future__ import annotations

import re

from starlette.requests import Request


def _fake_request(path: str = "/payments", query: str = "") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": query.encode(),
        }
    )


def _seed(db, admin, spec):
    """spec: [(房号tuple, 金额, 收款日, 方式, 凭证号, 收款人, 备注), ...]"""
    from app.models import Contract, Customer, Payment, Project, Room, User

    boss = User(username="boss", full_name="王经理", password_hash="x",
                role="finance", data_scope="all", is_active=1)
    db.add(boss)
    project = Project(name="颐园清苑")
    db.add(project)
    db.flush()

    out = []
    for i, (room_spec, amount, paid_at, method, voucher, receiver, remark) in enumerate(spec):
        cust = Customer(name=f"收款排序客户{i}", owner_id=admin.id, status="active", source="自然到店")
        db.add(cust)
        db.flush()
        if room_spec:
            room = Room(project_id=project.id, building=room_spec[0], room_no=room_spec[1])
            db.add(room)
            db.flush()
            cust.room_id = room.id
        contract = Contract(customer_id=cust.id, no=f"PC{i}", sign_date="2026-10-01",
                            total_amount=100000, status="active", created_by=admin.id)
        db.add(contract)
        db.flush()
        pay = Payment(
            contract_id=contract.id, amount=amount, paid_at=paid_at, method=method,
            voucher_no=voucher, remark=remark,
            received_by=boss.id if receiver == "boss" else admin.id,
        )
        db.add(pay)
        db.flush()
        out.append(pay)
    db.commit()
    return out


def _html(handler, db, user, **params):
    resp = handler(request=_fake_request(), db=db, user=user, **params)
    return bytes(resp.body).decode()


def _cell_text(cell: str) -> str:
    return re.sub(r"<[^>]+>", "", cell).replace("&amp;", "&").strip()


def _col(html: str, index: int) -> list[str]:
    body = html.rsplit("<tbody>", 1)[-1]
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
    out = []
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) > index:
            out.append(_cell_text(tds[index]))
    return out


def test_seven_headers_are_sortable_but_photo_is_not(db, admin):
    from app.routers.payments import list_payments

    _seed(db, admin, [(("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "首款")])
    html = _html(list_payments, db, admin)

    for key, label in [
        ("paid_at", "收款日期"), ("amount", "金额"), ("room", "房号"),
        ("method", "方式"), ("voucher_no", "凭证号"), ("received_by", "收款人"),
        ("remark", "备注"),
    ]:
        assert f"sort={key}" in html, f"{label} 表头没有排序链接"
        assert f">{label}" in html, f"找不到表头 {label}"
    # 付款截图列不参与排序
    assert "sort=photo" not in html
    # 默认列是收款日期，且首点给 asc（再点回 desc —— 首点规则与其它页一致：当前 desc 则切 asc）
    assert re.search(r"sort=paid_at[^\"']*order=asc", html), "当前排序列应指向反方向"


def test_paid_at_sorts_both_directions(db, admin):
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "a"),
        (("6", "402"), 1000.0, "2026-10-03", "微信", "P2", "boss", "b"),
        (("6", "403"), 1000.0, "2026-10-02", "微信", "P3", "boss", "c"),
    ])
    desc = _col(_html(list_payments, db, admin, sort="paid_at", order="desc"), 0)
    assert desc == ["2026-10-03", "2026-10-02", "2026-10-01"]
    asc = _col(_html(list_payments, db, admin, sort="paid_at", order="asc"), 0)
    assert asc == ["2026-10-01", "2026-10-02", "2026-10-03"]


def test_amount_sorts_both_directions(db, admin):
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 5000.0, "2026-10-01", "微信", "P1", "boss", "a"),
        (("6", "402"), 30000.0, "2026-10-01", "微信", "P2", "boss", "b"),
        (("6", "403"), 12000.0, "2026-10-01", "微信", "P3", "boss", "c"),
    ])
    desc = _col(_html(list_payments, db, admin, sort="amount", order="desc"), 1)
    assert desc == ["30,000.00", "12,000.00", "5,000.00"]
    asc = _col(_html(list_payments, db, admin, sort="amount", order="asc"), 1)
    assert asc == ["5,000.00", "12,000.00", "30,000.00"]


def test_room_sorts_naturally(db, admin):
    """房号自然排序：10#201 不能排在 6#401 前面。"""
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("10", "201"), 1000.0, "2026-10-01", "微信", "P1", "boss", "a"),
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P2", "boss", "b"),
        (("2", "101"), 1000.0, "2026-10-01", "微信", "P3", "boss", "c"),
    ])
    asc = _col(_html(list_payments, db, admin, sort="room", order="asc"), 2)
    # 房号展示串带楼盘名前缀（颐园清苑2#101），比「楼栋#房号」那一段
    assert [r.replace("颐园清苑", "").split("#")[0] for r in asc] == ["2", "6", "10"]


def test_method_and_received_by_sort(db, admin):
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "a"),
        (("6", "402"), 1000.0, "2026-10-01", "现金", "P2", "admin", "b"),
        (("6", "403"), 1000.0, "2026-10-01", "刷卡", "P3", "boss", "c"),
    ])
    methods = _col(_html(list_payments, db, admin, sort="method", order="asc"), 3)
    assert methods == sorted(methods) and len(set(methods)) == 3

    # 收款人按中文显示名排（王经理 / 管理员），不是按 id
    names = _col(_html(list_payments, db, admin, sort="received_by", order="asc"), 5)
    assert names == sorted(names)
    assert "王经理" in names and "管理员" in names


def test_voucher_no_sorts_naturally(db, admin):
    """凭证号是「字母 + 序号」：P10 必须排在 P6 后面。"""
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P10", "boss", "a"),
        (("6", "402"), 1000.0, "2026-10-01", "微信", "P6", "boss", "b"),
        (("6", "403"), 1000.0, "2026-10-01", "微信", "P2", "boss", "c"),
    ])
    asc = _col(_html(list_payments, db, admin, sort="voucher_no", order="asc"), 4)
    assert asc == ["P2", "P6", "P10"]


def test_remark_sorts(db, admin):
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "尾款"),
        (("6", "402"), 1000.0, "2026-10-01", "微信", "P2", "boss", "首款"),
        (("6", "403"), 1000.0, "2026-10-01", "微信", "P3", "boss", "中期款"),
    ])
    asc = _col(_html(list_payments, db, admin, sort="remark", order="asc"), 7)
    assert asc == sorted(asc)


def test_default_is_paid_at_desc(db, admin):
    """不传 sort 时保持原行为：收款日期倒序。"""
    from app.routers.payments import list_payments

    _seed(db, admin, [
        (("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "a"),
        (("6", "402"), 1000.0, "2026-10-05", "微信", "P2", "boss", "b"),
        (("6", "403"), 1000.0, "2026-10-03", "微信", "P3", "boss", "c"),
    ])
    default = _col(_html(list_payments, db, admin), 0)
    assert default == ["2026-10-05", "2026-10-03", "2026-10-01"]
    # 非法 sort 也回退到收款日期倒序
    bogus = _col(_html(list_payments, db, admin, sort="drop_table", order="asc"), 0)
    assert bogus == default


def test_sort_links_keep_active_filters(db, admin):
    """点列头排序不丢筛选条件。"""
    from app.routers.payments import list_payments

    _seed(db, admin, [(("6", "401"), 1000.0, "2026-10-01", "微信", "P1", "boss", "a")])
    html = _html(list_payments, db, admin, date_from="2026-10-01", date_to="2026-10-31", method="微信")

    m = re.search(r'href="([^"]*sort=amount[^"]*)"', html)
    assert m, "找不到金额排序链接"
    link = m.group(1)
    assert "date_from=2026-10-01" in link
    assert "date_to=2026-10-31" in link
    assert "method=" in link
