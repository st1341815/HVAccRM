"""合同台账的操作列必须能直接进「电子合同 / 结算单」，以及 HTML 不缓存。

线上反馈：「手机端不显示打印和电子合同控件」。核查结论是服务端输出一直在
（详情页确实渲染了这两个按钮），真正的缺口是**列表页一个入口都没有** ——
手机上要先进详情、再在一堆按钮里找，路径太长。所以把入口补到台账行内，
并用测试钉住，避免以后改模板时又被顺手删掉。

同时钉住 `Cache-Control: no-store`：动态页面被浏览器/PWA 缓存，就会出现
「电脑上有、手机上没有」这种只能靠清缓存复现的幽灵问题。
"""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

from app.main import _apply_security_headers
from app.models import Contract, Customer
from app.routers import contracts as contracts_router
from app.services import contract_docs as doc_svc


def _req(path: str = "/contracts", query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [(b"host", b"testserver"), (b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 5555),
            "server": ("testserver", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _seed(db, admin) -> Contract:
    c = Customer(name="陶姐", phone="15900000000", type="家装", owner_id=admin.id)
    db.add(c)
    db.flush()
    ctr = Contract(
        customer_id=c.id, no="ONE202610250012", product_type="暖气片",
        total_amount=12000, sign_date="2026-10-25", status="active", created_by=admin.id,
    )
    db.add(ctr)
    db.flush()
    doc_svc.ensure_default_template(db, created_by=admin.id)
    db.flush()
    return ctr


def test_contract_list_exposes_doc_links(db, admin):
    ctr = _seed(db, admin)
    resp = contracts_router.list_contracts(
        request=_req(), customer_q="", status="", settled="",
        sort="sign_date", order="desc", db=db, user=admin,
    )
    html = resp.body.decode()
    assert f"/contracts/{ctr.id}/print/contract" in html, "台账里没有「电子合同」入口"
    assert f"/contracts/{ctr.id}/print/settlement" in html, "台账里没有「结算单」入口"


def test_html_response_is_not_cached():
    """动态页面必须 no-store，否则手机端会拿旧 HTML（按钮看起来「消失」）。"""
    resp = Response("<html>ok</html>", media_type="text/html")
    _apply_security_headers(resp, secure=True, path="/contracts/12")
    assert "no-store" in resp.headers["cache-control"]


def test_static_response_keeps_cache_semantics():
    """静态资源不进这个分支：缓存静态文件是对的，别一刀切成 no-store。"""
    resp = Response("body{}", media_type="text/css")
    _apply_security_headers(resp, secure=True, path="/static/app.css")
    assert "cache-control" not in resp.headers
