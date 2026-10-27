"""分享访问统计：匿名访问记录、分享封面图、二维码与聚合查询。

设计要点：
- 只记录「谁在什么时候从哪打开、停留多久、看了几张图」，**不含任何客户隐私**；
- **不存原始 IP**，只存 `sha256(ip + 应用密钥)` 前 16 位（能判重、不可反查）；
- 前端 beacon 可能重复上报（`pagehide` + `visibilitychange` 各一次），
  写入一律「取最大值」，天然幂等，不会把停留时长翻倍。
"""
from __future__ import annotations

import hashlib
import io
import secrets
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Case, CaseImage, ShareVisit, now_iso
from ..utils import client_ip
from .cases import case_image_file, case_image_root

# 匿名访客 cookie（只作用于分享页路径，不污染登录态）
VISITOR_COOKIE = "hv_v"
VISITOR_COOKIE_PATH = "/cases/share"
VISITOR_MAX_AGE = 365 * 24 * 3600

# 允许的来源标记（分享链接可带 ?src=qr / ?src=link；其他一律按 direct）
SRC_ALLOWED = {"qr", "link", "direct"}

# 停留时长上限（6 小时，防异常上报把平均值带歪）
DWELL_MAX_MS = 6 * 3600 * 1000

COVER_SIZE = (1200, 630)  # 微信/社交平台通用的 1.91:1 分享图


# ------------------------------------------------------------ 请求解析
def parse_ua(ua: str) -> dict[str, object]:
    """从 User-Agent 粗判「是否微信内打开 / 设备类型 / 系统」。

    只做统计展示用，不追求精确；识别不出来就归 desktop + 空系统名。
    """
    u = (ua or "").lower()
    if not u:
        return {"is_wechat": 0, "device": "desktop", "os_name": ""}
    is_wechat = 1 if "micromessenger" in u or "wechat" in u else 0
    if "ipad" in u or ("tablet" in u and "mobile" not in u):
        device = "tablet"
    elif any(k in u for k in ("iphone", "ipod", "android", "mobile", "phone", "harmonyos")):
        device = "mobile"
    else:
        device = "desktop"
    os_name = ""
    for key, label in (
        ("iphone", "iOS"),
        ("ipad", "iPadOS"),
        ("android", "Android"),
        ("windows", "Windows"),
        ("mac os x", "macOS"),
        ("linux", "Linux"),
    ):
        if key in u:
            os_name = label
            break
    return {"is_wechat": is_wechat, "device": device, "os_name": os_name}


def visitor_id(request) -> tuple[str, bool]:
    """取（或新生成）匿名访客标识。返回 (id, 是否新建)。"""
    cur = (request.cookies.get(VISITOR_COOKIE) or "").strip()
    if 8 <= len(cur) <= 24:
        return cur, False
    return secrets.token_urlsafe(12)[:16], True


def hash_ip(ip: str) -> str:
    """IP 单向摘要（加应用密钥当盐），不可反查原始地址。"""
    secret = get_settings().app_secret_key or "-"
    return hashlib.sha256(f"{ip}|{secret}".encode("utf-8")).hexdigest()[:16]


def public_base(request) -> str:
    """对外可访问的站点根地址（带尾斜杠）。

    优先级：app_settings/env 的 PUBLIC_BASE_URL → 反代传来的 X-Forwarded-Proto → 请求自身。
    反代（Lucky）终止 TLS 后转发的是 http，uvicorn 默认不认 X-Forwarded-Proto，
    故这里显式兜一层，避免把 https 站点生成成 http 分享链接。
    """
    base = (get_settings().public_base_url or "").strip().rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
        fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if fwd in ("http", "https") and base.startswith("http://") and fwd == "https":
            base = "https://" + base[len("http://"):]
    return base + "/"


def share_url(request, token: str, src: str | None = None) -> str:
    url = public_base(request) + "cases/share/" + token
    return f"{url}?src={src}" if src else url


