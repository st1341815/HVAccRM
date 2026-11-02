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


# --------------------------------------------------- 变更指纹 / 跳过重复备份
def _add_tables(fake_fs: "_FakeSettings", db=None) -> None:
    """在 fake 库上补出日志类与业务类表，用于指纹判定测试。

    engine 是 session 级共享内存库，前面用例可能改过 backup.dir，
    这里显式把保存目录钉到 fake 目录，避免用例间互相污染。
    """
    if db is not None:
        settings_svc.set_(db, "backup.dir", str(fake_fs.backup_dir))
        db.commit()
    conn = sqlite3.connect(fake_fs.db_path)
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action TEXT)")
    conn.execute("CREATE TABLE share_visits (id INTEGER PRIMARY KEY, who TEXT)")
    conn.execute("CREATE TABLE sms_codes (id INTEGER PRIMARY KEY, code TEXT)")
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()


def test_db_fingerprint_stable_for_same_data(fake_fs, tmp_path):
    """同一份数据重复计算，指纹必须完全一致（否则跳过永远不生效）。"""
    f1 = backup_svc._db_fingerprint(tmp_path)
    f2 = backup_svc._db_fingerprint(tmp_path)
    assert f1 == f2


def test_db_fingerprint_ignores_log_tables(fake_fs, tmp_path):
    """登录/分享/短信验证码这类日志表的变化，不算「数据变了」。"""
    _add_tables(fake_fs)
    before = backup_svc._db_fingerprint(tmp_path)
    conn = sqlite3.connect(fake_fs.db_path)
    conn.execute("INSERT INTO audit_log (action) VALUES ('login')")
    conn.execute("INSERT INTO share_visits (who) VALUES ('v1')")
    conn.execute("INSERT INTO sms_codes (code) VALUES ('123456')")
    conn.commit()
    conn.close()
    assert backup_svc._db_fingerprint(tmp_path) == before


def test_db_fingerprint_changes_on_business_data(fake_fs, tmp_path):
    """业务表（customers）变化必须让指纹改变，保证有变更当天一定备份。"""
    _add_tables(fake_fs)
    before = backup_svc._db_fingerprint(tmp_path)
    conn = sqlite3.connect(fake_fs.db_path)
    conn.execute("INSERT INTO customers (name) VALUES ('陶姐')")
    conn.commit()
    conn.close()
    assert backup_svc._db_fingerprint(tmp_path) != before


def test_media_fingerprint_tracks_add_and_remove(fake_fs):
    """媒体指纹：新增/删除文件要变，恢复原状要回到原值。"""
    base = backup_svc._media_fingerprint(fake_fs.media_dir)
    f = fake_fs.media_dir / "a.jpg"
    f.write_bytes(b"x" * 32)
    with_file = backup_svc._media_fingerprint(fake_fs.media_dir)
    assert with_file != base
    f.unlink()
    assert backup_svc._media_fingerprint(fake_fs.media_dir) == base


def test_run_backup_job_skips_when_unchanged(fake_fs, db):
    """第一次备份生成 zip；数据没变时第二次跳过；改了业务数据后再次生成。"""
    _add_tables(fake_fs, db)
    first = backup_svc.run_backup_job(db)
    assert first["skipped"] is False and first["file"]

    second = backup_svc.run_backup_job(db)
    assert second["skipped"] is True
    assert second["file"] is None
    # 跳过时不应产生新 zip
    assert len(list(fake_fs.backup_dir.glob("crm-backup-*.zip"))) == 1

    conn = sqlite3.connect(fake_fs.db_path)
    conn.execute("INSERT INTO customers (name) VALUES ('董小姐')")
    conn.commit()
    conn.close()

    third = backup_svc.run_backup_job(db)
    assert third["skipped"] is False
    assert len(list(fake_fs.backup_dir.glob("crm-backup-*.zip"))) == 2


def test_run_backup_job_log_only_changes_still_skip(fake_fs, db):
    """只产生登录日志时，自动备份仍应跳过（这是方案一的核心诉求）。"""
    _add_tables(fake_fs, db)
    backup_svc.run_backup_job(db)
    conn = sqlite3.connect(fake_fs.db_path)
    conn.execute("INSERT INTO audit_log (action) VALUES ('login')")
    conn.commit()
    conn.close()
    assert backup_svc.run_backup_job(db)["skipped"] is True


def test_run_backup_job_force_overrides_skip(fake_fs, db):
    """手动「立即备份」必须强制生成，不受去重影响。"""
    _add_tables(fake_fs, db)
    backup_svc.run_backup_job(db)
    forced = backup_svc.run_backup_job(db, force=True)
    assert forced["skipped"] is False and forced["file"]
    assert len(list(fake_fs.backup_dir.glob("crm-backup-*.zip"))) == 2


def test_last_fingerprint_recorded(fake_fs, db):
    """备份成功后应记录指纹与时间，供页面展示。"""
    _add_tables(fake_fs, db)
    assert backup_svc.last_fingerprint(db) == (None, None)
    backup_svc.run_backup_job(db)
    fp, at = backup_svc.last_fingerprint(db)
    assert fp and at


def test_db_fingerprint_excludes_own_metadata(fake_fs, db, tmp_path):
    """回归：指纹自身存在 app_settings，写指纹不能改变指纹（自我指涉会导致跳过永不生效）。"""
    _add_tables(fake_fs, db)
    before = backup_svc._db_fingerprint(tmp_path)
    settings_svc.set_(db, backup_svc.FINGERPRINT_SETTING_KEY, "deadbeef" * 8)
    settings_svc.set_(db, backup_svc.FINGERPRINT_TIME_KEY, "2026-11-02T10:00:00")
    db.commit()
    assert backup_svc._db_fingerprint(tmp_path) == before


def test_run_backup_job_skip_is_repeatable(fake_fs, db):
    """连续三次 run_backup_job：只有第一次真正打包，后两次都跳过。"""
    _add_tables(fake_fs, db)
    assert backup_svc.run_backup_job(db)["skipped"] is False
    assert backup_svc.run_backup_job(db)["skipped"] is True
    assert backup_svc.run_backup_job(db)["skipped"] is True
    assert len(list(fake_fs.backup_dir.glob("crm-backup-*.zip"))) == 1
