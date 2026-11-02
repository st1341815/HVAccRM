"""安全入口（外网门牌）：把「知道地址」变成一道独立的准入门槛。

为什么需要它
------------
系统对公网暴露时，最廉价也最有效的加固不是「把登录页做得更难猜」，而是**让公网根本
看不到登录页**。扫描器/爬虫/自动化爆破工具都是先 GET `/`、`/login`，再对着表单跑字典；
这些请求连页面都拿不到时，绝大多数自动化攻击链会直接放弃。

工作方式
--------
1. 管理员在「系统 → 安全入口」里开启并设置一个自定义字符串（如 `hy3crm-prod`）；
2. 外网访问必须先打开 `<站点根>/s/<字符串>`，服务端校验通过后下发一枚**签名 Cookie**
   （默认 30 天，HMAC-SHA256，密钥取 `APP_SECRET_KEY`）；
3. 之后同一浏览器带着这枚 Cookie 正常访问，URL 里不需要一直带字符串（否则全站链接、
   重定向、静态资源、PWA scope 都得做前缀重写，侵入性极大且容易漏）；
4. 没有 Cookie 的外网请求一律返回 **与「不存在的路由」完全一致** 的 404 ——
   不返回「请通过安全入口访问」这类提示，避免告诉扫描器「这里有个被藏起来的系统」。

内网免检
--------
内网/回环访问（客户端 IP 是私网，且 Host 是私网 IP / localhost / *.lan）直接放行，
NAS 上直连 `http://192.168.11.8:8090` 的日常使用完全不受影响。

刻意**不用**「客户端 IP 是私网就算内网」这一条单独判定：反代（Lucky）若不在
`TRUSTED_PROXIES` 里，XFF 不被采信，看到的是反代自己的私网 IP；只看 IP 会把
「公网经反代进来」误判成内网，等于门牌失效。所以新增 Host 信号：**Host 是公网域名
就一律按外网处理**，哪怕客户端 IP 看着是内网（NAT 回流也一样要过门牌）。
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from hashlib import sha256

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import net
from .config import get_settings
from .utils import safe_next

log = logging.getLogger("crm.gate")

# Cookie 名（不带产品名前缀，减少指纹；value 是 HMAC，不是明文）
COOKIE_NAME = "crm_gate"
# 入口前缀：/s/<字符串>
ENTRY_PREFIX = "/s"

# 入口字符串：4–64 位，只允许 URL 安全字符（不用 / ? # & = 空格，避免被各类客户端转义）
_PATH_RE = re.compile(r"^[A-Za-z0-9._~-]{4,64}$")

# 永远不受门牌限制的公开资源：
# - /static、PWA 相关：分享页/离线页要用；
# - /cases/share：客户分享页，本来就靠一次性 token 授权，且必须能被外网访问；
# - /robots.txt、/favicon.ico：给爬虫的明确信号。
PUBLIC_EXACT = ("/favicon.ico", "/robots.txt", "/manifest.webmanifest", "/service-worker.js", "/offline.html")
PUBLIC_PREFIXES = ("/static", "/cases/share")

# 本机/局域网主机名后缀（Host 判定用）
_LOCAL_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain", ".localhost")

# 配置缓存：避免每个请求都读一次库（改配置时保存路径会主动 invalidate）
_CFG_TTL = 3.0
_cache: dict = {"at": 0.0, "cfg": None}


# ------------------------------------------------------------------ 配置
def config(force: bool = False) -> dict:
    """读取门牌配置（3 秒进程内缓存）。"""
    now = time.time()
    cached = _cache.get("cfg")
    if not force and cached is not None and now - float(_cache.get("at") or 0) < _CFG_TTL:
        return cached
    from .db import SessionLocal
    from .services import settings as settings_svc

    db = SessionLocal()
    try:
        cfg = settings_svc.get_gate_config(db)
    except Exception:  # pragma: no cover - 库不可用时不阻断请求
        log.exception("读取安全入口配置失败，本次按「未启用」处理")
        cfg = {"enabled": False, "path": "", "cookie_days": 30, "bypass_private": True, "ready": False}
    finally:
        db.close()
    _cache["at"] = now
    _cache["cfg"] = cfg
    return cfg


def invalidate() -> None:
    """保存配置后调用：立即让下一次请求读到新值。"""
    _cache["at"] = 0.0
    _cache["cfg"] = None


def set_config_for_tests(cfg: dict | None) -> None:
    """测试钩子：直接塞缓存，免得为一条判定去建库。"""
    _cache["at"] = time.time() if cfg is not None else 0.0
    _cache["cfg"] = cfg


# ------------------------------------------------------------------ 归一化 / 签名
def normalize_path(raw: str | None) -> str | None:
    """清洗自定义字符串：去首尾斜杠与空白，校验字符集与长度。不合法返回 None。"""
    value = (raw or "").strip().strip("/")
    if not value or ".." in value or not _PATH_RE.match(value):
        return None
    return value


def random_path(length: int = 12) -> str:
    """生成一个随机入口字符串（后台「随机生成」按钮用，纯字母数字，便于手输）。"""
    import secrets

    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"  # 去掉易混的 l/o/0/1
    return "".join(secrets.choice(alphabet) for _ in range(length))


def cookie_value(path: str) -> str:
    """Cookie 载荷 = HMAC(APP_SECRET_KEY, "gate-v1|<入口字符串>")。

    入口字符串一改，旧 Cookie 立即失效（不需要额外维护失效列表）。
    """
    key = str(get_settings().app_secret_key).encode("utf-8", "ignore")
    return hmac.new(key, f"gate-v1|{path}".encode("utf-8"), sha256).hexdigest()


def cookie_ok(request: Request, cfg: dict) -> bool:
    path = cfg.get("path") or ""
    if not path:
        return False
    raw = request.cookies.get(COOKIE_NAME) or ""
    return bool(raw) and hmac.compare_digest(raw, cookie_value(path))


def set_cookie(response: Response, cfg: dict, *, secure: bool) -> None:
    days = int(cfg.get("cookie_days") or 30)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value(cfg["path"]),
        max_age=max(1, days) * 86400,
        httponly=True,
        samesite="lax",
        secure=bool(secure),
        path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ------------------------------------------------------------------ 内外网判定
def _host_is_local(host: str) -> bool:
    """Host 是否属于「本机/局域网」：私网 IP、localhost、*.lan/*.local 之类。"""
    if not host:
        return True  # 没有 Host（HTTP/1.0 裸请求）→ 当作本机
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(_LOCAL_SUFFIXES)
    return bool(net.is_private_ip(str(addr)))


def is_local(request: Request) -> bool:
    """明确属于内网访问：客户端 IP 私网 **且** Host 不是公网域名/IP。"""
    ip = net.client_ip(request)
    private = net.is_private_ip(ip)
    if private is False:  # 公网客户端 → 外网
        return False
    return _host_is_local(net.host_of(request))


def is_public_path(path: str) -> bool:
    """公开资源（分享页/静态/PWA/域名验证文件）不受门牌限制。"""
    if path in PUBLIC_EXACT:
        return True
    for prefix in PUBLIC_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    # 域名所有权验证文件：根路径单段 .txt（路由只服务真实存在的文件）
    if path.count("/") == 1 and path.endswith(".txt"):
        return True
    return False


# ------------------------------------------------------------------ 判定
@dataclass
class Decision:
    """一次请求的门牌结论：`response` 非空表示已经该由它结束本次请求。"""

    response: Response | None = None
    security: dict = field(default_factory=dict)


def _not_found() -> JSONResponse:
    """与「路由不存在」时 FastAPI 的响应**逐字节一致**，不做任何提示。

    这样可以避免「门牌拦截」和「真的没这个页面」在响应上出现可区分的差异
    （差异本身就是一种指纹，等于告诉扫描器「这里有东西」）。
    """
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _entry_token(path: str) -> str | None:
    """从 `/s/<token>`（允许末尾斜杠）里取出 token；不是入口路径返回 None。"""
    prefix = ENTRY_PREFIX + "/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):].strip("/")
    if not rest or "/" in rest:
        return None
    return rest


def evaluate(request: Request, cfg: dict, *, secure: bool) -> Decision:
    """门牌主判定。返回 Decision（response 为空 = 放行）。"""
    path = request.url.path
    external = not is_local(request)
    active = bool(cfg.get("ready"))
    gated = active and (external or not cfg.get("bypass_private", True))

    token = _entry_token(path)
    if active and token is not None:
        if hmac.compare_digest(token, str(cfg.get("path") or "")):
            target = safe_next(request.query_params.get("next"), "/")
            response = RedirectResponse(target, status_code=303)
            set_cookie(response, cfg, secure=secure)
            response.headers["Cache-Control"] = "no-store"
            log.info("安全入口通过：ip=%s host=%s → %s", net.client_ip(request), net.host_of(request), target)
            return Decision(response, _state(external, secure, gated=True))
        log.warning(
            "安全入口字符串不正确：ip=%s host=%s ua=%r",
            net.client_ip(request), net.host_of(request), (request.headers.get("user-agent") or "")[:80],
        )
        return Decision(_not_found(), _state(external, secure, gated=True))

    passed = False
    if gated and not is_public_path(path):
        if not cookie_ok(request, cfg):
            log.info(
                "安全入口拦截：%s %s ip=%s host=%s",
                request.method, path, net.client_ip(request), net.host_of(request),
            )
            return Decision(_not_found(), _state(external, secure, gated=True))
        passed = True

    return Decision(None, _state(external, secure, gated=gated, passed=passed))


def _state(external: bool, secure: bool, *, gated: bool, passed: bool = False) -> dict:
    """本次请求的安全画像，供页脚「安全等级」徽章显示。

    口径（给用户看的，不要随意改）：
    - **低**：外网 + 明文 HTTP（数据在公网裸奔，且没过门牌）；
    - **中**：满足其一即可 —— 外网 + HTTPS，或 内网 + 明文 HTTP；
    - **高**：外网 + HTTPS，或 内网 + HTTPS / 内网免检。
    「是否过了安全入口」在 title 里补充说明，不参与分级——
    否则内网直连（免检）会因为没走入口而永远评不上高。
    """
    if not secure:
        level, key = ("低", "low") if external else ("中", "mid")
    else:
        level, key = ("高", "high")
    bits = ["外网" if external else "内网", "HTTPS" if secure else "HTTP"]
    if gated:
        bits.append("安全入口")
    return {
        "level": level,
        "key": key,
        "external": external,
        "https": secure,
        "gate": gated,
        "title": " · ".join(bits) + ("（已通过入口）" if passed else ""),
    }
