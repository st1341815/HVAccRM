"""静态检查：所有 `log_action(...)` 调用的关键字参数必须与函数签名一致。

起因（v1.7.2 线上 500）：`share_custom_save` 里写了 `log_action(..., detail={...})`，
而签名里没有 `detail`（正确的键是 `new`/`old`）→ `TypeError` 在写审计那一刻抛出，
页面显示「服务器内部错误（追踪号 xxxx）」。这类错误纯靠人工 review 很难全部抓到，
用 AST 扫一遍全仓库，一劳永逸。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.audit import log_action

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _log_action_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if name == "log_action":
            calls.append(node)
    return calls


def test_log_action_calls_use_valid_kwargs():
    params = set(inspect.signature(log_action).parameters)
    positional_max = len(params)

    bad: list[str] = []
    files = [p for p in APP_DIR.rglob("*.py")]
    total = 0
    for path in files:
        try:
            calls = _log_action_calls(path)
        except SyntaxError:  # pragma: no cover - 语法错误由别处覆盖
            continue
        for call in calls:
            # 只检查「本仓库自己的」调用：定义处的 def 不算 Call，无需排除
            total += 1
            for kw in call.keywords:
                if kw.arg is None:  # **kwargs 展开，静态无法校验
                    continue
                if kw.arg not in params:
                    bad.append(f"{path.relative_to(APP_DIR.parent)}:{call.lineno} 未知关键字 {kw.arg}=")
            if len(call.args) > positional_max:
                bad.append(f"{path.relative_to(APP_DIR.parent)}:{call.lineno} 位置参数过多 ({len(call.args)})")

    assert total > 10, f"只扫到 {total} 处 log_action 调用，扫描逻辑可能失效"
    assert not bad, "log_action 调用参数非法（会在运行时抛 TypeError → 500）：\n" + "\n".join(bad)
