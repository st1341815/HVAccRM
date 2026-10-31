"""公众号正文生成：多套预设 + 自定义。

═══ 核心约束（2026-10-31 实测踩出来的，改之前务必读） ═══

微信的正文编辑器（UEditor/ProseMirror 混合内核）对「自动注入」极其挑剔：

**实测结论（决定性的）：**
  1. `document.execCommand('insertHTML', ...)` 注入的任何 HTML，都会被编辑器
     **剥掉所有 style 属性**，且**合并相邻的多个块级元素**成一段。
     —— 注入 8 个 `<section>`，读回只剩 1 个 section、0 个 `<br>`，全挤一行。
  2. `<br>` 在「保存草稿」时会被微信后端吞成 `&nbsp;`（软空格）。
  3. **唯一可靠的分段方式**：`insertText` 写入一行文字，再用
     `Input.dispatchKeyEvent` 模拟回车（Enter），产生微信原生的
     `<section><span leaf="">文字</span></section>` 段落结构。
     —— 这是微信编辑器自己生成段落的方式，保存后一定保留。

所以发布时的策略只有一条：
    **正文 = 逐行 `insertText`，行与行之间用模拟回车分段，绝不用 insertHTML。**
    样式（字号/颜色/粗体）在自动注入场景下无法可靠保留，只能放弃，
    改用「▍ 符号 + 空行 + 纯文字」造层次。

    `body_lines()` 负责产出「行列表」（纯文本，发布用）；
    `body_html()` 保留富 HTML，仅用于**站内预览**（给用户看样式效果，不代表微信最终样式）。

预设：
  text    —— 零装饰，▍ 标签 + 值，最保险
  card    —— 每字段一个浅底卡片（预览用；发布时退化为 ▍ + 空行分层）
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
    "preset": "text",
    "brand": "",
    "lead": "",
    "tail": "",
    "show": {k: True for k, _ in FIELDS},
    "accent": "#6c7f91",
    "font_size": 15,
    "line_height": 1.9,
}


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


def _items(t: dict, vals: dict, escape_value: bool = True) -> list[tuple[str, str]]:
    """收集要展示的字段（标签, 值）。

    escape_value=True：对值做 HTML 转义 —— 只给 body_html()（预览，拼进 HTML）。
    escape_value=False：保留原文 —— 给 body_lines()（发布，走 insertText 纯文本）。
    ⚠️ 两者不能混用：纯文本路径若带 HTML 转义，案例字段含 & < > 时会发出
    `&amp;` / `&lt;` 字面乱码（公众号是对外内容，属于用户可见错误）。
    """
    out = []
    for key, label in FIELDS:
        if not t["show"].get(key):
            continue
        raw = vals[key].strip()
        value = (escape(raw) if escape_value else raw) or "—"
        out.append((label, value))
    return out


# ------------------------------------------------------------------ 行模型
# 发布用的正文 = 一个「行列表」。每行是纯文本，行间靠模拟回车分段。
# 空字符串 = 一个空行（在微信里表现为段落间隔）。

def body_lines(case, template: dict | str | None = None) -> list[str]:
    """返回正文的**行列表**（纯文本，发布用）。

    紧凑单行排版：每个字段一行「▍标签　值」，字段间紧挨，不加空行。
    微信编辑器会剥掉富样式，所以层次靠「▍ + 标签名 + 全角空格 + 值」的字面结构区分。
    """
    t = normalize(template)
    items = _items(t, _values(case), escape_value=False)  # 纯文本路径，不转义
    lines: list[str] = []

    # 品牌条 / 引导语
    if t["brand"]:
        lines.append(f"【{t['brand']}】")
    if t["lead"]:
        lines.append(t["lead"])

    # 字段：单行「▍标签　值」
    for label, value in items:
        lines.append(f"▍{label}　{value}")

    # 落款
    if t["tail"]:
        lines.append("────────────")
        lines.append(t["tail"])

    return lines


# ------------------------------------------------------------------ 预览 HTML
def _sect(inner: str, style: str = "") -> str:
    return f'<section style="{style}">{inner}</section>'


def _head_blocks(t: dict) -> list[str]:
    blocks = []
    if t["brand"]:
        blocks.append(
            _sect(
                f'<span style="background:{t["accent"]};color:#ffffff;'
                f'padding:3px 10px;border-radius:3px;font-size:12px;'
                f'letter-spacing:1px;">{escape(t["brand"])}</span>'
            )
        )
    if t["lead"]:
        blocks.append(
            _sect(f'<span style="color:#666;font-size:14px;">{escape(t["lead"])}</span>')
        )
    return blocks


def _tail_block(t: dict) -> list[str]:
    if not t["tail"]:
        return []
    return [
        _sect('<span style="color:#d8d2c8;">────────────</span>'),
        _sect(f'<span style="color:#9a958c;font-size:13px;">{escape(t["tail"])}</span>'),
    ]


def _render_text(t: dict, items: list[tuple[str, str]]) -> list[str]:
    fs = t["font_size"]
    blocks = []
    for label, value in items:
        blocks.append(
            _sect(
                f'<span style="color:#9a958c;font-size:{max(13, fs - 2)}px;">▍{label}</span>',
                "margin:0 0 2px;",
            )
        )
        blocks.append(
            _sect(
                f'<span style="color:#222;font-size:{fs + 2}px;font-weight:bold;">{value}</span>',
                "margin:0 0 10px;",
            )
        )
    return blocks


def _render_card(t: dict, items: list[tuple[str, str]]) -> list[str]:
    fs, accent = t["font_size"], t["accent"]
    blocks = []
    for label, value in items:
        blocks.append(
            _sect(
                f'<span style="color:{accent};font-size:12px;">▍</span>'
                f'<span style="color:#9a958c;font-size:12px;">{label}</span>'
                f'<span style="color:#2f2f2f;font-size:{fs + 2}px;font-weight:bold;">{value}</span>',
                f"padding:12px 16px;background:#f7f4ee;border-radius:8px;"
                f"border-left:3px solid {accent};margin:0 0 8px;",
            )
        )
    return blocks


def _render_plain(t: dict, items: list[tuple[str, str]]) -> list[str]:
    fs = t["font_size"]
    blocks = []
    for label, value in items:
        blocks.append(
            _sect(
                f'<span style="color:#8a8a8a;font-size:{fs}px;">{label}</span>'
                f'<span style="color:#d8d2c8;">&nbsp;&nbsp;|&nbsp;&nbsp;</span>'
                f'<span style="color:#333;font-size:{fs}px;">{value}</span>'
            )
        )
    return blocks


def _render_compact(t: dict, items: list[tuple[str, str]]) -> list[str]:
    fs = max(13, t["font_size"] - 1)
    blocks = []
    for label, value in items:
        blocks.append(
            _sect(
                f'<span style="color:#9a958c;font-size:{fs}px;">{label}</span>'
                f'<span style="color:#444;font-size:{fs}px;">&nbsp;&nbsp;{value}</span>'
            )
        )
    return blocks


_RENDERERS = {
    "text": _render_text,
    "card": _render_card,
    "plain": _render_plain,
    "compact": _render_compact,
}


def body_html(case, template: dict | str | None = None) -> str:
    """富 HTML 正文 —— 仅用于站内预览（展示样式效果，不代表微信最终样式）。"""
    t = normalize(template)
    items = _items(t, _values(case))
    base = (
        f'font-size:{t["font_size"]}px;line-height:{t["line_height"]};color:#333;'
    )
    blocks = _head_blocks(t) + _RENDERERS[t["preset"]](t, items) + _tail_block(t)
    styled = []
    for b in blocks:
        if 'style="' in b and b.index('style="') < len(b):
            head, rest = b.split('style="', 1)
            style_val, tail = rest.split('"', 1)
            styled.append(f'{head}style="{base}{style_val}"{tail}')
        else:
            styled.append(b.replace("<section", f'<section style="{base}"', 1))
    return "".join(styled)


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
