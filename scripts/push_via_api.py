"""用 GitHub Git Data API 推送提交与 tag（当 github.com 的 git 端点不可用时的兜底）。

背景：沙箱/家庭网络到 github.com:443 会阶段性中断，但 api.github.com 通常仍可达。
本脚本把本地已有 commit 的内容通过 REST API 重建到远端，sha 与本地保持一致，
最后更新 refs/heads/main 并创建 tag refs（创建 ref 同样会触发 Actions 的 push 事件）。

用法：
    GH_TOKEN=xxx python3 scripts/push_via_api.py
    # 或把 GH_TOKEN 写在工作区 .env，脚本会自动读取

幂等：远端 main 已是最终提交时跳过 commit 构建；tag 已存在时跳过（HTTP 422）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "st1341815/HVAccRM"
API = "https://api.github.com/repos/" + REPO
CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.dirname(os.path.dirname(CWD))  # agents/<ID>

# 需要发布的提交链（从远端当前 head 开始，按顺序）
BASE = "67fd2f502093032af5c0d67527cd10b6fe9ff0ef"
CHAIN = [
    "1ebc486d4e5e1d68279414d1713627dfee7430e0",  # v1.4.3
    "bc7181c3109325408a0d1274bd9ce5a25132bca6",  # v1.4.4
    "3e5360faa79a4bef50a6108254ee4e21f0a5eb36",  # v1.4.5
]
TAGS = {
    "v1.4.3": CHAIN[0],
    "v1.4.4": CHAIN[1],
    "v1.4.5": CHAIN[2],
}


def load_token() -> str:
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    env_path = os.path.join(WORKSPACE, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("缺少 GH_TOKEN（环境变量或工作区 .env）")


TOKEN = load_token()


def api(method: str, path: str, data=None, tries: int = 4):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    last: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(API + path, data=body, method=method)
        req.add_header("Authorization", "Bearer " + TOKEN)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            return json.loads(raw.decode()) if raw else {}
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read().decode()[:400], file=sys.stderr)
            raise
        except Exception as e:  # 网络抖动重试
            last = e
            print("retry %d/%d: %s" % (attempt + 1, tries, type(e).__name__), file=sys.stderr)
            time.sleep(4)
    raise last  # type: ignore[misc]


def git(*args: str, binary: bool = False):
    out = subprocess.check_output(["git", *args], cwd=CWD)
    return out if binary else out.decode("utf-8")


def main() -> int:
    final = CHAIN[-1]
    remote = api("GET", "/git/ref/heads/main")["object"]["sha"]
    print("remote main =", remote)

    chain: list[str] = []
    if remote == final:
        print("main 已在最终提交，跳过 commit 构建")
    elif remote == BASE:
        chain = CHAIN
    else:
        print("远端 main 处于未知状态（%s），中止" % remote, file=sys.stderr)
        return 2

    parent = remote
    tree_sha = api("GET", "/git/commits/" + remote)["tree"]["sha"]

    for full in chain:
        diff = git("diff-tree", "--no-commit-id", "--name-status", "-r", "-z", parent + ".." + full, binary=True)
        toks = [t for t in diff.split(b"\x00") if t]
        entries: list[dict] = []
        i = 0
        while i < len(toks):
            st = toks[i].decode("utf-8")
            if st[:1] in ("R", "C"):
                old, new = toks[i + 1].decode(), toks[i + 2].decode()
                i += 3
                entries.append({"path": old, "mode": "100644", "type": "blob", "sha": None})
                path, status = new, "M"
            else:
                path = toks[i + 1].decode()
                status = st[:1]
                i += 2
            if status == "D":
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            else:
                ls = git("ls-tree", full, "--", path).strip()
                mode = ls.split("\t")[0].split()[0] if ls else "100644"
                content = open(os.path.join(CWD, path), "rb").read()
                blob = api("POST", "/git/blobs", {
                    "content": base64.b64encode(content).decode(),
                    "encoding": "base64",
                })
                entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
            print("   ", status, path)

        new_tree = api("POST", "/git/trees", {"base_tree": tree_sha, "tree": entries})
        _, message = git("cat-file", "commit", full, binary=True).decode("utf-8").split("\n\n", 1)
        author = {
            "name": git("log", "-1", "--format=%an", full).strip(),
            "email": git("log", "-1", "--format=%ae", full).strip(),
            "date": git("log", "-1", "--format=%aI", full).strip(),
        }
        committer = {
            "name": git("log", "-1", "--format=%cn", full).strip(),
            "email": git("log", "-1", "--format=%ce", full).strip(),
            "date": git("log", "-1", "--format=%cI", full).strip(),
        }
        new_commit = api("POST", "/git/commits", {
            "message": message,
            "tree": new_tree["sha"],
            "parents": [parent],
            "author": author,
            "committer": committer,
        })
        same = "SAME" if new_commit["sha"] == full else "DIFF"
        print("commit", full[:8], "->", new_commit["sha"][:8], same)
        parent = new_commit["sha"]
        tree_sha = new_tree["sha"]

    if parent != remote:
        api("PATCH", "/git/ref/heads/main", {"sha": parent, "force": False})
        print("main ->", parent)

    for name, sha in TAGS.items():
        try:
            r = api("POST", "/git/refs", {"ref": "refs/tags/" + name, "sha": sha})
            print("tag", name, "created ->", r["object"]["sha"][:8])
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print("tag", name, "已存在，跳过")
            else:
                raise

    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
