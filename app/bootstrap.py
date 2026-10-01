"""启动引导：建目录 → 迁移数据库 → 全文索引 → 工序模板 → admin 账号。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import select, text

from .config import get_settings
from .db import SessionLocal, engine
from .models import DEFAULT_STAGES, Base, StageTemplate, User, now_iso
from .security import hash_password

log = logging.getLogger("crm.bootstrap")
ROOT = Path(__file__).resolve().parents[1]


def run_migrations() -> str:
    """优先 Alembic；不可用时回退 create_all（两条路径幂等）。"""
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
        command.upgrade(cfg, "head")
        return "alembic"
    except Exception as exc:
        log.warning("Alembic 迁移不可用（%s），回退 Base.metadata.create_all", exc)
        Base.metadata.create_all(bind=engine)
        return "create_all"


def ensure_fts() -> None:
    from .models import FTS_DDL

    with engine.begin() as conn:
        for stmt in FTS_DDL:
            conn.execute(text(stmt))
        try:
            conn.execute(text("INSERT INTO customers_fts(customers_fts) VALUES('rebuild')"))
        except Exception as exc:  # pragma: no cover
            log.warning("FTS rebuild 跳过: %s", exc)


def seed_stages() -> int:
    added = 0
    with SessionLocal() as db:
        existing = {s.name for s in db.scalars(select(StageTemplate)).all()}
        for name, order, days, require_photo, output in DEFAULT_STAGES:
            if name in existing:
                continue
            db.add(
                StageTemplate(
                    name=name,
                    sort_order=order,
                    default_days=days,
                    require_photo=require_photo,
                    is_active=1,
                    output_doc=output,
                )
            )
            added += 1
        db.commit()
    return added


def seed_admin() -> bool:
    s = get_settings()
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == s.admin_user)).first()
        if user:
            return False
        db.add(
            User(
                username=s.admin_user,
                password_hash=hash_password(s.admin_pass),
                role="admin",
                data_scope="all",
                must_change_password=1 if s.admin_force_password_change else 0,
                session_version=1,
                is_active=1,
                created_at=now_iso(),
            )
        )
        db.commit()
        log.info("已创建管理员账号 %s", s.admin_user)
        return True


def configure_logging() -> None:
    """控制台 + 滚动文件日志（日志目录随数据目录独立挂载，便于 NAS 侧排障）。"""
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    if getattr(configure_logging, "_done", False):
        return
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        from logging.handlers import RotatingFileHandler

        s.log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            s.log_dir / "crm.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:  # 只读或未挂载日志目录时退化为控制台
        log.warning("文件日志不可用（%s），仅输出到 stdout", exc)

    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    configure_logging._done = True


def main() -> None:
    configure_logging()
    s = get_settings()
    s.ensure_dirs()
    if s.is_secret_default:
        log.warning("APP_SECRET_KEY 仍为默认值，请在生产环境更换（否则会话可被伪造）")
    backend = run_migrations()
    # Alembic 会按 alembic.ini 重设 root logger 级别，这里恢复应用日志级别
    logging.getLogger().setLevel(getattr(logging, s.log_level.upper(), logging.INFO))
    ensure_fts()
    stages = seed_stages()
    created = seed_admin()
    log.info(
        "bootstrap 完成：迁移=%s 新增工序模板=%d admin新建=%s db=%s",
        backend,
        stages,
        created,
        s.db_path,
    )


if __name__ == "__main__":
    main()
