"""认证：登录/登出、强制改密、可选 TOTP 二步验证、会话版本失效。"""
from __future__ import annotations

import ipaddress
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_action
from .config import get_settings
from .csrf import csrf_protect
from .db import get_db
from .models import User, now_iso
from .security import (
    generate_totp_secret,
    hash_password,
    make_session,
    password_problem,
    totp_now,
    totp_uri,
    verify_csrf,
    verify_password,
    verify_totp,
)
from .templating import cookie_secure, csrf_cookie_name, redirect, render
from .utils import safe_next

log = logging.getLogger("crm.auth")
router = APIRouter()

PUBLIC_PATHS = {"/login", "/health", "/static", "/favicon.ico", "/logout"}
_attempts: dict[str, list[float]] = {}


class NotAuthenticated(Exception):
    """未登录访问受保护页面。"""


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def get_client_ip(request: Request) -> str:
    """取客户端 IP。

    `X-Forwarded-For` 只在**首段是合法 IP** 时才采纳（防伪造垃圾头污染审计与限速键）；
    真正的防绕过不靠这里，而是靠 `login_max_attempts_account` —— 账号维度锁定与 IP 无关。
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if _looks_like_ip(first):
            return first
    return request.client.host if request.client else "-"


def _lock_keys(ip: str, username: str) -> list[str]:
    """两个维度：IP+账号（防撞库）、纯账号（防伪造 XFF 绕过）。"""
    name = username.strip().lower()
    return [f"{ip}|{name}", f"acct|{name}"]


def _locked(key: str, limit: int) -> int:
    s = get_settings()
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < s.login_lock_seconds]
    _attempts[key] = hits
    if len(hits) >= limit:
        wait = int(s.login_lock_seconds - (now - hits[0]))
        return max(wait, 1)
    return 0


def _wait_seconds(ip: str, username: str) -> int:
    s = get_settings()
    ip_key, acct_key = _lock_keys(ip, username)
    return max(_locked(ip_key, s.login_max_attempts), _locked(acct_key, s.login_max_attempts_account))


def _record_failure(ip: str, username: str) -> None:
    now = time.time()
    for key in _lock_keys(ip, username):
        _attempts.setdefault(key, []).append(now)


def _clear_failures(ip: str, username: str) -> None:
    for key in _lock_keys(ip, username):
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
        secure=cookie_secure(),
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(get_settings().session_cookie, path="/")


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if getattr(request.state, "user", None):
        # 已登录再访问 /login：落点同样必须净化，否则 //evil.com 这类会变成开放重定向
        return RedirectResponse(safe_next(next, "/"), status_code=303)
    return render(request, "login.html", next=next)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    totp: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    username = username.strip()
    ip = get_client_ip(request)
    if not verify_csrf(request.cookies.get(csrf_cookie_name()), csrf_token):
        return render(
            request,
            "login.html",
            status_code=400,
            next=next,
            error="页面已过期，请刷新后重新登录",
            username=username,
        )
    wait = _wait_seconds(ip, username)
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
        _record_failure(ip, username)
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
            _record_failure(ip, username)
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

    _clear_failures(ip, username)
    user.last_login_at = now_iso()
    log_action(db, user, "login", "users", user.id, ip=ip, commit=True)

    if user.must_change_password:
        target = "/account/password"
    else:
        target = safe_next(next, "/")
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, user)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    """登出只接受 POST：GET 会带来「被第三方页面强制登出」和被代理缓存的问题。"""
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


@router.post("/account/profile")
def update_profile(
    request: Request,
    full_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
    _csrf: None = Depends(csrf_protect),
):
    """本人修改姓名（表决不用管理员，改完立刻在各类表单里生效）。"""
    me = fresh_user(db, user)
    before = {"full_name": me.full_name}
    new_name = full_name.strip() or None
    if new_name and len(new_name) > 32:
        return redirect("/account", "姓名过长（≤32 字）", "err")
    me.full_name = new_name
    log_action(db, me, "profile_update", "users", me.id, old=before, new={"full_name": new_name}, ip=get_client_ip(request))
    db.commit()
    return redirect("/account", f"姓名已更新为「{me.display_name}」")


@router.post("/account/password")
def change_password(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user_or_redirect),
    _csrf: None = Depends(csrf_protect),
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
    _csrf: None = Depends(csrf_protect),
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
    _csrf: None = Depends(csrf_protect),
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
