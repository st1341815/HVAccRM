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


# ---------------------------------------------------------------- v1.13.4
# 这一批是「打出来只有一个合同号 + 压进一张 A4」的回归钉子。


def test_print_shows_contract_no_only_once():
    """屏幕上标题行（h1）和 meta 行都带合同信息，打印时由 print-head 统一给出。

    不隐藏就变成两行一样的「合同 ONExxxx」——纸面上重复且难看。
    状态 / 结算两个徽标要搬进 print-head，别顺手把信息也删了。
    """
    html = _detail()
    assert '<div class="page-head no-print">' in html, "标题行打印时要隐藏（否则合同号出现两次）"
    assert '<div class="meta no-print">' in html, "meta 行信息已并入打印页眉，打印时隐藏"
    head_start = html.index('class="print-only print-head"')
    head_end = html.index("</div>", html.index("print-sub", head_start))
    head = html[head_start:head_end]
    assert "summary.status" in head, "打印页眉要保留状态徽标"
    assert "已结算" in head and "未结算" in head, "打印页眉要保留结算徽标"


def test_print_forces_table_layout_over_mobile_cards():
    """A4 可用宽 ≈748px，会落进 `max-width:780px` 的移动端断点，
    把宽表拆成「标签/值」竖排卡片（table.js-stack）——一竖排高度翻几倍，
    一张纸根本装不下。打印必须强制回到表格布局。

    注意 `table.js-stack td` 特异性高于 `.no-print`，所以重置时必须
    `:not(.no-print)`，否则「附图 / 操作」列又被放出来。
    """
    block = _print_css()
    assert "table.js-stack { display: table !important; }" in block
    assert "table.js-stack thead { display: table-header-group !important; }" in block
    assert "table.js-stack th:not(.no-print)" in block, (
        "重置表格布局时要排除 .no-print，否则被隐藏的列会重新出现"
    )
    assert "table.js-stack td::before { content: none !important; }" in block, (
        "移动端的 td::before 伪元素标签（「金额：」之类）必须清掉"
    )


def test_print_app_shell_does_not_claim_full_page():
    """`.app` 是 `display:flex; min-height:100vh` 的应用外壳，打印里 100vh = 一整页，
    外壳恒定占满 → 内容稍多就溢出空白第 2 页。打印必须退回块级且不设最小高度。"""
    block = _print_css()
    assert ".app { display: block; min-height: 0; }" in block


def test_print_page_is_a4_with_tight_margins():
    """纸张按 A4、收紧页边距；根字号降一档（全站用 rem，一改全跟着收）。"""
    block = _print_css()
    assert "@page { size: A4 portrait; margin: 7mm 6mm; }" in block
    assert "html { font-size: 82%; }" in block
