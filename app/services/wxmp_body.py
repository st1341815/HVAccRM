"""公众号正文生成：支持多套模板 + 自定义。

关于「换行」的实测结论（2026-10-31）：
  用 `<p>` 排 4 行，微信编辑器里 tops=[272,315,357,400]，行距 43px —— **换行本身没问题**。
  用户觉得「没有换行」，实际是 4 行 15px 文字挤在一起**没有层次**。
  所以这一版的重点是排版层次（标签/值分离、卡片底色、间距），不是加 <br>。

三套预设（都只用内联 style，class 会被微信丢弃）：
  plain   —— 单行「标签：值」，间距宽松，最素
  card    —— 浅底色卡片，标签在上（小灰字）、值在下（大字），层次最清楚
  compact —— 单行紧凑，适合图多的案例（正文短一点，图片占主体）

自定义项（存 app_settings 的 wxmp.template）：品牌条、引导语、落款、字段开关、主色、字号、行距。
"""
from __future__ import annotations

import json
from html import escape

PRESETS = ("card", "plain", "compact", "text")

FIELDS = (
    ("site", "工地位置"),
    ("date", "施工时间"),
    ("stage", "施工工序"),
    ("product", "产品类型"),
)

DEFAULT_TEMPLATE: dict = {
    "preset": "card",
    "brand": "",       # 顶部品牌条，空则不显示
    "lead": "",        # 头部引导语，空则不显示
    "tail": "",        # 尾部落款，空则不显示
    "show": {k: True for k, _ in FIELDS},
    "accent": "#6c7f91",
    "font_size": 15,
    "line_height": 1.9,
}


def normalize(raw: dict | str | None) -> dict:
    """把存库的（可能是半截/脏的）配置收敛成完整模板。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    t = json.loads(json.dumps(DEFAULT_TEMPLATE))  # 深拷贝

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


def _brand_bar(text: str, accent: str) -> str:
    """顶部小标签条：底色 + 白字的胶囊，视觉锚点。"""
    return (
        f'<section style="margin:0 0 18px;padding:0;">'
        f'<section style="display:inline-block;padding:4px 12px;background:{accent};'
        f'border-radius:3px;">'
        f'<span style="font-size:12px;line-height:1.4;color:#ffffff;letter-spacing:1px;">'
        f"{escape(text)}</span></section></section>"
    )


def _divider(accent: str) -> str:
    return (
        f'<section style="margin:26px 0;padding:0;">'
        f'<section style="height:1px;background:#e5e0d8;"></section></section>'
    )


def _plain(t: dict, vals: dict) -> str:
    fs, lh = t["font_size"], t["line_height"]
    out = []
    for key, label in FIELDS:
        if not t["show"].get(key):
            continue
        v = escape(vals[key].strip()) or "—"
        out.append(
            f'<p style="margin:0 0 16px;font-size:{fs}px;line-height:{lh};color:#333;">'
            f'<span style="color:#8a8a8a;">{escape(label)}</span>'
            f'<span style="color:#d8d2c8;">&nbsp;&nbsp;|&nbsp;&nbsp;</span>'
            f"{v}</p>"
        )
    return "".join(out)


def _card(t: dict, vals: dict) -> str:
    """卡片式：浅底色块 + 标签（小灰字）/ 值（大字）。

    2026-10-31 加固：**不再只靠 CSS 造层次**。标签前加 `▍` 字符、值与标签字号拉开，
    即使微信保存草稿时剥掉 background/border-radius，符号和字号差仍能撑住结构。
    """
    fs, lh, accent = t["font_size"], t["line_height"], t["accent"]
    rows = []
    items = [(k, l) for k, l in FIELDS if t["show"].get(k)]
    for i, (key, label) in enumerate(items):
        last = i == len(items) - 1
        v = escape(vals[key].strip()) or "—"
        mb = "0" if last else "16px"
        rows.append(
            f'<p style="margin:0 0 4px;font-size:12px;line-height:1.5;color:#9a958c;'
            f'letter-spacing:0.5px;"><span style="color:{accent};">▍</span>{escape(label)}</p>'
            f'<p style="margin:0 0 {mb};font-size:{fs + 2}px;line-height:{lh - 0.2};'
            f'color:#2f2f2f;font-weight:bold;">{v}</p>'
        )
    return (
        f'<section style="margin:0 0 24px;padding:18px 20px;background:#f7f4ee;'
        f'border-radius:8px;border-left:3px solid {accent};">' + "".join(rows) + "</section>"
    )


def _text(t: dict, vals: dict) -> str:
    """零 CSS 依赖版（防剥落）。

    只依赖两样微信几乎不会过滤的东西：**换行**和**字符本身**。
    每项用 `▍标签` + 值 + 空行，即使颜色/字号/背景全丢，排版依然是分明的四块。
    """
    fs = t["font_size"]
    rows = []
    for key, label in FIELDS:
        if not t["show"].get(key):
            continue
        v = escape(vals[key].strip()) or "—"
        rows.append(
            f'<p style="margin:0 0 6px;font-size:{max(13, fs - 2)}px;line-height:1.6;color:#999;">'
            f'▍{escape(label)}</p>'
            f'<p style="margin:0 0 20px;font-size:{fs + 2}px;line-height:1.7;color:#222;">'
            f"{v}</p>"
        )
    return "".join(rows)


def _compact(t: dict, vals: dict) -> str:
    fs = max(13, t["font_size"] - 1)
    rows = []
    for key, label in FIELDS:
        if not t["show"].get(key):
            continue
        v = escape(vals[key].strip()) or "—"
        rows.append(
            f'<p style="margin:0 0 6px;font-size:{fs}px;line-height:1.7;color:#444;">'
            f'<span style="color:#9a958c;">{escape(label)}</span>&nbsp;&nbsp;{v}</p>'
        )
    return f'<section style="margin:0 0 18px;">' + "".join(rows) + "</section>"


_RENDERERS = {"plain": _plain, "card": _card, "compact": _compact, "text": _text}


def body_html(case, template: dict | str | None = None) -> str:
    """生成正文（图片由调用方逐张插到末尾）。"""
    t = normalize(template)
    vals = _values(case)
    parts: list[str] = []

    if t["brand"]:
        parts.append(_brand_bar(t["brand"], t["accent"]))
    if t["lead"]:
        parts.append(
            f'<p style="margin:0 0 18px;font-size:14px;line-height:1.8;color:#666;">'
            f'{escape(t["lead"])}</p>'
        )

    parts.append(_RENDERERS[t["preset"]](t, vals))

    if t["tail"]:
        parts.append(_divider(t["accent"]))
        parts.append(
            f'<p style="margin:0;font-size:13px;line-height:1.8;color:#9a958c;'
            f'text-align:center;">{escape(t["tail"])}</p>'
        )
    return "".join(parts)


def og_title(case, cfg: dict) -> str:
    """与案例分享页同源的标题规则（含「自定义 → 分享页文案」里的 meta_title 模板）。"""
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
    """标题：优先用案例页已算好的，超 64 字截断。"""
    t = (og_title or "").strip()
    if not t:
        bits = [b for b in (case.site_label, case.product_type, case.stage) if b]
        t = "  ".join(bits) or "施工案例"
    return t[:max_len]
