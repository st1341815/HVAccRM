"""FastAPI 入口：中间件、路由注册、异常处理、健康检查。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from . import __version__
from .auth import NotAuthenticated, load_user, router as auth_router
from .bootstrap import configure_logging, ensure_fts, run_migrations, seed_admin, seed_stages
from .config import get_settings, set_timezone
from .db import healthcheck, session_scope
from .routers import admin, contracts, customers, payments, photos, projects, tasks, ui
from .scheduler import start_scheduler, stop_scheduler
from .templating import flash_cookie_name, render

log = logging.getLogger("crm.main")
settings = get_settings()

EXEMPT_PREFIXES = ("/static", "/login", "/logout", "/health", "/favicon.ico", "/api/docs", "/api/openapi.json", "/redoc")
PASSWORD_CHANGE_PATHS = ("/account", "/account/password", "/logout")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    s = get_settings()
    set_timezone(s.tz)
    s.ensure_dirs()
    log.info("启动 %s v%s（%s）", s.app_name, __version__, s.database_url)
    backend = run_migrations()
    ensure_fts()
    seed_stages()
    seed_admin()
    log.info("数据库初始化完成（迁移方式：%s）", backend)
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        log.info("已停止定时任务")


app = FastAPI(
    title="家居建材客户管理系统 CRM",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    if not any(path == p or path.startswith(p) for p in EXEMPT_PREFIXES):
        with session_scope() as db:
            user = load_user(request, db)
            if user:
                request.state.user = user
        if request.state.user is None:
            if path.startswith("/api"):
                return JSONResponse({"detail": "未登录"}, status_code=401)
            if request.headers.get("hx-request"):
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
        if request.state.user.must_change_password and not any(
            path == p or path.startswith(p) for p in PASSWORD_CHANGE_PATHS
        ):
            return RedirectResponse("/account/password?forced=1", status_code=303)
    response = await call_next(request)
    if not path.startswith("/static"):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


app.include_router(auth_router)
app.include_router(ui.router)
app.include_router(projects.router)
app.include_router(customers.router)
app.include_router(contracts.router)
app.include_router(payments.router)
app.include_router(tasks.router)
app.include_router(photos.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    ok = healthcheck()
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "db": ok, "version": __version__},
        status_code=200 if ok else 503,
    )


@app.get("/account/password")
def forced_password_page(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    forced = request.query_params.get("forced") == "1" or bool(user.must_change_password)
    secret = None
    if not user.totp_enabled:
        from .security import generate_totp_secret

        secret = generate_totp_secret()
    return render(request, "account.html", forced=forced, new_totp_secret=secret, totp_uri=None, totp_preview=None)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "未登录"}, status_code=401)
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    path = request.url.path
    if path.startswith("/api") and not request.headers.get("hx-request"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 403:
        return render(request, "403.html", status_code=403, detail=exc.detail)
    if exc.status_code == 404:
        return render(request, "404.html", status_code=404, detail=exc.detail)
    return render(request, "error.html", status_code=exc.status_code, detail=exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": exc.errors()}, status_code=422)
    return render(request, "error.html", status_code=422, detail=f"表单参数不合法：{exc.errors()[:2]}")


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError):
    """唯一约束/外键冲突：给出可读提示而不是 500。"""
    log.warning("数据完整性冲突 %s %s: %s", request.method, request.url.path, exc.orig)
    detail = f"数据冲突（唯一性或关联约束）：{exc.orig}"
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": detail}, status_code=409)
    return render(request, "error.html", status_code=409, detail=detail)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("未处理异常 %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": f"服务器内部错误：{exc}"}, status_code=500)
    return render(request, "error.html", status_code=500, detail=f"服务器内部错误：{exc}")
