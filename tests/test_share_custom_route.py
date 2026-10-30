"""`POST /cases/custom` 的端到端测试（直调路由函数，含审计写入）。

**为什么这么测**：v1.7.2 上线后点「保存」报 500（追踪号 912fa0bb），根因是
`log_action(..., detail={...})` 传了签名里不存在的关键字。裸的 service 单测抓不到，
静态 AST 检查能抓，但还需要一条「真的跑一遍保存路径」的用例，防止下次
`log_action` / `commit_retry` / `redirect` 任何一环在运行时炸掉。
"""
from __future__ import annotations

from starlette.requests import Request

from app.routers.cases import share_custom_reset, share_custom_save
from app.services import share_custom as cs


def _fake_request(path: str = "/cases/custom") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8090),
            "client": ("10.0.0.9", 54321),
            "headers": [(b"user-agent", b"pytest")],
            "query_string": b"",
        }
    )


def test_save_returns_redirect_and_persists(db, admin):
    resp = share_custom_save(
        request=_fake_request(),
        share_title="精工案例",
        share_brand="某某暖通",
        share_brand_icon="❄",
        share_meta_title="{site} {product}精工案例",
        share_meta_desc="本地 12 年老店",
        share_footer="某某暖通科技\n苏ICP备12345号",
        share_tail="售后电话 400-000-0000",
        db=db,
        user=admin,
        _csrf=None,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/cases/custom")

    got = cs.get_all(db)
    assert got["share.title"] == "精工案例"
    assert got["share.brand"] == "某某暖通"
    assert cs.footer_lines(got["share.footer"]) == ["某某暖通科技", "苏ICP备12345号"]

    # 审计必须真的写进库（正是这一步过去炸的）
    from sqlalchemy import select

    from app.models import AuditLog

    rows = db.scalars(select(AuditLog).where(AuditLog.action == "share_custom_save")).all()
    assert len(rows) == 1
    assert rows[0].username == admin.username
    assert "share.footer" in (rows[0].new_val or "")


def test_reset_returns_redirect_and_clears(db, admin):
    share_custom_save(
        request=_fake_request(),
        share_title="临时", share_brand="", share_brand_icon="",
        share_meta_title="", share_meta_desc="", share_footer="", share_tail="",
        db=db, user=admin, _csrf=None,
    )
    assert cs.get_all(db)["share.title"] == "临时"

    resp = share_custom_reset(request=_fake_request("/cases/custom/reset"), db=db, user=admin, _csrf=None)
    assert resp.status_code == 303
    states = cs.get_all(db)
    assert states["share.title"] == "施工案例"      # 回落默认
    assert states["share.meta_title"] == "{product}施工案例 · {site}"


def test_save_with_overlong_text_still_succeeds(db, admin):
    """超长截断走 warning 分支，也必须不 500。"""
    resp = share_custom_save(
        request=_fake_request(),
        share_title="标" * 80, share_brand="牌" * 40, share_brand_icon="❄",
        share_meta_title="", share_meta_desc="", share_footer="1\n2\n3", share_tail="",
        db=db, user=admin, _csrf=None,
    )
    assert resp.status_code == 303
    assert len(cs.get_all(db)["share.title"]) == cs.LIMITS["share.title"]
    assert cs.footer_lines(cs.get_all(db)["share.footer"]) == ["1", "2", "3"]
