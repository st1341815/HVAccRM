"""版本号同步助手：保证 pyproject.toml 与 app/__init__.py 两处一致。

用法：
    python scripts/bump_version.py                 # 一致性检查（CI / 部署前用）
    python scripts/bump_version.py 1.2.3          # bump + 检查 + 失败即报错
    python scripts/bump_version.py bump 1.2.3     # 同上
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "app" / "__init__.py"

# 严格 X.Y.Z；预发布标签（-rc1 / +local）不识别 —— 想要 RC 流程再扩展
SEMVER_FULL = r"\d+\.\d+\.\d+"
SEMVER_LOOSE = r"\d+\.\d+\.\d+"
PYPROJECT_RE = re.compile(rf'^version\s*=\s*"({SEMVER_FULL})"', re.MULTILINE)
INIT_RE = re.compile(rf'^__version__\s*=\s*"({SEMVER_FULL})"', re.MULTILINE)


def read_versions() -> tuple[str, str]:
    py = PYPROJECT_RE.search(PYPROJECT.read_text(encoding="utf-8"))
    init = INIT_RE.search(INIT.read_text(encoding="utf-8"))
    if not py:
        raise SystemExit(f"未在 {PYPROJECT} 找到 version 行")
    if not init:
        raise SystemExit(f"未在 {INIT} 找到 __version__ 行")
    return py.group(1), init.group(1)


def write_version(version: str) -> None:
    py_text = PYPROJECT.read_text(encoding="utf-8")
    new_py, n1 = re.subn(
        rf'^version\s*=\s*"{SEMVER_FULL}"',
        f'version = "{version}"',
        py_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n1 != 1:
        raise SystemExit(f"未在 {PYPROJECT} 匹配到 version 行（aborting）")
    PYPROJECT.write_text(new_py, encoding="utf-8")

    init_text = INIT.read_text(encoding="utf-8")
    new_init, n2 = re.subn(
        rf'^__version__\s*=\s*"{SEMVER_FULL}"',
        f'__version__ = "{version}"',
        init_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n2 != 1:
        # 已改了 pyproject 但 init 改不成功——必须回滚
        PYPROJECT.write_text(py_text, encoding="utf-8")
        raise SystemExit(f"未在 {INIT} 匹配到 __version__ 行（pyproject 已回滚）")
    INIT.write_text(new_init, encoding="utf-8")


def cmd_check() -> int:
    py, init = read_versions()
    if py != init:
        print(f"[FAIL] {PYPROJECT.name}={py} 与 app/__init__.py={init} 不一致", file=sys.stderr)
        return 1
    print(f"[OK] 版本号一致：{py}")
    return 0


def cmd_bump(version: str) -> int:
    if not re.fullmatch(SEMVER_LOOSE, version):
        print(f"用法：bump_version.py <X.Y.Z>（不接受预发布标签）", file=sys.stderr)
        return 2
    py, init = read_versions()
    if py == version and init == version:
        print(f"[SKIP] 已是 {version}")
        return 0
    write_version(version)
    print(f"[OK] bump 到 {version}（{PYPROJECT.name} 与 app/__init__.py 同步）")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("check", "-h", "--help"):
        return cmd_check()
    if argv[0] == "bump":
        return cmd_bump(argv[1]) if len(argv) >= 2 else cmd_check()
    return cmd_bump(argv[0])  # 直接 `bump_version.py 1.2.3` 也行


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
