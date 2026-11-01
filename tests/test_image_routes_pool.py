"""图片路由的连接池回归测试（2026-11-01 线上事故：案例页缩略图并发打爆连接池）。

事故链：图片路由用 `Depends(get_db)` 拿请求级会话，而 FileResponse 是流式响应，
会话要等文件传完才关闭 → 一个页面 30+ 张缩略图就吃干 5+10=15 个连接 →
排队 30s 抛 `QueuePool TimeoutError`，整页图片 500。

本文件守住三件事：
1. 图片/大文件路由不得再注入请求级 `Depends(get_db)`（必须用短会话）；
2. 连接池参数不能被改回默认 5+10；
3. `cached_file_response` 的 Cache-Control / Content-Disposition 行为。
"""
from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app import db as db_mod
from app.models import Base, CaseImage
from app.routers import cases as cases_router
from app.services import cases as cases_svc
from app.utils import cached_file_response

ROOT = Path(__file__).resolve().parent.parent

# 必须是「短会话」的路由：它们都返回 FileResponse（流式），大会话会占死连接
SHORT_SESSION_ROUTES = {
    ROOT / "app/routers/cases.py": {
        "case_image_file",
        "case_image_thumb",
        "share_case_image",
        "share_case_image_file",
        "share_case_image_thumb",
        "share_cover",
    },
    ROOT / "app/routers/photos.py": {"photo_file", "photo_thumb"},
    ROOT / "app/routers/admin.py": {"download_backup"},
}


def _injected_get_db(node: ast.FunctionDef) -> str | None:
    """函数签名里是否有 `xxx: Session = Depends(get_db)`，有则返回参数名。"""
    args = node.args.args
    defaults = node.args.defaults
    if not defaults:
        return None
    for arg, default in zip(args[len(args) - len(defaults):], defaults):
        text = ast.unparse(default)
        if "Depends" in text and "get_db" in text:
            return arg.arg
    return None


@pytest.mark.parametrize("path", list(SHORT_SESSION_ROUTES), ids=lambda p: p.name)
def test_streaming_routes_do_not_inject_request_db(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    expected = SHORT_SESSION_ROUTES[path]
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in expected:
            found[node.name] = _injected_get_db(node)
    assert set(found) == expected, f"未找到这些路由函数：{expected - set(found)}"
    bad = {name: arg for name, arg in found.items() if arg}
    assert not bad, f"这些流式路由仍在注入请求级 DB 会话（会占死连接池）：{bad}"


def test_pool_limits_not_reverted():
    assert db_mod.POOL_SIZE >= 15, "连接池太小会让并发缩略图排队超时"
    assert db_mod.MAX_OVERFLOW >= 30
    assert db_mod.POOL_TIMEOUT <= 15, "排队超时过长会让整页卡 30 秒"


def test_cached_file_response_headers(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x" * 32)

    r = cached_file_response(p, media_type="image/jpeg")
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.headers["content-type"] == "image/jpeg"

    r2 = cached_file_response(p, public=True, max_age=60)
    assert r2.headers["cache-control"] == "public, max-age=60"

    r3 = cached_file_response(p, filename="原图.jpg")
    assert "attachment" in r3.headers["content-disposition"]


def test_short_session_survives_concurrency(tmp_path, monkeypatch):
    """池只有 2 个连接时，20 个并发缩略图请求也不应超时（短会话快速归还连接）。"""
    dbfile = tmp_path / "t.db"
    eng = create_engine(
        f"sqlite:///{dbfile}",
        future=True,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=0,
        pool_timeout=2,
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)

    img = tmp_path / "thumb.webp"
    img.write_bytes(b"RIFF0000WEBP")

    with Session() as s:
        s.add(CaseImage(case_id=1, path="x/y.jpg", thumb_path="x/y.webp", sort_order=1))
        s.commit()
        image_id = s.query(CaseImage).first().id

    monkeypatch.setattr(cases_router, "SessionLocal", Session)
    monkeypatch.setattr(cases_svc, "case_image_file", lambda rel: img)

    errors: list[str] = []

    def hit():
        try:
            resp = cases_router.case_image_thumb(SimpleNamespace(), image_id, SimpleNamespace())
            assert resp.status_code == 200 or getattr(resp, "media_type", "") == "image/webp"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=hit) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"并发下出现异常（连接池又被打爆了？）：{errors[:3]}"
    eng.dispose()
