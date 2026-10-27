"""模板渲染与 Flash 提示。"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import __version__, models, permissions, regions
from .config import get_settings
from .models import today_str
from .security import loads, make_csrf_token, make_flash, verify_csrf

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 当前请求是否为 https（由 main.py 的中间件在每个请求开始时写入），
# 这样 Cookie 的 Secure 标志可以在不改 50 处 `set_cookie` 调用签名的前提下统一生效。
_SECURE_CTX: ContextVar[bool] = ContextVar("crm_secure_ctx", default=False)


def set_request_secure(flag: bool) -> None:
    _SECURE_CTX.set(bool(flag))


def cookie_secure() -> bool:
    return get_settings().cookie_secure_flag(_SECURE_CTX.get())


def money(v: Any) -> str:
    try:
        return f"{float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def number(v: Any, nd: int = 1) -> str:
    try:
        return f"{float(v or 0):,.{nd}f}"
    except (TypeError, ValueError):
        return "0.0"


def short_dt(v: Any) -> str:
    return str(v)[:16] if v else ""


def duration(v: Any) -> str:
    """毫秒 → 人话时长（统计页用）：83000 → 「1 分 23 秒」；0/空 → 「-」。"""
    try:
        ms = int(float(v or 0))
    except (TypeError, ValueError):
        return "-"
    if ms <= 0:
        return "-"
    sec = int(round(ms / 1000))
    if sec < 60:
        return f"{sec} 秒"
    minutes, sec = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 时 {minutes} 分" if minutes else f"{hours} 时"


templates.env.filters["money"] = money
templates.env.filters["number"] = number
templates.env.filters["short_dt"] = short_dt
templates.env.filters["duration"] = duration
templates.env.globals["role_labels"] = permissions.ROLE_LABELS
templates.env.globals["all_permissions"] = permissions.ALL_PERMISSIONS
templates.env.globals["role_perms"] = permissions.ROLE_PERMS
templates.env.globals["unit_options"] = models.UNIT_OPTIONS
templates.env.globals["product_options"] = models.PRODUCT_OPTIONS
templates.env.globals["customer_status_labels"] = models.CUSTOMER_STATUS_LABELS
templates.env.globals["city_options"] = regions.CITY_OPTIONS
templates.env.globals["city_districts"] = regions.CITY_DISTRICTS
templates.env.globals["districts_of"] = regions.districts_of
templates.env.globals["default_city"] = regions.DEFAULT_CITY
templates.env.globals["default_district"] = regions.DEFAULT_DISTRICT


def flash_cookie_name() -> str:
    return get_settings().session_cookie + "_flash"


def csrf_cookie_name() -> str:
    return get_settings().session_cookie + "_csrf"


def csrf_token_for(request: Request) -> str:
    """当前请求可用的 CSRF token：Cookie 里已有有效值就复用，否则现生成一个。

    复用而不是每次刷新新值，是为了让「同时打开多个标签页/多张表单」都能提交成功。
    """
    raw = request.cookies.get(csrf_cookie_name())
    if raw and verify_csrf(raw, raw):
        return raw
    return make_csrf_token()


def read_flash(request: Request) -> dict[str, str] | None:
    data = loads(request.cookies.get(flash_cookie_name()))
    if not data:
        return None
    return {"level": data.get("lvl", "ok"), "message": data.get("msg", "")}


def render(request: Request, name: str, status_code: int = 200, **ctx: Any):
    user = getattr(request.state, "user", None)
    csrf = csrf_token_for(request)
    ctx.update(
        {
            "user": user,
            "settings": get_settings(),
            "version": __version__,
            "today": today_str(),
            "csrf_token": csrf,
            "can": lambda perm: permissions.has_perm(user, perm),
            "can_amount": permissions.can_see_amount(user),
            "perms": sorted(permissions.perms_for(user)),
            "role_label": permissions.ROLE_LABELS.get(getattr(user, "role", ""), ""),
            "current_path": request.url.path,
        }
    )
    ctx.setdefault("flash", read_flash(request))
    ctx.setdefault("error", None)
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    if request.cookies.get(flash_cookie_name()):
        response.delete_cookie(flash_cookie_name(), path="/")
    if request.cookies.get(csrf_cookie_name()) != csrf:
        response.set_cookie(
            csrf_cookie_name(),
            csrf,
            max_age=get_settings().session_max_age,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(),
            path="/",
        )
    return response


def redirect(url: str, message: str | None = None, level: str = "ok") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if message:
        response.set_cookie(
            flash_cookie_name(),
            make_flash(level, message),
            max_age=60,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(),
            path="/",
        )
    return response
