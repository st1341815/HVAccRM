"""公众号正文生成：多套预设 + 自定义。

═══ 核心约束（2026-10-31 实测踩出来的，改之前务必读） ═══

微信保存草稿时会**把多个块级元素（<p>/<section>）压平成一个段落**。
实测证据：用 4 个独立 <p> 排 4 行，编辑器里 tops=[272,315,357,400] 是分开的，
但存成草稿后用户在公众号里看到的是：
    ▍工地位置颐园清苑 6栋▍施工时间2026-09-14▍施工工序后期施工▍产品类型暖气片
—— 字符全在，**段落分隔全丢**。

所以这里的排版策略只有一条：
    **把所有内容塞进【一个】块级元素，用 <br>（行内换行）分段。**
<br> 属于行内元素，不受「块合并」影响，是最可靠的换行方式。

预设：
  text    —— 零装饰，纯 ▍ + 字号差，最保险
  card    —— 浅底色块（inline 背景 + 边框），带 ▍ 标签
  plain   —— 单行「标签 | 值」
  compact —— 紧凑型
"""
from __future__ import annotations

import json
from html import escape

PRESETS = ("text", "card", "plain", "compact")

FIELDS = (
    ("site", "工地位置"),
    ("date", "施工时间"),
    ("stage", "施工工序"),
    ("product", "产品类型"),
)

DEFAULT_TEMPLATE: dict = {
    "preset": "text",   # 默认用最保险的纯文字
    "brand": "",
    "lead": "",
    "tail": "",
    "show": {k: True for k, _ in FIELDS},
    "accent": "#6c7f91",
    "font_size": 15,
    "line_height": 1.9,
}

# 段与段之间的空行：两个 <br> = 一个空行
_BR = "<br>"
_GAP = "<br><br>"


