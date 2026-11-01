"""自建短信网关（sms-multi）provider 测试。

覆盖：签名算法正确性、请求构造、各类错误映射、配置缺失拦截。
用 monkeypatch 替换 urlopen，不产生真实网络请求。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import urllib.error

import pytest

from app.services import sms as sms_svc


class _FakeResp(io.BytesIO):
    """最小化的 urlopen 返回值替身。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://gw/openapi/v1/sms/send",
        code=code,
        msg=f"HTTP {code}",
        hdrs=None,
        fp=io.BytesIO(json.dumps(payload).encode()),
    )


CREDS = {
    "multisms_base": "http://192.168.11.21:8080",
    "multisms_ak": "ak_test123",
    "multisms_sk": "sk_secret456",
    "multisms_tpl": "verify_code",
}


# ---------------------------------------------------------------- 签名
def test_signature_matches_gateway_algorithm(monkeypatch):
    """签名必须与网关侧 SignRequest 完全一致，否则网关会拒。"""
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["req"] = req
        return _FakeResp(json.dumps({"success": True, "code": "OK"}).encode())

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)

    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is True, err

    req = captured["req"]
    body = req.data
    ts = req.get_header("X-timestamp")
    nonce = req.get_header("X-nonce")
    sig = req.get_header("X-signature")
    ak = req.get_header("X-api-key")

    assert ak == CREDS["multisms_ak"]
    assert req.get_method() == "POST"
    assert req.full_url == CREDS["multisms_base"] + "/openapi/v1/sms/send"

    # 独立重算签名
    body_hash = hashlib.sha256(body).hexdigest()
    sts = "\n".join(["POST", "/openapi/v1/sms/send", "", body_hash, ts, nonce])
    expected = hmac.new(
        CREDS["multisms_sk"].encode(), sts.encode(), hashlib.sha256
    ).hexdigest()
    assert sig == expected


def test_body_shape(monkeypatch):
    """请求体结构必须符合网关约定。"""
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["req"] = req
        return _FakeResp(json.dumps({"success": True}).encode())

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    sms_svc._send_multisms(CREDS, "13900139000", "654321")

    payload = json.loads(captured["req"].data.decode())
    assert payload == {
        "phone": "13900139000",
        "template": "verify_code",
        "params": {"code": "654321"},
    }


def test_nonce_unique_per_call(monkeypatch):
    """每次调用的 nonce 必须不同（网关据此防重放）。"""
    nonces = []

    def fake_urlopen(req, timeout=0):
        nonces.append(req.get_header("X-nonce"))
        return _FakeResp(json.dumps({"success": True}).encode())

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    sms_svc._send_multisms(CREDS, "13800138000", "111111")
    sms_svc._send_multisms(CREDS, "13800138000", "222222")

    assert len(nonces) == 2
    assert nonces[0] != nonces[1]
    assert all(len(n) == 32 for n in nonces)


# ---------------------------------------------------------------- 配置校验
@pytest.mark.parametrize(
    "missing,expect",
    [
        ("multisms_base", "地址"),
        ("multisms_ak", "AccessKey"),
        ("multisms_sk", "SecretKey"),
        ("multisms_tpl", "模板编码"),
    ],
)
def test_missing_config_rejected(missing, expect):
    creds = dict(CREDS)
    creds[missing] = ""
    ok, err = sms_svc._send_multisms(creds, "13800138000", "123456")
    assert ok is False
    assert expect in err


def test_trailing_slash_stripped(monkeypatch):
    """网关地址末尾带斜杠不应产生 //。"""
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return _FakeResp(json.dumps({"success": True}).encode())

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    creds = dict(CREDS, multisms_base="http://192.168.11.21:8080/")
    sms_svc._send_multisms(creds, "13800138000", "123456")
    assert captured["url"] == "http://192.168.11.21:8080/openapi/v1/sms/send"


# ---------------------------------------------------------------- 错误映射
def test_http_401_auth_failed(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise _http_error(401, {"success": False, "message": "签名校验失败"})

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "鉴权失败" in err
    assert "签名校验失败" in err


def test_http_429_rate_limited(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise _http_error(429, {"success": False, "message": "该手机号发送过于频繁"})

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "过于频繁" in err


def test_http_503_device_offline(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise _http_error(503, {"success": False, "message": "设备不可用"})

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "设备当前不可用" in err


def test_http_400_business_error(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise _http_error(400, {"success": False, "message": "模板不存在: verify_code"})

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "模板不存在" in err


def test_connection_error(monkeypatch):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "无法连接短信网关" in err


def test_success_false_in_body(monkeypatch):
    """HTTP 200 但 success=false 也要判失败。"""
    def fake_urlopen(req, timeout=0):
        return _FakeResp(
            json.dumps({"success": False, "message": "手机号格式非法"}).encode()
        )

    monkeypatch.setattr(sms_svc.urllib.request, "urlopen", fake_urlopen)
    ok, err = sms_svc._send_multisms(CREDS, "13800138000", "123456")
    assert ok is False
    assert "手机号格式非法" in err


# ---------------------------------------------------------------- 路由接入
def test_provider_registered_in_labels():
    assert "multisms" in sms_svc.PROVIDER_LABELS
    assert "自建短信网关" in sms_svc.PROVIDER_LABELS["multisms"]


def test_secret_key_registered_for_backup_strip():
    """备份必须剔除网关 SecretKey，避免明文进备份包。"""
    assert "sms.multisms_sk" in sms_svc.SECRET_SETTING_KEYS


def test_dispatch_routes_to_multisms(monkeypatch):
    """_send() 按 provider 名正确分发。"""
    called = {}

    def fake(creds, mobile, code):
        called["hit"] = (mobile, code)
        return True, ""

    monkeypatch.setattr(sms_svc, "_send_multisms", fake)
    ok, _ = sms_svc._send("multisms", CREDS, "13800138000", "999999")
    assert ok is True
    assert called["hit"] == ("13800138000", "999999")
