"""审计日志与变更快照。"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, User

SENSITIVE_KEYS = {"password", "password_hash", "passwd", "totp_secret", "secret"}


def _dump(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if hasattr(obj, "__table__"):
        data = {
            c.name: getattr(obj, c.name)
            for c in obj.__table__.columns
            if c.name not in SENSITIVE_KEYS
        }
    elif isinstance(obj, dict):
        data = {k: v for k, v in obj.items() if k not in SENSITIVE_KEYS}
    else:
        return str(obj)
    return json.dumps(data, ensure_ascii=False, default=str)


def log_action(
    db: Session,
    user: User | None,
    action: str,
    table_name: str | None = None,
    record_id: int | None = None,
    old: Any = None,
    new: Any = None,
    ip: str | None = None,
    commit: bool = False,
) -> None:
    db.add(
        AuditLog(
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            action=action,
            table_name=table_name,
            record_id=record_id,
            old_val=_dump(old),
            new_val=_dump(new),
            ip=ip,
        )
    )
    if commit:
        db.commit()