# ------------------------------------------------------------ 访问记录
def record_visit(db: Session, case: Case, request, image_total: int) -> tuple[ShareVisit, bool]:
    """记一次分享页打开。返回 (访问记录, 是否需要下发访客 cookie)。"""
    ua = (request.headers.get("user-agent") or "")[:300]
    info = parse_ua(ua)
    src = (request.query_params.get("src") or "").strip().lower()
    if src not in SRC_ALLOWED:
        src = "direct"
    ref = request.headers.get("referer") or ""
    ref_host = (urlparse(ref).netloc if ref else "")[:120]
    vid, is_new = visitor_id(request)

    visit = ShareVisit(
        case_id=case.id,
        visitor_id=vid,
        visited_at=now_iso(),
        src=src,
        referer_host=ref_host or None,
        ip_hash=hash_ip(client_ip(request)),
        is_wechat=int(info["is_wechat"]),
        device=str(info["device"]),
        os_name=str(info["os_name"]) or None,
        ua=ua or None,
        dwell_ms=0,
        viewed_images=0,
        image_total=max(0, int(image_total or 0)),
    )
    db.add(visit)
    db.flush()
    return visit, is_new


def apply_beacon(
    db: Session,
    case_id: int,
    visit_id: int,
    dwell_ms: int | None,
    viewed_images: int | None,
    image_total: int | None,
) -> ShareVisit | None:
    """回填停留时长与图片曝光（重复上报取最大值，幂等）。"""
    visit = db.get(ShareVisit, visit_id)
    if visit is None or visit.case_id != case_id:
        return None
    try:
        dwell = int(dwell_ms or 0)
    except (TypeError, ValueError):
        dwell = 0
    dwell = max(0, min(dwell, DWELL_MAX_MS))
    if dwell > (visit.dwell_ms or 0):
        visit.dwell_ms = dwell

    total = max(0, int(image_total or 0))
    if total > (visit.image_total or 0):
        visit.image_total = total

    try:
        viewed = int(viewed_images or 0)
    except (TypeError, ValueError):
        viewed = 0
    viewed = max(0, viewed)
    if total:
        viewed = min(viewed, total)
    if viewed > (visit.viewed_images or 0):
        visit.viewed_images = viewed
    return visit


# ------------------------------------------------------------ 分享封面图
def cover_path(case_id: int) -> Path:
    return case_image_root() / str(case_id) / "share-cover.jpg"


def ensure_cover(case: Case, image: CaseImage | None) -> Path | None:
    """按第一张展示图生成 1200×630 分享封面（JPEG，剥 EXIF）。

    已生成且比源图新则直接复用；源图删掉/缺失时返回 None（页面降级为无图卡片）。
    """
    if image is None or not image.path:
        return None
    try:
        src = case_image_file(image.path)
    except ValueError:
        return None
    if not src.exists():
        return None
    dest = cover_path(case.id)
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    try:
        with Image.open(src) as im:
            im.load()
            flat = ImageOps.fit(im.convert("RGB"), COVER_SIZE, Image.LANCZOS, centering=(0.5, 0.4))
            dest.parent.mkdir(parents=True, exist_ok=True)
            flat.save(dest, "JPEG", quality=86, optimize=True, progressive=True)
    except Exception:  # pragma: no cover - 图片损坏时不影响分享页
        return None
    return dest


# ------------------------------------------------------------ 二维码
def qrcode_svg(url: str, scale: int = 8) -> bytes:
    """生成 SVG 二维码（segno，纯 Python，无字体依赖）。"""
    import segno

    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=2, dark="#3a3f47", light="#ffffff", xmldecl=True)
    return buf.getvalue()


# ------------------------------------------------------------ 聚合查询
def _time_filter(stmt, date_from: str = "", date_to: str = ""):
    if date_from:
        stmt = stmt.where(ShareVisit.visited_at >= date_from)
    if date_to:
        stmt = stmt.where(ShareVisit.visited_at <= f"{date_to} 23:59:59")
    return stmt


def _case_filter(stmt, case_ids: list[int] | None):
    """案例范围过滤。

    ⚠️ 语义区分：`None` = 不筛选（全部）；`[]` = 筛出空集（必须返回 0 行）。
    绝不写成 `case_ids or None`——空列表会被误判成"无筛选"，导致越权看到全部数据。
    """
    if case_ids is None:
        return stmt
    if not case_ids:
        return stmt.where(ShareVisit.case_id.in_([-1]))
    return stmt.where(ShareVisit.case_id.in_(case_ids))



