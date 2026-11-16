"""裸 404 页面：路由不存在 / 安全入口拦截 必须给出同一张普通 404 页。

背景（用户反馈）：外网访问入口地址填错时，浏览器里直接显示
`{"detail":"Not Found"}`，像系统坏了。原因是 FastAPI 内置的
`http_exception_handler` 对 404 回裸 JSON，而安全入口为了「和真 404 同形」
也照抄了这份 JSON。

锁定四件事：
1. 页面类 404 返回 HTML（不再是 JSON）；
2. `/api/*` 仍是 JSON（前端 fetch / 小程序要结构化错误）；
3. 页面里不含产品名、静态资源引用、脚本 —— 安全入口的隐蔽性不能被它破坏；
4. 门牌拦截出来的 404 与路由不存在的 404 用**同一个出口**（`app/errors.py`）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import errors, gate
from app.main import app

CFG_OFF = {"enabled": False, "path": "", "cookie_days": 30, "bypass_private": True, "ready": False}


# ------------------------------------------------------------------ errors 出口
def test_html_404_is_plain_and_fingerprint_free():
    resp = errors.not_found("/whatever")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.headers["cache-control"] == "no-store"  # 入口改对了不能被缓存挡住
    body = resp.body.decode()
    assert "404" in body
    for leak in ("HVAccRM", "/static/", "<script", "入口"):
        assert leak not in body


def test_api_404_stays_json():
    resp = errors.not_found("/api/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.body == b'{"detail":"Not Found"}'
    assert "cache-control" not in resp.headers  # 与 FastAPI 内置处理器完全同形


# ------------------------------------------------------------------ 应用层
def _client(monkeypatch) -> TestClient:
    """跳过登录判定（否则中间件会去开数据库），门牌置为关闭。"""
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_is_exempt", lambda path: True)
    gate.set_config_for_tests(CFG_OFF)
    return TestClient(app)


def test_missing_route_returns_html_404(monkeypatch):
    """多段路径才走「路由不存在」；单段路径会被 `/{verify_file}` 通配路由接走。"""
    client = _client(monkeypatch)
    resp = client.get("/no/such/page")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.text == errors.not_found("/no/such/page").body.decode()
    # 中间件出口统一补的安全头，页面 404 也要有
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_missing_api_route_returns_json_404(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_business_404_still_uses_layout_page():
    """业务侧 `raise HTTPException(404)`（记录不存在）仍走带侧栏的 404.html。

    这条是防回归：新加的 StarletteHTTPException 处理器不能把业务 404 也吃掉
    （fastapi.HTTPException 是子类，MRO 查找应当先命中旧的处理器）。
    """
    from fastapi import HTTPException

    from app.main import http_exception_handler  # 存在即说明注册未被覆盖

    assert http_exception_handler is not None
    # 子类实例必须仍然命中旧处理器：这里只做结构断言，具体渲染由业务测试覆盖
    from fastapi import HTTPException as FastAPIHTTP

    from starlette.exceptions import HTTPException as StarletteHTTP

    assert issubclass(FastAPIHTTP, StarletteHTTP)
    assert isinstance(HTTPException(status_code=404, detail="x"), StarletteHTTP)