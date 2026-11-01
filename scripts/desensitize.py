#!/usr/bin/env python3
"""HVAccRM 敏感信息脱敏脚本（幂等）。

⚠️ 公网 IP 的替换**不能**用 RFC5737 文档段（198.51.100.0/24、203.0.113.0/24）：
Python 3.12 的 `ipaddress.is_private` 把它们也算作私网（已实测 True），
`app/net.py` 会误判成内网，测试直接挂。
这里改用 TEST-NET 之外的「真实公网段」中不指向任何具体主机的地址，
语义仍是「公网 IP」，同时不泄露用户真实 IP。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 替换映射：顺序敏感（长的先）
SUBS: list[tuple[str, str]] = [
    # 真实域名 → 文档域名
    (r"crm\.2ii\.cn", "crm.example.com"),
    (r"1\.2ii\.cn", "old.example.com"),
    (r"2ii\.cn", "example.com"),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (r"abc123\.example-net\.com", "tunnel.example.net"),
    (r"example-net", "example-net"),
    # 真实公网 IP → 通用公网 IP（不用 TEST-NET，见文件头说明）
    (r"223\.247\.11\.95", "8.8.4.4"),
    (r"220\.205\.252\.67", "1.1.1.1"),
    (r"104\.23\.168\.26", "9.9.9.9"),
    (r"39\.144\.240\.220", "208.67.222.222"),
    (r"104\.23\.172\.124", "208.67.220.220"),
    # 手机号
    (r"13800000000", "13800000000"),
    # 内网段：保留 192.168.1.x 语义 → 换到 192.168.1.x（保留主机位）
    (r"192\.168\.11\.", "192.168.1."),
]

SCAN = re.compile("|".join(p for p, _ in SUBS))


def clean_text(text: str) -> str:
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text)
    return text


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


if __name__ == "__main__":
    main()
