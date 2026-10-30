"""域名验证文件路由测试（微信/腾讯云/ICP 备案会周期性下发新的随机 .txt）。

背景：2026-10-30 微信申诉要求部署 `41abda4e....txt`。原实现只认镜像内
`app/static/`，为了一个 40 字节的 txt 每次都要重建镜像。现改为
`app/static/` → `data/verify/` 两处查找，本次锁定这两条路径与路径穿越防护。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """独立的 static/ 与 data/verify/ 目录，避免污染真实工作区。"""
    static = tmp_path / "static"
    static.mkdir()
    data = tmp_path / "data"
    (data / "verify").mkdir(parents=True)
    monkeypatch.setattr(main, "static_dir", static)
    monkeypatch.setattr(main, "settings", SimpleNamespace(data_path=data))
    return static, data / "verify"


def test_serve_verify_file_from_static(dirs):
    static, _ = dirs
    (static / "abc.txt").write_text("token-1", encoding="utf-8")
    resp = main.serve_verify_file("abc.txt")
    assert resp.body == b"token-1"
    assert resp.media_type == "text/plain; charset=utf-8"


def test_serve_verify_file_from_data_verify(dirs):
    """数据卷里的验证文件也要能直出（这样以后不用重建镜像）。"""
    _, verify = dirs
    (verify / "def.txt").write_text("token-2", encoding="utf-8")
    resp = main.serve_verify_file("def.txt")
    assert resp.body == b"token-2"


def test_static_wins_over_data(dirs):
    """两处同名时以镜像内为准，避免数据卷文件意外覆盖。"""
    static, verify = dirs
    (static / "same.txt").write_text("from-static", encoding="utf-8")
    (verify / "same.txt").write_text("from-data", encoding="utf-8")
    assert main.serve_verify_file("same.txt").body == b"from-static"


@pytest.mark.parametrize(
    "name",
    [
        "../secret.txt",        # 路径穿越
        "a/b.txt",              # 多段
        "..\\win.txt",          # 反斜杠
        "/etc/passwd.txt",      # 绝对路径
        "noext",                # 非 .txt
        "index.html",           # 非 .txt
        "missing.txt",          # 不存在
        "",                     # 空
    ],
)
def test_serve_verify_file_rejects(dirs, name):
    with pytest.raises(HTTPException) as ei:
        main.serve_verify_file(name)
    assert ei.value.status_code == 404


def test_is_exempt_covers_verify_file(dirs):
    """验证文件必须免登录，否则平台抓取会拿到 303 登录跳转。"""
    _, verify = dirs
    (verify / "wechat.txt").write_text("x", encoding="utf-8")
    assert main._is_exempt("/wechat.txt") is True
    assert main._is_exempt("/missing.txt") is False
    # 原有白名单不受影响
    assert main._is_exempt("/health") is True
    assert main._is_exempt("/customers") is False
