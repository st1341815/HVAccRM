"""分享页自定义（`/cases/custom`）的单元测试。

覆盖：默认值 / 保存与读回 / 空值回落 / 文案占位符替换 / 页脚按行拆分 / 超长截断 / 恢复默认。
"""
from __future__ import annotations

from app.services import share_custom as cs


def test_defaults_have_all_keys():
    d = cs.defaults()
    assert set(d) == set(cs.LIMITS)
    assert d["share.title"] == "施工案例"
    assert "Copyright" in d["share.footer"]


def test_get_all_falls_back_to_defaults(db):
    got = cs.get_all(db)
    assert got["share.title"] == "施工案例"
    assert got["share.meta_title"] == "{product}施工案例 · {site}"


def test_save_and_read_back(db):
    cs.save(db, {
        "share.title": "精工案例",
        "share.brand": "某某暖通",
        "share.brand_icon": "❄",
        "share.meta_title": "{site} {product}精工案例",
        "share.meta_desc": "本地 12 年老店",
        "share.footer": "某某暖通科技\n苏ICP备12345号",
        "share.tail": "售后电话 400-000-0000",
    })
    db.commit()
    got = cs.get_all(db)
    assert got["share.title"] == "精工案例"
    assert got["share.brand"] == "某某暖通"
    assert cs.footer_lines(got["share.footer"]) == ["某某暖通科技", "苏ICP备12345号"]


def test_empty_value_overrides_to_empty(db):
    """显式清空 = 分享页不显示（不是回落默认）。"""
    cs.save(db, {"share.title": "", "share.footer": "", "share.tail": ""})
    db.commit()
    got = cs.get_all(db)
    assert got["share.title"] == ""
    assert cs.footer_lines(got["share.footer"]) == []


def test_render_text_replaces_known_placeholders_only():
    ctx = {"site": "颐园清苑 6#", "stage": "前期施工", "product": "地暖", "date": "2026-10-28"}
    assert cs.render_text("{product}施工案例 · {site}", ctx) == "地暖施工案例 · 颐园清苑 6#"
    # 未知占位符与大括号原样保留（不能抛 KeyError）
    assert cs.render_text("© {year} 某某 {site}", ctx) == "© {year} 某某 颐园清苑 6#"


def test_footer_lines_drops_blank_lines():
    assert cs.footer_lines("A\n\n  B  \n\n") == ["A", "B"]
    assert cs.footer_lines("") == []


def test_normalize_truncates_and_reports():
    cleaned, notes = cs.normalize({"share.title": "标" * 60, "share.footer": "a\r\nb"})
    assert len(cleaned["share.title"]) == cs.LIMITS["share.title"]
    assert cleaned["share.footer"] == "a\nb"  # CRLF 统一成 LF
    assert any("标题语" in n for n in notes)


def test_brand_icon_strips_newlines():
    cleaned, _ = cs.normalize({"share.brand_icon": "❄\n"})
    assert cleaned["share.brand_icon"] == "❄"


def test_reset_removes_share_keys(db):
    cs.save(db, {"share.title": "临时"})
    db.commit()
    assert cs.get_all(db)["share.title"] == "临时"
    cs.reset(db)
    db.commit()
    assert cs.get_all(db)["share.title"] == "施工案例"
