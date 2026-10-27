"""启动引导：建目录 → 迁移数据库 → 全文索引 → 工序模板 → admin 账号。"""
from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import func, select, text

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
    """仅在「库中还没有任何工序模板」时写入默认模板。

    刻意不做「缺哪个补哪个」：模板属于用户可维护的数据，
    后台删掉/改名/调整后不该在下次重启时被自动补回来。
    """
    added = 0
    with SessionLocal() as db:
        if db.scalar(select(func.count(StageTemplate.id))):
            return 0
        for name, order, days, require_photo, output in DEFAULT_STAGES:
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


def _write_initial_credentials(username: str, password: str) -> Path | None:
    """把首次启动生成的随机管理员密码写进 `DATA_DIR/first_boot.txt`（0600）。"""
    path = get_settings().data_path / "first_boot.txt"
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(
                "HVAccRM 首次启动创建的账号\n"
                f"用户名: {username}\n"
                f"密码: {password}\n\n"
                "请登录后立即在「账户」页修改密码，然后删除本文件。\n"
            )
        os.chmod(path, 0o600)  # 个别挂载不认 create 时的 mode，显式再收一次
        return path
    except OSError as exc:
        log.error("无法写入初始凭据文件 %s（%s）", path, exc)
        return None


def seed_admin() -> bool:
    """首次启动创建管理员。

    若 `ADMIN_PASS` 仍是占位默认值（admin888），**生成随机密码**并写入
    `DATA_DIR/first_boot.txt`（0600），避免「公开仓库/公开镜像 = 全网同一把默认钥匙」。
    显式设置过 `ADMIN_PASS` 的部署行为不变。
    """
    s = get_settings()
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.username == s.admin_user)).first()
        if user:
            return False
        password = s.admin_pass
        generated = False
        if s.admin_random_initial_password and s.is_admin_pass_default:
            password = secrets.token_urlsafe(9)
            generated = True
            if _write_initial_credentials(s.admin_user, password) is None:
                # 落盘失败（几乎等价于数据目录不可写）→ 回退默认口令，并强制首登改密
                password = s.admin_pass
                generated = False
        db.add(
            User(
                username=s.admin_user,
                password_hash=hash_password(password),
                role="admin",
                data_scope="all",
                must_change_password=1 if (s.admin_force_password_change or generated) else 0,
                session_version=1,
                is_active=1,
                created_at=now_iso(),
            )
        )
        db.commit()
        if generated:
            log.warning(
                "已创建管理员 %s，初始密码为随机生成，见 %s（0600），登录后请立即修改并删除该文件",
                s.admin_user,
                s.data_path / "first_boot.txt",
            )
        else:
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


def _apply_pending_restore_if_any() -> None:
    """启动最早期检查「待恢复」标记：若有则先替换 db/media/config，再走正常迁移。

    放在 run_migrations() 之前是刻意的——替换必须在数据库文件被打开/迁移之前完成。
    """
    from .services import backup as backup_svc

    if not backup_svc.has_pending_restore():
        return
    log.warning("检测到待恢复备份，开始执行数据恢复（替换数据库 / 媒体 / 设置）…")
    result = backup_svc.apply_pending_restore()
    log.warning("数据恢复完成：%s", result)


def main() -> None:
    configure_logging()
    s = get_settings()
    s.ensure_dirs()
    if s.secret_autogenerated:
        log.warning(
            "APP_SECRET_KEY 未显式配置：已自动生成并保存到 %s/.app_secret（0600）。"
            "多副本/多容器共享会话时请在环境变量里显式设置同一个值。",
            s.config_dir,
        )
    _apply_pending_restore_if_any()
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
