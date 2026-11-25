"""合同详情页「打印」按钮：打印当前页，但去掉图片部分。

**背景**（v1.13.3 需求）：合同详情是给客户看 / 存档的主力页面，销售要的是
「打印这一单」——但页面上有纸质合同扫描件、成本凭证缩略图、收款截图，
还有一堆录入表单和删除按钮，直接 Ctrl+P 会印出一堆没用的东西。

这里钉住四条约定：
1. 页头有「打印」按钮，调 `window.print()`（不新开窗口、不生成 PDF 接口）；
2. 打印时图片（图片块、附图列、付款截图列）一律不出；
3. 打印时录入表单 / 按钮也不出（`@media print` 兜底 + `.no-print` 逐处标记）；
4. 打印页眉给出合同号 / 客户 / 签订日 / 打印时间。屏幕上不显示。

只断言模板与 CSS 的约定，不做 DOM 快照——样式细节会迭代，约定不该跟着变。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETAIL = ROOT / "app" / "templates" / "contracts" / "detail.html"
CSS = ROOT / "app" / "static" / "app.css"


def _detail() -> str:
    return DETAIL.read_text()


def _print_css() -> str:
    """取出 CSS 里 `@media print` 那一段（到文件末尾）。"""
    css = CSS.read_text()
    start = css.index("@media print")
    return css[start:]


def test_has_print_button():
    html = _detail()
    assert "window.print()" in html, "合同详情页必须有一个调 window.print() 的打印按钮"
    assert ">打印</button>" in html, "按钮文案应为「打印」"


def test_paper_contract_photo_section_is_not_printed():
    """「纸质合同图片」整块（含扫件缩略图与上传表单）打印时不出。"""
    html = _detail()
    idx = html.index("纸质合同图片")
    # 往前找最近的 <section，确认挂了 no-print
    section_start = html.rindex("<section", 0, idx)
    section_tag = html[section_start:idx]
    assert "no-print" in section_tag, "「纸质合同图片」卡片打印时不应出现"


def test_photo_columns_are_not_printed():
    """成本表「附图」、收款流水「付款截图」两列表头与单元格都要标 no-print。

    只 display:none 掉 td 会让表头留一个空列，边框错位——所以表头也要标。
    """
    html = _detail()
    assert '<th class="no-print">附图</th>' in html, "成本表「附图」表头需要 no-print"
    assert '<th class="no-print">付款截图</th>' in html, "收款流水「付款截图」表头需要 no-print"
    assert html.count('class="photo-cell no-print"') == 2, (
        "成本附图与收款截图两个 photo-cell 都要标 no-print"
    )


def test_forms_hidden_in_print_css():
    """CSS 兜底：表单、按钮、输入框、图片在打印时一律隐藏。"""
    block = _print_css()
    for sel in (".no-print", "form", "button", "input", "img"):
        assert sel in block, f"@media print 里应有 {sel} 的隐藏规则（兜底漏标）"
    assert "display: none !important" in block


def test_print_header_only_on_paper():
    html = _detail()
    assert 'class="print-only print-head"' in html, "应有仅打印显示的页眉"
    assert 'id="print_ts"' in html, "打印页眉要带打印时间占位"
    css = CSS.read_text()
    assert ".print-only { display: none; }" in css, "页眉在屏幕上必须不显示"
    assert "display: block !important" in _print_css(), "打印时必须把 print-only 放出来"


def test_print_layout_keeps_tables_readable():
    """表格要有边框、表头跨页重复、行不拆开，否则打出来认不出哪列是哪列。"""
    block = _print_css()
    assert "border-collapse: collapse" in block
    assert "display: table-header-group" in block
    assert "break-inside: avoid" in block
