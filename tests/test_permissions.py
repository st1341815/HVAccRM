"""permissions.visible_customer_ids: 不同 role / data_scope 的可见性矩阵。"""
from app.models import Customer, Task
from app.permissions import visible_customer_ids


def test_admin_sees_all(db, admin):
    cust_a = Customer(name="A", owner_id=admin.id, status="active")
    cust_other = Customer(name="O", owner_id=99999, status="active")
    db.add_all([cust_a, cust_other]); db.flush()
    assert visible_customer_ids(db, admin) is None  # None = 不过滤


def test_sales_only_sees_own(db, sales, admin):
    cust_own = Customer(name="OWN", owner_id=sales.id, status="active")
    cust_admin = Customer(name="ADMIN_C", owner_id=admin.id, status="active")
    db.add_all([cust_own, cust_admin]); db.flush()
    visible = visible_customer_ids(db, sales)
    assert visible is not None
    assert cust_own.id in visible
    assert cust_admin.id not in visible


def test_installer_sees_only_customers_with_assigned_task(db, installer, admin):
    """白名单角色：仅当客户有任务派给 installer 才可见。"""
    cust_with = Customer(name="WITH", owner_id=admin.id, status="active")
    cust_without = Customer(name="NONE", owner_id=admin.id, status="active")
    db.add_all([cust_with, cust_without]); db.flush()
    t = Task(customer_id=cust_with.id, contract_id=None, stage="上门勘测",
             sort_order=1, status="doing", assignee_id=installer.id)
    db.add(t); db.flush()
    visible = visible_customer_ids(db, installer)
    assert visible is not None
    assert cust_with.id in visible
    assert cust_without.id not in visible


def test_seller_with_no_customers_returns_empty_set(db, sales):
    """新销售名下无客户 — 返回空集（不是 None）。"""
    visible = visible_customer_ids(db, sales)
    assert visible is not None
    assert len(visible) == 0
