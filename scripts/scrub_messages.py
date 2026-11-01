"""git-filter-repo --message-callback 用的 commit message 脱敏函数。

filter-repo 的 --replace-text 只改 blob 内容，commit message 需要单独处理。
敏感串用拼接构造，避免本文件成为新的泄露点。
"""
import re

_D = example.com
_TUNNEL_OLD = "example-net"

SUBS = [
    (r"crm\." + re.escape(_D), "crm.example.com"),
    (r"1\." + re.escape(_D), "old.example.com"),
    (re.escape(_D), "example.com"),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (r"abc123\." + _TUNNEL_OLD + r"\.com", "tunnel.example.net"),
    (_TUNNEL_OLD, "example-net"),
    (".".join(["223", "247", "11", "95"]).replace(".", r"\."), "8.8.4.4"),
    (".".join(["220", "205", "252", "67"]).replace(".", r"\."), "1.1.1.1"),
    (".".join(["104", "23", "168", "26"]).replace(".", r"\."), "9.9.9.9"),
    (".".join(["39", "144", "240", "220"]).replace(".", r"\."), "208.67.222.222"),
    (".".join(["104", "23", "172", "124"]).replace(".", r"\."), "208.67.220.220"),
    ("".join(["1895515", "1111"]), "13800000000"),
    (".".join(["192", "168", "11"]) + r"\.", "192.168.1."),
]


def message_callback(message: bytes) -> bytes:
    text = message.decode("utf-8", "replace")
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text)
    return text.encode("utf-8")
