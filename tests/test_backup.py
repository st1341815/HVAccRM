"""备份与恢复：设置读写、全量 zip 生成、zip 校验、待恢复流程。"""
import json
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from app.services import backup as backup_svc
from app.services import settings as settings_svc


class _FakeSettings:
    """指向 tmp_path 的假 settings，避免碰真实 /data。"""

    def __init__(self, root: Path):
        self.data_path = root
        self.db_path = str(root / "crm.db")
        self.media_dir = root / "media"
        self.backup_dir = root / "backups"
        self.config_dir = root / "config"

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.media_dir, self.backup_dir, self.config_dir):
            p.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def fake_fs(tmp_path, monkeypatch):
    fs = _FakeSettings(tmp_path)
    fs.ensure_dirs()
    conn = sqlite3.connect(fs.db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(backup_svc, "get_settings", lambda: fs)
    return fs


# ------------------------------------------------------------- 设置服务
def test_settings_defaults(db):
    cfg = settings_svc.get_backup_config(db)
    assert cfg["dir"]
    assert cfg["keep"] == 30
    assert cfg["daily_enabled"] is True
    assert cfg["weekly_enabled"] is True


def test_settings_save_and_read(db):
    settings_svc.save_backup_config(
        db,
        {
            "backup.dir": "/tmp/xx",
            "backup.keep": "7",
            "backup.daily.enabled": "0",
            "backup.daily.hour": "5",
            "backup.weekly.enabled": "1",
            "backup.weekly.dow": "3",
            "backup.weekly.hour": "6",
        },
    )
    db.flush()
    cfg = settings_svc.get_backup_config(db)
    assert cfg["dir"] == "/tmp/xx"
    assert cfg["keep"] == 7
    assert cfg["daily_enabled"] is False
    assert cfg["daily_hour"] == 5
    assert cfg["weekly_dow"] == 3
    assert cfg["weekly_hour"] == 6


def test_settings_clamp_invalid_values(db):
    settings_svc.save_backup_config(db, {"backup.keep": "99999", "backup.daily.hour": "77"})
    db.flush()
    cfg = settings_svc.get_backup_config(db)
    assert cfg["keep"] == 999        # 收敛到上限
    assert cfg["daily_hour"] == 23   # 超出 0-23 → clamp 到 23（UI 用 select 本就限定范围）


def test_settings_non_numeric_falls_back_to_default(db):
    """完全非数字的值 → 回退默认（区别于超范围 clamp）。"""
    settings_svc.save_backup_config(db, {"backup.daily.hour": "abc"})
    db.flush()
    cfg = settings_svc.get_backup_config(db)
    assert cfg["daily_hour"] == 3


# ------------------------------------------------------------- 全量备份
def test_make_full_backup_contains_db_media_config(fake_fs):
    (fake_fs.media_dir / "sub").mkdir(parents=True, exist_ok=True)
    (fake_fs.media_dir / "sub" / "a.jpg").write_bytes(b"JPEGDATA")
    (fake_fs.config_dir / "deploy.env").write_text("APP_SECRET_KEY=x\n", encoding="utf-8")

    path = backup_svc.make_full_backup()
    assert path.is_file() and path.suffix == ".zip"
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        assert "crm.db" in names
        assert "manifest.json" in names
        assert "media/sub/a.jpg" in names
        assert "config/deploy.env" in names
        m = json.loads(zf.read("manifest.json"))
        assert m["format_version"] == backup_svc.FORMAT_VERSION
        assert m["media_files"] == 1
        assert m["has_config"] is True


def test_make_full_backup_handles_no_media(fake_fs):
    """没有媒体文件 / 没有 config 时也要能正常产出（不报错）。"""
    path = backup_svc.make_full_backup()
    assert path.is_file()
    with zipfile.ZipFile(path) as zf:
        m = json.loads(zf.read("manifest.json"))
        assert m["media_files"] == 0
        assert m["has_config"] is False


def test_prune_backups_keeps_newest(fake_fs):
    for i in range(5):
        p = fake_fs.backup_dir / f"crm-backup-2026010{i}-000000.zip"
        p.write_bytes(b"x")
        time.sleep(0.01)
    removed = backup_svc.prune_backups(None, keep=3)
    remaining = sorted(p.name for p in fake_fs.backup_dir.glob("crm-backup-*.zip"))
    assert removed == 2
    assert len(remaining) == 3


# ------------------------------------------------------------- 校验
def test_validate_archive_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError):
        backup_svc.validate_archive(bad)


def test_validate_archive_rejects_missing_manifest(tmp_path):
    z = tmp_path / "nomanifest.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("crm.db", b"")
    with pytest.raises(ValueError) as e:
        backup_svc.validate_archive(z)
    assert "manifest" in str(e.value)


def test_validate_archive_rejects_newer_format(tmp_path):
    z = tmp_path / "future.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 999}))
        zf.writestr("crm.db", b"")
    with pytest.raises(ValueError) as e:
        backup_svc.validate_archive(z)
    assert "版本" in str(e.value)


# ------------------------------------------------------------- 恢复流程
def test_schedule_restore_writes_marker(fake_fs, tmp_path):
    z = tmp_path / "backup.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 1}))
        zf.writestr("crm.db", b"")
    backup_svc.schedule_restore(z, restore_config=True)
    assert backup_svc.has_pending_restore()
    payload = json.loads((fake_fs.data_path / backup_svc.PENDING_FILENAME).read_text())
    assert payload["zip"].endswith("backup.zip")
    assert payload["restore_config"] is True


def test_apply_pending_restore_replaces_db_and_clears_marker(fake_fs, tmp_path):
    new_db = tmp_path / "new.db"
    conn = sqlite3.connect(new_db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('RESTORED')")
    conn.commit()
    conn.close()

    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 1, "kind": "full"}))
        zf.write(new_db, "crm.db")

    backup_svc.schedule_restore(z, restore_config=False)
    result = backup_svc.apply_pending_restore()
    assert result is not None and result["db"] is True
    assert not backup_svc.has_pending_restore()

    conn = sqlite3.connect(fake_fs.db_path)
    v = conn.execute("SELECT v FROM t").fetchone()[0]
    conn.close()
    assert v == "RESTORED"


def test_apply_pending_restore_keeps_backup_on_bad_zip(fake_fs, tmp_path):
    """坏 zip：不替换、清标记、不抛异常（进程能继续启动）。"""
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"garbage")
    backup_svc.schedule_restore(bad, restore_config=False)
    result = backup_svc.apply_pending_restore()
    assert result is not None
    assert result["db"] is False
    assert result["error"]
    assert not backup_svc.has_pending_restore()
