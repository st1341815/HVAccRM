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
