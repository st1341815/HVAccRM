"""HVAccRM 敏感信息脱敏工具（幂等，可重复运行）。

用途：把仓库里的真实域名 / 公网 IP / 内网网段 / 手机号替换为示例值，
使公开仓库不泄露部署方的真实网络信息。

⚠️ 设计约束（踩过的坑，别改回去）：
1. **公网 IP 不能用 RFC5737 文档段**（198.51.100.0/24、203.0.113.0/24）：
   Python 3.12 的 `ipaddress.is_private` 把它们判为 True，`app/net.py` 会把
   「外网用户」误判成内网，测试直接挂。改用通用公共 DNS 地址，语义仍是公网。
2. **敏感串一律运行时拼接**：本文件会被推到公开仓库，源码里不能出现真实
   域名/IP 字面值。历史里连拼接写法（如 `["2ii","cn"]`）也要一起清，
   见 rules_as_filter_repo() 里补充的变体规则。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXAMPLE = "example.com"

# ---- 运行时拼接敏感串 ----
_DOMAIN_TAIL = "2ii"          # 真实域名的主标签
_SUFFIX = _DOMAIN_TAIL + ".cn"
_TUNNEL_NAME = "example-net"
_TUNNEL_DOMAIN = _TUNNEL_NAME + ".com"
_PHONE = "1895515" + "1111"
_LAN_PREFIX = "192.168.11" + "."


def _ip(parts: list[str]) -> str:
    return ".".join(parts)


_PUBLIC_IPS = [
    _ip(["223", "247", "11", "95"]),
    _ip(["220", "205", "252", "67"]),
    _ip(["104", "23", "168", "26"]),
    _ip(["39", "144", "240", "220"]),
    _ip(["104", "23", "172", "124"]),
]

# 替换映射：顺序敏感（长的先）
SUBS: list[tuple[str, str]] = [
    # 真实域名 → 文档域名
    (re.escape("crm." + _SUFFIX), "crm." + EXAMPLE),
    (re.escape("1." + _SUFFIX), "old." + EXAMPLE),
    (re.escape(_SUFFIX), EXAMPLE),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (re.escape("abc123." + _TUNNEL_DOMAIN), "tunnel.example.net"),
    (re.escape(_TUNNEL_NAME), "example-net"),
    # 真实公网 IP → 通用公网 IP
    (re.escape(_PUBLIC_IPS[0]), "8.8.4.4"),
    (re.escape(_PUBLIC_IPS[1]), "1.1.1.1"),
    (re.escape(_PUBLIC_IPS[2]), "9.9.9.9"),
    (re.escape(_PUBLIC_IPS[3]), "208.67.222.222"),
    (re.escape(_PUBLIC_IPS[4]), "208.67.220.220"),
    # 手机号
    (re.escape(_PHONE), "13800000000"),
    # 内网段：保留主机位，192.168.1.x → 192.168.1.x
    (re.escape(_LAN_PREFIX), "192.168.1."),
]

SCAN = re.compile("|".join(p for p, _ in SUBS))


def clean_text(text: str) -> str:
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text)
    return text


def rules_as_filter_repo() -> str:
    """导出 git-filter-repo --replace-text 需要的 `原文==>替换文` 规则。

    filter-repo 用**字面**替换（非正则），所以把 re.escape 的反斜杠还原掉。
    另外补充历史版本里出现过的**拼接写法与大小写变体**，避免脱敏脚本自身
    在旧 commit 里成为残留点。
    """
    lines: list[str] = []
    for pat, rep in SUBS:
        lines.append(f"{pat.replace(chr(92), '')}==>{rep}")

    # 变体：旧版脚本的拼接写法
    lines.append('".".join(["%s", "cn"])==>%s' % (_DOMAIN_TAIL, EXAMPLE))
    lines.append('["%s", "cn"]==>["example", "com"]' % _DOMAIN_TAIL)
    # 变体：大小写（测试里出现过全大写形式）
    lines.append("%s.CN==>EXAMPLE.COM" % _DOMAIN_TAIL.upper())
    return "\n".join(lines) + "\n"


def main() -> None:
    targets: list[Path] = []
    for base in (ROOT / "app", ROOT / "docs", ROOT / "tests", ROOT / "scripts"):
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in str(p):
                targets.append(p)
    for name in ("README.md", "DEVELOPING.md", "DEPLOY_NAS.md", "CHANGELOG.md",
                 "docker-compose.yml", ".env.example", "pyproject.toml"):
        p = ROOT / name
        if p.exists():
            targets.append(p)

    changed = 0
    for p in targets:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not SCAN.search(text):
            continue
        new = clean_text(text)
        if new != text:
            p.write_text(new, encoding="utf-8")
            print(f"cleaned: {p.relative_to(ROOT)}")
            changed += 1
    print(f"DONE, {changed} files cleaned")

    rules_path = ROOT / "scripts" / "replace-text.txt"
    rules_path.write_text(rules_as_filter_repo(), encoding="utf-8")
    print(f"filter-repo 规则已写入：{rules_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
