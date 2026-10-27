"""FastAPI 入口：中间件、路由注册、异常处理、健康检查。"""
from __future__ import annotations

import logging
import uuid
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
from .routers import (
    admin,
    cases,
    contracts,
    costs,
    customers,
    payments,
    photos,
    projects,
    reports,
    tasks,
    ui,
)
from .scheduler import start_scheduler, stop_scheduler
from .templating import flash_cookie_name, render, set_request_secure

log = logging.getLogger("crm.main")
settings = get_settings()

_BASE_EXEMPT = ("/static", "/login", "/logout", "/health", "/favicon.ico", "/manifest.webmanifest", "/service-worker.js", "/offline.html", "/cases/share")
# API 文档默认只在显式开启时暴露（公开仓库 + 公网实例不该让任何人拉到完整接口清单）
_DOCS_EXEMPT = ("/api/docs", "/api/openapi.json", "/redoc") if settings.api_docs else ()
EXEMPT_PREFIXES = _BASE_EXEMPT + _DOCS_EXEMPT
PASSWORD_CHANGE_PATHS = ("/account", "/account/password", "/logout")


def _is_https(request: Request) -> bool:
    """判定「客户端到反代这一跳」是否为 https（反代终止 TLS 时靠 X-Forwarded-Proto）。"""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto in ("https", "wss"):
        return True
    if proto in ("http", "ws"):
        return False
    return str(request.url.scheme) == "https"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    s = get_settings()
    set_timezone(s.tz)
    s.ensure_dirs()
    log.info("启动 %s（%s）v%s（%s）", s.app_name, s.app_name_en, __version__, s.database_url)
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
    title="HVAccRM · 暖通空调客户管理系统",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.api_docs else None,
    redoc_url="/redoc" if settings.api_docs else None,
    openapi_url="/api/openapi.json" if settings.api_docs else None,
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _is_exempt(path: str) -> bool:
    """判断请求路径是否免登录。

    白名单之外，域名所有权验证文件（static 目录下真实存在的单段 .txt）
    也需要免登录——腾讯云/DNSPod 等会直接 GET 根路径的随机文件名。
    """
    if any(path == p or path.startswith(p) for p in EXEMPT_PREFIXES):
        return True
    name = path.lstrip("/")
    if name.endswith(".txt") and "/" not in name and (static_dir / name).is_file():
        return True
    return False


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> Response:
    """PWA manifest：根路径 + 正确 Content-Type。"""
    return Response(
        (static_dir / "manifest.webmanifest").read_text(encoding="utf-8"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/service-worker.js", include_in_schema=False)
def service_worker() -> Response:
    """PWA Service Worker：根路径 + 全站 scope + 正确 Content-Type。"""
    return Response(
        (static_dir / "service-worker.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/offline.html", include_in_schema=False)
def offline_page() -> Response:
    return Response(
        (static_dir / "offline.html").read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
    )


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    secure = _is_https(request)
    set_request_secure(secure)  # 供 Cookie 的 Secure 标志统一取用
    request.state.user = None
    if not _is_exempt(path):
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
        hsts = int(get_settings().hsts_max_age or 0)
        if hsts > 0 and secure:
            response.headers.setdefault("Strict-Transport-Security", f"max-age={hsts}")
    return response


app.include_router(auth_router)
app.include_router(ui.router)
app.include_router(projects.router)
app.include_router(customers.router)
app.include_router(contracts.router)
app.include_router(payments.router)
app.include_router(costs.router)
app.include_router(tasks.router)
app.include_router(cases.router)
app.include_router(photos.router)
app.include_router(reports.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    ok = healthcheck()
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "db": ok, "version": __version__},
        status_code=200 if ok else 503,
    )


@app.get("/{verify_file}", include_in_schema=False)
def serve_verify_file(verify_file: str):
    """域名所有权验证文件：仅服务 static 目录下真实存在的单段 .txt，免登录。"""
    if not verify_file.endswith(".txt"):
        raise HTTPException(status_code=404, detail="未找到")
    if verify_file != Path(verify_file).name or "/" in verify_file:
        raise HTTPException(status_code=404, detail="未找到")
    target = static_dir / verify_file
    if not target.is_file():
        raise HTTPException(status_code=404, detail="未找到")
    return Response(
        target.read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
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


def _safe_validation_errors(exc: RequestValidationError) -> list:
    """把校验错误转成可 JSON 序列化的结构（空文件部件会夹带 ValueError，直接序列化会 500）。"""
    import json as _json

    safe = []
    for item in exc.errors():
        try:
            _json.dumps(item)
            safe.append(item)
        except (TypeError, ValueError):
            safe.append({k: (str(v) if k in ("msg", "type", "input") else v) for k, v in item.items()})
    return safe


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": _safe_validation_errors(exc)}, status_code=422)
    return render(request, "error.html", status_code=422, detail=f"表单参数不合法：{_safe_validation_errors(exc)[:2]}")


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError):
    """唯一约束/外键冲突：给可读提示，原文（含表名/列名）只进日志。"""
    log.warning("数据完整性冲突 %s %s: %s", request.method, request.url.path, exc.orig)
    detail = "数据冲突（唯一性或关联约束），请检查输入后重试"
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": detail}, status_code=409)
    return _error_page(request, 409, detail)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    """未处理异常：对外只给通用文案 + 追踪号，细节（可能含路径/SQL/表结构）只进日志。"""
    trace_id = uuid.uuid4().hex[:8]
    log.exception("未处理异常 [%s] %s %s", trace_id, request.method, request.url.path)
    detail = f"服务器内部错误，请稍后重试（追踪号 {trace_id}）"
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": detail, "trace_id": trace_id}, status_code=500)
    return _error_page(request, 500, detail)


def _error_page(request: Request, status_code: int, detail: str):
    """渲染错误页；渲染本身失败时退化为纯文本（异常处理器里不能再抛异常）。"""
    try:
        return render(request, "error.html", status_code=status_code, detail=detail)
    except Exception:  # pragma: no cover - 兜底
        log.exception("错误页渲染失败")
        return Response(f"{detail}\n", status_code=status_code, media_type="text/plain; charset=utf-8")
