"""成本明细修改页「录入人」字段的回归测试。

**背景**：成本明细的表里已经有「录入人」列（还能排序），但点进 `/costs/{id}/edit`
表单里没有这一项 —— 录错了人只能改库。

**修法**：编辑页补一个「录入人」下拉（启用账号，按显示名排；原录入人已停用也要留在
候选里，否则下拉会静默跳到别人），`update_cost` 接收并校验 `created_by`。
留空 = 不改；给值就必须是存在的账号（防构造 id 写入任意用户）。
"""
from __future__ import annotations

import asyncio
import re

from starlette.requests import Request


def _request(path: str = "/costs/1/edit") -> Request:
    return Request(
        {
            "type": "http", "method": "POST", "path": path,
            "raw_path": path.encode(), "root_path": "", "scheme": "http",
            "server": ("127.0.0.1", 8090), "client": ("127.0.0.1", 12345),
            "headers": [(b"user-agent", b"pytest")], "query_string": b"",
        }
    )


def _seed(db, admin, *, created_by=None):
    """一间客户 + 一份未结算合同 + 一条材料成本。"""
    from app.models import Contract, ContractCost, Customer, Supplier

    cust = Customer(name="录入人测试客户", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    contract = Contract(customer_id=cust.id, no="RC-1", sign_date="2026-10-01",
                        total_amount=10000, status="active", settled=0, created_by=admin.id)
    db.add(contract); db.flush()
    sup = Supplier(name="测试供应商")
    db.add(sup); db.flush()
    cost = ContractCost(contract_id=contract.id, category="材料成本", amount=880.0,
                        supplier_id=sup.id, remark="铜管", spent_at="2026-10-02",
                        created_by=created_by if created_by is not None else admin.id)
    db.add(cost); db.commit()
    return cost


def _edit(cost, admin, db, **over):
    """调 update_cost，参数默认保持原值。"""
    from app.routers.costs import update_cost

    kw = dict(
        category=cost.category, amount=str(cost.amount),
        supplier_id=str(cost.supplier_id or ""), installer_id="",
        remark=cost.remark or "", spent_at=cost.spent_at or "",
        created_by="", files=[], db=db, user=admin,
    )
    kw.update(over)
    return asyncio.run(update_cost(_request(), cost.id, **kw))


def _page(db, admin, cost_id):
    from app.routers.costs import edit_cost_form

    resp = edit_cost_form(_request("/costs/%d/edit" % cost_id), cost_id, db=db, user=admin)
    return bytes(resp.body).decode()


def test_edit_form_has_recorder_select(db, admin):
    """编辑页出现「录入人」下拉，且当前录入人被选中。"""
    cost = _seed(db, admin)
    html = _page(db, admin, cost.id)

    m = re.search(r'<select name="created_by".*?</select>', html, re.S)
    assert m, "编辑页没有录入人下拉"
    block = m.group(0)
    assert f'value="{admin.id}" selected' in block, "当前录入人没被选中"
    assert admin.display_name in block


def test_update_can_change_recorder(db, admin):
    """把录入人改到另一个账号，落库生效。"""
    from app.models import User

    other = User(username="worker1", full_name="王师傅", password_hash="x",
                 role="installer", data_scope="self", is_active=1)
    db.add(other); db.flush()
    cost = _seed(db, admin)
    assert cost.created_by == admin.id

    _edit(cost, admin, db, created_by=str(other.id))
    db.refresh(cost)
    assert cost.created_by == other.id


def test_blank_recorder_keeps_original(db, admin):
    """留空 = 不改（老表单 / 接口调用不带这个字段时不能把录入人清掉）。"""
    cost = _seed(db, admin)
    _edit(cost, admin, db, created_by="")
    db.refresh(cost)
    assert cost.created_by == admin.id


def test_invalid_recorder_is_rejected(db, admin):
    """不存在的用户 id 不能写进去。"""
    cost = _seed(db, admin)
    _edit(cost, admin, db, created_by="99999")
    db.refresh(cost)
    assert cost.created_by == admin.id, "非法录入人应被拒绝，落库不该变"


def test_inactive_recorder_still_listed(db, admin):
    """原录入人已停用：仍要出现在候选里，否则下拉会静默跳到别人。"""
    from app.models import User

    gone = User(username="old1", full_name="离职的老王", password_hash="x",
                role="sales", data_scope="self", is_active=0)
    db.add(gone); db.flush()
    cost = _seed(db, admin, created_by=gone.id)

    html = _page(db, admin, cost.id)
    m = re.search(r'<select name="created_by".*?</select>', html, re.S)
    assert m
    block = m.group(0)
    assert f'value="{gone.id}" selected' in block, "已停用的原录入人没被选中"
    assert "已停用" in block
