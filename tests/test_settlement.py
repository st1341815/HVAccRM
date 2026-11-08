"""合同结算：开关、冻结、选择框隐藏与核算口径回归测试。"""
from __future__ import annotations

import asyncio
import io

from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.models import (
    AuditLog,
    Contract,
    ContractCost,
    Customer,
    Photo,
    Task,
    now_iso,
)
from app.routers.accounting import settlement_view
from app.routers.contracts import settle_contract, unsettle_contract, update_contract
from app.routers.costs import create_cost, new_cost_form
from app.routers.payments import new_payment_form, record_payment
from app.routers.tasks import start
from app.security import loads
from app.services import settlement as settlement_svc


def _request(user=None, path: str = "/contracts", query: str = "") -> Request:
    r = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )
    if user is not None:
        r.state.user = user
    return r


def _customer(db, admin, name="结算客户") -> Customer:
    c = Customer(name=name, phone="13900000000", owner_id=admin.id, status="won")
    db.add(c)
    db.flush()
    return c


def _contract(db, customer, no="HT-SETTLE-1", total=20000.0, status="active") -> Contract:
    c = Contract(
        customer_id=customer.id,
        no=no,
        sign_date="2026-11-01",
        total_amount=total,
        product_type="地暖",
        status=status,
        created_by=customer.owner_id,
        created_at=now_iso(),
    )
    db.add(c)
    db.flush()
    return c


def _flash(response) -> str:
    cookie = response.headers.get("set-cookie", "")
    if "flash=" not in cookie:
        return ""
    value = cookie.split("flash=", 1)[1].split(";", 1)[0]
    data = loads(value)
    return (data or {}).get("msg", "") if isinstance(data, dict) else ""


# ---------------------------------------------------------------- 服务层


def test_new_contract_is_unsettled(db, admin):
    contract = _contract(db, _customer(db, admin))
    assert contract.settled in (0, None)
    assert contract.is_settled is False


def test_block_reason_matrix(db, admin):
    contract = _contract(db, _customer(db, admin))
    for action in ("payment", "cost", "task", "edit"):
        assert settlement_svc.block_reason(contract, action) is None
    contract.settled = 1
    for action in ("payment", "cost", "task", "edit"):
        reason = settlement_svc.block_reason(contract, action)
        assert reason and "已结算" in reason


def test_set_settled_is_idempotent(db, admin):
    contract = _contract(db, _customer(db, admin))
    before, changed = settlement_svc.set_settled(db, contract, True, admin)
    assert changed is True and before["settled"] in (0, None)
    first_at = contract.settled_at
    assert first_at and contract.settled_by == admin.id
    _, changed_again = settlement_svc.set_settled(db, contract, True, admin)
    assert changed_again is False
    assert contract.settled_at == first_at


def test_can_settle_blocks_pending_task_and_void(db, admin):
    customer = _customer(db, admin)
    contract = _contract(db, customer)
    db.add(
        Task(
            customer_id=customer.id,
            contract_id=contract.id,
            stage="工程施工",
            sort_order=1,
            status="doing",
        )
    )
    db.flush()
    ok, why = settlement_svc.can_settle(db, contract)
    assert ok is False and "工序" in why

    void_contract = _contract(db, customer, no="HT-VOID-1", status="void")
    ok, why = settlement_svc.can_settle(db, void_contract)
    assert ok is False and "作废" in why


# ---------------------------------------------------------------- 开关路由


def test_settle_route_marks_and_audits(db, admin):
    contract = _contract(db, _customer(db, admin))
    resp = settle_contract(_request(admin), contract_id=contract.id, back="/contracts?settled=0", db=db, user=admin)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/contracts?settled=0"
    assert contract.settled == 1
    audits = db.query(AuditLog).filter(AuditLog.action == "settle").all()
    assert len(audits) == 1

    again = settle_contract(_request(admin), contract_id=contract.id, back="", db=db, user=admin)
    assert again.status_code == 303
    assert "结算状态" in _flash(again)
    assert db.query(AuditLog).filter(AuditLog.action == "settle").count() == 1


def test_unsettle_route_reopens(db, admin):
    contract = _contract(db, _customer(db, admin))
    contract.settled = 1
    contract.settled_at = "2026-11-09"
    db.flush()
    resp = unsettle_contract(_request(admin), contract_id=contract.id, back="", db=db, user=admin)
    assert resp.status_code == 303
    assert contract.settled == 0 and contract.settled_at is None


# ---------------------------------------------------------------- 写入口冻结


def test_record_payment_frozen(db, admin):
    contract = _contract(db, _customer(db, admin))
    contract.settled = 1
    db.flush()
    before = db.query(Contract).filter(Contract.id == contract.id).one().settled
    resp = asyncio.run(
        record_payment(
            _request(admin),
            contract_id=str(contract.id),
            amount="1000",
            paid_at="2026-11-09",
            method="现金",
            voucher_no="",
            remark="",
            back=f"/contracts/{contract.id}",
            files=[],
            db=db,
            user=admin,
        )
    )
    assert resp.status_code == 303
    assert "已结算" in _flash(resp)
    from app.models import Payment

    assert db.query(Payment).count() == 0
    assert db.query(Contract).filter(Contract.id == contract.id).one().settled == before


