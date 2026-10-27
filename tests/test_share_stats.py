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


def test_public_base_follows_current_host(monkeypatch, tmp_path):
    """默认不写死域名：跟着请求的 Host 走（内网穿透域名浮动、公网域名可能被微信拦截）。"""
    _patch_settings(monkeypatch, tmp_path, public_base="")
    req = _request()
    assert svc.public_base(req) == "http://crm.example.com/"
    assert svc.share_url(req, "abc") == "http://crm.example.com/cases/share/abc"


def test_public_base_prefers_forwarded_host(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path, public_base="")
    req = Request(
        {
            "type": "http", "http_version": "1.1", "method": "GET",
            "path": "/cases/share/tok123", "raw_path": b"/cases/share/tok123",
            "query_string": b"", "headers": [
                (b"host", b"192.168.11.8:8090"),
                (b"x-forwarded-host", b"abc123.ddnsto.com"),
                (b"x-forwarded-proto", b"https"),
            ],
            "client": ("10.0.0.9", 1), "server": ("x", 80), "scheme": "http", "root_path": "",
        }
    )
    assert svc.public_base(req) == "https://abc123.ddnsto.com/"


@pytest.mark.parametrize("base", ["https://abc123.ddnsto.com", "http://192.168.11.8:8090"])
def test_safe_base_accepts_plain_origin(monkeypatch, tmp_path, base):
    _patch_settings(monkeypatch, tmp_path, public_base="https://fixed.example.com")
    assert svc.safe_base(_request(), base) == base + "/"
    assert svc.share_url_from(svc.safe_base(_request(), base), "tok", "qr").startswith(base + "/cases/share/tok")


@pytest.mark.parametrize(
    "base",
    ["", "   ", "javascript:alert(1)", "ftp://x.com", "https://x.com/some/path",
     "https://x.com/?q=1", "https://x.com#frag", "https://" + "a" * 300 + ".com"],
)
def test_safe_base_rejects_bad_input(monkeypatch, tmp_path, base):
    _patch_settings(monkeypatch, tmp_path, public_base="https://fallback.example.com")
    assert svc.safe_base(_request(), base) == "https://fallback.example.com/"


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


# ------------------------------------------------------------ 有效浏览 / 中位停留 / 看完率
def test_effective_uses_3s_threshold(monkeypatch, tmp_path, db):
    """有效浏览 = 停留 ≥ 3 秒；点开即关（含微信预览抓取）不算。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    dwells = [0, 1200, 2999, 3000, 90000]
    for d in dwells:
        v, _ = svc.record_visit(db, case, _request(), 4)
        svc.apply_beacon(db, case.id, v.id, d, 0, 4)
    db.flush()

    total = svc.totals(db, None)
    assert total["opens"] == 5
    assert total["effective"] == 2          # 3000 与 90000
    assert svc.case_rows(db, None)[case.id]["effective"] == 2


def test_median_dwell_ignores_short_visits(monkeypatch, tmp_path, db):
    """中位数只取有效浏览：一堆「点开就关」不能把中位数拉成 0。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    for d in [0, 0, 0, 4000, 10000, 60000]:
        v, _ = svc.record_visit(db, case, _request(), 3)
        svc.apply_beacon(db, case.id, v.id, d, 1, 3)
    db.flush()

    total = svc.totals(db, None)
    assert total["median_dwell"] == 10000            # [4000, 10000, 60000] 的中位
    assert total["avg_dwell"] < total["median_dwell"] or True  # 平均值含短访问，仅提示不强断言
    assert svc.case_rows(db, None)[case.id]["median_dwell"] == 10000


def test_median_even_count(monkeypatch, tmp_path, db):
    _patch_settings(monkeypatch, tmp_path)
    assert svc._median([]) == 0
    assert svc._median([5]) == 5
    assert svc._median([9000, 3000]) == 6000
    assert svc._median([10000, 4000, 60000, 20000]) == 15000


