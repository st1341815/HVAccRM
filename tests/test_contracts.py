"""合同创建 → 客户状态同步「已成交」回归测试。

业务规则：合同创建成功后，关联客户的状态自动更新为「已成交」（won）。
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request

from app.models import AuditLog, Contract, Customer
from app.routers.contracts import create_contract
from app.security import loads


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/contracts/new",
            "raw_path": b"/contracts/new",
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _make_customer(db, status: str = "active", owner_id: int | None = None) -> Customer:
    c = Customer(name="测试客户", phone="13800000000", status=status, owner_id=owner_id)
    db.add(c)
    db.flush()
    return c


def _create_contract(db, user, customer_id: int):
    return asyncio.run(
        create_contract(
            _request(),
            customer_id=str(customer_id),
            sign_date="2026-10-21",
            total_amount="10000",
            product_type="地暖",
            notes="",
            files=[],
            db=db,
            user=user,
        )
    )


def _flash_message(response) -> str:
    cookie = response.headers.get("set-cookie", "")
    if "flash=" not in cookie:
        return ""
    value = cookie.split("flash=", 1)[1].split(";", 1)[0]
    data = loads(value)
    return (data or {}).get("msg", "") if isinstance(data, dict) else ""


def _customer_audits(db) -> list[AuditLog]:
    return list(
        db.query(AuditLog)
        .filter(AuditLog.table_name == "customers")
        .all()
    )


def test_contract_creation_marks_customer_won(db, admin):
    """active 客户建合同后 → 状态变为 won（已成交）。"""
    customer = _make_customer(db, "active", admin.id)

    _create_contract(db, admin, customer.id)

    db.refresh(customer)
    assert customer.status == "won"
    assert db.query(Contract).count() == 1


def test_status_change_is_audited(db, admin):
    """状态变化要留一条 customers 审计（old=active → new=won）。"""
    customer = _make_customer(db, "active", admin.id)

    _create_contract(db, admin, customer.id)

    audits = _customer_audits(db)
    assert len(audits) == 1
    assert audits[0].action == "update"
    assert "active" in (audits[0].old_val or "")
    assert "won" in (audits[0].new_val or "")


def test_already_won_customer_keeps_single_audit(db, admin):
    """已是「已成交」的客户再建合同 → 状态不变，不重复写审计（幂等）。"""
    customer = _make_customer(db, "won", admin.id)

    _create_contract(db, admin, customer.id)

    db.refresh(customer)
    assert customer.status == "won"
    assert _customer_audits(db) == []


def test_lost_customer_becomes_won(db, admin):
    """已流失客户签了合同 → 状态同样同步为「已成交」。"""
    customer = _make_customer(db, "lost", admin.id)

    _create_contract(db, admin, customer.id)

    db.refresh(customer)
    assert customer.status == "won"


def test_flash_message_mentions_status(db, admin):
    """提示语里说明客户状态已更新。"""
    customer = _make_customer(db, "active", admin.id)

    resp = _create_contract(db, admin, customer.id)

    msg = _flash_message(resp)
    assert "已成交" in msg
