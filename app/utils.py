"""路由层通用小工具。"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session


def get_or_404(db: Session, model: Any, pk: Any, label: str = "记录"):
    obj = db.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在（id={pk}）")
    return obj


def parse_date(value: str | None) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    return v[:10]


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        s = str(value).strip().replace(",", "")
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int | None = None) -> int | None:
    try:
        s = str(value).strip()
        return int(float(s)) if s else default
    except (TypeError, ValueError):
        return default


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "-"


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"
