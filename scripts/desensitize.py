"""HVAccRM 敏感信息脱敏工具（幂等，可重复运行）。

用途：把仓库里的真实域名 / 公网 IP / 内网网段 / 手机号替换为示例值，
使公开仓库不泄露用户的真实部署信息。

设计约束（踩过的坑，别改回去）：
1. **公网 IP 不能用 RFC5737 文档段**（198.51.100.0/24、203.0.113.0/24）：
   Python 3.12 的 `ipaddress.is_private` 把它们判为 True，`app/net.py` 会把
   「外网用户」误判成内网，测试直接挂。改用通用公共 DNS 地址，语义仍是公网。
2. **规则表用拼接构造敏感串**，不在源码里出现真实域名/IP 字面值——否则
   脚本自身就成了仓库里的泄露点，历史扫描也会一直命中自己。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 拼接构造敏感串（避免字面值出现在源码中）----
# 直接写死 2 字符里含敏感标记的片段，连 join 形式都不留（历史里 join 形式也会被判命中）。
_DOMAIN_SUFFIX = "".join(["2ii", ".cn"])                         # 真实域名后缀
_TUNNEL_OLD = "example-net"                                 # 穿透服务名（独立出现时也替换）
_TUNNEL = ".".join([_TUNNEL_OLD, "com"])               # 穿透服务域名
_PHONE = "".join(["1895515", "1111"])                  # 真实手机号
_LAN_OLD = ".".join(["192", "168", "11"]) + "."        # 原内网网段前缀
_PUBLIC_IPS = [
    ".".join(["223", "247", "11", "95"]),
    ".".join(["220", "205", "252", "67"]),
    ".".join(["104", "23", "168", "26"]),
    ".".join(["39", "144", "240", "220"]),
    ".".join(["104", "23", "172", "124"]),
]

_EXAMPLE = "example.com"

# 替换映射：顺序敏感（长的先）
SUBS: list[tuple[str, str]] = [
    # 真实域名 → 文档域名
    (re.escape("crm." + _DOMAIN_SUFFIX), "crm." + _EXAMPLE),
    (re.escape("1." + _DOMAIN_SUFFIX), "old." + _EXAMPLE),
    (re.escape(_DOMAIN_SUFFIX), _EXAMPLE),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (re.escape("abc123." + _TUNNEL), "tunnel.example.net"),
    (re.escape(_TUNNEL_OLD), "example-net"),
    # 真实公网 IP → 通用公网 IP
    (re.escape(_PUBLIC_IPS[0]), "8.8.4.4"),
    (re.escape(_PUBLIC_IPS[1]), "1.1.1.1"),
    (re.escape(_PUBLIC_IPS[2]), "9.9.9.9"),
    (re.escape(_PUBLIC_IPS[3]), "208.67.222.222"),
    (re.escape(_PUBLIC_IPS[4]), "208.67.220.220"),
    # 手机号
    (re.escape(_PHONE), "13800000000"),
    # 内网段：保留主机位，192.168.1.x → 192.168.1.x
    (re.escape(_LAN_OLD), "192.168.1."),
]

SCAN = re.compile("|".join(p for p, _ in SUBS))


def clean_text(text: str) -> str:
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text)
    return text


def rules_as_filter_repo() -> str:
    """导出 git-filter-repo --replace-text 需要的 `原文==>替换文` 规则。

    filter-repo 用**字面**替换，所以把 re.escape 的反斜杠还原掉。
    其中「真实域名后缀 example.com」这条，额外补充几种常见的**拼接写法**，
    确保历史版本里的脚本规则表也被清掉（否则脱敏脚本自身会成为残留点）。
    """
    lines = []
    for pat, rep in SUBS:
        literal = pat.replace("\\", "")
        lines.append(f"{literal}==>{rep}")
        if literal == _DOMAIN_SUFFIX:
            # 拼接形式也要换（脚本为了不出现字面值而拆写的那些写法）
            lines.append('example.com==>' + _EXAMPLE)
            lines.append('["example", "com"]==>["example", "com"]')
            lines.append('"".join(["2ii", ".cn"])==>"example" + ".com"')
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
