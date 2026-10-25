"""客户详情页「收款明细 / 成本明细」按客户归集回归测试。"""
from __future__ import annotations

from starlette.requests import Request

from app.models import (
    Contract,
    ContractCost,
    Customer,
    Payment,
    Supplier,
    now_iso,
)
from app.routers.customers import customer_detail


def _request(user) -> Request:
    r = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/customers/1",
            "raw_path": b"/customers/1",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )
    r.state.user = user
    return r


def _customer(db, admin, name="测试客户") -> Customer:
    c = Customer(name=name, phone="13800000000", owner_id=admin.id, status="active")
    db.add(c)
    db.flush()
    return c


def _contract(db, customer, no, total=10000, sign_date="2026-10-01") -> Contract:
    c = Contract(
        customer_id=customer.id,
        no=no,
        sign_date=sign_date,
        total_amount=total,
        status="active",
        created_by=customer.owner_id,
        created_at=now_iso(),
    )
    db.add(c)
    db.flush()
    return c


def test_detail_collects_payments_and_costs(db, admin):
    cust = _customer(db, admin)
    c1 = _contract(db, cust, "ONE202610010001")
    c2 = _contract(db, cust, "ONE202610020001", total=5000, sign_date="2026-10-02")

    db.add(Payment(contract_id=c1.id, amount=3000, paid_at="2026-10-03"))
    db.add(Payment(contract_id=c2.id, amount=-200, paid_at="2026-10-04"))
    db.flush()

    sup = Supplier(name="供应商A")
    db.add(sup)
    db.flush()
    db.add(ContractCost(contract_id=c1.id, category="材料成本", amount=1000, supplier_id=sup.id, spent_at="2026-10-05"))
    db.add(ContractCost(contract_id=c2.id, category="施工费用", amount=800, installer_id=admin.id, spent_at="2026-10-06"))
    db.flush()

    resp = customer_detail(_request(admin), customer_id=cust.id, tab="payments", db=db, user=admin)
    ctx = resp.context

    # 收款：按 paid_at 降序
    assert [p.paid_at for p in ctx["payments"]] == ["2026-10-04", "2026-10-03"]
    assert [p.amount for p in ctx["payments"]] == [-200, 3000]
    # 成本：按 spent_at 降序
    assert [r.spent_at for r in ctx["cost_rows"]] == ["2026-10-06", "2026-10-05"]
    assert {r.amount for r in ctx["cost_rows"]} == {1000, 800}
    assert ctx["suppliers"][sup.id].name == "供应商A"
    assert ctx["users"][admin.id].username == "admin"


def test_detail_isolates_other_customers(db, admin):
    """只归集当前客户的合同，不串到别的客户。"""
    cust_a = _customer(db, admin, "客户A")
    cust_b = _customer(db, admin, "客户B")
    c_a = _contract(db, cust_a, "ONEA")
    c_b = _contract(db, cust_b, "ONEB")
    db.add(Payment(contract_id=c_a.id, amount=100, paid_at="2026-10-03"))
    db.add(Payment(contract_id=c_b.id, amount=200, paid_at="2026-10-04"))
    db.add(ContractCost(contract_id=c_b.id, category="材料成本", amount=999, spent_at="2026-10-05"))
    db.flush()

    resp = customer_detail(_request(admin), customer_id=cust_a.id, db=db, user=admin)
    ctx = resp.context

    assert [p.amount for p in ctx["payments"]] == [100]
    assert ctx["cost_rows"] == []
