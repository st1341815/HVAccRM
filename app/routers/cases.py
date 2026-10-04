"""案例：对外展示的完工案例列表、筛选、匿名分享页与外链管理。

只展示可公开字段（房号 / 工序 / 施工时间 / 师傅 / 产品类型 / 用户上传的展示图），
不含任何客户隐私或商业信息。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..db import commit_retry, get_db
from ..models import PRODUCT_OPTIONS, Case, CaseImage, Task, now_iso
from ..permissions import has_perm, require
from ..services import cases as cases_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_int, return_path

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
        base_url=str(request.base_url),
        can_edit=has_perm(user, "case:edit"),
        now=now_iso(),
    )


@router.get("/links")
def manage_links(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require("case:view")),
):
    """外链管理：查询所有分享外链的状态，支持手动失效/恢复与限制设置。"""
    cases = list(db.scalars(select(Case).order_by(Case.work_date.desc(), Case.id.desc())).all())
    rows = []
    for c in cases:
        valid, reason = cases_svc.share_state(c)
        rows.append({"case": c, "valid": valid, "reason": reason})
    return render(
        request,
        "cases/links.html",
        rows=rows,
        base_url=str(request.base_url),
        can_edit=has_perm(user, "case:edit"),
        durations=list(cases_svc.SHARE_DURATIONS.keys()) + [cases_svc.SHARE_UNLIMITED],
        view_limits=list(cases_svc.SHARE_VIEW_LIMITS.keys()) + [cases_svc.SHARE_UNLIMITED],
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
    """重新同步：把施工工序后来又补传的照片追加进案例（幂等去重，不删既有图）。"""
    case = get_or_404(db, Case, case_id, "案例")
    task = db.get(Task, case.task_id) if case.task_id else None
    if task is None:
        return redirect(return_path(back, request.headers.get("referer"), "/cases"), "该案例没有关联的施工工序", "err")
    added = cases_svc.import_task_photos(db, case, task)
    log_action(db, user, "case_sync_photos", "cases", case.id, new={"added": added}, ip=client_ip(request))
    commit_retry(db)
    message = f"重新同步完成：新增 {added} 张" if added else "没有新的施工照片需要同步"
    return redirect(return_path(back, request.headers.get("referer"), "/cases"), message)


# ------------------------------------------------------------ 分享限制与失效
@router.post("/{case_id}/share/settings")
def set_share_settings(
    request: Request,
    case_id: int,
    duration: str = Form("不限"),
    limit: str = Form("不限"),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require("case:edit")),
):
    case = get_or_404(db, Case, case_id, "案例")
    cases_svc.set_share_limits(db, case, duration, limit)
    log_action(
        db, user, "update", "cases", case.id,
        new={"share_expires_at": case.share_expires_at, "share_max_views": case.share_max_views, "duration": duration, "limit": limit},
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
    case.share_views = 0
    log_action(db, user, "share_enable", "cases", case.id, ip=client_ip(request))
    commit_retry(db)
    return redirect(return_path(back, request.headers.get("referer"), "/cases/links"), "分享链接已恢复，访问次数已清零")


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
@router.get("/share/{token}")
def share_case(request: Request, token: str, db: Session = Depends(get_db)):
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return render(request, "404.html", status_code=404, detail="分享链接无效或已失效")
    valid, reason = cases_svc.share_state(case)
    if not valid:
        return render(request, "cases/share_expired.html", reason=reason)
    # 有效访问：计数 +1
    case.share_views = (case.share_views or 0) + 1
    commit_retry(db)
    return render(
        request,
        "cases/share.html",
        case=case,
        images=list(db.scalars(select(CaseImage).where(CaseImage.case_id == case.id).order_by(CaseImage.sort_order, CaseImage.id)).all()),
    )


@router.get("/share/{token}/image/{image_id}")
def share_case_image(request: Request, token: str, image_id: int, db: Session = Depends(get_db)):
    return _share_image_response(db, token, image_id)


@router.get("/share/{token}/image/{image_id}/file")
def share_case_image_file(request: Request, token: str, image_id: int, db: Session = Depends(get_db)):
    return _share_image_response(db, token, image_id)


def _share_image_response(db: Session, token: str, image_id: int):
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None or case.share_disabled or (case.share_expires_at and now_iso() >= case.share_expires_at):
        return JSONResponse({"detail": "分享已失效"}, status_code=410)
    image = db.scalars(select(CaseImage).where(CaseImage.id == image_id, CaseImage.case_id == case.id)).first()
    if image is None:
        return JSONResponse({"detail": "图片不存在"}, status_code=404)
    path = cases_svc.case_image_file(image.path)
    if not path.exists():
        return JSONResponse({"detail": "文件已丢失"}, status_code=404)
    return FileResponse(path)