def case_rows(db: Session, case_ids: list[int] | None, date_from: str = "", date_to: str = "") -> dict[int, dict]:
    """按案例聚合：打开数 / 独立访客 / 微信占比 / 平均停留 / 平均看图 / 最近打开。"""
    stmt = select(
        ShareVisit.case_id,
        func.count(ShareVisit.id),
        func.count(distinct(ShareVisit.visitor_id)),
        func.sum(ShareVisit.is_wechat),
        func.avg(func.nullif(ShareVisit.dwell_ms, 0)),
        func.avg(ShareVisit.viewed_images),
        func.max(ShareVisit.visited_at),
    ).group_by(ShareVisit.case_id)
    stmt = _case_filter(_time_filter(stmt, date_from, date_to), case_ids)
    out: dict[int, dict] = {}
    for cid, opens, uv, wechat, avg_dwell, avg_imgs, last_at in db.execute(stmt).all():
        out[cid] = {
            "opens": int(opens or 0),
            "uv": int(uv or 0),
            "wechat": int(wechat or 0),
            "avg_dwell": float(avg_dwell or 0),
            "avg_imgs": float(avg_imgs or 0),
            "last_at": last_at,
        }
    return out


def totals(db: Session, case_ids: list[int] | None, date_from: str = "", date_to: str = "") -> dict:
    """总览指标。"""
    stmt = select(
        func.count(ShareVisit.id),
        func.count(distinct(ShareVisit.visitor_id)),
        func.sum(ShareVisit.is_wechat),
        func.avg(func.nullif(ShareVisit.dwell_ms, 0)),
        func.avg(ShareVisit.viewed_images),
        func.count(distinct(ShareVisit.case_id)),
        # 「有停留的访问」：微信在生成链接预览时也会抓一次页面（停留 0、看图 0），
        # 用这个口径把「真人真的看了」与「只是被抓了预览」区分开。
        func.sum(case((ShareVisit.dwell_ms > 0, 1), else_=0)),
    )
    stmt = _case_filter(_time_filter(stmt, date_from, date_to), case_ids)
    opens, uv, wechat, avg_dwell, avg_imgs, cases_hit, effective = db.execute(stmt).one()
    return {
        "opens": int(opens or 0),
        "uv": int(uv or 0),
        "wechat": int(wechat or 0),
        "avg_dwell": float(avg_dwell or 0),
        "avg_imgs": float(avg_imgs or 0),
        "cases_hit": int(cases_hit or 0),
        "effective": int(effective or 0),
    }


def today_opens(db: Session, day: str, case_ids: list[int] | None) -> int:
    stmt = select(func.count(ShareVisit.id)).where(ShareVisit.visited_at >= day)
    stmt = _case_filter(stmt, case_ids)
    return int(db.execute(stmt).scalar_one() or 0)


def daily_trend(db: Session, days: list[str], case_ids: list[int] | None) -> list[tuple[str, int]]:
    """按天统计打开数；days 为日期列表（含无数据的 0 值补全）。"""
    if not days:
        return []
    stmt = (
        select(func.substr(ShareVisit.visited_at, 1, 10), func.count(ShareVisit.id))
        .where(ShareVisit.visited_at >= days[0])
        .where(ShareVisit.visited_at <= f"{days[-1]} 23:59:59")
        .group_by(func.substr(ShareVisit.visited_at, 1, 10))
    )
    stmt = _case_filter(stmt, case_ids)
    got = {d: int(n or 0) for d, n in db.execute(stmt).all()}
    return [(d, got.get(d, 0)) for d in days]


def recent_visits(db: Session, case_ids: list[int] | None, limit: int = 50) -> list[dict]:
    """最近访问明细（带案例信息，用于排查「谁什么时候看了」）。"""
    stmt = (
        select(ShareVisit, Case)
        .join(Case, Case.id == ShareVisit.case_id)
        .order_by(ShareVisit.visited_at.desc(), ShareVisit.id.desc())
        .limit(limit)
    )
    stmt = _case_filter(stmt, case_ids)
    rows = []
    for visit, case in db.execute(stmt).all():
        rows.append({"visit": visit, "case": case})
    return rows
