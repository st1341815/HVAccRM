"""案例：对外展示的完工案例列表、筛选、匿名分享页与外链管理。

只展示可公开字段（房号 / 工序 / 施工时间 / 师傅 / 产品类型 / 用户上传的展示图），
不含任何客户隐私或商业信息。
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..db import commit_retry, get_db
from ..models import PRODUCT_OPTIONS, Case, CaseImage, Task, now_iso, today_str
from ..permissions import has_perm, require
from ..services import cases as cases_svc
from ..services import share_stats as stats_svc
from ..templating import cookie_secure, redirect, render
from ..utils import client_ip, get_or_404, parse_date, parse_int, return_path

router = APIRouter(prefix="/cases", tags=["cases"])

CASE_STAGE_OPTIONS = ["前期施工", "后期施工"]


def _installer_options(db: Session) -> list[tuple[int | None, str]]:
    """责任人筛选下拉：取自案例数据本身（快照，用户被删仍可筛）。"""
    out: list[tuple[int | None, str]] = []
    seen: set[int | None] = set()
    for c in db.scalars(select(Case).order_by(Case.id)).all():
        if c.installer_id in seen:
            continue
        seen.add(c.installer_id)
        out.append((c.installer_id, c.installer_name or f"#{c.installer_id}"))
    return out


@router.get("")
def list_cases(
    request: Request,
    q: str = "",
    stage: str = "",
    product_type: str = "",
    installer: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    stmt = select(Case).order_by(Case.work_date.desc(), Case.id.desc())
    if q and q.strip():
        stmt = stmt.where(Case.room_label.like(f"%{q.strip()}%"))
    if stage:
        stmt = stmt.where(Case.stage == stage)
    if product_type:
        stmt = stmt.where(Case.product_type == product_type)
    if installer:
        stmt = stmt.where(Case.installer_id == parse_int(installer))
    if date_from:
        stmt = stmt.where(Case.work_date >= date_from)
    if date_to:
        stmt = stmt.where(Case.work_date <= date_to)
    cases = list(db.scalars(stmt.limit(1000)).all())

    return render(
        request,
        "cases/list.html",
        cases=cases,
        case_images=cases_svc.images_by_case(db, [c.id for c in cases]),
        share_states={c.id: cases_svc.share_state(c) for c in cases},
        installer_options=_installer_options(db),
        stage_options=CASE_STAGE_OPTIONS,
        product_options=PRODUCT_OPTIONS,
        filters={
            "q": q,
            "stage": stage,
            "product_type": product_type,
            "installer": installer,
            "date_from": date_from,
            "date_to": date_to,
        },
        base_url=stats_svc.public_base(request),
        can_edit=has_perm(user, "case:edit"),
        now=now_iso(),
    )


@router.get("/links")
def manage_links(
    request: Request,
    q: str = "",
    stage: str = "",
    product_type: str = "",
    installer: str = "",
    date_from: str = "",
    date_to: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    """外链管理：查询所有分享外链的状态，支持筛选 + 手动失效/恢复与限制设置。"""
    stmt = select(Case).order_by(Case.work_date.desc(), Case.id.desc())
    if q and q.strip():
        stmt = stmt.where(Case.room_label.like(f"%{q.strip()}%"))
    if stage:
        stmt = stmt.where(Case.stage == stage)
    if product_type:
        stmt = stmt.where(Case.product_type == product_type)
    if installer:
        stmt = stmt.where(Case.installer_id == parse_int(installer))
    if date_from:
        stmt = stmt.where(Case.work_date >= date_from)
    if date_to:
        stmt = stmt.where(Case.work_date <= date_to)
    cases = list(db.scalars(stmt.limit(1000)).all())

    rows = []
    for c in cases:
        valid, reason = cases_svc.share_state(c)
        rows.append({"case": c, "valid": valid, "reason": reason})
    # 外链状态是计算属性（时间 / 次数判定），只能在 Python 层过滤
    if status == "valid":
        rows = [r for r in rows if r["valid"]]
    elif status == "invalid":
        rows = [r for r in rows if not r["valid"]]

    return render(
        request,
        "cases/links.html",
        rows=rows,
        base_url=stats_svc.public_base(request),
        can_edit=has_perm(user, "case:edit"),
        durations=list(cases_svc.SHARE_DURATIONS.keys()) + [cases_svc.SHARE_UNLIMITED],
        installer_options=_installer_options(db),
        stage_options=CASE_STAGE_OPTIONS,
        product_options=PRODUCT_OPTIONS,
        filters={
            "q": q,
            "stage": stage,
            "product_type": product_type,
            "installer": installer,
            "date_from": date_from,
            "date_to": date_to,
            "status": status,
        },
        now=now_iso(),
    )


# ------------------------------------------------------------ 展示图上传/删除
@router.post("/{case_id}/images")
async def upload_images(
    request: Request,
    case_id: int,
    files: list[UploadFile] = File(default=[]),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    saved, errors = 0, []
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        img, msg = cases_svc.save_case_image(db, case, raw, f.filename)
        if img is not None:
            saved += 1
            log_action(db, user, "upload", "case_images", img.id, new={"case_id": case.id, "file": f.filename}, ip=client_ip(request))
        else:
            errors.append(f"{f.filename}: {msg}")
    commit_retry(db)
    message = f"上传完成：新增 {saved} 张"
    if errors:
        message += "；失败 " + "；".join(errors)
    return redirect(return_path(back, request.headers.get("referer"), "/cases"), message, "err" if errors else "ok")


@router.post("/{case_id}/images/{image_id}/delete")
def delete_image(
    request: Request,
    case_id: int,
    image_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    image = db.scalars(select(CaseImage).where(CaseImage.id == image_id, CaseImage.case_id == case.id)).first()
    if image is None:
        return render(request, "404.html", status_code=404, detail="图片不存在")
    msg = cases_svc.delete_case_image(db, image)
    log_action(db, user, "delete", "case_images", image_id, old={"case_id": case.id, "path": image.path}, ip=client_ip(request))
    commit_retry(db)
    return redirect(return_path(back, request.headers.get("referer"), "/cases"), msg)


@router.post("/{case_id}/sync-photos")
def sync_photos(
    request: Request,
    case_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    """重新同步：把施工工序后来又补传的照片追加进案例，并把施工时间/工序/责任人/产品类型等字段刷新为最新值。"""
    case = get_or_404(db, Case, case_id, "案例")
    task = db.get(Task, case.task_id) if case.task_id else None
    if task is None:
        return redirect(return_path(back, request.headers.get("referer"), "/cases"), "该案例没有关联的施工工序", "err")
    added = cases_svc.import_task_photos(db, case, task)
    changed = cases_svc.sync_case_fields(db, case, task)
    log_action(db, user, "case_sync_photos", "cases", case.id, new={"added": added, "fields": changed}, ip=client_ip(request))
    commit_retry(db)
    parts = []
    if added:
        parts.append(f"新增 {added} 张照片")
    if changed:
        parts.append("同步字段：" + "、".join(changed))
    message = "重新同步完成：" + "；".join(parts) if parts else "没有新的照片或字段需要同步"
    return redirect(return_path(back, request.headers.get("referer"), "/cases"), message)


# ------------------------------------------------------------ 分享限制与失效
@router.post("/{case_id}/share/settings")
def set_share_settings(
    request: Request,
    case_id: int,
    duration: str = Form("不限"),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    cases_svc.set_share_limits(db, case, duration)
    log_action(
        db, user, "update", "cases", case.id,
        new={"share_expires_at": case.share_expires_at, "duration": duration},
        ip=client_ip(request),
    )
    commit_retry(db)
    return redirect(return_path(back, request.headers.get("referer"), "/cases/links"), "分享限制已更新")


@router.post("/{case_id}/share/disable")
def disable_share(
    request: Request,
    case_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    case.share_disabled = 1
    log_action(db, user, "share_disable", "cases", case.id, ip=client_ip(request))
    commit_retry(db)
    return redirect(return_path(back, request.headers.get("referer"), "/cases/links"), "分享链接已失效")


@router.post("/{case_id}/share/enable")
def enable_share(
    request: Request,
    case_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    case.share_disabled = 0
    log_action(db, user, "share_enable", "cases", case.id, ip=client_ip(request))
    commit_retry(db)
    return redirect(return_path(back, request.headers.get("referer"), "/cases/links"), "分享链接已恢复")


# ------------------------------------------------------------ 图片读取
@router.get("/images/{image_id}/file")
def case_image_file(
    request: Request,
    image_id: int,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    image = get_or_404(db, CaseImage, image_id, "图片")
    path = cases_svc.case_image_file(image.path)
    if not path.exists():
        return JSONResponse({"detail": "文件已丢失"}, status_code=404)
    return FileResponse(path)


@router.get("/images/{image_id}/thumb")
def case_image_thumb(
    request: Request,
    image_id: int,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    image = get_or_404(db, CaseImage, image_id, "图片")
    rel = image.thumb_path or image.path
    path = cases_svc.case_image_file(rel)
    if not path.exists():
        return JSONResponse({"detail": "缩略图缺失"}, status_code=404)
    return FileResponse(path, media_type="image/webp" if str(path).endswith(".webp") else "image/jpeg")


# ------------------------------------------------------------ 匿名分享
def _og_title(case: Case) -> str:
    site = case.site_label or "施工案例"
    kind = f"{case.product_type}施工案例" if case.product_type else f"{case.stage}案例"
    return f"{kind} · {site}"


def _og_desc(case: Case) -> str:
    bits = [b for b in (case.work_date, case.stage, case.product_type) if b]
    head = " · ".join(bits)
    return (head + " 现场施工实拍，仅作效果展示参考。") if head else "现场施工实拍，仅作效果展示参考。"


@router.get("/share/{token}")
def share_case(request: Request, token: str, db: Session = Depends(get_db)):
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return render(request, "404.html", status_code=404, detail="分享链接无效或已失效")
    valid, reason = cases_svc.share_state(case)
    if not valid:
        return render(request, "cases/share_expired.html", reason=reason)

    images = list(
        db.scalars(
            select(CaseImage)
            .where(CaseImage.case_id == case.id)
            .order_by(CaseImage.sort_order, CaseImage.id)
        ).all()
    )
    # 留一条匿名访问明细（统计的唯一数据源；v1.6.0 起不再维护 cases.share_views）
    visit, need_cookie = stats_svc.record_visit(db, case, request, len(images))
    commit_retry(db)

    cover = stats_svc.ensure_cover(case, images[0] if images else None)
    base = stats_svc.public_base(request)
    response = render(
        request,
        "cases/share.html",
        case=case,
        images=images,
        visit_id=visit.id,
        share_url=stats_svc.share_url(request, token),
        qr_url=stats_svc.share_url(request, token, "qr"),
        og_title=_og_title(case),
        og_desc=_og_desc(case),
        og_image=(f"{base}cases/share/{token}/cover.jpg?v={len(images)}" if cover else None),
    )
    if need_cookie:
        response.set_cookie(
            stats_svc.VISITOR_COOKIE,
            visit.visitor_id or "",
            max_age=stats_svc.VISITOR_MAX_AGE,
            path=stats_svc.VISITOR_COOKIE_PATH,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(),
        )
    return response


@router.post("/share/{token}/ping")
async def share_ping(request: Request, token: str, db: Session = Depends(get_db)):
    """分享页埋点：回填停留时长与图片曝光。始终 204，不暴露链接是否有效。"""
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return Response(status_code=204)
    try:
        payload = json.loads((await request.body()) or b"{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        visit_id = int(payload.get("visit") or 0)
    except (TypeError, ValueError):
        visit_id = 0
    if visit_id:
        stats_svc.apply_beacon(
            db,
            case.id,
            visit_id,
            payload.get("dwell"),
            payload.get("imgs"),
            payload.get("total"),
        )
        commit_retry(db)
    return Response(status_code=204)


@router.get("/share/{token}/cover.jpg")
def share_cover(token: str, db: Session = Depends(get_db)):
    """分享封面图（1200×630 JPEG，按第一张展示图生成并缓存）——供社交平台抓取。"""
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return JSONResponse({"detail": "分享链接无效"}, status_code=404)
    valid, _reason = cases_svc.share_state(case)
    if not valid:
        return JSONResponse({"detail": "分享已失效"}, status_code=410)
    first = db.scalars(
        select(CaseImage)
        .where(CaseImage.case_id == case.id)
        .order_by(CaseImage.sort_order, CaseImage.id)
        .limit(1)
    ).first()
    path = stats_svc.ensure_cover(case, first)
    if path is None or not path.exists():
        return JSONResponse({"detail": "无封面图"}, status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/share/{token}/qrcode.svg")
def share_qrcode(request: Request, token: str, base: str = "", db: Session = Depends(get_db)):
    """分享二维码（SVG）：内容为带 ?src=qr 的分享页地址。

    `base` 由前端传当前 `location.origin`——内网穿透域名不固定、公网域名可能被微信拦截，
    写死任何域名都会变成死链；不传/不合法则回落到按请求头推断。
    """
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return JSONResponse({"detail": "分享链接无效"}, status_code=404)
    valid, _reason = cases_svc.share_state(case)
    if not valid:
        return JSONResponse({"detail": "分享已失效"}, status_code=410)
    url = stats_svc.share_url_from(stats_svc.safe_base(request, base), token, "qr")
    svg = stats_svc.qrcode_svg(url)
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/share/{token}/image/{image_id}")
def share_case_image(request: Request, token: str, image_id: int, db: Session = Depends(get_db)):
    return _share_image_response(db, token, image_id)


@router.get("/share/{token}/image/{image_id}/file")
def share_case_image_file(request: Request, token: str, image_id: int, db: Session = Depends(get_db)):
    return _share_image_response(db, token, image_id)


@router.get("/share/{token}/image/{image_id}/thumb")
def share_case_image_thumb(request: Request, token: str, image_id: int, db: Session = Depends(get_db)):
    return _share_image_response(db, token, image_id, thumb=True)


def _share_image_response(db: Session, token: str, image_id: int, thumb: bool = False):
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None or case.share_disabled or (case.share_expires_at and now_iso() >= case.share_expires_at):
        return JSONResponse({"detail": "分享已失效"}, status_code=410)
    image = db.scalars(select(CaseImage).where(CaseImage.id == image_id, CaseImage.case_id == case.id)).first()
    if image is None:
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    rel = (image.thumb_path or image.path) if thumb else image.path
    path = cases_svc.case_image_file(rel)
    if not path.exists():
        return JSONResponse({"detail": "文件已丢失"}, status_code=404)
    if thumb and str(path).endswith(".webp"):
        return FileResponse(path, media_type="image/webp")
    return FileResponse(path)


# ------------------------------------------------------------ 分享统计
@router.get("/stats")
def share_stats_page(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    q: str = "",
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    """分享统计：谁在什么时候打开了哪条案例、看了多久、看了几张图。

    数据只来自匿名访问明细（share_visits），不含任何客户信息。
    """
    today = today_str()
    d_from = parse_date(date_from) or (date.today() - timedelta(days=29)).isoformat()
    d_to = parse_date(date_to) or today
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    # 关键词先圈定案例范围：None = 不筛选；[] = 无匹配（必须返回空，不能退化成"全部"）
    case_ids: list[int] | None = None
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        case_ids = list(db.scalars(select(Case.id).where(Case.room_label.like(like))).all())

    agg = stats_svc.case_rows(db, case_ids, d_from, d_to)
    cases_map: dict[int, Case] = {}
    if agg:
        cases_map = {
            c.id: c
            for c in db.scalars(select(Case).where(Case.id.in_(list(agg.keys())))).all()
        }
    rows = []
    for cid, data in agg.items():
        case = cases_map.get(cid)
        if case is None:
            continue
        rows.append({"case": case, "wechat_pct": (data["wechat"] / data["opens"] * 100) if data["opens"] else 0.0, **data})
    rows.sort(key=lambda r: (-r["opens"], r["case"].work_date or "", r["case"].id))

    days = [(date.today() - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    trend = stats_svc.daily_trend(db, days, case_ids)
    trend_max = max([n for _, n in trend] + [1])

    total = stats_svc.totals(db, case_ids, d_from, d_to)
    total["wechat_pct"] = (total["wechat"] / total["opens"] * 100) if total["opens"] else 0.0
    total["today"] = stats_svc.today_opens(db, today, case_ids)

    return render(
        request,
        "cases/stats.html",
        rows=rows,
        trend=[{"day": d, "opens": n, "pct": round(n / trend_max * 100)} for d, n in trend],
        total=total,
        recent=stats_svc.recent_visits(db, case_ids, 50),
        filters={"date_from": d_from, "date_to": d_to, "q": kw},
        truncated=len(rows) >= 200,
    )
