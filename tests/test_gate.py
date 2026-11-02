"""安全入口（外网门牌）判定测试。

锁定三件事：
1. **内网不能被误伤**——NAS 直连 `192.168.11.8:8090` 必须照常可用；
2. **外网必须被挡住**——包括「反代不在 TRUSTED_PROXIES 里、客户端 IP 看着是私网」这种
   最容易漏判的场景（靠 Host 信号兜住），以及 NAT 回流（内网 IP + 公网域名）；
3. **拦住时不能泄露信息**——响应必须与「路由不存在」逐字节一致（404 + `{"detail":"Not Found"}`）。

另外覆盖 Cookie 签名：入口字符串一改，旧 Cookie 立即失效。
"""
from __future__ import annotations

import hmac
from hashlib import sha256

from starlette.requests import Request

from app import gate
from app.config import get_settings

CFG = {"enabled": True, "path": "sec-entry-13", "cookie_days": 30, "bypass_private": True, "ready": True}


def make_request(
    *,
    client: str = "192.168.1.2",
    host: str = "192.168.11.8:8090",
    path: str = "/",
    cookie: str | None = None,
    xff: str | None = None,
    query: bytes = b"",
    method: str = "GET",
    forwarded_proto: str | None = None,
) -> Request:
    headers = [(b"host", host.encode()), (b"user-agent", b"pytest")]
    if cookie is not None:
        headers.append((b"cookie", f"crm_gate={cookie}".encode()))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if forwarded_proto is not None:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return Request(
        {
            "type": "http", "method": method, "path": path, "raw_path": path.encode(),
            "root_path": "", "scheme": "http", "server": ("192.168.11.8", 8090),
            "client": (client, 54321), "headers": headers, "query_string": query,
        }
    )


def good_cookie(path: str | None = None) -> str:
    return gate.cookie_value(path or CFG["path"])


# ------------------------------------------------------------------ 归一化
def test_normalize_path_strips_and_accepts():
    assert gate.normalize_path("  sec-entry  ") == "sec-entry"
    assert gate.normalize_path("/sec-entry/") == "sec-entry"


def test_normalize_path_rejects_bad_values():
    for bad in ("", "  ", "ab", "a b", "a/b", "..", "中文入口", "x" * 65):
        assert gate.normalize_path(bad) is None, bad


def test_random_path_is_url_safe_and_long_enough():
    p = gate.random_path()
    assert gate.normalize_path(p) == p and len(p) == 12


# ------------------------------------------------------------------ 内外网
def test_internal_direct_is_local():
    assert gate.is_local(make_request()) is True


def test_lan_hostname_is_local():
    assert gate.is_local(make_request(client="192.168.1.2", host="nas.lan:8090")) is True


def test_public_client_ip_is_external():
    assert gate.is_local(make_request(client="8.8.8.8")) is False


def test_public_domain_even_with_private_client_ip_is_external():
    """反代不在 TRUSTED_PROXIES 里时看到的是反代私网 IP —— 必须靠 Host 判成外网。"""
    assert gate.is_local(make_request(client="192.168.1.6", host="crm.example.com:8443")) is False


def test_nat_hairpin_is_external():
    """内网用户经公网域名访问（源 IP 被 SNAT 成内网地址）也算外网，要过门牌。"""
    assert gate.is_local(make_request(client="192.168.1.1", host="crm.example.com:8443")) is False


# ------------------------------------------------------------------ 拦截 / 放行
def test_gate_disabled_passes_everything():
    decision = gate.evaluate(make_request(client="8.8.8.8"), {**CFG, "ready": False}, secure=True)
    assert decision.response is None
    assert decision.security["gate"] is False


def test_external_without_cookie_is_404_like_missing_route():
    decision = gate.evaluate(make_request(client="8.8.8.8", host="crm.example.com"), CFG, secure=True)
    resp = decision.response
    assert resp is not None and resp.status_code == 404
    assert resp.body == b'{"detail":"Not Found"}'
    assert "入口" not in resp.body.decode()  # 不提示，避免暴露


def test_external_with_cookie_passes():
    req = make_request(client="8.8.8.8", host="crm.example.com", cookie=good_cookie())
    decision = gate.evaluate(req, CFG, secure=True)
    assert decision.response is None
    assert decision.security["level"] == "高"


