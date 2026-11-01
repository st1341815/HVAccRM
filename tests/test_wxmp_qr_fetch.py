"""v1.9.21 二维码抓取回归：fetch 原图为主、截图裁剪为兜底。

复现的问题（v1.9.20 退出登录后用户实测）：新 profile 首次加载时布局未稳，
「截图 + rect 裁剪」错位 → 图上半截是两行 tofu 文字、二维码只露一部分；
且二维码 5 分钟过期后 login_state 不 reload，永远拿到过期白图。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import wxmp as wxmp_svc


class _FakeCDP:
    def __init__(self, fetch_result="data:image/png;base64,iVBORw0KGgo=", expired=False, has_qr=True, token=""):
        self._fetch = fetch_result
        self._expired = expired
        self._has_qr = has_qr
        self._token = token
        self.calls = []

    def js(self, expr, await_promise=False):
        self.calls.append(expr[:40])
        if "scanloginqrcode" in expr and not await_promise and "innerText" not in expr:
            return "true" if self._has_qr else "false"
        if "二维码已过期" in expr:
            return "true" if self._expired else "false"
        if "token" in expr:
            return self._token
        if await_promise and "fetch" in expr:
            return self._fetch
        return "false"

    def call(self, method, **kw):
        self.calls.append(f"{method}:{kw}")


def test_fetch_qr_writes_original_image(tmp_path):
    """fetch 返回 data URL → 原图字节直接落盘。"""
    dest = tmp_path / "qr.png"
    c = _FakeCDP(fetch_result="data:image/png;base64,aGVsbG8=")
    assert wxmp_svc._fetch_qr(c, dest) is True
    assert dest.read_bytes() == b"hello"


def test_fetch_qr_failure_returns_false(tmp_path):
    """fetch 失败（HTTP 状态 / 异常）→ 返回 False，让调用方回落截图裁剪。"""
    dest = tmp_path / "qr.png"
    c = _FakeCDP(fetch_result="HTTP404")
    assert wxmp_svc._fetch_qr(c, dest) is False
    assert not dest.exists()


def test_login_state_uses_fetch_first(tmp_path, monkeypatch):
    """login_state：有二维码且未过期 → 走 fetch 原图，不做截图裁剪。"""
    monkeypatch.setattr(wxmp_svc, "is_available", lambda: True)
    monkeypatch.setattr(wxmp_svc, "ensure_browser", lambda: None)
    monkeypatch.setattr(wxmp_svc, "_open", lambda *a, **k: None)

    dest = tmp_path / "qr.png"
    monkeypatch.setattr(wxmp_svc, "qr_path", lambda: dest)

    crop_called = []
    monkeypatch.setattr(wxmp_svc, "_crop_qr", lambda *a: crop_called.append(1) or False)

    c = _FakeCDP()
    out = {}
    monkeypatch.setattr(
        wxmp_svc.cdp_mod, "CDP", lambda ws: c
    )
    # _open 返回 None 时 login_state 的 c.close() 会挂——用真实包装代替
    class _W:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, n): return getattr(self.inner, n)
        def close(self): pass
    monkeypatch.setattr(wxmp_svc, "_open", lambda *a, **k: _W(c))

    state = wxmp_svc.login_state()
    assert state == {"ok": True, "logged_in": False, "qr": True}
    # FakeCDP 默认 fetch 返回 data:image/png;base64,iVBORw0KGgo= → PNG 魔数
    assert dest.read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert not crop_called, "fetch 成功时不应回落截图裁剪"


def test_login_state_reloads_expired_qr(tmp_path, monkeypatch):
    """页面显示「二维码已过期」→ 必须 Page.reload 一次再拿码（老码 5 分钟过期后
    页面不会自己换，_open 因 URL 未变也不重载）。"""
    monkeypatch.setattr(wxmp_svc, "is_available", lambda: True)
    monkeypatch.setattr(wxmp_svc, "ensure_browser", lambda: None)

    c = _FakeCDP(expired=True)

    class _W:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, n): return getattr(self.inner, n)
        def close(self): pass

    monkeypatch.setattr(wxmp_svc, "_open", lambda *a, **k: _W(c))
    dest = tmp_path / "qr.png"
    monkeypatch.setattr(wxmp_svc, "qr_path", lambda: dest)
    monkeypatch.setattr(wxmp_svc, "_crop_qr", lambda *a: False)

    state = wxmp_svc.login_state()
    assert state["qr"] is True
    assert any(x.startswith("Page.reload") for x in c.calls), "过期必须触发 Page.reload"
