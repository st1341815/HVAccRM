"""wxmp logout 路由回归测试：
1. 有 running 任务时拒绝退出（防删 profile 毁掉进行中的发布）；
2. 无任务时调用 service 并写审计日志；
3. service.logout() 的行为契约：停浏览器 → 删 profile → 重启 → 返回 state。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers import wxmp as wxmp_router
from app.services import wxmp as wxmp_svc


def test_logout_rejected_while_task_running(monkeypatch):
    """正在同步的案例存在时，退出必须被拒绝（否则删 profile 毁掉发布）。"""
    called = {"svc": False}
    monkeypatch.setattr(wxmp_svc, "logout", lambda: called.update(svc=True) or {"ok": True})
    monkeypatch.setattr(
        wxmp_router, "_TASKS",
        {9: {"status": "running", "started": "2999-01-01 00:00:00", "user": "x"}},
    )

    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), url=SimpleNamespace(path="/wxmp/logout"))
    resp = wxmp_router.wxmp_logout(request=req, db=None, user=SimpleNamespace(id=1, display_name="t"), _csrf=None)

    assert called["svc"] is False, "running 任务存在时不应调用 service.logout()"
    assert resp.status_code == 303 and "/wxmp" in resp.headers["location"]
    # 验证 redirect 带了提示（flash 由 cookie 承载，这里只验路由行为）


def test_logout_calls_service_and_audits(monkeypatch):
    """空闲时正常退出：调 service、写审计、跳回 /wxmp。"""
    audit = []
    monkeypatch.setattr(wxmp_router, "_TASKS", {})
    monkeypatch.setattr(wxmp_svc, "logout", lambda: {"ok": True, "state": {"ok": True, "logged_in": False, "qr": True}})
    monkeypatch.setattr(
        wxmp_router, "log_action",
        lambda db, user, action, table, rid, **kw: audit.append(action),
    )

    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"), url=SimpleNamespace(path="/wxmp/logout"))
    resp = wxmp_router.wxmp_logout(request=req, db=None, user=SimpleNamespace(id=1, display_name="t"), _csrf=None)

    assert audit == ["wxmp_logout"]
    assert resp.status_code == 303 and resp.headers["location"] == "/wxmp"


def test_logout_service_clears_profile(tmp_path, monkeypatch):
    """logout() 契约：停浏览器 → 删 profile → 重启 → 返回登录态。"""
    profile = tmp_path / "wxmp-profile"
    profile.mkdir()
    (profile / "Default" / "Cookies").parent.mkdir(parents=True)
    (profile / "Default" / "Cookies").write_bytes(b"fake-cookie-db")

    calls = []
    monkeypatch.setattr(wxmp_svc, "profile_dir", lambda: profile)
    monkeypatch.setattr(wxmp_svc, "stop_browser", lambda: calls.append("stop"))
    monkeypatch.setattr(wxmp_svc, "ensure_browser", lambda: calls.append("start"))
    monkeypatch.setattr(
        wxmp_svc, "login_state",
        lambda: {"ok": True, "logged_in": False, "qr": True},
    )

    out = wxmp_svc.logout()

    assert out["ok"] is True and out["cleared"] is True
    assert out["state"]["logged_in"] is False and out["state"]["qr"] is True
    assert calls == ["stop", "start"]
    assert not profile.exists(), "profile 应被删除（登录态彻底清除）"