def test_external_with_wrong_cookie_is_blocked():
    req = make_request(client="8.8.8.8", host="crm.example.com", cookie="deadbeef")
    assert gate.evaluate(req, CFG, secure=True).response.status_code == 404


def test_entry_token_sets_cookie_and_redirects():
    req = make_request(
        client="8.8.8.8", host="crm.example.com", path="/s/sec-entry-13", query=b"next=/customers"
    )
    resp = gate.evaluate(req, CFG, secure=True).response
    assert resp.status_code == 303
    assert resp.headers["location"] == "/customers"
    set_cookie = resp.headers.get("set-cookie", "")
    assert gate.COOKIE_NAME in set_cookie and "HttpOnly" in set_cookie and "Secure" in set_cookie


def test_entry_token_rejects_open_redirect():
    req = make_request(
        client="8.8.8.8", host="crm.example.com", path="/s/sec-entry-13", query=b"next=//evil.com/x"
    )
    resp = gate.evaluate(req, CFG, secure=True).response
    assert resp.status_code == 303 and resp.headers["location"] == "/"


def test_wrong_entry_token_is_404():
    req = make_request(client="8.8.8.8", host="crm.example.com", path="/s/not-right")
    assert gate.evaluate(req, CFG, secure=True).response.status_code == 404


def test_internal_bypasses_gate():
    decision = gate.evaluate(make_request(), CFG, secure=True)
    assert decision.response is None
    assert decision.security["level"] == "高"


def test_internal_checked_when_bypass_disabled():
    decision = gate.evaluate(make_request(), {**CFG, "bypass_private": False}, secure=True)
    assert decision.response is not None and decision.response.status_code == 404


def test_public_paths_never_gated():
    req = make_request(
        client="8.8.8.8", host="crm.example.com", path="/cases/share/abc123"
    )
    assert gate.evaluate(req, CFG, secure=True).response is None
    verify = make_request(client="8.8.8.8", host="crm.example.com", path="/894b15d2.txt")
    assert gate.evaluate(verify, CFG, secure=True).response is None


# ------------------------------------------------------------------ Cookie 签名
def test_cookie_value_follows_path_and_secret(monkeypatch):
    before = gate.cookie_value("sec-entry-13")
    assert before == gate.cookie_value("sec-entry-13")
    assert before != gate.cookie_value("another-entry")
    s = get_settings()
    old = s.app_secret_key
    try:
        s.app_secret_key = old + "-rotated"
        assert gate.cookie_value("sec-entry-13") != before
    finally:
        s.app_secret_key = old


def test_cookie_value_is_hmac_of_secret():
    """值必须是 HMAC 而不是明文/可逆编码——Cookie 泄露也不能反推出入口字符串。"""
    expected = hmac.new(
        str(get_settings().app_secret_key).encode(), b"gate-v1|sec-entry-13", sha256
    ).hexdigest()
    assert gate.cookie_value("sec-entry-13") == expected
    assert "sec-entry-13" not in gate.cookie_value("sec-entry-13")


# ------------------------------------------------------------------ 安全等级
def test_security_level_matrix():
    """低：外网 + HTTP；中：外网 + HTTPS 或 内网 + HTTP；高：内网 + HTTPS。"""
    ext = {"client": "8.8.8.8", "host": "crm.example.com", "cookie": good_cookie()}
    assert gate.evaluate(make_request(**ext), CFG, secure=True).security["level"] == "高"
    assert gate.evaluate(make_request(**ext), CFG, secure=False).security["level"] == "低"
    assert gate.evaluate(make_request(), CFG, secure=False).security["level"] == "中"
    assert gate.evaluate(make_request(), CFG, secure=True).security["level"] == "高"


# ------------------------------------------------------------------ 公开资源
def test_public_path_matcher():
    assert gate.is_public_path("/static/app.css") is True
    assert gate.is_public_path("/cases/share/tok/image/1/thumb") is True
    assert gate.is_public_path("/robots.txt") is True
    assert gate.is_public_path("/customers") is False
    assert gate.is_public_path("/staticadmin") is False
