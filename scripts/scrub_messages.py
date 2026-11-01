"""git-filter-repo --message-callback 用的 commit message 脱敏函数。

filter-repo 的 --replace-text 只改 blob 内容，commit message 需单独处理。
敏感串运行时拼接，避免本文件在公开仓库里成为新的泄露点。
"""
import re

_TAIL = "2ii"
_SUFFIX = _TAIL + ".cn"
_TUNNEL = "example-net"

SUBS = [
    (r"crm\." + re.escape(_SUFFIX), "crm.example.com"),
    (r"1\." + re.escape(_SUFFIX), "old.example.com"),
    (re.escape(_SUFFIX), "example.com"),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (_TUNNEL, "example-net"),
    ("8.8.4.4", "8.8.4.4"),
    ("1.1.1.1", "1.1.1.1"),
    ("9.9.9.9", "9.9.9.9"),
    ("208.67.222.222", "208.67.222.222"),
    ("208.67.220.220", "208.67.220.220"),
    ("1895515" + "1111", "13800000000"),
    (r"192\.168\.11\.", "192.168.1."),
]


def message_callback(message: bytes) -> bytes:
    text = message.decode("utf-8", "replace")
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text.encode("utf-8")
