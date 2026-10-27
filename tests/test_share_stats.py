"""分享页统计回归测试：匿名访问记录、埋点幂等、封面/二维码、统计聚合与越权防护。

重点回归两条历史教训：
1. `_case_filter` 传空列表必须返回 0 行（`列表 or None` 曾导致「无匹配 → 返回全部」的数据泄露）；
2. beacon 重复上报必须取最大值（`pagehide` + `visibilitychange` 会各发一次）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from starlette.requests import Request

from app.models import Case, CaseImage, ShareVisit, now_iso
from app.routers import cases as cases_router
from app.services import cases as cases_svc
from app.services import share_stats as svc


# ------------------------------------------------------------ 夹具
def _patch_settings(monkeypatch, tmp_path, public_base="") -> SimpleNamespace:
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    fake = SimpleNamespace(media_dir=media, app_secret_key="test-secret", public_base_url=public_base)
    monkeypatch.setattr(svc, "get_settings", lambda: fake)
    monkeypatch.setattr(cases_svc, "get_settings", lambda: fake)
    return fake


def _request(path="/cases/share/tok123", query="", ua="", referer="", cookie="", proto=""):
    headers = [(b"host", b"crm.example.com")]
    if ua:
        headers.append((b"user-agent", ua.encode()))
    if referer:
        headers.append((b"referer", referer.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": headers,
            "client": ("192.168.11.66", 51234),
            "server": ("crm.example.com", 80),
            "scheme": "http",
            "root_path": "",
        }
    )


def _make_case(db, token="tok123", images=2) -> Case:
    case = Case(
        task_id=900 + db.query(Case).count(),
        customer_id=1,
        room_label="颐园清苑6#401",
        project_name="颐园清苑",
        building="6#",
        stage="前期施工",
        work_date="2026-10-20",
        product_type="地暖",
        installer_name="张师傅",
        share_token=token,
    )
    db.add(case)
    db.flush()
    return case


def _make_image(db, case, idx, media_dir) -> CaseImage:
    rel = f"{case.id}/{idx}.jpg"
    dest = media_dir / "cases" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 900), (200, 190, 170)).save(dest, "JPEG")
    img = CaseImage(case_id=case.id, path=rel, sort_order=idx)
    db.add(img)
    db.flush()
    return img


# ------------------------------------------------------------ UA 解析
def test_parse_ua():
    wechat = svc.parse_ua(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "Mobile/15E148 MicroMessenger/8.0.44"
    )
    assert wechat["is_wechat"] == 1
    assert wechat["device"] == "mobile"
    assert wechat["os_name"] == "iOS"

    desktop = svc.parse_ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")
    assert desktop["is_wechat"] == 0
    assert desktop["device"] == "desktop"
    assert desktop["os_name"] == "Windows"

    ipad = svc.parse_ua("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X) Mobile/15E148 MicroMessenger/8.0")
    assert ipad["device"] == "tablet"

    assert svc.parse_ua("")["device"] == "desktop"


# ------------------------------------------------------------ IP 只存摘要
def test_hash_ip_is_one_way(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    h = svc.hash_ip("192.168.11.66")
    assert len(h) == 16
    assert "192.168" not in h
    assert h == svc.hash_ip("192.168.11.66")
    assert h != svc.hash_ip("192.168.11.67")


# ------------------------------------------------------------ 对外地址
def test_public_base_prefers_setting(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, public_base="https://crm.example.com:8443")
    req = _request()
    assert svc.public_base(req) == "https://crm.example.com:8443/"
    assert svc.share_url(req, "abc") == "https://crm.example.com:8443/cases/share/abc"
    assert svc.share_url(req, "abc", "qr").endswith("?src=qr")


def test_public_base_honors_forwarded_proto(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, public_base="")
    req = _request(proto="https")
    assert svc.public_base(req).startswith("https://")


# ------------------------------------------------------------ 访问记录
def test_record_visit_fields(monkeypatch, tmp_path, db):
    fake = _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    req = _request(
        query="src=qr",
        ua="Mozilla/5.0 (Linux; Android 14) Mobile MicroMessenger/8.0",
        referer="https://weixin.qq.com/",
    )
    visit, need_cookie = svc.record_visit(db, case, req, 3)
    assert need_cookie is True
    assert visit.case_id == case.id
    assert visit.src == "qr"
    assert visit.is_wechat == 1
    assert visit.device == "mobile"
    assert visit.referer_host == "weixin.qq.com"
    assert visit.image_total == 3
    assert visit.ip_hash and "192.168" not in visit.ip_hash
    assert visit.visitor_id and len(visit.visitor_id) >= 8
    assert fake.media_dir.exists()


def test_record_visit_rejects_unknown_src(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    visit, _ = svc.record_visit(db, case, _request(query="src=evil"), 0)
    assert visit.src == "direct"


def test_record_visit_reuses_cookie(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    req = _request(cookie="hv_v=abcdefgh12345678")
    visit, need_cookie = svc.record_visit(db, case, req, 1)
    assert need_cookie is False
    assert visit.visitor_id == "abcdefgh12345678"


# ------------------------------------------------------------ 埋点幂等
def test_apply_beacon_takes_max(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    visit, _ = svc.record_visit(db, case, _request(), 5)

    svc.apply_beacon(db, case.id, visit.id, 3000, 2, 5)
    assert visit.dwell_ms == 3000 and visit.viewed_images == 2

    # 重复上报（pagehide + visibilitychange）：小值不回退
    svc.apply_beacon(db, case.id, visit.id, 1200, 1, 5)
    assert visit.dwell_ms == 3000 and visit.viewed_images == 2

    # 更大值覆盖
    svc.apply_beacon(db, case.id, visit.id, 9000, 4, 5)
    assert visit.dwell_ms == 9000 and visit.viewed_images == 4


def test_apply_beacon_clamps(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    visit, _ = svc.record_visit(db, case, _request(), 3)
    svc.apply_beacon(db, case.id, visit.id, -5, 99, 3)
    assert visit.dwell_ms == 0
    assert visit.viewed_images == 3  # 不超过图片总数
    svc.apply_beacon(db, case.id, visit.id, 10 ** 12, None, None)
    assert visit.dwell_ms == svc.DWELL_MAX_MS


def test_apply_beacon_rejects_other_case(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db, token="tokA")
    other = _make_case(db, token="tokB")
    visit, _ = svc.record_visit(db, case, _request(), 1)
    assert svc.apply_beacon(db, other.id, visit.id, 5000, 1, 1) is None
    assert visit.dwell_ms == 0


# ------------------------------------------------------------ 聚合与越权防护
def test_case_filter_empty_list_returns_nothing(monkeypatch, tmp_path, db):
    """空列表 = 无匹配，必须 0 行；绝不能退化成「不筛选 → 返回全部」。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    svc.record_visit(db, case, _request(), 1)
    db.flush()

    assert svc.totals(db, [])["opens"] == 0
    assert svc.case_rows(db, []) == {}
    assert svc.recent_visits(db, []) == []
    assert svc.daily_trend(db, [date.today().isoformat()], []) == [(date.today().isoformat(), 0)]

    assert svc.totals(db, None)["opens"] == 1
    assert svc.totals(db, [case.id])["opens"] == 1


