"""认证：登录/登出、强制改密、可选 TOTP 二步验证、会话版本失效。"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_action
from .config import get_settings
from .db import get_db
from .models import User, now_iso
from .security import (
    generate_totp_secret,
    hash_password,
    make_session,
    password_problem,
    totp_now,
    totp_uri,
    verify_password,
    verify_totp,
)
from .templating import redirect, render

log = logging.getLogger("crm.auth")
router = APIRouter()

PUBLIC_PATHS = {"/login", "/health", "/static", "/favicon.ico", "/logout"}
_attempts: dict[str, list[float]] = {}


class NotAuthenticated(Exception):
    """未登录访问受保护页面。"""


def get_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "-"


def _locked(key: str) -> int:
    s = get_settings()
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < s.login_lock_seconds]
    _attempts[key] = hits
    if len(hits) >= s.login_max_attempts:
        wait = int(s.login_lock_seconds - (now - hits[0]))
        return max(wait, 1)
    return 0


def _record_failure(key: str) -> None:
    _attempts.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    _attempts.pop(key, None)


def load_user(request: Request, db: Session) -> User | None:
    from .security import loads

    data = loads(request.cookies.get(get_settings().session_cookie))
    if not data:
        return None
    user = db.get(User, int(data.get("uid", 0)))
    if not user or not user.is_active:
        return None
    if int(data.get("sv", 0)) != int(user.session_version or 1):
        return None  # 权限/状态变更后强制重新登录
    request.state.session_data = data
    return user


def current_user_or_redirect(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise NotAuthenticated()
    return user


def fresh_user(db: Session, user: User) -> User:
    """中间件里的 user 属于已关闭的会话（detached），修改前必须重新加载。"""
    return db.get(User, user.id) or user


def set_session_cookie(response, user: User) -> None:
    s = get_settings()
    token = make_session(user.id, int(user.session_version or 1), bool(user.must_change_password))
    response.set_cookie(
        s.session_cookie,
        token,
        max_age=s.session_max_age,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(get_settings().session_cookie, path="/")


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if getattr(request.state, "user", None):
        return RedirectResponse(next or "/", status_code=303)
    return render(request, "login.html", next=next)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    totp: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    username = username.strip()
    ip = get_client_ip(request)
    key = f"{ip}|{username.lower()}"
    wait = _locked(key)
    if wait:
        return render(
            request,
            "login.html",
            status_code=429,
            next=next,
            error=f"失败次数过多，请 {wait} 秒后重试",
            username=username,
        )
    user = db.scalars(select(User).where(User.username == username)).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        _record_failure(key)
        if user:
            log_action(db, user, "login_failed", "users", user.id, ip=ip, commit=True)
        log.warning("登录失败 user=%s ip=%s", username, ip)
        return render(request, "login.html", status_code=401, next=next, error="账号或密码错误", username=username)

    if user.totp_enabled:
        if not totp:
            return render(
                request,
                "login.html",
                status_code=401,
                next=next,
                error="请输入动态验证码",
                username=username,
                need_totp=True,
            )
        if not verify_totp(user.totp_secret or "", totp):
            _record_failure(key)
            log_action(db, user, "totp_failed", "users", user.id, ip=ip, commit=True)
            return render(
                request,
                "login.html",
                status_code=401,
                next=next,
                error="动态验证码不正确",
                username=username,
                need_totp=True,
            )

    _clear_failures(key)
    user.last_login_at = now_iso()
    log_action(db, user, "login", "users", user.id, ip=ip, commit=True)

    if user.must_change_password:
        target = "/account/password"
    else:
        target = next if next and next.startswith("/") else "/"
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, user)
    return response


@router.get("/logout")
@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user:
        log_action(db, user, "logout", "users", user.id, ip=get_client_ip(request), commit=True)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/account")
def account_page(request: Request, user: User = Depends(current_user_or_redirect)):
    secret = None
    if not user.totp_enabled:
        secret = generate_totp_secret()
    return render(
        request,
        "account.html",
        new_totp_secret=secret,
        totp_uri=totp_uri(secret, user.username) if secret else None,
        totp_preview=totp_now(secret) if secret else None,
    )


@router.post("/account/password")
def change_password(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    if new_password != confirm_password:
        return render(request, "account.html", status_code=400, error="两次输入的新密码不一致", new_totp_secret=generate_totp_secret() if not user.totp_enabled else None)
    problem = password_problem(new_password)
    if problem:
        return render(request, "account.html", status_code=400, error=problem, new_totp_secret=generate_totp_secret() if not user.totp_enabled else None)
    if not user.must_change_password and not verify_password(old_password, user.password_hash):
        return render(request, "account.html", status_code=400, error="原密码不正确", new_totp_secret=generate_totp_secret() if not user.totp_enabled else None)

    user = fresh_user(db, user)
    old = {"must_change_password": user.must_change_password, "changed_at": user.last_login_at}
    user.password_hash = hash_password(new_password)
    user.must_change_password = 0
    user.session_version = int(user.session_version or 1) + 1
    log_action(
        db,
        user,
        "password_change",
        "users",
        user.id,
        old=old,
        new={"must_change_password": 0},
        ip=get_client_ip(request),
    )
    db.commit()
    response = redirect("/account", "密码已更新，其他设备需重新登录", "ok")
    set_session_cookie(response, user)
    return response


@router.post("/account/totp/enable")
def totp_enable(
    request: Request,
    secret: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    if not verify_totp(secret, code):
        return render(request, "account.html", status_code=400, error="验证码不正确，请确认手机时间同步后重试", new_totp_secret=secret)
    user = fresh_user(db, user)
    user.totp_secret = secret
    user.totp_enabled = 1
    user.session_version = int(user.session_version or 1) + 1
    log_action(db, user, "totp_enable", "users", user.id, ip=get_client_ip(request))
    db.commit()
    response = redirect("/account", "已开启二步验证，请重新登录", "ok")
    clear_session_cookie(response)
    return response


@router.post("/account/totp/disable")
def totp_disable(
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
):
    if not verify_password(password, user.password_hash):
        return render(request, "account.html", status_code=400, error="密码不正确，无法关闭二步验证", new_totp_secret=None)
    user = fresh_user(db, user)
    user.totp_enabled = 0
    user.totp_secret = None
    user.session_version = int(user.session_version or 1) + 1
    log_action(db, user, "totp_disable", "users", user.id, ip=get_client_ip(request))
    db.commit()
    response = redirect("/login", "已关闭二步验证，请重新登录", "ok")
    clear_session_cookie(response)
    return response


def touch_session(user: User) -> None:  # pragma: no cover - 预留
    pass


def days_since(dt_str: str | None) -> int:
    if not dt_str:
        return 0
    try:
        d = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            d = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        except ValueError:
            return 0
    return max((datetime.now() - d).days, 0)
