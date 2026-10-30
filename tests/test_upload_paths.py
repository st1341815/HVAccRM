"""上传路径安全回归测试：写入侧必须与读取侧对称，禁止路径遍历。

背景：2026-10-30 微信风控排查时发现 `save_photo(kind=...)` 的 kind 来自表单、
未净化就拼进 `media/{key}/{kind}/`，`kind="../../../x"` 可把文件写出 media 目录。
本文件锁定该修复，防止回退。
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.config import get_settings
from app.services import cases as cases_svc
from app.services import photos as photo_svc


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 128, 255)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def media(tmp_path, monkeypatch):
    """把 media 根指到临时目录（media_dir 是 Settings 只读属性，只能改底层 data_dir）。"""
    s = get_settings()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(type(s), "media_dir", property(lambda self: data_dir / "media"), raising=False)
    root = (data_dir / "media").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.parametrize(
    "kind",
    [
        "../../../escaped_dir",
        "..",
        ".",
        "a/b",
        "C:\\win",
        "/etc/passwd",
        "..%2f..%2fx",
    ],
)
def test_save_photo_kind_cannot_escape_media(db, media, kind):
    """任何恶意 kind 都必须限制在 media 目录内。"""
    photo, msg = photo_svc.save_photo(
        db, customer_id=1, kind=kind, raw=_jpeg_bytes(), orig_name="a.jpg", uploaded_by=1
    )
    if photo is None:  # 被直接拒绝也算安全
        assert "非法" in msg or "空文件" in msg
        return
    target = (media / photo.path).resolve()
    assert str(target).startswith(str(media)), f"逃出 media：{target}"
    assert target.exists()


def test_save_photo_kind_keeps_chinese_label(db, media):
    """净化不应误伤正常中文分类名（如「现场」「收款截图」）。"""
    photo, _ = photo_svc.save_photo(
        db, customer_id=2, kind="现场照片", raw=_jpeg_bytes(), orig_name="b.jpg", uploaded_by=1
    )
    assert photo is not None
    assert "现场照片" in photo.path


def test_safe_segment_defaults_when_empty():
    """净化后为空必须回落默认值，不能生成空目录名（否则路径变成 media//）。"""
    assert photo_svc._safe_segment("", "other") == "other"
    assert photo_svc._safe_segment(None, "other") == "other"
    assert photo_svc._safe_segment("../..", "other") == "other"


def test_case_image_stays_inside_case_root(db, media):
    """案例展示图同样必须限制在 media/cases 内。"""
    from types import SimpleNamespace

    case = SimpleNamespace(id=99)
    img, _msg = cases_svc.save_case_image(db, case, _jpeg_bytes(), "../../evil.jpg")
    assert img is not None
    root = (media / "cases").resolve()
    target = (root / img.path).resolve()
    assert str(target).startswith(str(root)), f"逃出 cases 目录：{target}"
    assert target.exists()