def test_create_cost_frozen_before_photo_write(db, admin):
    contract = _contract(db, _customer(db, admin))
    contract.settled = 1
    db.flush()
    fake = UploadFile(filename="invoice.jpg", file=io.BytesIO(b"not-a-real-image"))
    resp = asyncio.run(
        create_cost(
            _request(admin),
            contract_id=str(contract.id),
            category="施工费用",
            amount="500",
            supplier_id="",
            installer_id=str(admin.id),
            remark="不应入库",
            spent_at="2026-11-09",
            back=f"/contracts/{contract.id}",
            files=[fake],
            db=db,
            user=admin,
        )
    )
    assert resp.status_code == 303
    assert "已结算" in _flash(resp)
    assert db.query(ContractCost).count() == 0
    assert db.query(Photo).count() == 0  # 拦截发生在附图落盘之前


def test_task_start_frozen(db, admin):
    customer = _customer(db, admin)
    contract = _contract(db, customer)
    contract.settled = 1
    task = Task(
        customer_id=customer.id,
        contract_id=contract.id,
        stage="工程施工",
        sort_order=1,
        status="ready",
    )
    db.add(task)
    db.flush()
    resp = start(_request(admin, path=f"/tasks/{task.id}/start"), task_id=task.id, back="", db=db, user=admin)
    assert resp.status_code == 303
    assert "已结算" in _flash(resp)
    assert db.query(Task).filter(Task.id == task.id).one().status == "ready"


def test_contract_amount_edit_frozen_but_notes_allowed(db, admin):
    contract = _contract(db, _customer(db, admin), total=20000.0)
    contract.settled = 1
    db.flush()

    bad = update_contract(
        _request(admin, path=f"/contracts/{contract.id}/edit"),
        contract_id=contract.id,
        no=contract.no,
        sign_date=contract.sign_date,
        total_amount="30000",
        product_type=contract.product_type,
        status="active",
        notes="",
        db=db,
        user=admin,
    )
    assert "已结算" in _flash(bad)
    assert float(db.query(Contract).filter(Contract.id == contract.id).one().total_amount) == 20000.0

    ok = update_contract(
        _request(admin, path=f"/contracts/{contract.id}/edit"),
        contract_id=contract.id,
        no=contract.no,
        sign_date=contract.sign_date,
        total_amount="20000",
        product_type=contract.product_type,
        status="active",
        notes="结算后备注可改",
        db=db,
        user=admin,
    )
    assert ok.status_code == 303
    assert db.query(Contract).filter(Contract.id == contract.id).one().notes == "结算后备注可改"


# ---------------------------------------------------------------- 选择框隐藏


def test_contract_options_hide_settled(db, admin):
    customer = _customer(db, admin)
    open_c = _contract(db, customer, no="HT-OPEN-1")
    done_c = _contract(db, customer, no="HT-DONE-1")
    done_c.settled = 1
    db.flush()

    cost_page = new_cost_form(_request(admin, path="/costs/new"), db=db, user=admin)
    ids = [o["id"] for o in cost_page.context["contract_options"]]
    assert open_c.id in ids and done_c.id not in ids

    pay_page = new_payment_form(_request(admin, path="/payments/new"), db=db, user=admin)
    ids = [o["id"] for o in pay_page.context["contract_options"]]
    assert open_c.id in ids and done_c.id not in ids


# ---------------------------------------------------------------- 核算页口径


def test_accounting_settlement_only_counts_settled(db, admin):
    customer = _customer(db, admin)
    open_c = _contract(db, customer, no="HT-OPEN-2", total=10000.0)
    done_c = _contract(db, customer, no="HT-DONE-2", total=30000.0)
    done_c.settled = 1
    db.add(
        ContractCost(
            contract_id=done_c.id,
            category="材料成本",
            amount=5000.0,
            spent_at="2026-11-02",
            created_at=now_iso(),
        )
    )
    db.flush()

    page = settlement_view(
        _request(admin, path="/accounting/settlement"),
        date_from="",
        date_to="",
        customer_q="",
        only_cost="",
        sort="sign_date",
        order="desc",
        db=db,
        user=admin,
    )
    ids = [c.id for c, _, _ in page.context["rows"]]
    assert ids == [done_c.id]
    assert open_c.id not in ids
    assert page.context["totals"]["income"] == 30000.0
    assert page.context["totals"]["cost"] == 5000.0
    assert page.context["totals"]["gross_profit"] == 25000.0


# ---------------------------------------------------------------- 迁移/权限/台账


def test_costs_overview_redirects_to_accounting(db, admin):
    from app.routers.costs import overview

    resp = overview(_request(admin, path="/costs/overview", query="sort=cost&order=asc"))
    assert resp.status_code == 302
    assert resp.headers["location"] == "/accounting/profit?sort=cost&order=asc"


def test_contract_list_settled_filter(db, admin):
    from app.routers.contracts import list_contracts

    customer = _customer(db, admin)
    open_c = _contract(db, customer, no="HT-L-OPEN")
    done_c = _contract(db, customer, no="HT-L-DONE")
    done_c.settled = 1
    db.flush()

    page = list_contracts(
        _request(admin, path="/contracts"),
        customer_q="",
        status="",
        settled="1",
        db=db,
        user=admin,
    )
    ids = [c.id for c, _ in page.context["rows"]]
    assert ids == [done_c.id]
    assert page.context["can_settle"] is True

    page = list_contracts(
        _request(admin, path="/contracts"), customer_q="", status="", settled="0", db=db, user=admin
    )
    ids = [c.id for c, _ in page.context["rows"]]
    assert ids == [open_c.id]


def test_role_permissions_include_settlement():
    from app.permissions import perms_for

    class _U:
        def __init__(self, role):
            self.role = role

    finance = perms_for(_U("finance"))
    assert {"settlement:view", "settlement:edit"} <= finance
    sales = perms_for(_U("sales"))
    assert "settlement:view" in sales and "settlement:edit" not in sales
    for role in ("designer", "installer"):
        assert "settlement:view" not in perms_for(_U(role))

