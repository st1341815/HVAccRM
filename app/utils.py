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


def _to_local_path(value: str | None) -> str | None:
    """把可能是绝对 URL 的值裁成站内相对路径；非站内路径返回 None。"""
    if not value:
        return None
    v = value.strip()
    if v.startswith(("http://", "https://")):
        from urllib.parse import urlparse

        parsed = urlparse(v)
        v = parsed.path or ""
        if parsed.query:
            v = f"{v}?{parsed.query}"
    # 反斜杠在浏览器的 URL 解析里等价于 "/"（WHATWG URL 规范），
    # 所以 `/\evil.com` 会被解析成 `//evil.com` → 协议相对外跳；
    # 控制字符（\r\n\t\0）可以拼接出畸形 Location 头。两者一律拒收。
    if "\\" in v or any(ord(ch) < 0x20 for ch in v):
        return None
    if v.startswith("/") and not v.startswith("//"):
        return v
    return None


def return_path(back: str | None, referer: str | None, default: str) -> str:
    """决定操作后跳回哪里：显式 back 优先，其次 Referer，都不可信时用 default。

    只接受站内相对路径，避免被构造成外部跳转（开放重定向）。
    """
    return _to_local_path(back) or _to_local_path(referer) or default


def safe_next(value: str | None, default: str = "/") -> str:
    """登录后跳转等「用户可控落点」的净化：只接受站内相对路径。

    必须挡住 `//evil.com`（协议相对 URL）与 `https://evil.com`，
    所以统一走 `_to_local_path`（它唯一一处判断写对了，别在调用点重复实现）。
    """
    return _to_local_path(value) or default
