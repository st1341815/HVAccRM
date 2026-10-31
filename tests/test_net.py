"""内外网判定矩阵（app/net.py）。

覆盖设计文档 §3.1 的全部信号组合，重点是两个安全方向：
- 误伤（内网判外网）只影响体验（多发短信）；
- 漏放（外网判内网）是安全漏洞——必须零容忍。
"""
from __future__ import annotations

from starlette.requests import Request

from app import net
from app.config import get_settings

EXT = ["crm.example.com", "*.example.com"]


def make_request(client_ip: str = "192.168.1.2", xff: str | None = None, host: str = "192.168.1.8:8090") -> Request:
    headers = [(b"host", host.encode())]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request(
        {
            "type": "http", "method": "GET", "path": "/login", "raw_path": b"/login",
            "root_path": "", "scheme": "http", "server": ("192.168.1.8", 8090),
            "client": (client_ip, 54321), "headers": headers, "query_string": b"",
        }
    )


def set_trusted(value: str) -> None:
    get_settings().trusted_proxies = value


# ------------------------------------------------------------------ 内网（不得误伤）
def test_internal_direct():
    assert net.is_external(make_request(client_ip="192.168.1.2"), EXT) is False

def test_internal_localhost():
    assert net.is_external(make_request(client_ip="127.0.0.1"), EXT) is False

def test_internal_loopback_v6():
    assert net.is_external(make_request(client_ip="::1"), EXT) is False

def test_internal_nat_hairpin():
    """NAT 回流：内网用户经公网域名访问，iKuai SNAT 成路由器 192.168.1.1 → 内网（D6）"""
    assert net.is_external(make_request(client_ip="192.168.1.1", host="crm.example.com:8443"), EXT) is False

def test_internal_tailscale():
    """Tailscale/CGNAT 100.64.0.0/10 算内网（D2）"""
    assert net.is_external(make_request(client_ip="100.101.1.5"), EXT) is False

def test_internal_other_rfc1918():
    assert net.is_external(make_request(client_ip="10.1.2.3"), EXT) is False
    assert net.is_external(make_request(client_ip="172.17.0.2"), EXT) is False

def test_internal_container_bridge_with_internal_xff():
    """Docker 桥网段是可信代理（默认 TRUSTED_PROXIES），XFF 内网 IP → 内网"""
    assert net.is_external(make_request(client_ip="172.18.0.1", xff="192.168.1.6"), EXT) is False


# ------------------------------------------------------------------ 外网（不得漏放）
def test_external_trusted_proxy_public_xff():
    """可信代理 + XFF 公网 IP（Lucky 正常链路）→ 外网"""
    assert net.is_external(make_request(client_ip="192.168.1.6", xff="1.1.1.1"), EXT) is True

def test_external_direct_public_ip():
    """绕过反代直连公网 IP → 外网（IP 本身就是公网）"""
    assert net.is_external(make_request(client_ip="1.1.1.1"), EXT) is True

def test_external_public_ipv6():
    assert net.is_external(make_request(client_ip="2408:8000::1"), EXT) is True

def test_external_nat_hairpin_router_ip_with_xff():
    """路由器 IP 若是可信代理且 XFF 是公网（理论上外网穿透到路由器层的场景）→ 外网"""
    set_trusted("127.0.0.1,::1,172.16.0.0/12,192.168.1.6,192.168.1.1")
    try:
        assert net.is_external(make_request(client_ip="192.168.1.1", xff="8.8.4.4"), EXT) is True
    finally:
        set_trusted("127.0.0.1,::1,172.16.0.0/12,192.168.1.6")


# ------------------------------------------------------------------ 伪造 XFF（核心安全用例）
def test_forged_xff_from_untrusted_source_ignored():
    """不可信直连来源 + 伪造 XFF 内网 IP → XFF 不采信，直连公网 → 外网（fail-safe）"""
    req = make_request(client_ip="1.1.1.1", xff="192.168.1.99", host="192.168.1.8:8090")
    assert net.client_ip(req) == "1.1.1.1"
    assert net.is_external(req, EXT) is True

def test_forged_xff_garbage_from_trusted():
    """可信来源 + XFF 是垃圾值 → 跳过垃圾段取直连来源"""
    req = make_request(client_ip="192.168.1.6", xff="garbage, 1.1.1.1")
    assert net.client_ip(req) == "1.1.1.1"

def test_xff_chain_takes_first():
    """XFF 链取第一个合法段（Lucky 覆盖式写入，首段即真实客户端）"""
    req = make_request(client_ip="192.168.1.6", xff="1.1.1.1, 10.0.0.1")
    assert net.client_ip(req) == "1.1.1.1"


# ------------------------------------------------------------------ Host 兜底
def test_host_fallback_when_ip_unparseable():
    """客户端 IP 缺失 → Host 命中外网域名 → 外网（fail-safe）"""
    req = make_request(client_ip="", host="crm.example.com:8443")
    assert net.is_external(req, EXT) is True

def test_host_fallback_wildcard():
    req = make_request(client_ip="", host="shop.example.com")
    assert net.is_external(req, EXT) is True

def test_host_fallback_public_ip_host():
    req = make_request(client_ip="", host="8.8.4.4:8443")
    assert net.is_external(req, EXT) is True

def test_host_fallback_internal():
    req = make_request(client_ip="", host="192.168.1.8:8090")
    assert net.is_external(req, EXT) is False

def test_host_unparseable_ip_failsafe():
    """两个信号都判不出 → 外网（fail-safe 收口）"""
    req = make_request(client_ip="", host="unknown-host")
    assert net.is_external(req, EXT) is True

def test_host_v6_bracket():
    req = make_request(client_ip="", host="[::1]:8090")
    assert net.is_external(req, EXT) is False


# ------------------------------------------------------------------ 工具函数
def test_host_of_strips_port_and_lowercases():
    assert net.host_of(make_request(host="CRM.EXAMPLE.COM:8443")) == "crm.example.com"
    assert net.host_of(make_request(host="192.168.1.8")) == "192.168.1.8"

def test_mask_mobile():
    from app.services import sms as sms_svc
    assert sms_svc.mask_mobile("13912345678") == "139****5678"

def test_mask_secret_in_sms_module():
    from app.services import sms
    assert sms.mask_secret("abcd1234efgh") == "****efgh"
    assert sms.mask_secret("") == ""
    assert sms.valid_mobile("13912345678") is True
    assert sms.valid_mobile("1391234567") is False
    assert sms.valid_mobile("23912345678") is False
