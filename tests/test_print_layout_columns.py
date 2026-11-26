"""打印排版：甲乙方签字区 / 信息区在纸面上必须是左右两列，和屏幕预览一致。

**背景**（v1.15.1 用户实测）：电子合同 `ONE202610060007` 打出来最后一页，
甲方一块、乙方一块上下堆叠。查下来模板没问题，是 `@media (max-width: 760px)`
里的 `.doc-sign { grid-template-columns: 1fr }` 在打印时被命中了 ——
Chrome 打印页面宽 ≈ 748px（A4 减去 @page 边距），正好小于 760px 断点。
`@media print` 只改了 margin-top，没把列数写回来。

同一类坑 v1.13.4 在 `table.js-stack` 上踩过（780px 断点在打印时生效，
明细表被拆成竖排卡片），当时只在表格上打补丁，没推广到 doc 区块。

这里钉住三条：
1. `@media print` 里 `.doc-sign` / `.doc-parties` / `.doc-meta` 显式写了桌面列数；
2. 这三条不许再来一条单列规则把前面的覆盖掉；
3. 手机上（760px 断点）**仍然**是单列 —— 修打印不能把手机预览改坏。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "app" / "static" / "app.css"


def _css() -> str:
    return CSS.read_text()


def _block(selector_start: str) -> str:
    """取从某个 @media 开头到文件末尾的片段（两者都是文件里最后一个媒体查询）。"""
    css = _css()
    idx = css.index(selector_start)
    return css[idx:]


def _rules(block: str, selector: str) -> list[str]:
    """取出某个选择器在大括号里的声明文本（可能有多条）。"""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    return [m.group(1) for m in re.finditer(pattern, block)]


def _columns(decl: str) -> str:
    m = re.search(r"grid-template-columns:\s*([^;]+);", decl)
    return m.group(1).strip() if m else ""


# 打印时要保持桌面列数的三块：签字区两列、甲乙乙方信息两列、概况三列
PRINT_COLUMNS = {
    ".doc-sign": "1fr 1fr",
    ".doc-parties": "1fr 1fr",
    ".doc-meta": "repeat(3, minmax(0, 1fr))",
}


def test_print_keeps_desktop_columns():
    """A4 纸宽 ≈ 748px 会掉进 760px 手机断点，print 块必须把列数写回来。"""
    block = _block("@media print")
    for selector, expect in PRINT_COLUMNS.items():
        rules = _rules(block, selector)
        assert rules, f"@media print 里没有 {selector} 的规则"
        cols = [c for c in (_columns(r) for r in rules) if c]
        assert cols, f"@media print 里 {selector} 没有 grid-template-columns，纸面会掉成单列"
        assert expect in cols, f"@media print 里 {selector} 的列数应为 {expect}，实际 {cols}"


def test_print_columns_not_overridden_by_single_column():
    """后面再来一条 `1fr` 会把前面的两列覆盖掉——顺序在 CSS 里就是优先级。"""
    block = _block("@media print")
    for selector in PRINT_COLUMNS:
        for decl in _rules(block, selector):
            cols = _columns(decl)
            assert cols != "1fr", (
                f"@media print 里 {selector} 出现单列覆盖，纸面上甲乙方又会上下堆叠"
            )


def test_mobile_preview_still_single_column():
    """手机屏幕预览仍要单列（两列会把甲方那行挤成三行），别把打印的修复反向带过来。"""
    block = _block("@media (max-width: 760px)")
    rules = _rules(block, ".doc-sign")
    assert any("1fr" in _columns(r) for r in rules), (
        "手机断点里 .doc-sign 应保持单列：手机屏宽装不下两列签字区"
    )


# ---------------------------------------------------------------- v1.15.2
def test_page_margin_is_document_standard():
    """纸张边距：上下 2.2cm、左右 1.8cm（用户 2026-11-26 指定）。

    这条特别容易被「把合同压进一页」的优化悄悄改小（v1.13.4 就压成过 7mm/6mm），
    所以钉死。注意可用宽 = 210 − 2×18 = 174mm ≈ 658px，仍小于 760px 断点 ——
    上面那三条列数覆盖依赖这个前提，改边距前先想清楚。
    """
    block = _block("@media print")
    m = re.search(r"@page\s*\{([^}]*)\}", block)
    assert m, "@media print 里没有 @page 规则，纸张边距会退回浏览器默认"
    decl = m.group(1)
    assert "size: A4" in decl, "@page 应显式声明 A4，否则纸张跟着打印机默认走"
    margin = re.search(r"margin:\s*([^;]+);", decl)
    assert margin, "@page 缺 margin"
    value = margin.group(1).strip()
    assert value == "2.2cm 1.8cm", f"页边距应为上下 2.2cm / 左右 1.8cm，实际 {value!r}"
