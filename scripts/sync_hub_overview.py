"""把 docs/DockerHub-Overview.md 同步到 Docker Hub 仓库页（Overview / 短描述）。

用法（CI 里用 secrets 注入环境变量；本地临时用也行，但别把 token 写进命令历史）：
    DH_USER=xxx DH_TOKEN=<PAT> python3 scripts/sync_hub_overview.py [markdown 文件]

为什么需要它：Docker Hub 仓库页的 Description **不会**自动跟随 GitHub README，
只有走 Docker Hub 的 Autobuild 才会；本项目用 GitHub Actions 构建推镜像，所以在这里显式同步。

失败策略：拿不到凭证 / 网络不通 → 打印原因并以 0 退出（发镜像不该被"写简介"拖垮）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_DOC = "docs/DockerHub-Overview.md"


def _clip_bytes(text: str, limit: int) -> str:
    """按 **UTF-8 字节数** 截断（Docker Hub 的 100 是字节，不是字符）。

    直接 `text[:100]` 对中文毫无意义：100 个汉字 = 300 字节，仍会 400。
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", "ignore")


# 搜索结果与仓库卡片上显示的短描述。
# ⚠️ Docker Hub 限制的是 **100 字节**（不是 100 字符）——中文 3 字节/字，所以只能放 ~33 个汉字。
# 超限时报错：{"fielderrors":{"description":"Exceeded max number of bytes 100 - actual 141"}}
SHORT_DESC_LIMIT = 100
SHORT_DESC = _clip_bytes("暖通空调客户管理系统：房号·合同·收款·成本·施工·案例·自托管", SHORT_DESC_LIMIT)


def _post_json(url: str, payload: dict, headers: dict | None = None, method: str = "POST") -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 固定 https 端点
        return json.loads(resp.read() or b"{}")


def _describe_http_error(exc: urllib.error.HTTPError) -> str:
    """把 Docker Hub 的报错正文带出来，否则 400 无法定位（凭证错？字段超长？无权限？）。"""
    try:
        body = exc.read().decode("utf-8", "replace")[:300]
    except Exception:  # noqa: BLE001
        body = "<无正文>"
    return f"HTTP {exc.code} {exc.reason} | {body}"


def main(argv: list[str]) -> int:
    user = (os.environ.get("DH_USER") or "").strip()
    token = (os.environ.get("DH_TOKEN") or "").strip()
    doc = Path(argv[1] if len(argv) > 1 else DEFAULT_DOC)

    if not user or not token:
        print("跳过：未提供 DH_USER / DH_TOKEN")
        return 0
    if not doc.is_file():
        print(f"跳过：找不到 {doc}")
        return 0

    overview = doc.read_text(encoding="utf-8")
    try:
        jwt = _post_json(
            "https://hub.docker.com/v2/users/login/", {"username": user, "password": token}
        )["token"]
    except urllib.error.HTTPError as exc:
        print(f"登录 Docker Hub 失败：{_describe_http_error(exc)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"登录 Docker Hub 失败：{type(exc).__name__}: {exc}")
        return 0

    try:
        _post_json(
            f"https://hub.docker.com/v2/repositories/{user}/hvaccrm/",
            {"full_description": overview, "description": SHORT_DESC},
            headers={"Authorization": f"JWT {jwt}"},
            method="PATCH",
        )
    except urllib.error.HTTPError as exc:  # 无写权限 / 字段不合法
        print(f"更新仓库描述失败：{_describe_http_error(exc)}")
        return 0
    except Exception as exc:  # noqa: BLE001 - 网络抖动不该让发版流程变红
        print(f"更新仓库描述失败：{type(exc).__name__}: {exc}")
        return 0

    print(f"已同步 {doc} → {user}/hvaccrm（正文 {len(overview)} 字，短描述 {len(SHORT_DESC)} 字）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
