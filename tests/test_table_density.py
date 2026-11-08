"""列表页表格「密度」一致性的回归测试。

**背景**：`app.css` 里有两套表格密度——
`th, td { padding: .45rem .5rem }` + 继承 body 的 15px，
和 `table.tight th, table.tight td { padding: .35rem .45rem; font-size: .88rem }`（13.2px）。
表头两边都是 .8rem（12px），所以漏写 `tight` 的页面会变成「表头比内容还小」，
一眼看过去就是「这个页面的字偏大」。

2026-11-08 用户对比「客户」与「楼盘 / 房号」两页时发现楼盘页偏大，根因就是漏了 class。
这里盯住：**列表页的 `<table>` 必须带 `tight`**（`kv` / `tasks` / `js-stack` 是合理例外）。
"""
from __future__ import annotations

import re
from pathlib import Path

TPL = Path(__file__).resolve().parent.parent / "app" / "templates"

# 数据列表页（有筛选栏 + 一行一条记录的那种）
LIST_TEMPLATES = [
    "customers/list.html",
    "_fragments/customer_rows.html",
    "contracts/list.html",
    "payments/list.html",
    "payments/overdue.html",
    "costs/entries.html",
    "projects/list.html",
    "cases/list.html",
    "reports/index.html",
    "admin/users.html",
    "admin/stages.html",
]

ALLOWED_EXTRA = ("tight", "kv", "tasks", "js-stack")


def test_list_tables_use_tight_density():
    offenders = []
    for rel in LIST_TEMPLATES:
        path = TPL / rel
        assert path.exists(), f"模板不存在：{rel}"
        html = path.read_text()
        for m in re.finditer(r"<table([^>]*)>", html):
            attrs = m.group(1)
            if any(k in attrs for k in ALLOWED_EXTRA):
                continue
            line = html[: m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line}  <table{attrs}>")
    assert not offenders, (
        "下列列表表格没带 class=\"tight\"，字号会比其它页大一圈（表头反比内容小）：\n  "
        + "\n  ".join(offenders)
    )


def test_tight_density_numbers_are_the_small_set():
    """守一下 CSS 口径：tight 是 13.2px（.88rem），普通 td 继承 15px。

    改了任一边都要有意识（这是用户直接看得见的东西），
    这里把两个数字钉住，避免哪天顺手把 tight 调大或把 body 调小。
    """
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.css").read_text()
    assert re.search(r"table\.tight th, table\.tight td \{[^}]*font-size: \.88rem", css), (
        "table.tight 的字号应该是 .88rem"
    )
    assert re.search(r"body \{[^}]*font-size: 15px", css, re.S), (
        "body 基础字号应该是 15px（普通表格 td 继承它，所以是「大字」那一套）"
    )
