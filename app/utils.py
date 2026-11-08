"""路由层通用小工具。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session


def get_or_404(db: Session, model: Any, pk: Any, label: str = "记录"):
    obj = db.get(model, pk)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{label}不存在（id={pk}）")
    return obj


_NUM_SEG = re.compile(r"(\d+)")


def room_sort_key(text: str | None) -> list[tuple[int, int, str]]:
    """房号「自然排序」key：数字段按数值比、其余按字面比。

    纯字符串排序会把「10#201」排在「6#401」前面（'1' < '6'），房号一多就没法看。
    切成 (类型, 数值, 文本) 三元组，既能混排也不会在比较时抛 TypeError。
    合同台账、客户列表两处共用（同一栋楼的房号在两边顺序必须一致）。
    """
    parts = _NUM_SEG.split(text or "")
    out: list[tuple[int, int, str]] = []
    for i, part in enumerate(parts):
        out.append((0, int(part), "") if i % 2 else (1, 0, part))
    return out


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


def cached_file_response(
    path: Path,
    media_type: str | None = None,
    max_age: int = 86400,
    public: bool = False,
    filename: str | None = None,
) -> FileResponse:
    """带浏览器缓存的图片/文件响应。

    为什么需要：一个页面就要拉几十张缩略图，全走网络既慢又占数据库连接。
    加 `Cache-Control` 后同一浏览器再次打开直接命中本地缓存；304 协商由
    Starlette FileResponse 自带的 ETag/Last-Modified 处理，无需手写。

    private：默认不缓存到 CDN/代理（内部图片是鉴权资源，走 Cloudflare 时尤其重要）；
    分享页那种公开图片传 public=True。传 filename 会变成「下载」语义（Content-Disposition）。
    """
    scope = "public" if public else "private"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": f"{scope}, max-age={max_age}"},
    )

