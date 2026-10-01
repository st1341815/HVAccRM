"""后台管理：用户/角色/权限、审计日志、工序模板、备份、系统信息。"""
from __future__ import annotations

import platform
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..audit import log_action
from ..config import get_settings
from ..db import commit_retry, engine, get_db
from ..models import AuditLog, Customer, Photo, StageTemplate, User, now_iso
from ..permissions import (
    DEFAULT_SCOPE,
    ROLE_LABELS,
    ROLE_PERMS,
    ALL_PERMISSIONS,
    require,
)
from ..security import generate_totp_secret, hash_password, password_problem
from ..services import backup as backup_svc
from ..services import photos as photo_svc
from ..services import search as search_svc
from ..templating import redirect, render
from ..utils import client_ip, parse_int

router = APIRouter(prefix="/admin", tags=["admin"])
SCOPES = {
    "all": "全部数据",
    "self": "仅本人负责",
    "shared": "本人 + 显式共享",
}


@router.get("")
def overview(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    s = get_settings()
    db_size = Path(s.db_path).stat().st_size if Path(s.db_path).exists() else 0
    counts = {
        "users": db.query(User).count(),
        "customers": db.query(Customer).count(),
        "photos": db.query(Photo).count(),
    }
    from ..scheduler import _scheduler

    jobs = []
    if _scheduler:
        jobs = [{"id": j.id, "next": str(j.next_run_time)} for j in _scheduler.get_jobs()]
    return render(
        request,
        "admin/overview.html",
        db_size=db_size,
        counts=counts,
        media=photo_svc.total_usage(),
        fts=search_svc.fts_status(db),
        jobs=jobs,
        backups=backup_svc.list_backups()[:5],
        engine_url=str(engine.url),
        platform_info=f"{platform.platform()} / Python {sys.version.split()[0]}",
        secret_default=s.is_secret_default,
        scheduler_running=bool(_scheduler and _scheduler.running),
    )


@router.get("/users")
def users_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:user")),
):
    users = list(db.scalars(select(User).order_by(User.id)).all())
    return render(
        request,
        "admin/users.html",
        users=users,
        roles=ROLE_LABELS,
        role_perms={k: sorted(v) for k, v in ROLE_PERMS.items()},
        scopes=SCOPES,
        new_totp_secret=generate_totp_secret(),
    )


@router.post("/users/new")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("sales"),
    data_scope: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:user")),
):
    username = username.strip()
    if db.scalars(select(User).where(User.username == username)).first():
        return redirect("/admin/users", f"用户名 {username} 已存在", "err")
    problem = password_problem(password)
    if problem:
        return redirect("/admin/users", problem, "err")
    if role not in ROLE_PERMS:
        return redirect("/admin/users", "角色不正确", "err")
    new_user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        data_scope=data_scope or DEFAULT_SCOPE.get(role, "self"),
        must_change_password=1,
        session_version=1,
        is_active=1,
        created_at=now_iso(),
    )
    db.add(new_user)
    db.flush()
    log_action(db, user, "create", "users", new_user.id, new={"username": username, "role": role}, ip=client_ip(request))
    commit_retry(db)
    return redirect("/admin/users", f"用户 {username} 已创建，首次登录需修改密码")


@router.post("/users/{user_id}/update")
def update_user(
    request: Request,
    user_id: int,
    role: str = Form(""),
    data_scope: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:user")),
):
    target = db.get(User, user_id)
    if not target:
        return redirect("/admin/users", "用户不存在", "err")
    if target.id == user.id and not is_active:
        return redirect("/admin/users", "不能禁用当前登录账号", "err")
    before = {"role": target.role, "data_scope": target.data_scope, "is_active": target.is_active}
    changed = False
    if role and role != target.role and role in ROLE_PERMS:
        target.role = role
        if not data_scope:
            target.data_scope = DEFAULT_SCOPE.get(role, "self")
        changed = True
    if data_scope and data_scope != target.data_scope:
        target.data_scope = data_scope
        changed = True
    new_active = 1 if is_active else 0
    if new_active != target.is_active:
        target.is_active = new_active
        changed = True
    if changed:
        # 权限/状态变化：session_version +1，强制重新登录
        target.session_version = int(target.session_version or 1) + 1
        log_action(db, user, "update", "users", target.id, old=before, new={"role": target.role, "data_scope": target.data_scope, "is_active": target.is_active}, ip=client_ip(request))
        commit_retry(db)
        return redirect("/admin/users", f"{target.username} 权限已更新，其现有会话已失效")
    return redirect("/admin/users", "无变更")


