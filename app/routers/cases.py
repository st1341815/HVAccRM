"""案例：对外展示的完工案例列表、筛选与匿名分享页。

只展示可公开字段（房号 / 工序 / 施工时间 / 师傅 / 产品类型），
不含任何客户隐私或商业信息。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PRODUCT_OPTIONS, Case
from ..permissions import require
from ..templating import render
from ..utils import parse_int

router = APIRouter(prefix="/cases", tags=["cases"])

CASE_STAGE_OPTIONS = ["前期施工", "后期施工"]


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

    # 责任人筛选下拉：取自案例数据本身（快照，用户被删仍可筛）
    installer_options: list[tuple[int | None, str]] = []
    seen: set[int | None] = set()
    for c in db.scalars(select(Case).order_by(Case.id)).all():
        if c.installer_id in seen:
            continue
        seen.add(c.installer_id)
        installer_options.append((c.installer_id, c.installer_name or f"#{c.installer_id}"))

    return render(
        request,
        "cases/list.html",
        cases=cases,
        installer_options=installer_options,
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
    )


@router.get("/share/{token}")
def share_case(request: Request, token: str, db: Session = Depends(get_db)):
    case = db.scalars(select(Case).where(Case.share_token == token)).first()
    if case is None:
        return render(request, "404.html", status_code=404, detail="分享链接无效或已失效")
    # 对外仅展现：施工时间、施工工序、产品类型
    return render(request, "cases/share.html", case=case)
