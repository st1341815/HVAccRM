"""公众号正文 HTML 生成。

微信编辑器只认内联 style，class 会被全部丢弃——所以不能复用 app.css，
这里单独产一份纯内联样式片段。

内容口径（用户 2026-10-31 定稿）：
  **只放 4 个字段 + 全部现场图，不加任何营销文案/二维码/引导语。**
"""
from __future__ import annotations

from html import escape

# 15px/1.9 是公众号图文里最稳的阅读密度（微信默认字号偏大）
P_STYLE = "margin:0 0 14px;font-size:15px;line-height:1.9;color:#333;"
P_LAST = "margin:0 0 26px;font-size:15px;line-height:1.9;color:#333;"
SECTION_STYLE = "margin:0;padding:0;"


def _line(label: str, value: str, last: bool = False) -> str:
    style = P_LAST if last else P_STYLE
    val = escape(str(value or "").strip()) or "-"
    return f'<p style="{style}">{escape(label)}：{val}</p>'


def body_html(case) -> str:
    """4 行字段。图片由调用方逐张插入（正文末尾）。"""
    lines = [
        _line("工地位置", case.site_label),
        _line("施工时间", case.work_date),
        _line("施工工序", case.stage),
        _line("产品类型", case.product_type, last=True),
    ]
    return f'<section style="{SECTION_STYLE}">' + "".join(lines) + "</section>"


def og_title(case, cfg: dict) -> str:
    """与案例分享页同源的标题规则（含「自定义 → 分享页文案」里的 meta_title 模板）。

    刻意在这里复刻一份、而不是 import routers/cases.py 的私有函数——
    路由之间互相 import 容易踩循环引用，且这两处语义本来就该同步演进。
    """
    from ..services import share_custom as custom_svc

    tpl = (cfg.get("share.meta_title") or "").strip()
    if tpl:
        ph = {
            "site": case.site_label or "",
            "stage": case.stage or "",
            "product": case.product_type or "",
            "date": case.work_date or "",
        }
        out = custom_svc.render_text(tpl, ph).strip(" ·-|")
        if out:
            return out
    site = case.site_label or "施工案例"
    kind = f"{case.product_type}施工案例" if case.product_type else f"{case.stage}案例"
    return f"{kind} · {site}"


def title_of(og_title: str, case, max_len: int = 64) -> str:
    """标题：优先用案例页已算好的（含「自定义 → 分享页文案」模板），超 64 字截断。"""
    t = (og_title or "").strip()
    if not t:
        bits = [b for b in (case.site_label, case.product_type, case.stage) if b]
        t = "  ".join(bits) or "施工案例"
    return t[:max_len]
