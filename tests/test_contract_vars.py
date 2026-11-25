"""合同文档变量：工程地点 / 房号 / 项目名称 / 业务员。

为什么单独立这一组：线上 11 个客户实测——**房号 11 个都填了，`customer.address` 11 个全空**。
于是合同模板里用 `{{甲方地址}}` 的「工程地点」一直是空白，纸面上那一行等于没写。
现在 `工程地点` 优先从房号拼（市+区+楼盘+楼栋+单元+房号），拼不出来才退回手填地址。
"""
from __future__ import annotations

import pytest

from app.models import Contract, Customer, Project, Room, User
from app.services import contract_docs as doc_svc


def _user(db, name="王销售", username="sales1"):
    u = User(username=username, full_name=name, password_hash="x", role="sales",
             data_scope="all", is_active=1)
    db.add(u)
    db.flush()
    return u


def _customer(db, owner, *, with_room=True, address=None):
    project = room = None
    if with_room:
        project = Project(name="珠光花园一期", city="合肥", district="包河区")
        db.add(project)
        db.flush()
        room = Room(project_id=project.id, building="6栋", unit="3单元", room_no="506")
        db.add(room)
        db.flush()
    c = Customer(name="董小姐", phone="13900000000", type="家装", address=address,
                 owner_id=owner.id, room_id=room.id if room else None)
    db.add(c)
    db.flush()
    return c


def _contract(db, customer, creator, no="HT-1"):
    ctr = Contract(customer_id=customer.id, no=no, sign_date="2026-11-25",
                   total_amount=10000, product_type="中央空调",
                   created_by=getattr(creator, "id", None))
    db.add(ctr)
    db.flush()
    return ctr


def _ctx(db, contract):
    return doc_svc.build_context(db, contract)


def test_site_address_uses_room_when_address_empty(db):
    """客户地址为空、只挂了房号 —— 工程地点必须拼得出来（线上真实情况）。"""
    u = _user(db)
    c = _customer(db, u, address=None)
    ctr = _contract(db, c, u)
    assert _ctx(db, ctr)["工程地点"] == "合肥市包河区珠光花园一期 6栋3单元506"


def test_site_address_skips_missing_unit(db):
    """没有单元的房号（8栋-701）不能拼出「8栋  701」这种双空格。"""
    u = _user(db)
    project = Project(name="育才苑二期", city="合肥", district="长丰县")
    db.add(project)
    db.flush()
    room = Room(project_id=project.id, building="8栋", unit=None, room_no="701")
    db.add(room)
    db.flush()
    c = Customer(name="陶姐", owner_id=u.id, room_id=room.id)
    db.add(c)
    db.flush()
    ctr = _contract(db, c, u)
    assert _ctx(db, ctr)["工程地点"] == "合肥市长丰县育才苑二期 8栋701"


def test_site_address_falls_back_to_address(db):
    """没挂房号时退回手填地址，不能输出空白。"""
    u = _user(db)
    c = _customer(db, u, with_room=False, address="合肥市蜀山区某某路 88 号 3 栋 1801")
    ctr = _contract(db, c, u)
    assert _ctx(db, ctr)["工程地点"] == "合肥市蜀山区某某路 88 号 3 栋 1801"


def test_room_and_project_vars(db):
    u = _user(db)
    c = _customer(db, u)
    ctr = _contract(db, c, u)
    ctx = _ctx(db, ctr)
    assert ctx["房号"] == "6栋-3单元-506"
    assert ctx["房号简称"] == "珠光花园一期6#506"
    assert ctx["项目名称"] == "珠光花园一期"
    assert ctx["施工地址"] == ctx["工程地点"]


def test_sales_rep_var(db):
    u = _user(db, "王销售")
    other = _user(db, "李主管", username="sales2")
    c = _customer(db, other)
    ctr = _contract(db, c, u)
    assert _ctx(db, ctr)["业务员"] == "王销售", "有创建人时用创建人"

    ctr2 = _contract(db, c, None, no="HT-2")
    assert _ctx(db, ctr2)["业务员"] == "李主管", "没有创建人时退回客户负责人"


def test_new_vars_are_registered_in_variable_list():
    """后台「可用变量」清单必须收录，否则用户不知道能填。"""
    keys = {k for k, _ in doc_svc.available_vars()}
    for need in ("工程地点", "施工地址", "房号", "房号简称", "项目名称", "业务员"):
        assert need in keys, f"变量清单缺 {need}"


def test_unknown_var_does_not_leak_placeholder(db):
    """写错变量名不能把 {{xxx}} 原样印在合同上（会走 missing 提示）。"""
    ctx = {"客户名称": "张三"}
    missing: list[str] = []
    out = doc_svc.render_text("甲方：{{客户名称}}，{{不存在的变量}}", ctx, missing)
    assert out == "甲方：张三，"
    assert missing == ["不存在的变量"]
