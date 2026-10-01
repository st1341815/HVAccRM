"""备份：SQLite 在线备份 + 保留最近 N 份。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("crm.backup")


def make_backup() -> Path:
    s = get_settings()
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = s.backup_dir / f"crm-{stamp}.db"
    src_conn = sqlite3.connect(s.db_path, timeout=30)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)  # 在线备份，兼容 WAL
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    log.info("数据库已备份至 %s", dest)
    return dest


def prune_backups(keep: int | None = None) -> int:
    s = get_settings()
    keep = keep or s.backup_keep
    files = sorted(s.backup_dir.glob("crm-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError as exc:  # pragma: no cover
            log.warning("删除旧备份失败 %s: %s", f, exc)
    return removed


def list_backups() -> list[dict]:
    s = get_settings()
    out = []
    for f in sorted(s.backup_dir.glob("crm-*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append(
            {
                "name": f.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return out


def run_backup_job() -> dict:
    path = make_backup()
    removed = prune_backups()
    return {"file": path.name, "pruned": removed}
