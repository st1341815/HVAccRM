"""finance.svc: 合同 / 客户级汇总的边界与口径。"""
from app.models import Contract, Customer, Payment
from app.services.finance import contract_summary, customer_summary


def test_full_payment_marks_contract_cleared(db, admin, today):
    cust = Customer(name="全额收", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="ONE-FULL", sign_date=today,
                 total_amount=10000, status="active")
    db.add(c); db.flush()
    db.add(Payment(contract_id=c.id, amount=10000, paid_at=today))
    db.flush()
    s = contract_summary(db, c)
    assert s.received == 10000.0
    assert s.outstanding == 0.0
    assert s.status == "已结清"
    assert s.recovery_rate == 100.0


def test_partial_payment_shows_outstanding(db, admin, today):
    cust = Customer(name="部分收", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="ONE-PART", sign_date=today,
                 total_amount=10000, status="active")
    db.add(c); db.flush()
    db.add(Payment(contract_id=c.id, amount=4000, paid_at=today))
    db.flush()
    s = contract_summary(db, c)
    assert s.received == 4000.0
    assert s.outstanding == 6000.0
    assert s.status == "正常"
    assert 39.9 < s.recovery_rate < 40.1


def test_overpayment_marks_status(db, admin, today):
    cust = Customer(name="超收", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="ONE-OVER", sign_date=today,
                 total_amount=10000, status="active")
    db.add(c); db.flush()
    db.add(Payment(contract_id=c.id, amount=15000, paid_at=today))
    db.flush()
    s = contract_summary(db, c)
    assert s.received == 15000.0
    assert s.status == "超收"


def test_customer_summary_aggregates_multiple_contracts(db, admin, today):
    cust = Customer(name="聚合客", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c1 = Contract(customer_id=cust.id, no="A1", sign_date=today,
                  total_amount=5000, status="active")
    c2 = Contract(customer_id=cust.id, no="A2", sign_date=today,
                  total_amount=3000, status="active")
    db.add_all([c1, c2]); db.flush()
    db.add(Payment(contract_id=c1.id, amount=5000, paid_at=today))
    db.add(Payment(contract_id=c2.id, amount=1000, paid_at=today))
    db.flush()
    s = customer_summary(db, cust)
    assert s.total == 8000.0
    assert s.received == 6000.0
    assert s.outstanding == 2000.0


def test_refund_reduces_received(db, admin, today):
    """退款（负数 Payment.amount）冲减已收、毛利口径自动反映净收。"""
    cust = Customer(name="退款客", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="ONE-RF", sign_date=today,
                 total_amount=10000, status="active")
    db.add(c); db.flush()
    db.add(Payment(contract_id=c.id, amount=5000, paid_at=today))
    db.add(Payment(contract_id=c.id, amount=-2000, paid_at=today))
    db.flush()
    s = contract_summary(db, c)
    assert s.received == 3000.0
    assert s.outstanding == 7000.0


def test_zero_total_handles_gracefully(db, admin, today):
    """total = 0 时不应除零，恢复率应为 0.0 而非崩溃。"""
    cust = Customer(name="零总额", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="ONE-Z", sign_date=today,
                 total_amount=0, status="active")
    db.add(c); db.flush()
    s = contract_summary(db, c)
    assert s.recovery_rate == 0.0
    assert s.outstanding == 0.0
