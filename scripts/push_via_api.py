"""用 GitHub Git Data API 推送提交与 tag（github.com 的 git 端点不可用时的兜底）。

背景：本机到 github.com:443 会阶段性 TLS 中断，但 api.github.com 通常仍可达。
本脚本把「远端 main → 本地 HEAD」之间的所有 commit 通过 REST API 重建到远端
（tree/message/author/committer 都照搬，正常情况下重建出的 sha 与本地完全一致），
最后更新 refs/heads/main，并为链上的本地 tag 创建 refs。

用法：
    GH_TOKEN=xxx python3 scripts/push_via_api.py
    # 或把 GH_TOKEN 写在工作区 .env，脚本会自动读取

幂等：远端 main 已是本地 HEAD 时跳过；tag 已存在（422）跳过。
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


def load_token() -> str:
    tok = os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    for p in (os.path.join(WORKSPACE, ".env"), os.path.join(WORKSPACE, ".octop", ".env")):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.startswith("GH_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("缺少 GH_TOKEN（环境变量或工作区 .env）")


TOKEN = load_token()


def api(method: str, path: str, data=None, tries: int = 6):
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
            time.sleep(5)
    raise last  # type: ignore[misc]


def git(*args: str, binary: bool = False):
    out = subprocess.check_output(["git", *args], cwd=CWD)
    return out if binary else out.decode("utf-8")


def build_plan():
    """返回 (remote_sha, chain, tags{name: local_sha})"""
    remote = api("GET", "/git/ref/heads/main")["object"]["sha"]
    head = git("rev-parse", "HEAD").strip()
    print("remote main =", remote[:8], " local HEAD =", head[:8])

    if remote == head:
        return remote, [], {}

    r = subprocess.run(["git", "merge-base", "--is-ancestor", remote, head], cwd=CWD)
    if r.returncode != 0:
        raise SystemExit("远端 main 与本地 HEAD 分叉，中止（需要人工 rebase）")

    chain = git("rev-list", "--reverse", f"{remote}..{head}").split()
    tags: dict[str, str] = {}
    for line in git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/tags").splitlines():
        name, _, sha = line.partition(" ")
        if sha in chain:
            tags[name] = sha
    print("chain:", [c[:8] for c in chain], "tags:", list(tags))
    return remote, chain, tags


def main() -> int:
    _, chain, tags = build_plan()

    if not chain:
        # 只补 tag
        created = create_tags(tags)
        print("DONE" if created or not tags else "DONE")
        return 0

    remote = api("GET", "/git/ref/heads/main")["object"]["sha"]
    parent = remote
    tree_sha = api("GET", "/git/commits/" + remote)["tree"]["sha"]
    sha_map: dict[str, str] = {}

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
        sha_map[full] = new_commit["sha"]
        parent = new_commit["sha"]
        tree_sha = new_tree["sha"]

    if parent != remote:
        api("PATCH", "/git/refs/heads/main", {"sha": parent, "force": False})
        print("main ->", parent[:8])

    # tag 指向重建后的 sha
    mapped = {name: sha_map.get(sha, sha) for name, sha in tags.items()}
    create_tags(mapped)
    print("DONE")
    return 0


def create_tags(tags: dict[str, str]) -> bool:
    any_ok = False
    for name, sha in tags.items():
        try:
            r = api("POST", "/git/refs", {"ref": "refs/tags/" + name, "sha": sha})
            print("tag", name, "created ->", r["object"]["sha"][:8])
            any_ok = True
        except urllib.error.HTTPError as e:
            if e.code == 422:
                print("tag", name, "已存在，跳过")
                any_ok = True
            else:
                raise
    return any_ok


if __name__ == "__main__":
    sys.exit(main())
