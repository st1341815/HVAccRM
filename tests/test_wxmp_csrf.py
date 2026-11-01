"""公众号三个 POST 端点的 CSRF 校验（P1-5 回归测试）。

背景：`routers/wxmp.py` 曾全文没有 csrf_protect，而 admin/cases 都有。
`/wxmp/cases/{id}`、`/wxmp/refresh` 又是「对外产生内容 / 重启浏览器」这种
高危动作，属于站内规范不一致。

两个 fetch 端点是无 body 的 POST，带不了表单字段，所以走 `X-CSRF-Token` 头。
"""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers.wxmp import _csrf_any
from app.security import make_csrf_token
from app.templating import csrf_cookie_name


def _req(headers=None):
    hs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/wxmp/cases/1",
        "headers": hs,
        "query_string": b"",
    }
    return Request(scope)


def test_rejects_without_any_token():
    with pytest.raises(HTTPException) as ei:
        _csrf_any(_req(), "")
    assert ei.value.status_code == 403


def test_rejects_wrong_token():
    tok = make_csrf_token()
    req = _req({"cookie": f"{csrf_cookie_name()}={tok}", "x-csrf-token": "bogus.sig"})
    with pytest.raises(HTTPException) as ei:
        _csrf_any(req, "")
    assert ei.value.status_code == 403


def test_accepts_token_from_header():
    """fetch 调用：无 body，token 在 X-CSRF-Token 头里。"""
    tok = make_csrf_token()
    req = _req({"cookie": f"{csrf_cookie_name()}={tok}", "x-csrf-token": tok})
    assert _csrf_any(req, "") is None  # 不抛异常 = 通过


def test_accepts_token_from_form_field():
    """表单提交（如 /wxmp/template）：token 在 csrf_token 字段里。"""
    tok = make_csrf_token()
    req = _req({"cookie": f"{csrf_cookie_name()}={tok}"})
    assert _csrf_any(req, tok) is None


def test_header_takes_precedence_over_form():
    """头里是错的、表单里是对的 → 仍应拒绝（避免误放行）。"""
    tok = make_csrf_token()
    req = _req({"cookie": f"{csrf_cookie_name()}={tok}", "x-csrf-token": "wrong.sig"})
    with pytest.raises(HTTPException):
        _csrf_any(req, tok)