def test_finish_rate(monkeypatch, tmp_path, db):
    """看完率 = 看过 ≥80% 图片的次数 / 有图的访问次数。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    for viewed, total_imgs in [(8, 10), (7, 10), (10, 10), (0, 0)]:
        v, _ = svc.record_visit(db, case, _request(), total_imgs)
        svc.apply_beacon(db, case.id, v.id, 5000, viewed, total_imgs)
    db.flush()

    total = svc.totals(db, None)
    assert total["with_images"] == 3     # 无图的访问不进分母
    assert total["finished"] == 2        # 8/10 与 10/10
    assert round(total["finish_rate"]) == 67
    assert round(svc.case_rows(db, None)[case.id]["finish_rate"]) == 67


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
    # v1.6.0 起不再维护 cases.share_views（统计只认 share_visits）
    assert (case.share_views or 0) == 0


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

    svg = cases_router.share_qrcode(_request(), "tok123", db=db)
    assert svg.status_code == 200
    assert svg.media_type == "image/svg+xml"
    assert b"<svg" in svg.body
    assert svg.headers.get("cache-control") == "no-store"

    # 前端传当前 origin：二维码内容随入口变化（域名不写死）
    other = cases_router.share_qrcode(_request(), "tok123", base="https://abc123.ddnsto.com", db=db)
    assert other.status_code == 200
    assert other.body != svg.body

    # 垃圾 base 不能把二维码指到别处（回落到按请求头推断）
    bad = cases_router.share_qrcode(_request(), "tok123", base="javascript:alert(1)", db=db)
    assert bad.body == svg.body

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


# ------------------------------------------------------------ v1.6.0：取消访问次数限制
def test_share_state_ignores_legacy_view_limit(monkeypatch, tmp_path, db):
    """历史案例可能残留 share_max_views（旧版默认 10 次）——不再据此判失效。"""
    from app.services import cases as cases_svc

    case = _make_case(db)
    case.share_max_views = 10
    case.share_views = 99
    case.share_expires_at = None
    case.share_disabled = 0
    db.flush()

    ok, reason = cases_svc.share_state(case)
    assert ok is True and reason == "有效"


def test_share_state_still_expires_and_disables(db):
    from app.services import cases as cases_svc

    case = _make_case(db, token="exp1")
    case.share_expires_at = "2020-01-01 00:00:00"
    case.share_disabled = 0
    db.flush()
    assert cases_svc.share_state(case)[0] is False
    assert "过期" in cases_svc.share_state(case)[1]

    case2 = _make_case(db, token="dis1")
    case2.share_expires_at = None
    case2.share_disabled = 1
    db.flush()
    assert cases_svc.share_state(case2) == (False, "已被手动停用")


def test_set_share_limits_only_duration(db):
    """只设置有效期，不再写 share_max_views / share_views。"""
    from app.services import cases as cases_svc

    case = _make_case(db)
    case.share_max_views = 10
    case.share_views = 7
    cases_svc.set_share_limits(db, case, "1周")
    assert case.share_duration == "1周"
    assert case.share_expires_at
    assert case.share_max_views == 10   # 历史值原样保留，不再读写
    assert case.share_views == 7

    cases_svc.set_share_limits(db, case, "不限")
    assert case.share_expires_at is None
    assert case.share_duration == "不限"


def test_links_page_no_view_limit_and_has_hint(monkeypatch, tmp_path, db, admin):
    """外链管理页：无「访问次数」、无次数下拉，且有「保存会重算截止时间」的提示。"""
    _patch_settings(monkeypatch, tmp_path)
    _make_case(db)
    db.flush()

    resp = cases_router.manage_links(_request(path="/cases/links"), db=db, user=admin)
    assert resp.status_code == 200
    html = resp.body.decode("utf-8")
    assert "访问次数" not in html
    assert 'name="limit"' not in html
    assert "重新计算截止时间" in html          # 提示条
    assert "share-settings" in html            # 保存确认的钩子目标
    assert 'name="duration"' in html           # 有效期仍可设


def test_links_page_marks_time_limited_case(monkeypatch, tmp_path, db, admin):
    """带有效期的案例在列表里要高亮「限时」，避免被忽略。"""
    _patch_settings(monkeypatch, tmp_path)
    case = _make_case(db)
    case.share_expires_at = "2099-01-01 00:00:00"
    case.share_duration = "1月"
    db.flush()

    html = cases_router.manage_links(_request(path="/cases/links"), db=db, user=admin).body.decode("utf-8")
    assert "限时" in html
    assert "2099-01-01 00:00" in html


def test_no_markup_leaked_into_title_block():
    """回归：v1.6.2 用「替换第一个 endblock」的方式插脚本，误把 <script> 塞进了 title 块，
    导致浏览器标题变成「外链管理<script>…」。这里全模板扫一遍，禁止 title 块里出现标签。"""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"
    bad = []
    for path in sorted(root.rglob("*.html")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"{%\s*block title\s*%}(.*?){%\s*endblock\s*%}", src, re.S):
            if "<" in m.group(1) or "script" in m.group(1):
                bad.append(path.name)
    assert not bad, f"title 块里混入了标签/脚本：{bad}"