def test_totals_and_rows(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    v1, _ = svc.record_visit(db, case, _request(), 2)   # 无 cookie → 各算一个访客
    v2, _ = svc.record_visit(db, case, _request(), 2)
    svc.apply_beacon(db, case.id, v1.id, 4000, 2, 2)
    svc.apply_beacon(db, case.id, v2.id, 8000, 1, 2)
    db.flush()

    total = svc.totals(db, None)
    assert total["opens"] == 2
    assert total["uv"] == 2
    assert total["avg_dwell"] == pytest.approx(6000.0)
    assert total["avg_imgs"] == pytest.approx(1.5)
    assert total["cases_hit"] == 1
    assert total["effective"] == 2  # 两条都有停留

    rows = svc.case_rows(db, None)
    assert rows[case.id]["opens"] == 2
    assert rows[case.id]["wechat"] == 0

    # 微信预览抓取（停留 0）计入打开数、但不计入「有停留的访问」
    svc.record_visit(db, case, _request(), 2)
    db.flush()
    total = svc.totals(db, None)
    assert total["opens"] == 3 and total["effective"] == 2


# ------------------------------------------------------------ 分享页
def test_share_page_records_visit_and_og(monkeypatch, tmp_path, db):
    fake = _patch_settings(monkeypatch, tmp_path, public_base="https://crm.example.com:8443")
    case = _make_case(db)
    _make_image(db, case, 0, fake.media_dir)
    _make_image(db, case, 1, fake.media_dir)
    db.flush()

    req = _request(ua="Mozilla/5.0 (iPhone) MicroMessenger/8.0")
    resp = cases_router.share_case(req, "tok123", db)
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "og:title" in html
    assert "地暖施工案例 · 颐园清苑 6#" in html
    assert "https://crm.example.com:8443/cases/share/tok123/cover.jpg" in html
    assert "qrcode.svg" in html
    assert "颐园清苑6#401" not in html  # 完整房号不外露

    visits = db.query(ShareVisit).all()
    assert len(visits) == 1
    assert visits[0].case_id == case.id
    assert case.share_views == 1


def test_share_page_404_and_expired(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    assert cases_router.share_case(_request(), "nope", db).status_code == 404

    case = _make_case(db)
    case.share_disabled = 1
    db.flush()
    resp = cases_router.share_case(_request(), "tok123", db)
    assert resp.status_code == 200
    assert "已失效" in resp.body.decode("utf-8")


def test_ping_updates_visit(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    visit, _ = svc.record_visit(db, case, _request(), 4)
    db.flush()

    req = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/cases/share/tok123/ping",
            "raw_path": b"/cases/share/tok123/ping",
            "query_string": b"",
            "headers": [(b"host", b"crm.example.com"), (b"content-type", b"application/json")],
            "client": ("192.168.11.66", 51234),
            "server": ("crm.example.com", 80),
            "scheme": "http",
            "root_path": "",
        }
    )

    async def _call(body: bytes):
        async def _receive():
            return {"type": "http.request", "body": body, "more_body": False}

        req._receive = _receive  # type: ignore[attr-defined]
        return await cases_router.share_ping(req, "tok123", db)

    resp = asyncio.run(_call(json.dumps({"visit": visit.id, "dwell": 7000, "imgs": 3, "total": 4}).encode()))
    assert resp.status_code == 204
    assert visit.dwell_ms == 7000
    assert visit.viewed_images == 3

    # 坏 payload 不该 500
    resp = asyncio.run(_call(b"not-json"))
    assert resp.status_code == 204


def test_cover_and_qrcode(monkeypatch, tmp_path, db):
    fake = _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    _make_image(db, case, 0, fake.media_dir)
    db.flush()

    resp = cases_router.share_cover("tok123", db)
    assert resp.status_code == 200
    assert resp.media_type == "image/jpeg"
    out = svc.cover_path(case.id)
    assert out.exists()
    with Image.open(out) as im:
        assert im.size == svc.COVER_SIZE

    svg = cases_router.share_qrcode(_request(), "tok123", db)
    assert svg.status_code == 200
    assert svg.media_type == "image/svg+xml"
    assert b"<svg" in svg.body

    assert cases_router.share_cover("nope", db).status_code == 404


# ------------------------------------------------------------ 统计页
def test_stats_page_renders(monkeypatch, tmp_path, db, admin):
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    visit, _ = svc.record_visit(db, case, _request(), 2)
    svc.apply_beacon(db, case.id, visit.id, 65000, 2, 2)
    db.flush()

    resp = cases_router.share_stats_page(_request(path="/cases/stats"), db=db, user=admin)
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "分享统计" in html
    assert "1 分 5 秒" in html          # 停留 65000ms 的 duration 过滤器
    assert "颐园清苑 6#" in html
    assert "已失效" not in html


def test_stats_page_keyword_no_match_is_empty(monkeypatch, tmp_path, db, admin):
    """关键词无匹配时必须是 0 行，不能把全部访问都倒出来。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    svc.record_visit(db, case, _request(), 1)
    db.flush()

    resp = cases_router.share_stats_page(_request(path="/cases/stats"), q="不存在的楼盘", db=db, user=admin)
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "还没有访问记录" in html
    assert "颐园清苑 6#" not in html
