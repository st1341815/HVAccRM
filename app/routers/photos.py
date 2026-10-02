"""照片：多张上传、缩略图、相册页、文件读取（带数据范围校验）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..auth import current_user_or_redirect
from ..db import commit_retry, get_db
from ..models import Customer, Photo, Task, User, today_str
from ..permissions import has_perm, require, visible_customer_ids
from ..services import photos as photo_svc
from ..templating import redirect, render
from ..utils import client_ip, get_or_404, parse_int, return_path, return_path

router = APIRouter(tags=["photos"])

KINDS = ["量尺", "复尺", "现场", "到货", "施工", "安装", "验收", "完工", "售后", "纸质合同", "收款截图", "成本凭证", "其他"]


def _may_upload(db: Session, user: User, customer: Customer) -> bool:
    """安装工白名单：仅被指派任务的客户可上传照片。"""
    if not has_perm(user, "photo:upload"):
        return False
    if user.role in {"admin", "sales", "finance", "designer"}:
        allowed = visible_customer_ids(db, user)
        return allowed is None or customer.id in allowed
    ids = set(db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all())
    return customer.id in ids


def _may_view(db: Session, user: User, customer: Customer) -> bool:
    allowed = visible_customer_ids(db, user)
    if allowed is None:
        return True
    if customer.id in allowed:
        return True
    ids = set(db.scalars(select(Task.customer_id).where(Task.assignee_id == user.id)).all())
    return customer.id in ids


@router.get("/photos/album/{customer_id}")
def album(
    request: Request,
    customer_id: int,
    kind: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not _may_view(db, user, customer):
        return render(request, "403.html", status_code=403)
    stmt = select(Photo).where(Photo.customer_id == customer_id)
    if kind:
        stmt = stmt.where(Photo.kind == kind)
    photos = list(db.scalars(stmt.order_by(Photo.created_at.desc())).all())
    tasks = sorted(customer.tasks, key=lambda t: t.sort_order)
    users = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return render(
        request,
        "photos/album.html",
        customer=customer,
        photos=photos,
        tasks=tasks,
        kinds=KINDS,
        kind=kind,
        users=users,
        may_upload=_may_upload(db, user, customer),
        can_delete=has_perm(user, "photo:delete"),
    )


@router.post("/api/customers/{customer_id}/photos")
async def upload_photos(
    request: Request,
    customer_id: int,
    files: list[UploadFile] = File(default=[]),
    kind: str = Form("其他"),
    task_id: str = Form(""),
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    customer = get_or_404(db, Customer, customer_id, "客户")
    if not _may_upload(db, user, customer):
        return render(request, "403.html", status_code=403)
    saved, skipped, errors = 0, 0, []
    tid = parse_int(task_id)
    # 工序照片：只有「已开工」（doing / done）的节点才允许上传；未开工直接拒绝
    if tid:
        task = db.get(Task, tid)
        if not task or task.customer_id != customer.id:
            return render(request, "error.html", status_code=400, detail="工序不存在或不属于该客户")
        if task.status not in ("doing", "done"):
            target = return_path(back, request.headers.get("referer"), f"/photos/album/{customer.id}")
            return redirect(target, "该工序尚未开工，请先点击「开工」再上传照片", "err")
    for f in files:
        if not f or not f.filename:
            continue
        raw = await f.read()
        photo, msg = photo_svc.save_photo(
            db,
            customer_id=customer.id,
            kind=kind,
            raw=raw,
            orig_name=f.filename,
            uploaded_by=user.id,
            task_id=tid,
        )
        if photo is None:
            errors.append(f"{f.filename}: {msg}")
        else:
            if "重复" in msg:
                skipped += 1
            else:
                saved += 1
            log_action(db, user, "upload", "photos", None, new={"file": f.filename, "kind": kind, "msg": msg}, ip=client_ip(request))
    commit_retry(db)
    message = f"上传完成：新增 {saved} 张，去重 {skipped} 张"
    if errors:
        message += "；失败 " + "；".join(errors)
    # 从施工看板/客户工序页上传时跳回原页面（只接受站内相对路径）
    target = return_path(back, request.headers.get("referer"), f"/photos/album/{customer.id}")
    return redirect(target, message, "err" if errors else "ok")


@router.post("/photos/{photo_id}/delete")
def delete_photo(
    request: Request,
    photo_id: int,
    back: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("photo:delete")),
):
    photo = get_or_404(db, Photo, photo_id, "照片")
    cid = photo.customer_id
    msg = photo_svc.delete_photo(db, photo)
    log_action(db, user, "delete", "photos", photo_id, old={"path": photo.path}, ip=client_ip(request))
    commit_retry(db)
    # 允许调用方指定返回页（合同详情页/施工看板），仅接受站内相对路径
    target = return_path(back, request.headers.get("referer"), f"/photos/album/{cid}")
    return redirect(target, msg)


@router.get("/photos/{photo_id}")
def photo_view(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    """照片查看页：适配屏幕的放大显示 + 返回入口 + 同分类上一张/下一张。

    点缩略图仍优先由灯箱拦截（体验更顺）；中键/新标签打开、或未启用 JS 时
    落到这个页面，不会再出现「裸图片、无返回」的情况。
    """
    photo = get_or_404(db, Photo, photo_id, "照片")
    customer = db.get(Customer, photo.customer_id)
    if not customer or not _may_view(db, user, customer):
        return render(request, "403.html", status_code=403)
    siblings = list(
        db.scalars(
            select(Photo)
            .where(Photo.customer_id == photo.customer_id)
            .where(Photo.kind == photo.kind)
            .order_by(Photo.id)
        ).all()
    )
    ids = [p.id for p in siblings]
    idx = ids.index(photo.id) if photo.id in ids else 0
    uploader = db.get(User, photo.uploaded_by) if photo.uploaded_by else None
    return render(
        request,
        "photos/view.html",
        photo=photo,
        customer=customer,
        uploader=uploader,
        prev_photo=siblings[idx - 1] if idx > 0 else None,
        next_photo=siblings[idx + 1] if idx + 1 < len(siblings) else None,
        back_url=return_path(None, request.headers.get("referer"), f"/photos/album/{photo.customer_id}"),
        can_delete=has_perm(user, "photo:delete"),
        total=len(siblings),
        pos=idx + 1,
        today=today_str(),
    )


@router.get("/photos/{photo_id}/file")
def photo_file(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    photo = get_or_404(db, Photo, photo_id, "照片")
    customer = db.get(Customer, photo.customer_id)
    if not customer or not _may_view(db, user, customer):
        return render(request, "403.html", status_code=403)
    path = photo_svc.photo_file(photo.path)
    if not path.exists():
        return JSONResponse({"detail": "文件已丢失"}, status_code=404)
    return FileResponse(path, filename=photo.orig_name or path.name)


@router.get("/photos/{photo_id}/thumb")
def photo_thumb(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    photo = get_or_404(db, Photo, photo_id, "照片")
    customer = db.get(Customer, photo.customer_id)
    if not customer or not _may_view(db, user, customer):
        return render(request, "403.html", status_code=403)
    rel = photo.thumb_path or photo.path
    path = photo_svc.photo_file(rel)
    if not path.exists():
        return JSONResponse({"detail": "缩略图缺失"}, status_code=404)
    return FileResponse(path, media_type="image/webp" if str(path).endswith(".webp") else "image/jpeg")
