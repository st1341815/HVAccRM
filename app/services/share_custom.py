"""案例分享页的个性化配置（只作用于对外分享页 `/cases/share/<token>`）。

设计原则：
- 存 `app_settings` 表（key 前缀 `share.`），与备份配置同一套「读时回落默认、保存时 UPSERT」；
- **只影响分享页**：内部页面（案例列表 / 登录页 / 后台）一律不读这里；
- 页脚按「行」存：`share.footer` 多行文本，渲染时按行拆成多个 div——用户可自由增删行；
- 空值语义 = 用默认（例如把标题语清空，分享页就回落成「施工案例」）；
- 结构化默认值以 JSON 文本写死在 `_DEFAULTS`，不写库，避免升级后把默认值固化成脏数据。
"""
from __future__ import annotations

import json
from sqlalchemy.orm import Session

from . import settings as settings_svc

# 分享页「标题模板」支持的占位符：{site} 工地位置、{stage} 工序、{product} 产品类型、{date} 施工日期
PLACEHOLDERS = ("site", "stage", "product", "date")

# ------------------------------------------------------------- 默认值
_DEFAULTS: dict[str, str] = {
    # 分享页 <h1> 标题语
    "share.title": "施工案例",
    # 品牌字样（页面左上角）；空 = 跟随系统英文名
    "share.brand": "HVAccRM",
    "share.brand_icon": "🌡️",
    # 浏览器 / 社交平台标题模板（微信分享卡片标题也用它）
    "share.meta_title": "{product}施工案例 · {site}",
    # 分享卡片描述（og:description / meta description）
    "share.meta_desc": "现场施工实拍，仅作效果展示参考。",
    # 页脚：一个 div 一行
    "share.footer": (
        "Copyright © 2026 Uncle13. All Rights Reserved.\n"
        "皖ICP备XXXXXXXX号-1 | 皖公网安备 XXXXXXXXXXXXX号"
    ),
    # 图片下方的小字说明
    "share.tail": "由 {brand} 生成 · 仅作展示参考",
}

# 单个字段长度上限（防止粘贴一整篇文章把页面撑爆）
LIMITS = {
    "share.title": 40,
    "share.brand": 24,
    "share.brand_icon": 8,
    "share.meta_title": 120,
    "share.meta_desc": 200,
    "share.footer": 600,
    "share.tail": 120,
}

FIELD_LABELS = {
    "share.title": "页面标题语",
    "share.brand": "品牌字样",
    "share.brand_icon": "品牌图标",
    "share.meta_title": "浏览器 / 分享卡片标题",
    "share.meta_desc": "分享卡片描述",
    "share.footer": "页面底部",
    "share.tail": "图片下方说明",
}


def defaults() -> dict[str, str]:
    return dict(_DEFAULTS)


def get_all(db: Session) -> dict[str, str]:
    """当前生效的配置（未存过的字段取默认值）。"""
    stored = settings_svc.all_settings(db)
    out: dict[str, str] = {}
    for key, default in _DEFAULTS.items():
        val = stored.get(key)
        out[key] = default if val is None else val
    return out


def render_text(tpl: str, ctx: dict[str, str]) -> str:
    """把 {site} / {stage} / {product} / {date} 渲染成实际值。

    用 `str.replace` 而不是 `str.format`：模板里可能含用户输入的 `{` `}`（比如版权声明），
    `format` 遇到未注册的占位符会抛 KeyError，而 replace 只替换认识的几个，其余原样保留。
    """
    out = tpl or ""
    for key in PLACEHOLDERS:
        out = out.replace("{" + key + "}", ctx.get(key, ""))
    return out


def footer_lines(text: str) -> list[str]:
    """页脚按行拆；空行丢弃（用空行的视觉间隔交给 CSS 行高处理）。"""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def normalize(raw: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """清洗用户提交：去首尾空白、统一换行、按 LIMITS 截断。返回 (值, 提示)。"""
    cleaned: dict[str, str] = {}
    notes: list[str] = []
    for key, limit in LIMITS.items():
        val = (raw.get(key) or "")
        val = val.replace("\r\n", "\n").replace("\r", "\n").strip()
        if key == "share.brand_icon":
            val = val.replace("\n", "")
        if len(val) > limit:
            notes.append(f"{FIELD_LABELS[key]}超过 {limit} 字，已截断")
            val = val[:limit]
        cleaned[key] = val
    return cleaned, notes


def save(db: Session, raw: dict[str, str]) -> list[str]:
    """保存（空字符串也写库——空=显式回落默认，例如清空标题语）。"""
    cleaned, notes = normalize(raw)
    for key, val in cleaned.items():
        settings_svc.set_(db, key, val)
    return notes


def reset(db: Session) -> None:
    """恢复默认：删掉库里所有 share.* 键（读时自然回落默认）。"""
    from sqlalchemy import delete

    from ..models import AppSetting

    db.execute(delete(AppSetting).where(AppSetting.key.like("share.%")))
