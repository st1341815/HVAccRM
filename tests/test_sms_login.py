"""外网短信验证码登录：发码 / 验码 / 限流 / 内外网切换（services/sms + auth 路由分支）。

验证码明文只在「发码时捕获日志」可观察——issue_code 不返回明文，这里通过
monkeypatch `_send` 拦截 code 来测 verify_code（等价于从日志读码）。
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.models import SmsCode, User
from app.security import hash_password
from app.services import settings as settings_svc
from app.services import sms as sms_svc

EXT = ["crm.example.com"]


@pytest.fixture
def sms_user(db):
    u = User(
        username="smsuser", full_name="短信测试", password_hash=hash_password("Passw0rd123"),
        role="sales", data_scope="self", is_active=1, mobile="13912345678",
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def no_mobile_user(db):
    u = User(
        username="nomobile", password_hash=hash_password("Passw0rd123"),
        role="sales", data_scope="self", is_active=1,
    )
    db.add(u)
    db.flush()
    return u


def enable_sms(db, provider="console"):
    settings_svc.set_(db, "sms.enabled", "1")
    settings_svc.set_(db, "sms.provider", provider)
    db.flush()  # autoflush=False：写入后须 flush，get_sms_config 的 select 才能读到


def make_request(client_ip="192.168.1.2", xff=None, host="192.168.1.8:8090", path="/login", method="POST"):
    headers = [(b"host", host.encode())]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request(
        {
            "type": "http", "method": method, "path": path, "raw_path": path.encode(),
            "root_path": "", "scheme": "http", "server": ("192.168.1.8", 8090),
            "client": (client_ip, 54321), "headers": headers, "query_string": b"",
        }
    )


@pytest.fixture
def capture_send(monkeypatch):
    """拦截发送，记录验证码明文（console 模式等价行为）。"""
    sent: list[str] = []
    from app.services.sms import _send_console

    def fake(provider, creds, mobile, code):
        sent.append(code)
        return _send_console(mobile, code)
    monkeypatch.setattr("app.services.sms._send", fake)
    return sent


# ------------------------------------------------------------------ 发码
def test_issue_and_verify_roundtrip(db, sms_user, capture_send):
    enable_sms(db)
    ok, err = sms_svc.issue_code(db, sms_user, "1.1.1.1")
    assert ok, err
    assert len(capture_send) == 1 and len(capture_send[0]) == 6
    ok, err = sms_svc.verify_code(db, sms_user, capture_send[0])
    assert ok, err


def test_issue_requires_mobile(db, no_mobile_user, capture_send):
    enable_sms(db)
    ok, err = sms_svc.issue_code(db, no_mobile_user, "1.2.3.4")
    assert not ok and "未绑定" in err


def test_code_one_time(db, sms_user, capture_send):
    enable_sms(db)
    sms_svc.issue_code(db, sms_user, "1.2.3.4")
    code = capture_send[0]
    ok, _ = sms_svc.verify_code(db, sms_user, code)
    assert ok
    ok, err = sms_svc.verify_code(db, sms_user, code)  # 重放
    assert not ok


def test_code_not_stored_plaintext(db, sms_user, capture_send):
    enable_sms(db)
    sms_svc.issue_code(db, sms_user, "1.2.3.4")
    row = db.scalars(
        __import__("sqlalchemy").select(SmsCode).where(SmsCode.user_id == sms_user.id)
    ).first()
    assert capture_send[0] not in row.code_hash
    assert ":" in row.code_hash  # salt:digest


def test_code_wrong_3_times_invalidates(db, sms_user, capture_send):
    enable_sms(db)
    sms_svc.issue_code(db, sms_user, "1.2.3.4")
    for i in range(3):
        ok, _ = sms_svc.verify_code(db, sms_user, "000000")
        assert not ok
    ok, err = sms_svc.verify_code(db, sms_user, capture_send[0])  # 正确码也已作废
    assert not ok


def test_code_bound_to_user(db, sms_user, no_mobile_user, capture_send):
    """A 的码不能登 B（B 无手机发不了码，这里直接构造 B 的行验证 verify 按 user 隔离）"""
    enable_sms(db)
    sms_svc.issue_code(db, sms_user, "1.2.3.4")
    no_mobile_user.mobile = "13900000000"
    db.flush()
    ok, err = sms_svc.verify_code(db, no_mobile_user, capture_send[0])
    assert not ok


def test_cooldown(db, sms_user, capture_send):
    enable_sms(db)
    ok, _ = sms_svc.issue_code(db, sms_user, "1.2.3.4")
    assert ok
    ok, err = sms_svc.issue_code(db, sms_user, "1.2.3.4")
    assert not ok and "频繁" in err


def test_daily_limit_user(db, sms_user, capture_send):
    settings_svc.set_(db, "sms.enabled", "1")
    settings_svc.set_(db, "sms.provider", "console")
    settings_svc.set_(db, "sms.daily_limit_per_user", "2")
    settings_svc.set_(db, "sms.send_cooldown", "30")
    db.flush()
    # 造两条「今天清晨」的已发送记录：越过冷却（>30s），又计入今日限额
    from datetime import date, datetime, timedelta
    old = f"{date.today().isoformat()} 00:00:10"
    for _ in range(2):
        db.add(SmsCode(
            user_id=sms_user.id, mobile=sms_user.mobile, code_hash="x:y",
            scene="login", attempts=0,
            expires_at=(datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
            created_ip="1.2.3.4", created_at=old,
        ))
    db.flush()
    ok, err = sms_svc.issue_code(db, sms_user, "1.2.3.4")
    assert not ok and "上限" in err


# ------------------------------------------------------------------ 配置读取
def test_get_sms_config_defaults(db):
    cfg = settings_svc.get_sms_config(db)
    assert cfg["enabled"] is False
    assert cfg["provider"] == "console"
    assert cfg["external_domains"] == ["crm.example.com"]
    assert cfg["code_ttl_seconds"] == 300


def test_invalid_provider_falls_back_console(db):
    settings_svc.set_(db, "sms.provider", "hacker")
    db.flush()
    cfg = settings_svc.get_sms_config(db)
    assert cfg["provider"] == "console"


# ------------------------------------------------------------------ 路由判定分支
def test_sms_required_internal_off(db):
    """总开关关：内外网都不要求验证码"""
    req = make_request()
    from app.auth import _sms_required
    assert _sms_required(req, db) is False


def test_sms_required_internal_on(db):
    """开关开 + 内网请求 → 不要求"""
    enable_sms(db)
    from app.auth import _sms_required
    assert _sms_required(make_request(), db) is False


def test_sms_required_external_on(db):
    """开关开 + 可信代理 + XFF 公网 → 要求"""
    enable_sms(db)
    from app.auth import _sms_required
    req = make_request(client_ip="192.168.1.6", xff="1.1.1.1")
    assert _sms_required(req, db) is True


def test_sms_required_forged_xff_untrusted(db):
    """开关开 + 不可信来源伪造内网 XFF → 仍按直连公网判外网（核心安全用例）"""
    enable_sms(db)
    from app.auth import _sms_required
    req = make_request(client_ip="1.1.1.1", xff="192.168.1.99")
    assert _sms_required(req, db) is True


# ------------------------------------------------------------------ 备份剔除密钥
def test_backup_scrubs_sms_secrets(tmp_path):
    """备份剔除短信密钥（文件库，便于 SQLite 在线备份到副本再 scrb）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    dbfile = tmp_path / "test.db"
    eng = create_engine(f"sqlite:///{dbfile}")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)()

    settings_svc.set_(db, "sms.enabled", "1")
    settings_svc.set_(db, "sms.provider", "zhenzi")  # 非密钥键应保留
    settings_svc.set_(db, "sms.zhenzi_secret", "topsecret-value-123")
    settings_svc.set_(db, "sms.tencent_skey", "tencent-key-456")
    settings_svc.set_(db, "sms.aliyun_sk", "aliyun-key-789")
    db.commit()

    import sqlite3
    from app.services.backup import _scrub_secrets
    snap = tmp_path / "snap.db"
    src = sqlite3.connect(str(dbfile))
    dst = sqlite3.connect(str(snap))
    src.backup(dst)
    dst.close()
    src.close()

    _scrub_secrets(snap)

    con = sqlite3.connect(str(snap))
    keys = {k for (k,) in con.execute('select "key" from app_settings where "key" like \'sms.%\'')}
    con.close()
    db.close()
    assert "sms.zhenzi_secret" not in keys
    assert "sms.tencent_skey" not in keys
    assert "sms.aliyun_sk" not in keys
    assert "sms.provider" in keys  # 非密钥配置保留
