"""routers.reports: 按月 / 按客户聚合的口径与边界。"""
from app.models import ChangeOrder, Contract, ContractCost, Customer, Payment
from app.routers import reports as R


class _Req:
    query_params = {}


def _call(monkeypatch, fn, *args, **kwargs):
    """monkeypatch render 捕获 ctx，避免真模板渲染。"""
    captured = {}
    monkeypatch.setattr(
        R, "render",
        lambda request, name, **ctx: captured.update({"_tpl": name}, **ctx) or ctx,
    )
    fn(_Req(), *args, **kwargs)
    return captured


def test_customers_aggregates_current_year(db, admin, monkeypatch, today):
    Y = today[:4]
    a = Customer(name="A 客户", owner_id=admin.id, status="active")
    b = Customer(name="B 客户", owner_id=admin.id, status="active")
    db.add_all([a, b]); db.flush()
    ca = Contract(customer_id=a.id, no="CA", sign_date=f"{Y}-01-15",
                  total_amount=100000, discount=0, status="active")
    cb = Contract(customer_id=b.id, no="CB", sign_date=f"{Y}-03-01",
                  total_amount=30000, discount=0, status="active")
    db.add_all([ca, cb]); db.flush()
    db.add(ChangeOrder(contract_id=ca.id, reason="加装", amount=5000))
    db.add(ContractCost(contract_id=ca.id, category="材料成本", amount=60000, spent_at=f"{Y}-02-01"))
    db.add(ContractCost(contract_id=cb.id, category="材料成本", amount=40000, spent_at=f"{Y}-04-01"))
    db.add(Payment(contract_id=ca.id, amount=80000, paid_at=f"{Y}-02-10"))
    db.add(Payment(contract_id=cb.id, amount=10000, paid_at=f"{Y}-03-15"))
    db.flush()

    cap = _call(monkeypatch, R.reports_customers,
                q="", sort="gross", order="desc", db=db, user=admin)
    rows = {r["customer_name"]: r for r in cap["rows"]}
    # A 收入 100000+5000=105000；成本 60000；毛利 45000；已收 80000
    assert rows["A 客户"]["income"] == 105000.0
    assert rows["A 客户"]["cost"] == 60000.0
    assert rows["A 客户"]["gross"] == 45000.0
    assert rows["A 客户"]["received"] == 80000.0
    # B 收入 30000；成本 40000；毛利 -10000
    assert rows["B 客户"]["gross"] == -10000.0
    # gross desc 排序：A 在前
    assert cap["rows"][0]["customer_name"] == "A 客户"
    assert cap["totals"]["income"] == 135000.0
    assert cap["totals"]["gross"] == 35000.0


def test_customers_aggregates_all_contracts_regardless_of_sign_year(db, admin, monkeypatch, today):
    """按客户视图的设计：不按日期拆分——去年 / 今年的合同全部计入整体收入。"""
    Y = today[:4]
    prev = str(int(Y) - 1)
    cust = Customer(name="跨年客", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c1 = Contract(customer_id=cust.id, no="P1", sign_date=f"{prev}-06-01",
                 total_amount=50000, status="active")
    c2 = Contract(customer_id=cust.id, no="P2", sign_date=f"{Y}-03-01",
                 total_amount=30000, status="active")
    db.add_all([c1, c2]); db.flush()
    db.add(Payment(contract_id=c1.id, amount=50000, paid_at=f"{prev}-08-01"))
    db.add(Payment(contract_id=c2.id, amount=30000, paid_at=f"{Y}-03-15"))
    db.flush()

    cap = _call(monkeypatch, R.reports_customers,
                q="", sort="gross", order="desc", db=db, user=admin)
    rows = {r["customer_name"]: r for r in cap["rows"]}
    # 整体聚合：所有合同、所有收款全部计入
    assert rows["跨年客"]["income"] == 80000.0
    assert rows["跨年客"]["received"] == 80000.0


def test_customers_respects_visibility_scope(db, sales, admin, monkeypatch, today):
    """非 admin 用户只能看到自己的客户。"""
    Y = today[:4]
    own = Customer(name="我的客", owner_id=sales.id, status="active")
    other = Customer(name="别人的客", owner_id=admin.id, status="active")
    db.add_all([own, other]); db.flush()
    db.add(Contract(customer_id=own.id, no="OWN", sign_date=f"{Y}-02-01",
                    total_amount=10000, status="active"))
    db.add(Contract(customer_id=other.id, no="OTH", sign_date=f"{Y}-03-01",
                    total_amount=20000, status="active"))
    db.flush()

    cap = _call(monkeypatch, R.reports_customers,
                q="", sort="gross", order="desc", db=db, user=sales)
    names = {r["customer_name"] for r in cap["rows"]}
    assert "我的客" in names
    assert "别人的客" not in names


def test_dashboard_year_aggregation_filters_by_sign_year(db, admin, monkeypatch, today):
    """ui.dashboard 的「当年」聚合（v0.14.76 的销售/收款/成本/利润）**真按当年过滤**，
    与 reports_customers 整体聚合不同——这是有意为之的两种口径并存。"""
    Y = today[:4]
    prev = str(int(Y) - 1)
    cust = Customer(name="当年验证客", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c = Contract(customer_id=cust.id, no="C", sign_date=f"{prev}-06-01",
                 total_amount=50000, status="active")
    db.add(c); db.flush()
    # 成本和收款在当年——但合同签订在去年
    db.add(ContractCost(contract_id=c.id, category="材料成本", amount=20000, spent_at=f"{Y}-07-01"))
    db.add(Payment(contract_id=c.id, amount=10000, paid_at=f"{Y}-08-01"))
    db.flush()

    from app.routers import ui as U

    captured = {}
    monkeypatch.setattr(
        U, "render",
        lambda request, name, **ctx: captured.update({"_tpl": name}, **ctx) or ctx,
    )

    class _Req:
        class _URL:
            path = "/"
        class _State:
            user = None
        url = _URL(); state = _State(); cookies = {}
        query_params = {}

    U.dashboard(_Req(), db=db, user=admin)
    ys = captured["year_stats"]
    # 当年销售额按「签订日」归集 → 去年合同不算
    assert ys["sales"] == 0.0
    # 当年成本按「费用发生日」归集 → 当年的成本计入
    assert ys["cost"] == 20000.0
    # 当年收款按「收款日」归集 → 当年的收款计入
    assert ys["received"] == 10000.0
    # 利润 = 0 - 20000（负数，是预期）
    assert ys["profit"] == -20000.0


def test_by_month_aggregates_signing_date(db, admin, monkeypatch, today):
    """reports(mode='month', year=Y) 按合同签订日分桶。"""
    Y = today[:4]
    cust = Customer(name="月份", owner_id=admin.id, status="active")
    db.add(cust); db.flush()
    c1 = Contract(customer_id=cust.id, no="M1", sign_date=f"{Y}-02-01",
                  total_amount=10000, status="active")
    c2 = Contract(customer_id=cust.id, no="M2", sign_date=f"{Y}-05-01",
                  total_amount=5000, status="active")
    db.add_all([c1, c2]); db.flush()

    cap = _call(monkeypatch, R.reports,
                mode="month", year=Y, db=db, user=admin)
    by_period = {r["period"]: r for r in cap["rows"]}
    assert by_period[f"{Y}-02"]["income"] == 10000.0
    assert by_period[f"{Y}-05"]["income"] == 5000.0
    assert by_period[f"{Y}-01"]["income"] == 0.0
    assert by_period[f"{Y}-02"]["contracts"] == 1
