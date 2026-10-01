"""模板渲染与 Flash 提示。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import __version__, models, permissions
from .config import get_settings
from .models import today_str
from .security import make_flash, loads

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


templates.env.filters["money"] = money
templates.env.filters["number"] = number
templates.env.filters["short_dt"] = short_dt
templates.env.globals["role_labels"] = permissions.ROLE_LABELS
templates.env.globals["all_permissions"] = permissions.ALL_PERMISSIONS
templates.env.globals["role_perms"] = permissions.ROLE_PERMS
templates.env.globals["unit_options"] = models.UNIT_OPTIONS
templates.env.globals["product_options"] = models.PRODUCT_OPTIONS


def flash_cookie_name() -> str:
    return get_settings().session_cookie + "_flash"


def read_flash(request: Request) -> dict[str, str] | None:
    data = loads(request.cookies.get(flash_cookie_name()))
    if not data:
        return None
    return {"level": data.get("lvl", "ok"), "message": data.get("msg", "")}


def render(request: Request, name: str, status_code: int = 200, **ctx: Any):
    user = getattr(request.state, "user", None)
    ctx.update(
        {
            "user": user,
            "settings": get_settings(),
            "version": __version__,
            "today": today_str(),
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
            path="/",
        )
    return response
