"""登录页短信验证码提示文案：三种失效情形要给出不同的、可指导操作的提示。

背景（2026-11-02 线上事故）：用户跳过「获取验证码」直接输入验证码提交，
拿到的是「验证码不存在或已过期」，看不出要先去点按钮，误判成"发送失败"。
现在三种情形必须区分：
  1) 从未发过码        → 提示先点「获取验证码」
  2) 发过但已过期/失效 → 提示重新获取
  3) 有有效记录但码错  → 提示码不正确
"""
from __future__ import annotations

import pytest

from app.models import SmsCode, User
from app.security import hash_password
from app.services import sms as sms_svc


@pytest.fixture
def user(db):
    u = User(
        username="hintuser", full_name="提示测试", password_hash=hash_password("Passw0rd123"),
        role="sales", data_scope="self", is_active=1, mobile="13912345678",
    )
    db.add(u)
    db.flush()
    return u


def _make_code(db, user, code="123456", expires_delta_seconds=300, used=False, attempts=0):
    """直接落一条验证码记录（绕过发送，专注测校验分支）。"""
    from datetime import datetime, timedelta

    now = datetime.now()
    row = SmsCode(
        user_id=user.id,
        mobile=user.mobile,
        code_hash=sms_svc._code_hash(code),
        scene="login",
        attempts=attempts,
        expires_at=(now + timedelta(seconds=expires_delta_seconds)).strftime("%Y-%m-%d %H:%M:%S"),
        created_ip="192.168.1.2",
        created_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if used:
        row.used_at = now.strftime("%Y-%m-%d %H:%M:%S")
    db.add(row)
    db.flush()
    return row


def test_never_sent_hint_tells_user_to_request_first(db, user):
    """从未发过码 → 明确提示先点「获取验证码」。"""
    ok, err = sms_svc.verify_code(db, user, "123456")
    assert ok is False
    assert "获取验证码" in err, f"提示应引导用户去点获取按钮，实际: {err}"
    assert "尚未获取" in err


def test_expired_hint_asks_to_resend(db, user):
    """发过但已过期 → 提示重新获取（不应说"尚未获取"）。"""
    _make_code(db, user, expires_delta_seconds=-10)
    ok, err = sms_svc.verify_code(db, user, "123456")
    assert ok is False
    assert "过期" in err
    assert "重新获取" in err
    assert "尚未获取" not in err, "已发过码时不应提示'尚未获取'"


def test_used_hint_asks_to_resend(db, user):
    """已使用过 → 提示重新获取。"""
    _make_code(db, user, used=True)
    ok, err = sms_svc.verify_code(db, user, "123456")
    assert ok is False
    assert "重新获取" in err


def test_wrong_code_hint_says_incorrect(db, user):
    """有有效记录但码不对 → 提示码不正确，不是"没发过"。"""
    _make_code(db, user, code="999999")
    ok, err = sms_svc.verify_code(db, user, "123456")
    assert ok is False
    assert "不正确" in err
    assert "获取验证码" not in err, "有有效码时不该提示去获取"


def test_correct_code_passes(db, user):
    """正确码通过，且记录被标记已使用。"""
    row = _make_code(db, user, code="246810")
    ok, err = sms_svc.verify_code(db, user, "246810")
    assert ok is True, err
    db.refresh(row)
    assert row.used_at is not None


def test_three_wrong_attempts_invalidate(db, user):
    """错 3 次后作废，第 4 次提示重新获取。"""
    _make_code(db, user, code="999999")
    for _ in range(3):
        ok, _ = sms_svc.verify_code(db, user, "111111")
        assert ok is False
    ok, err = sms_svc.verify_code(db, user, "111111")
    assert ok is False
    assert "重新获取" in err


def test_hint_does_not_leak_code(db, user):
    """提示文案绝不能泄露验证码或哈希。"""
    _make_code(db, user, code="135790")
    _, err = sms_svc.verify_code(db, user, "000000")
    assert "135790" not in err
    assert "$" not in err
