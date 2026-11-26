"""首页看板 2.0 的 JSON 接口（v1.16.0）。

为什么单开一个路由文件而不塞进 ui.py：
- ui.py 里是「HTML 页面 + HTMX 片段」，这里全是 JSON；
- 看板接口的权限/CSRF/错误码处理和页面路由不一样，混在一起容易互相污染。

写操作（POST/PATCH/DELETE）统一要 `X-CSRF-Token` 头。没走 `csrf_protect` 依赖是因为
那个依赖读 Form body，JSON 请求体里读不到——用请求头传 token 更自然。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import current_user_or_redirect
from ..db import get_db
from ..models import User
from ..security import verify_csrf
from ..services import dashboard_layout as layout_svc
from ..services import metrics as metrics_svc
from ..templating import csrf_cookie_name

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# --------------------------------------------------------------- 依赖
def csrf_header(request: Request, x_csrf_token: str = Header("")) -> None:
    if not verify_csrf(request.cookies.get(csrf_cookie_name()), x_csrf_token):
        raise HTTPException(status_code=403, detail="页面已过期或来源不可信，请刷新后重试")


def _view_payload(v) -> dict:
    lay = layout_svc.parse_layout(v)
    return {
        "id": v.id,
        "name": v.name,
        "is_default": bool(v.is_default),
        "items": lay["items"],
        "v": lay["v"],
        "updated_at": v.updated_at,
    }


# --------------------------------------------------------------- 模型
class LayoutItemIn(BaseModel):
    id: str | None = None
    metric: str
    kind: str | None = None
    span: int = 12
    h: int = 0
    opts: dict[str, Any] = Field(default_factory=dict)


class ViewCreateIn(BaseModel):
    name: str = "新方案"
    items: list[LayoutItemIn] = Field(default_factory=list)
    make_default: bool = False


class ViewPatchIn(BaseModel):
    name: str | None = None
    items: list[LayoutItemIn] | None = None
    is_default: bool | None = None


# --------------------------------------------------------------- 读
@router.get("/catalog")
def catalog(
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    """模块库：只列当前用户有权限的指标，无权限的根本不下发。"""
    return {"metrics": metrics_svc.catalog(user), "max_views": layout_svc.MAX_VIEWS}


@router.get("/views")
def list_views(
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    layout_svc.ensure_default_view(db, user)
    return {"views": [_view_payload(v) for v in layout_svc.list_views(db, user)]}


@router.get("/widget")
def widget(
    metric: str = Query(..., max_length=40),
    months: int | None = None,
    days: int | None = None,
    n: int | None = None,
    weeks: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    """单模块数据（切方案后局部刷新 / 懒加载用）。首屏不走这里，直接内联在页面里。"""
    opts = {k: v for k, v in
            {"months": months, "days": days, "n": n, "weeks": weeks}.items() if v is not None}
    try:
        data = metrics_svc.compute(db, user, metric, opts)
    except KeyError:
        raise HTTPException(status_code=404, detail="指标不存在")
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权查看该指标")
    return {"metric": metric, "data": data}


# --------------------------------------------------------------- 写
@router.post("/views", dependencies=[Depends(csrf_header)])
def create_view(
    body: ViewCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    items = [i.model_dump() for i in body.items]
    try:
        v = layout_svc.create_view(db, user, body.name, items, make_default=body.make_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"view": _view_payload(v)}


@router.patch("/views/{view_id}", dependencies=[Depends(csrf_header)])
def patch_view(
    view_id: int,
    body: ViewPatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    items = [i.model_dump() for i in body.items] if body.items is not None else None
    try:
        v = layout_svc.update_view(db, user, view_id, name=body.name, items=items,
                                   is_default=body.is_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if v is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"view": _view_payload(v)}


@router.delete("/views/{view_id}", dependencies=[Depends(csrf_header)])
def remove_view(
    view_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    if not layout_svc.delete_view(db, user, view_id):
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"ok": True}


@router.post("/views/{view_id}/duplicate", dependencies=[Depends(csrf_header)])
def duplicate_view(
    view_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    try:
        v = layout_svc.duplicate_view(db, user, view_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if v is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"view": _view_payload(v)}
