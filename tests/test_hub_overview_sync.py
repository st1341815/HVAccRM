"""Docker Hub Overview 同步脚本的回归测试（不联网，mock urlopen）。

这个脚本跑在 CI 的 continue-on-error 步骤里，出错会被静默吞掉——所以必须有测试兜住请求构造。
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "sync_hub_overview.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_hub_overview", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    status = 200


@pytest.fixture
def calls(monkeypatch):
    mod = _load_module()
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if "users/login" in req.full_url:
            return _Resp(json.dumps({"token": "JWT.FAKE"}).encode())
        return _Resp(b"{}")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    return mod, seen


def test_skips_without_credentials(monkeypatch, capsys):
    mod = _load_module()
    monkeypatch.delenv("DH_USER", raising=False)
    monkeypatch.delenv("DH_TOKEN", raising=False)
    assert mod.main(["prog"]) == 0
    assert "跳过" in capsys.readouterr().out


def test_patches_full_and_short_description(calls, monkeypatch):
    mod, seen = calls
    monkeypatch.setenv("DH_USER", "13uncle")
    monkeypatch.setenv("DH_TOKEN", "tok")

    assert mod.main(["prog", str(ROOT / "docs" / "DockerHub-Overview.md")]) == 0
    assert len(seen) == 2

    login, patch = seen
    assert login.full_url == "https://hub.docker.com/v2/users/login/"
    assert json.loads(login.data) == {"username": "13uncle", "password": "tok"}

    assert patch.full_url == "https://hub.docker.com/v2/repositories/13uncle/hvaccrm/"
    assert patch.get_method() == "PATCH"
    assert patch.headers["Authorization"] == "JWT JWT.FAKE"
    body = json.loads(patch.data)
    assert body["full_description"].startswith("<p align=\"center\">")
    assert len(body["description"]) <= 100  # Docker Hub 短描述上限


def test_missing_doc_does_not_crash(calls, monkeypatch, capsys):
    mod, seen = calls
    monkeypatch.setenv("DH_USER", "13uncle")
    monkeypatch.setenv("DH_TOKEN", "tok")
    assert mod.main(["prog", "docs/不存在.md"]) == 0
    assert seen == []
    assert "跳过" in capsys.readouterr().out


def test_http_error_is_swallowed(calls, monkeypatch, capsys):
    """凭证没写权限（403）不该让发版流程变红。"""
    mod, _ = calls
    import urllib.error

    def raise_403(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_403)
    monkeypatch.setenv("DH_USER", "13uncle")
    monkeypatch.setenv("DH_TOKEN", "tok")
    assert mod.main(["prog"]) == 0
    assert "同步失败" in capsys.readouterr().out
