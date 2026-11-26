"""首页看板不再展示「最新现场照片」模块（v1.12.10）。"""
from pathlib import Path

from app.models import Customer


TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"


class _Req:
    class _URL:
        path = "/"

    class _State:
        user = None

    url = _URL()
    state = _State()
    cookies = {}
    query_params = {}


def test_dashboard_ctx_drops_recent_photos(db, admin, monkeypatch):
    """dashboard 视图不再查照片、不再往上下文塞 recent_photos。

    反证：照片查询会多打一次 SQL，且模板已删块，留着就是死代码。
    """
    cust = Customer(name="首页照片验证客", owner_id=admin.id, status="active")
    db.add(cust)
    db.flush()

    from app.routers import ui as U

    captured = {}
    monkeypatch.setattr(
        U, "render",
        lambda request, name, **ctx: captured.update({"_tpl": name}, **ctx) or ctx,
    )
    U.dashboard(_Req(), db=db, user=admin)

    assert captured["_tpl"] == "dashboard.html"
    assert "recent_photos" not in captured


def test_dashboard_template_has_no_photo_block(db, admin):
    """模板里不能再出现照片区块，避免被顺手加回来。"""
    src = TEMPLATE.read_text(encoding="utf-8")
    assert "最新现场照片" not in src
    assert "recent_photos" not in src
    assert "class=\"gallery\"" not in src


def test_year_profit_counts_settled_only(db, admin, monkeypatch):
    """年利润只统计已结算合同：未标记结算的销售额与成本都不计入。

    需求口径（v1.15.12）：首页看板「年利润」= 已结算合同的利润。
    对照：销售额 / 成本两个 KPI 仍是全口径，所以 profit ≠ sales − cost 是刻意的
    —— 钱没关账之前不确认利润。
    """
    from datetime import date

    from app.models import Contract, ContractCost
    from app.routers import ui as U

    year = str(date.today().year)
    cust = Customer(name="利润口径验证客", owner_id=admin.id, status="active")
    db.add(cust)
    db.flush()

    settled = Contract(
        customer_id=cust.id, no="HT-PROFIT-S", sign_date=f"{year}-01-10",
        total_amount=10000, discount=0, status="active", settled=1, created_by=admin.id,
    )
    opened = Contract(
        customer_id=cust.id, no="HT-PROFIT-O", sign_date=f"{year}-02-10",
        total_amount=20000, discount=0, status="active", settled=0, created_by=admin.id,
    )
    db.add_all([settled, opened])
    db.flush()
    db.add_all([
        ContractCost(contract_id=settled.id, category="其他", amount=4000, spent_at=f"{year}-01-20"),
        ContractCost(contract_id=opened.id, category="其他", amount=8000, spent_at=f"{year}-02-20"),
    ])
    db.commit()

    captured = {}
    monkeypatch.setattr(
        U, "render",
        lambda request, name, **ctx: captured.update({"_tpl": name}, **ctx) or ctx,
    )
    U.dashboard(_Req(), db=db, user=admin)

    ys = captured["year_stats"]
    assert ys["profit"] == 6000.0, "只算已结算合同：10000 - 4000"
    assert ys["settled_count"] == 1
    assert ys["sales"] == 30000.0, "销售额仍是全口径"
    assert ys["cost"] == 12000.0, "成本仍是全口径"


def test_dashboard_template_marks_profit_scope(db, admin):
    """模板上必须标明利润口径，否则用户会以为 profit = sales − cost。"""
    src = TEMPLATE.read_text(encoding="utf-8")
    assert "仅已结算" in src
    assert "kpi-note" in src
