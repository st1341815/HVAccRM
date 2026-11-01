#!/usr/bin/env python3
"""批量清洗 GitHub Release 正文里的敏感信息（保持其余内容不变）。"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

TOKEN = Path("/tmp/tok_ok.txt").read_text().strip()
REPO = "st1341815/HVAccRM"
API = f"https://api.github.com/repos/{REPO}"

SUBS = [
    (r"crm\.2ii\.cn", "crm.example.com"),
    (r"1\.2ii\.cn", "old.example.com"),
    (r"2ii\.cn", "example.com"),
    (r"1\.13uncle\.top", "old.example.org"),
    (r"13uncle\.top", "old.example.org"),
    (r"abc123\.ddnsto\.com", "tunnel.example.net"),
    (r"ddnsto", "example-net"),
    (r"223\.247\.11\.95", "8.8.4.4"),
    (r"220\.205\.252\.67", "1.1.1.1"),
    (r"104\.23\.168\.26", "9.9.9.9"),
    (r"39\.144\.240\.220", "208.67.222.222"),
    (r"104\.23\.172\.124", "208.67.220.220"),
    (r"18955151111", "13800000000"),
    (r"192\.168\.11\.", "192.168.1."),
]
SCAN = re.compile("|".join(p for p, _ in SUBS), re.I)


def api(path: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return r.status, (json.loads(body) if body else None)


def clean(text: str) -> str:
    for pat, rep in SUBS:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def main() -> None:
    _, releases = api("/releases?per_page=100")
    fixed = 0
    for rel in releases or []:
        body = rel.get("body") or ""
        name = rel.get("name") or ""
        if not SCAN.search(body) and not SCAN.search(name):
            continue
        new_body = clean(body)
        new_name = clean(name)
        payload = {}
        if new_body != body:
            payload["body"] = new_body
        if new_name != name:
            payload["name"] = new_name
        code, _ = api(f"/releases/{rel['id']}", "PATCH", payload)
        print(f"{rel.get('tag_name')}: PATCH {code} (body_changed={new_body != body})")
        fixed += 1
    print(f"DONE, {fixed} releases updated")


if __name__ == "__main__":
    main()
