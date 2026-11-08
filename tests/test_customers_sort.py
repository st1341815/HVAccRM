"""客户列表（客户 → 客户列表）列排序的回归测试。

**需求**：客户列表里「客户 / 房号 / 状态 / 合同额 / 已收 / 未收」列头点不了排序。

**实现**：客户列表不像合同台账有天然时间序（默认顺序来自 FTS 相关度），
所以 `sort` 默认空 → **不排序**，保持 `search_customers` 原顺序；
按房号排序走 `utils.room_sort_key` 自然排序（与合同台账共用，同一栋楼两边顺序必须一致）；
状态按 `CUSTOMER_STATUS_LABELS` 的**业务定义顺序**排，不是中文拼音；
金额三列仅对 `payment:view + can_see_amount` 可见。

本文件盯住：
1. 6 个目标列头都是排序链接，手机/意向产品不是；
2. name / status / total 双向行序正确（room 走自然排序单测）；
3. 无金额权限时三列不出现、`?sort=total` 静默回退不 500；
4. 不传 sort 时保持原顺序（不偷偷改默认行为）；
5. 点列头排序不丢筛选条件。
"""
from __future__ import annotations

import re

from starlette.requests import Request

from app.utils import room_sort_key


def _fake_request(query: str = "") -> Request:
    return Request(
        {
            "type": "http", "method": "GET", "path": "/customers",
            "raw_path": b"/customers", "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": query.encode(),
        }
    )


def _text(cell: str) -> str:
    return re.sub(r"<[^>]+>", "", cell).strip()


def _col(html: str, idx: int) -> list[str]:
    body = html.rsplit("<tbody>", 1)[-1]
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) > idx:
            out.append(_text(tds[idx]))
    return out


def _html(handler, db, user, **params) -> str:
    resp = handler(request=_fake_request(), db=db, user=user, **params)
    return bytes(resp.body).decode()


def _seed(db, admin, spec):
    """spec: [(客户名, 房号(楼栋,房号号), 状态, 合同额)]，合同额 0 表示不建合同。"""
    from app.models import Contract, Customer, Project, Room

    proj = Project(name="", city=None, district=None)
    db.add(proj)
    db.flush()

    out = []
    for i, (name, (building, room_no), status, amount) in enumerate(spec):
        room = None
        if building:
            room = Room(project_id=proj.id, building=building, room_no=room_no)
            db.add(room)
            db.flush()
        cust = Customer(name=name, owner_id=admin.id, status=status,
                        source="自然到店", room_id=room.id if room else None)
        db.add(cust)
        db.flush()
        if amount:
            db.add(Contract(customer_id=cust.id, no=f"CS-{i}", sign_date="2026-10-0%d" % (i + 1),
                            total_amount=amount, status="active", created_by=admin.id))
            db.flush()
        out.append(cust)
    db.commit()
    return out


def test_six_columns_are_sortable_links(db, admin):
    """6 个目标列头可排；手机、意向产品保持静态表头。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [("张三", ("6", "401"), "active", 10000)])
    html = _html(list_customers, db, admin)

    for key in ("name", "room", "status", "total", "received", "outstanding"):
        assert f"sort={key}" in html, f"{key} 列没有排序链接"
    assert "手机" in html and "意向产品" in html
    # 静态列不应被包成链接
    assert not re.search(r'<th class="sortable"><a href="[^"]*sort=phone', html)
    # 首点统一 desc，当前列显示 ▼
    assert re.search(r'href="[^"]*sort=name[^"]*order=desc"', html)


def test_name_column_sorts(db, admin):
    """客户名双向排序。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [
        ("王五", ("6", "401"), "active", 0),
        ("李四", ("7", "101"), "active", 0),
        ("赵六", ("8", "301"), "active", 0),
    ])
    asc = _col(_html(list_customers, db, admin, sort="name", order="asc"), 0)
    assert asc == sorted(asc) and len(asc) == 3
    desc = _col(_html(list_customers, db, admin, sort="name", order="desc"), 0)
    assert desc == list(reversed(asc))


def test_room_column_sorts_naturally(db, admin):
    """房号自然排序：6#401 在 10#201 之前（纯字符串会把 10# 排前面）。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [
        ("甲", ("10", "201"), "active", 0),
        ("乙", ("6", "401"), "active", 0),
        ("丙", ("2", "101"), "active", 0),
    ])
    asc = _col(_html(list_customers, db, admin, sort="room", order="asc"), 1)
    assert asc == ["2#101", "6#401", "10#201"]

    # 直接钉住 helper 的行为（合同台账也用它）
    assert sorted(["10#201", "6#401"], key=room_sort_key) == ["6#401", "10#201"]


def test_status_sorts_by_business_order(db, admin):
    """状态按定义顺序（active → won → paused → lost），不是中文拼音。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [
        ("已流失客户", ("6", "401"), "lost", 0),
        ("跟进中客户", ("7", "101"), "active", 0),
        ("暂停客户", ("8", "301"), "paused", 0),
        ("成交客户", ("9", "201"), "won", 0),
    ])
    asc = _col(_html(list_customers, db, admin, sort="status", order="asc"), 4)
    assert asc == ["跟进中", "已成交", "暂停跟进", "已流失"]


def test_amount_columns_sort(db, admin):
    """合同额 / 已收 / 未收三列按汇总值排序（无收款时未收 == 合同额）。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [
        ("小客户", ("6", "401"), "active", 10000),
        ("大客户", ("7", "101"), "active", 30000),
        ("中客户", ("8", "301"), "active", 20000),
    ])
    desc = _col(_html(list_customers, db, admin, sort="total", order="desc"), 5)
    assert desc == ["30,000.00", "20,000.00", "10,000.00"]
    asc = _col(_html(list_customers, db, admin, sort="outstanding", order="asc"), 7)
    assert asc == ["10,000.00", "20,000.00", "30,000.00"]


def test_no_sort_keeps_original_order(db, admin):
    """不传 sort 时不排序 —— 默认顺序仍是 search 给的顺序，不能被顺手改成别的。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [
        ("丙三", ("6", "401"), "active", 0),
        ("乙二", ("7", "101"), "active", 0),
        ("甲一", ("8", "301"), "active", 0),
    ])
    baseline = _col(_html(list_customers, db, admin), 0)
    assert len(baseline) == 3
    # 显式空 sort 等价于不传
    assert _col(_html(list_customers, db, admin, sort="", order="desc"), 0) == baseline
    # 非法/未知 sort 也回退到「不排序」，而不是偷偷按某列排
    assert _col(_html(list_customers, db, admin, sort="drop_table", order="asc"), 0) == baseline


def test_no_money_permission_hides_amount_columns(db, installer):
    """无金额权限：三列不出现，?sort=total 静默回退不报错。"""
    from app.routers.customers import list_customers

    _seed(db, installer, [
        ("小客户", ("6", "401"), "active", 10000),
        ("大客户", ("7", "101"), "active", 30000),
    ])
    html = _html(list_customers, db, installer, sort="total", order="desc")
    assert "合同额" not in html
    assert "sort=total" not in html


def test_sort_links_keep_active_filters(db, admin):
    """点列头排序不丢筛选条件。"""
    from app.routers.customers import list_customers

    _seed(db, admin, [("张三", ("6", "401"), "active", 10000)])
    html = _html(list_customers, db, admin, status="active", source="自然到店")

    m = re.search(r'href="([^"]*sort=name[^"]*)"', html)
    assert m, "找不到客户排序链接"
    link = m.group(1)
    assert "status=active" in link and "source=" in link
    assert "sort=name" in link
