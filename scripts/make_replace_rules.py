#!/usr/bin/env python3
"""HVAccRM git 历史脱敏脚本（git-filter-repo 的 --replace-text 规则）。

格式：每行 `原文==>替换文`，filter-repo 会对所有 blob 做字面替换。
规则必须与 scripts/desensitize.py 保持一致（否则历史与工作区脱敏口径不同）。
"""
from pathlib import Path

SUBS = [
    ("crm.example.com", "crm.example.com"),
    ("old.example.com", "old.example.com"),
    ("example.com", "example.com"),
    ("old.example.org", "old.example.org"),
    ("old.example.org", "old.example.org"),
    ("tunnel.example.net", "tunnel.example.net"),
    ("8.8.4.4", "8.8.4.4"),
    ("1.1.1.1", "1.1.1.1"),
    ("9.9.9.9", "9.9.9.9"),
    ("208.67.222.222", "208.67.222.222"),
    ("208.67.220.220", "208.67.220.220"),
    ("13800000000", "13800000000"),
    ("192.168.1.", "192.168.1."),
]

out = Path(__file__).resolve().parent / "replace-text.txt"
out.write_text("".join(f"{a}==>{b}\n" for a, b in SUBS), encoding="utf-8")
print(out)
print(out.read_text(encoding="utf-8"))