def normalize(raw: dict | str | None) -> dict:
    """把存库的（可能半截/脏的）配置收敛成完整模板。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    t = json.loads(json.dumps(DEFAULT_TEMPLATE))

    preset = str(raw.get("preset") or "").strip()
    if preset in PRESETS:
        t["preset"] = preset
    for k in ("brand", "lead", "tail"):
        t[k] = str(raw.get(k) or "").strip()
    show = raw.get("show")
    if isinstance(show, dict):
        for k, _ in FIELDS:
            t["show"][k] = bool(show.get(k, True))
    accent = str(raw.get("accent") or "").strip()
    if accent.startswith("#") and len(accent) in (4, 7):
        t["accent"] = accent
    try:
        fs = int(raw.get("font_size") or 0)
        t["font_size"] = fs if 12 <= fs <= 20 else DEFAULT_TEMPLATE["font_size"]
    except (TypeError, ValueError):
        pass
    try:
        lh = float(raw.get("line_height") or 0)
        t["line_height"] = round(lh, 2) if 1.4 <= lh <= 2.4 else DEFAULT_TEMPLATE["line_height"]
    except (TypeError, ValueError):
        pass
    return t


def _values(case) -> dict:
    return {
        "site": case.site_label or "",
        "date": case.work_date or "",
        "stage": case.stage or "",
        "product": case.product_type or "",
    }


def _items(t: dict, vals: dict) -> list[tuple[str, str]]:
    out = []
    for key, label in FIELDS:
        if not t["show"].get(key):
            continue
        out.append((label, escape(vals[key].strip()) or "—"))
    return out


def _head_html(t: dict) -> str:
    """品牌条 + 引导语（都在同一个段落里，用 <br> 接）。"""
    parts = []
    if t["brand"]:
        # 用 inline 的 background/padding 做胶囊：inline 元素的背景比 block 更容易保留
        parts.append(
            f'<span style="background:{t["accent"]};color:#ffffff;'
            f'padding:3px 10px;border-radius:3px;font-size:12px;letter-spacing:1px;">'
            f"{escape(t['brand'])}</span>"
        )
    if t["lead"]:
        parts.append(f'<span style="color:#666;font-size:14px;">{escape(t["lead"])}</span>')
    return (_GAP.join(parts) + _GAP) if parts else ""


def _tail_html(t: dict) -> str:
    if not t["tail"]:
        return ""
    sep = '<span style="color:#d8d2c8;">────────────</span>'
    return _GAP + sep + _GAP + f'<span style="color:#9a958c;font-size:13px;">{escape(t["tail"])}</span>'


# ---------------------------------------------------------------- 预设渲染
def _render_text(t: dict, items: list[tuple[str, str]]) -> str:
    """纯文字：▍标签 + 换行 + 值 + 空行。零依赖。"""
    fs = t["font_size"]
    blocks = []
    for label, value in items:
        blocks.append(
            f'<span style="color:#9a958c;font-size:{max(13, fs - 2)}px;">▍{label}</span>{_BR}'
            f'<span style="color:#222;font-size:{fs + 2}px;font-weight:bold;">{value}</span>'
        )
    return _GAP.join(blocks)


def _render_card(t: dict, items: list[tuple[str, str]]) -> str:
    """卡片：外层 section 带底色/圆角（若被剥，里面的 ▍+字号差仍撑得住）。"""
    fs, accent = t["font_size"], t["accent"]
    blocks = []
    for label, value in items:
        blocks.append(
            f'<span style="color:{accent};font-size:12px;">▍</span>'
            f'<span style="color:#9a958c;font-size:12px;">{label}</span>{_BR}'
            f'<span style="color:#2f2f2f;font-size:{fs + 2}px;font-weight:bold;">{value}</span>'
        )
    inner = _GAP.join(blocks)
    return (
        f'<section style="padding:18px 20px;background:#f7f4ee;border-radius:8px;'
        f'border-left:3px solid {accent};">{inner}</section>'
    )


def _render_plain(t: dict, items: list[tuple[str, str]]) -> str:
    """单行：标签 | 值。"""
    fs = t["font_size"]
    blocks = []
    for label, value in items:
        blocks.append(
            f'<span style="color:#8a8a8a;font-size:{fs}px;">{label}</span>'
            f'<span style="color:#d8d2c8;">&nbsp;&nbsp;|&nbsp;&nbsp;</span>'
            f'<span style="color:#333;font-size:{fs}px;">{value}</span>'
        )
    return _GAP.join(blocks)


def _render_compact(t: dict, items: list[tuple[str, str]]) -> str:
    fs = max(13, t["font_size"] - 1)
    blocks = []
    for label, value in items:
        blocks.append(
            f'<span style="color:#9a958c;font-size:{fs}px;">{label}</span>'
            f'<span style="color:#444;font-size:{fs}px;">&nbsp;&nbsp;{value}</span>'
        )
    return _BR.join(blocks)


_RENDERERS = {
    "text": _render_text,
    "card": _render_card,
    "plain": _render_plain,
    "compact": _render_compact,
}


def body_html(case, template: dict | str | None = None) -> str:
    """生成正文。**整体只有一个块级元素**，内部靠 <br> 分段（见模块顶部说明）。"""
    t = normalize(template)
    items = _items(t, _values(case))
    body = _RENDERERS[t["preset"]](t, items)
    head = _head_html(t)
    tail = _tail_html(t)

    base = (
        f'font-size:{t["font_size"]}px;line-height:{t["line_height"]};color:#333;'
    )
    # 统一包一层：即便这一层被微信拆掉，内部的 <br> 也仍然是行内换行
    return f'<p style="margin:0;{base}">{head}{body}{tail}</p>'


def og_title(case, cfg: dict) -> str:
    """与案例分享页同源的标题规则。"""
    from . import share_custom as custom_svc

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
    t = (og_title or "").strip()
    if not t:
        bits = [b for b in (case.site_label, case.product_type, case.stage) if b]
        t = "  ".join(bits) or "施工案例"
    return t[:max_len]