@router.post("/users/{user_id}/reset-password")
def reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:user")),
):
    target = db.get(User, user_id)
    if not target:
        return redirect("/admin/users", "用户不存在", "err")
    problem = password_problem(new_password)
    if problem:
        return redirect("/admin/users", problem, "err")
    target.password_hash = hash_password(new_password)
    target.must_change_password = 1
    target.session_version = int(target.session_version or 1) + 1
    log_action(db, user, "reset_password", "users", target.id, ip=client_ip(request))
    commit_retry(db)
    return redirect("/admin/users", f"{target.username} 密码已重置，需登录后修改")


@router.get("/audit")
def audit_page(
    request: Request,
    action: str = "",
    username: str = "",
    limit: str = "200",
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:user")),
):
    stmt = select(AuditLog).order_by(desc(AuditLog.id)).limit(parse_int(limit) or 200)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if username:
        stmt = stmt.where(AuditLog.username == username)
    logs = list(db.scalars(stmt).all())
    actions = sorted({row.action for row in db.scalars(select(AuditLog).limit(2000)).all() or []})
    return render(request, "admin/audit.html", logs=logs, actions=actions, action=action, username=username, limit=limit)


@router.get("/stages")
def stages_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    stages = list(db.scalars(select(StageTemplate).order_by(StageTemplate.sort_order)).all())
    return render(request, "admin/stages.html", stages=stages)


@router.post("/stages/new")
def create_stage(
    request: Request,
    name: str = Form(...),
    sort_order: str = Form("0"),
    default_days: str = Form("1"),
    require_photo: str = Form(""),
    output_doc: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    if db.scalars(select(StageTemplate).where(StageTemplate.name == name.strip())).first():
        return redirect("/admin/stages", "同名工序已存在", "err")
    stage = StageTemplate(
        name=name.strip(),
        sort_order=parse_int(sort_order) or 0,
        default_days=parse_int(default_days) or 1,
        require_photo=1 if require_photo else 0,
        is_active=1,
        output_doc=output_doc.strip() or None,
    )
    db.add(stage)
    db.flush()
    log_action(db, user, "create", "stage_templates", stage.id, new=stage, ip=client_ip(request))
    commit_retry(db)
    return redirect("/admin/stages", f"工序「{stage.name}」已添加（仅影响新建客户）")


@router.post("/stages/{stage_id}/update")
def update_stage(
    request: Request,
    stage_id: int,
    sort_order: str = Form("0"),
    default_days: str = Form("1"),
    require_photo: str = Form(""),
    is_active: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    stage = db.get(StageTemplate, stage_id)
    if not stage:
        return redirect("/admin/stages", "工序不存在", "err")
    before = {"sort_order": stage.sort_order, "default_days": stage.default_days, "require_photo": stage.require_photo, "is_active": stage.is_active}
    stage.sort_order = parse_int(sort_order) or 0
    stage.default_days = parse_int(default_days) or 1
    stage.require_photo = 1 if require_photo else 0
    stage.is_active = 1 if is_active else 0
    log_action(db, user, "update", "stage_templates", stage.id, old=before, new=stage, ip=client_ip(request))
    commit_retry(db)
    return redirect("/admin/stages", f"工序「{stage.name}」已更新")


@router.get("/backups")
def backups_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    return render(
        request,
        "admin/backups.html",
        backups=backup_svc.list_backups(),
        keep=get_settings().backup_keep,
        media=photo_svc.total_usage(),
    )


@router.post("/backups/run")
def run_backup(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require("admin:setting")),
):
    try:
        result = backup_svc.run_backup_job()
    except Exception as exc:
        return redirect("/admin/backups", f"备份失败：{exc}", "err")
    log_action(db, user, "backup", "database", None, new=result, ip=client_ip(request))
    commit_retry(db)
    return redirect("/admin/backups", f"备份完成：{result['file']}（清理旧备份 {result['pruned']} 份）")


@router.get("/permissions")
def permissions_page(request: Request, user: User = Depends(require("admin:user"))):
    return render(
        request,
        "admin/permissions.html",
        roles=ROLE_LABELS,
        role_perms={k: sorted(v) for k, v in ROLE_PERMS.items()},
        all_perms=ALL_PERMISSIONS,
        scopes=SCOPES,
    )
