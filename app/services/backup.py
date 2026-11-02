"""全功能备份与导入：把数据库 + 媒体 + 设置打包为一份 zip；支持恢复到全新环境。

备份（`make_full_backup`）产物结构::

    crm-backup-YYYYMMDD-HHMMSS.zip
    ├── manifest.json        # 元信息（格式版本 / app 版本 / 内容清单）
    ├── crm.db               # 数据库一致性副本（SQLite online backup，兼容 WAL）
    ├── media/…              # 照片 / 缩略图 / 案例图
    └── config/deploy.env    # 设置（存在才含；含密钥——用户已确认）

导入（`schedule_restore` → 进程退出 → 容器 `restart: unless-stopped` 拉起 → `apply_pending_restore`）：
不直接覆盖正在使用的 db —— 先写「待恢复」标记并让进程退出，由容器重启在**启动早期**
执行替换；替换前把现有 db / media 改名留底（`pre-restore-<ts>`），失败可人工回退。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import get_settings
from . import settings as settings_svc

log = logging.getLogger("crm.backup")

FORMAT_VERSION = 1
ARCHIVE_PREFIX = "crm-backup-"
PENDING_FILENAME = ".pending_restore.json"
MANIFEST_NAME = "manifest.json"
DB_NAME = "crm.db"
MEDIA_DIR_NAME = "media"
CONFIG_DIR_NAME = "config"


# ------------------------------------------------------------------ 工具
def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_archive_path(backup_dir: Path, stamp: str) -> Path:
    """同秒内重复备份（连续点两次「立即备份」）不能互相覆盖，加 -2/-3 后缀。"""
    dest = backup_dir / f"{ARCHIVE_PREFIX}{stamp}.zip"
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = backup_dir / f"{ARCHIVE_PREFIX}{stamp}-{n}.zip"
        if not cand.exists():
            return cand
        n += 1


def _pending_path() -> Path:
    return get_settings().data_path / PENDING_FILENAME


def _resolve_backup_dir(db=None) -> Path:
    """备份保存目录：优先库内设置，回退环境变量默认；保证存在。"""
    d = None
    if db is not None:
        try:
            d = settings_svc.get_backup_config(db)["dir"]
        except Exception:  # pragma: no cover - 库未就绪时回退
            d = None
    p = Path(d or str(get_settings().backup_dir))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_snapshot(dest: Path) -> None:
    """SQLite 在线备份到 dest（兼容 WAL，无需停机）。"""
    src = sqlite3.connect(get_settings().db_path, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


# ------------------------------------------------------------------ 备份
def _scrub_secrets(db_file: Path) -> None:
    """从备份副本的 app_settings 里删除密钥类键（fail-closed：删不动就抛）。

    目前包含：短信服务商密钥、安全入口字符串（`gate.path`）。删除后该键回落默认空值，
    导入备份后需要重新在后台填一次。
    """
    from .sms import SECRET_SETTING_KEYS

    con = sqlite3.connect(db_file, timeout=30)
    try:
        has_table = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_settings'"
        ).fetchone()
        if not has_table:
            return  # 无 app_settings 表（老库/测试桩），无密钥可剔
        cur = con.execute(
            "DELETE FROM app_settings WHERE \"key\" IN (%s)" % ",".join("?" * len(SECRET_SETTING_KEYS)),
            SECRET_SETTING_KEYS,
        )
        con.commit()
        if cur.rowcount:
            log.info("备份副本已剔除 %s 条敏感设置（短信密钥/安全入口）", cur.rowcount)
    finally:
        con.close()


def make_full_backup(db=None) -> Path:
    """生成全量 zip（db + media + config），返回 zip 路径。"""
    s = get_settings()
    backup_dir = _resolve_backup_dir(db)
    stamp = _stamp()
    tmp_db = backup_dir / f".tmp-{stamp}.db"
    dest = _unique_archive_path(backup_dir, stamp)

    _db_snapshot(tmp_db)
    # 备份副本剔除短信密钥（13叔定案：服务商/密钥后台可改，但备份 zip 不携带）
    try:
        _scrub_secrets(tmp_db)
    except Exception:
        log.exception("备份剔除短信密钥失败——本次备份中止（宁可失败也不带密钥出库）")
        tmp_db.unlink(missing_ok=True)
        raise
    media_count = 0
    media_bytes = 0
    has_config = False
    config_file = Path(s.config_dir) / "deploy.env"

    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, DB_NAME)
            if s.media_dir.exists():
                for p in s.media_dir.rglob("*"):
                    if p.is_file():
                        zf.write(p, f"{MEDIA_DIR_NAME}/{p.relative_to(s.media_dir).as_posix()}")
                        media_count += 1
                        media_bytes += p.stat().st_size
            if config_file.is_file():
                zf.write(config_file, f"{CONFIG_DIR_NAME}/deploy.env")
                has_config = True
            manifest = {
                "format_version": FORMAT_VERSION,
                "kind": "full",
                "app_version": __version__,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "db_bytes": tmp_db.stat().st_size,
                "media_files": media_count,
                "media_bytes": media_bytes,
                "has_config": has_config,
            }
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        tmp_db.unlink(missing_ok=True)

    log.info("全量备份已生成 %s（媒体 %d 个 / %.1f MB）", dest, media_count, media_bytes / 1048576)
    return dest


def list_backups(db=None) -> list[dict]:
    backup_dir = _resolve_backup_dir(db)
    out: list[dict] = []
    for f in sorted(backup_dir.glob("crm-*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not f.is_file():
            continue
        st = f.stat()
        out.append(
            {
                "name": f.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "is_zip": f.suffix == ".zip",
            }
        )
    return out


def prune_backups(db=None, keep: int | None = None) -> int:
    """只清理新版 zip 备份；旧版 crm-*.db 备份保留不删（避免误删历史）。"""
    if keep is None:
        keep = settings_svc.get_backup_config(db)["keep"] if db is not None else 30
    backup_dir = _resolve_backup_dir(db)
    files = sorted(backup_dir.glob(f"{ARCHIVE_PREFIX}*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError as exc:  # pragma: no cover
            log.warning("删除旧备份失败 %s: %s", f, exc)
    return removed


def run_backup_job(db=None) -> dict:
    path = make_full_backup(db)
    removed = prune_backups(db)
    return {"file": path.name, "size": path.stat().st_size, "pruned": removed}


# ------------------------------------------------------------------ 变更判定
# 这些表「不参与变更判定」：它们跟随登录 / 分享访问 / 发码自然增长，
# 业务数据没动也会天天变。若计入指纹，跳过逻辑永远不会触发。
# 注意：它们**仍会照常打进备份包**，只是不作为「是否值得出新包」的依据。
FINGERPRINT_EXCLUDED_TABLES = frozenset({"audit_log", "share_visits", "sms_codes"})
FINGERPRINT_SETTING_KEY = "backup.last_fingerprint"
FINGERPRINT_TIME_KEY = "backup.last_fingerprint_at"
# 指纹自身存在 app_settings 里——若不排除，写指纹会改变指纹，形成自我指涉，跳过永远不成立。
FINGERPRINT_EXCLUDED_SETTINGS = (FINGERPRINT_SETTING_KEY, FINGERPRINT_TIME_KEY)


def _db_fingerprint(tmp_dir: Path) -> str:
    """数据库内容指纹：排除 FINGERPRINT_EXCLUDED_TABLES 后，对规范化副本算 sha256。

    为什么不直接 hash 主库文件：WAL 模式下即使只改一行，文件布局也可能变；
    反之 checkpoint / VACUUM 即使数据未变也会重写文件字节。用 `VACUUM INTO`
    先重建一份规范化副本，同一份逻辑数据永远得到同样的字节（已实测三次一致）。
    """
    tmp = tmp_dir / ".fingerprint.db"
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(get_settings().db_path, timeout=30)
    try:
        dst = sqlite3.connect(str(tmp), timeout=30)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    try:
        con = sqlite3.connect(str(tmp), timeout=30)
        try:
            existing = {
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for t in FINGERPRINT_EXCLUDED_TABLES & existing:
                con.execute(f'DELETE FROM "{t}"')
            # 指纹自身的存储记录也排除，否则「写指纹」本身就会改变指纹（自我指涉）
            if "app_settings" in existing:
                con.execute(
                    'DELETE FROM app_settings WHERE "key" IN (%s)'
                    % ",".join("?" * len(FINGERPRINT_EXCLUDED_SETTINGS)),
                    FINGERPRINT_EXCLUDED_SETTINGS,
                )
            con.commit()
            con.execute("VACUUM")  # 让删除后的空页也规范化掉
        finally:
            con.close()
        h = hashlib.sha256()
        with open(tmp, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    finally:
        tmp.unlink(missing_ok=True)


def _media_fingerprint(media_dir: Path) -> str:
    """媒体内容指纹：(相对路径, 大小, mtime_ns) 排序后哈希。

    照片上传后不再被改写，这个判据足够；不读文件内容，千级文件也是毫秒级。
    """
    h = hashlib.sha256()
    if not media_dir.exists():
        return h.hexdigest()
    entries: list[tuple[str, int, int]] = []
    for p in media_dir.rglob("*"):
        if p.is_file():
            st = p.stat()
            entries.append((p.relative_to(media_dir).as_posix(), st.st_size, st.st_mtime_ns))
    entries.sort()
    for rel, size, mtime in entries:
        h.update(f"{rel}\0{size}\0{mtime}\n".encode("utf-8"))
    return h.hexdigest()


def compute_fingerprint(tmp_dir: Path | None = None) -> str:
    """业务数据 + 媒体的联合指纹。tmp_dir 供测试注入。"""
    s = get_settings()
    media_dir = Path(s.media_dir)
    if tmp_dir is None:
        with tempfile.TemporaryDirectory(prefix="hva-fp-") as d:
            db_fp = _db_fingerprint(Path(d))
    else:
        db_fp = _db_fingerprint(Path(tmp_dir))
    media_fp = _media_fingerprint(media_dir)
    return hashlib.sha256(f"{db_fp}:{media_fp}".encode()).hexdigest()


def last_fingerprint(db=None) -> tuple[str | None, str | None]:
    """返回 (上次备份时的指纹, 时间)。未记录过则为 (None, None)。"""
    if db is None:
        return None, None
    try:
        return (
            settings_svc.get(db, FINGERPRINT_SETTING_KEY) or None,
            settings_svc.get(db, FINGERPRINT_TIME_KEY) or None,
        )
    except Exception:  # pragma: no cover - 库未就绪
        return None, None


def run_backup_job(db=None, force: bool = False) -> dict:
    """自动备份入口：业务数据与媒体都没变 → 跳过打包（force=True 则强制全量）。"""
    result: dict[str, Any] = {"skipped": False}
    if not force:
        try:
            current = compute_fingerprint()
        except Exception:
            log.exception("计算备份指纹失败——本次按「有变化」处理，照常备份")
            current = None
        if current is not None:
            prev, prev_at = last_fingerprint(db)
            if prev and prev == current:
                log.info("数据自上次备份以来无变化，跳过本次打包（上次 %s）", prev_at)
                return {
                    "skipped": True,
                    "since": prev_at,
                    "pruned": 0,
                    "file": None,
                    "size": 0,
                }
    else:
        current = None

    path = make_full_backup(db)
    removed = prune_backups(db)
    # 备份成功后才更新指纹；记录本次备份时刻对应的数据版本
    if current is None:
        try:
            current = compute_fingerprint()
        except Exception:
            log.exception("备份后计算指纹失败——下次将按「有变化」处理")
    if current and db is not None:
        try:
            settings_svc.set_(db, FINGERPRINT_SETTING_KEY, current)
            settings_svc.set_(
                db, FINGERPRINT_TIME_KEY, datetime.now().isoformat(timespec="seconds")
            )
            db.commit()
        except Exception:
            log.exception("记录备份指纹失败（不影响备份本身）")
    result.update(
        {"file": path.name, "size": path.stat().st_size, "pruned": removed, "skipped": False}
    )
    return result


# ------------------------------------------------------------------ 导入
def validate_archive(zip_path: Path) -> dict[str, Any]:
    """校验 zip 是否为合法备份；返回 manifest。不合法抛 ValueError。"""
    if not zip_path.is_file():
        raise ValueError("备份文件不存在")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            if MANIFEST_NAME not in names:
                raise ValueError("不是有效的 HVAccRM 备份（缺少 manifest.json）")
            if DB_NAME not in names:
                raise ValueError("备份缺少数据库文件 crm.db")
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise ValueError("文件不是有效的 zip 压缩包") from exc
    if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise ValueError(
            f"备份格式版本 {manifest.get('format_version')} 高于当前支持的 {FORMAT_VERSION}，请先升级系统"
        )
    return manifest


def read_manifest(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))


def schedule_restore(zip_path: Path, restore_config: bool = True) -> None:
    """写「待恢复」标记。调用方随后应让进程退出，由容器重启触发真正的替换。"""
    payload = {
        "zip": str(Path(zip_path).resolve()),
        "restore_config": bool(restore_config),
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    }
    _pending_path().write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.warning("已登记待恢复任务（重启后执行）：%s", payload["zip"])


def has_pending_restore() -> bool:
    return _pending_path().is_file()


def apply_pending_restore() -> dict[str, Any] | None:
    """启动早期执行：替换 db / media /（可选）config。返回结果摘要，无任务则 None。"""
    p = _pending_path()
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("待恢复标记损坏，跳过：%s", exc)
        p.unlink(missing_ok=True)
        return None

    s = get_settings()
    zip_path = Path(payload.get("zip", ""))
    restore_config = bool(payload.get("restore_config", True))
    stamp = _stamp()
    result: dict[str, Any] = {"zip": zip_path.name, "db": False, "media": False, "config": False, "stamp": stamp, "error": None}

    if not zip_path.is_file():
        result["error"] = "zip 文件不存在"
        p.unlink(missing_ok=True)
        return result
    try:
        validate_archive(zip_path)
    except ValueError as exc:
        result["error"] = str(exc)
        p.unlink(missing_ok=True)
        return result

    # 释放数据库连接
    try:
        from ..db import engine

        engine.dispose()
    except Exception:  # pragma: no cover
        pass

    staging = s.data_path / f".restore-staging-{stamp}"
    keep_dir = s.backup_dir / f"pre-restore-{stamp}"
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
        keep_dir.mkdir(parents=True, exist_ok=True)

        # --- 数据库 ---
        new_db = staging / DB_NAME
        if new_db.is_file():
            cur_db = Path(s.db_path)
            if cur_db.exists():
                shutil.copy2(cur_db, keep_dir / cur_db.name)
            for suffix in ("-wal", "-shm"):  # 清掉旧库残留，避免混用
                side = Path(str(cur_db) + suffix)
                if side.exists():
                    side.unlink()
            shutil.copy2(new_db, cur_db)
            result["db"] = True

        # --- 媒体 ---
        new_media = staging / MEDIA_DIR_NAME
        if new_media.is_dir():
            s.media_dir.parent.mkdir(parents=True, exist_ok=True)
            if s.media_dir.exists():
                shutil.move(str(s.media_dir), str(keep_dir / MEDIA_DIR_NAME))
            shutil.move(str(new_media), str(s.media_dir))
            result["media"] = True

        # --- 设置（config/deploy.env）---
        if restore_config:
            new_cfg = staging / CONFIG_DIR_NAME / "deploy.env"
            tgt = Path(s.config_dir) / "deploy.env"
            if new_cfg.is_file():
                try:
                    if tgt.exists():
                        shutil.copy2(tgt, keep_dir / "deploy.env")
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(new_cfg, tgt)
                    result["config"] = True
                except OSError as exc:
                    log.warning("恢复 config 失败（挂载可能只读）：%s", exc)
    except Exception as exc:  # pragma: no cover - 出错保留现场
        result["error"] = str(exc)
        log.exception("恢复过程出错：%s", exc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        p.unlink(missing_ok=True)

    log.warning("备份恢复完成：%s", result)
    return result
