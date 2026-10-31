"""内外网判定与可信代理解析（外网短信验证的信号层）。

信号模型（设计见 docs/开发报告-外网短信验证码登录.md §3.1）：

1. **真实客户端 IP**（主信号，IP 优先）：
   仅当 TCP 直连来源 ∈ TRUSTED_PROXIES 时才采信 X-Forwarded-For 首段。
   反代（Lucky）对 XFF 是覆盖式写入——2026-10-31 实测从公网伪造
   `X-Forwarded-For` 无效（日志仍记录真实客户端 IP）。
   直连来源不在白名单 → XFF 一律不采信（防「绕过反代直连 + 伪造 XFF
   冒充内网」），真实 IP = 直连来源本身。
   NAT 回流（内网用户经公网域名访问，iKuai SNAT 成 192.168.1.1）
   解析为私网 → 判内网（D6 决策：物理在内网即免验证）。

2. **Host 兜底**：IP 解析不出（如测试桩、异常代理）时才用 Host——
   命中外网域名列表（支持通配 `*.example.com`）或 Host 本身是公网 IP → 外网。

3. **fail-safe**：两个信号都判不出 → 按外网处理（宁可多发一条短信，
   不可漏放一次外网登录）。
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

from .config import get_settings

log = logging.getLogger("crm.net")

# 默认可信代理：本机回环、Docker 桥网段（容器访问宿主服务）、Lucky 所在地址。
# Lucky 搬家 / 换反代时改环境变量 TRUSTED_PROXIES（逗号分隔，支持 CIDR）。
_DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1,172.16.0.0/12,192.168.1.6"

# 视为内网的网段：RFC1918 / 回环 / 链路本地 / CGNAT（Tailscale 等 VPN 隧道，D2 决策算内网）
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = (getattr(get_settings(), "trusted_proxies", None) or _DEFAULT_TRUSTED_PROXIES).strip()
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("TRUSTED_PROXIES 中的 %r 不是合法 IP/CIDR，已忽略", item)
    return nets


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def is_private_ip(value: str) -> bool | None:
    """私网/回环/链路本地/CGNAT → True；公网 → False；解析失败 → None。"""
    addr = _parse_ip(value)
    if addr is None:
        return None
    if addr.version == 4 and addr in _CGNAT:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified


def trusted_source(request: Request) -> bool:
    """TCP 直连来源是否可信代理（可信才采信其 X-Forwarded-For）。"""
    client = getattr(request, "client", None)
    host = client.host if client else ""
    addr = _parse_ip(host)
    if addr is None:
        return False
    return any(addr in net for net in _trusted_networks())


def client_ip(request: Request) -> str:
    """真实客户端 IP：可信代理 → XFF 首个合法段；否则直连来源（XFF 不采信）。"""
    if trusted_source(request):
        fwd = request.headers.get("x-forwarded-for") or ""
        for part in fwd.split(","):
            addr = _parse_ip(part)
            if addr is not None:
                return str(addr)
    client = getattr(request, "client", None)
    return (client.host if client else "") or "-"


def host_of(request: Request) -> str:
    """Host 头归一：剥端口、小写；IPv6 形如 [::1]:8080 → ::1。"""
    host = (request.headers.get("host") or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):  # [IPv6]:port
        end = host.find("]")
        return host[1:end] if end > 0 else host[1:]
    if host.count(":") == 1:  # 域名或 IPv4:port
        return host.rsplit(":", 1)[0]
    return host  # 裸 IPv6 或无端口域名


def _host_matches(host: str, domains: list[str]) -> bool:
    host = host.strip().lower()
    if not host:
        return False
    for domain in domains:
        domain = domain.strip().lower()
        if not domain:
            continue
        if domain.startswith("*."):
            suffix = domain[1:]  # ".example.com"
            if host.endswith(suffix) and "." in host[: -len(suffix)]:
                return True
        elif host == domain:
            return True
    return False


def is_external(request: Request, external_domains: list[str]) -> bool:
    """判定请求是否来自外网。IP 优先，Host 兜底，判不出按外网（fail-safe）。"""
    ip = client_ip(request)
    private = is_private_ip(ip)
    if private is not None:
        return not private  # 公网 → 外网；私网/回环/CGNAT → 内网
    host = host_of(request)
    if host:
        if _host_matches(host, external_domains or []):
            return True
        addr = _parse_ip(host)
        if addr is not None:
            return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified)
        # Host 既不在域名列表也不是 IP：非标准来源（如直接 IP + 端口访问但被代理改写）
        # → 无法证明是内网，按外网
    return True
