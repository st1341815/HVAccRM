#!/usr/bin/env python3
"""把 app.css 换成「清锋蓝」主题（亮色 + 深色），并清掉硬编码色值。

设计原则：
  1. 只动颜色，不动任何布局/字号/间距 —— 那些有回归测试钉着。
  2. 深色块与 :root 的变量键名必须完全一致（脚本末尾会自检）。
  3. 凡是「白块」一律换成 --field-bg，否则深色模式下会一片刺眼白。
  4. 有两处白色**故意保留**：二维码图片底（白底才扫得出）、灯箱（永远深底）。

跑完输出自检报告，人工过一遍再提交。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSS = HERE.parent.parent / "app" / "static" / "app.css"

# ---------------------------------------------------------------- 亮色 token
ROOT = """/* ════════════════════════════════════════════════════════════════════
   配色主题：清锋蓝（restrained SaaS blue）
   ────────────────────────────────────────────────────────────────────
   换肤只改这一处：所有组件只引用变量，全文件不出现裸色值（打印块除外）。
   深色同位体见下方 html[data-theme="dark"] —— 两套变量键名必须完全一致，
   深色漏定义某个变量时会静默回落成亮色值，页面会当场花掉。
   验收口径（WCAG AA 之上留余量）：正文 ≥13:1、次要字 ≥5:1、白字按钮 ≥6:1。
   ════════════════════════════════════════════════════════════════════ */
:root {
  /* 基础层 */
  --bg: #f5f7fa;                     /* 页面底：冷白偏蓝灰 */
  --card: #ffffff;                   /* 卡片 / 表格底 */
  --paper-2: #f3f7fa;                /* 斑马纹 */
  --paper-3: #edf3fa;                /* 表头 / 合计行 / 浅填充 */
  --field-bg: #ffffff;               /* 输入框、胶囊 tab、任务卡等一切「白块」 */
  --ink: #16202e;                    /* 主文字 16.4:1 */
  --muted: #5c6a80;                  /* 标签 / 次要文字 5.5:1 */
  --line: #d5dfee;                   /* 发丝线（装饰级 1.34:1） */
  --card-border: #dbe3ee;            /* 卡片描边，比发丝线略深一档做分层 */

  /* 主色层 */
  --brand: #2563eb;                  /* 图标 / 指示条 / 选中胶囊底 */
  --brand-dark: #1d4ed8;
  --brand-strong: #1d4ed8;           /* 实心按钮底（白字 6.70:1） */
  --brand-strong-hover: #1a4bc4;     /* ⚠️ 实心按钮 hover 专用，不能复用 --brand-ink */
  --brand-ink: #1a4bc4;              /* 链接 / 文字按钮（对白 7.37:1） */
  --brand-50: #eaf1fe;               /* 幽灵按钮底 / 选中底 */
  --brand-50-hover: #dbe7fd;
  --brand-200: #b6cdf8;              /* 焦点环 / 侧栏指示条 */

  /* 语义层（浅底 + 深字 + 边框三档） */
  --danger-ink: #b42318;
  --danger-bg: #fdeceb;
  --danger-border: #f6cfcb;
  --success-ink: #146b3a;
  --success-bg: #e7f5ec;
  --success-border: #bfe3cd;
  --warning-ink: #8a5300;
  --warning-bg: #fdf4e0;
  --warning-border: #f0d9a6;
  /* 「样板间」标记：独立语义，别借用 danger —— 借了会让人以为这条出错了 */
  --tag-room-bg: #f0e2e2;
  --tag-room-ink: #8f5a5a;

  /* 深色侧栏 */
  --sidebar-bg: #16233a;
  --sidebar-fg: #dbe4f2;
  --sidebar-fg-muted: #94a4bd;
  --sidebar-hover: rgba(255, 255, 255, .07);
  --sidebar-active: rgba(255, 255, 255, .12);

  /* 交互 */
  --row-hover: #ecf2fb;              /* 表格行悬浮（对卡片 1.13） */

  /* 尺度 */
  --radius-sm: 6px;                  /* 控件级：按钮 / 输入 / 小卡 */
  --radius-md: 8px;                  /* 面板内小块 */
  --radius: 10px;                    /* 卡片 / 面板级 */
  --shadow-md: 0 4px 16px -4px rgb(20 35 60 / .10), 0 1px 2px rgb(20 35 60 / .06);
}"""

# ---------------------------------------------------------------- 深色 token
DARK = """/* ════════════════════════════════════════════════════════════════════
   深色主题
   ────────────────────────────────────────────────────────────────────
   不是把亮色反相，而是重新定一套语义：
   · 卡片（#161e2a）比页面底（#0e141c）**亮** —— 和亮色同一条分层逻辑，幅度收窄
   · 正文不用纯白（#e6ecf5）—— 纯白配深底长时间盯屏，笔画边缘有眩光感
   · --brand-dark / --brand-ink 在深色下**反向提亮**：它们承载幽灵按钮文字与链接
   · 实心按钮 hover 必须走 --brand-strong-hover：深色下 --brand-ink 是亮蓝，
     拿它当 hover 底色会让白字直接失效
   · 阴影在深底上几乎不可见，分层交给 --card-border（1.49:1）
   ════════════════════════════════════════════════════════════════════ */
html[data-theme="dark"] {
  --bg: #0e141c;
  --card: #161e2a;
  --paper-2: #121a24;
  --paper-3: #1c2634;
  --field-bg: #1a2431;
  --ink: #e6ecf5;                    /* 14.1:1 */
  --muted: #94a3b8;                  /* 6.5:1 */
  --line: #2b3746;
  --card-border: #2f3c4c;

  --brand: #2b66d9;                  /* 白字 5.24:1（选中胶囊底靠它） */
  --brand-dark: #8ab4ff;             /* ⚠️ 深色下变亮：幽灵按钮文字用它 */
  --brand-strong: #2b66d9;
  --brand-strong-hover: #1e50b8;
  --brand-ink: #7db3ff;              /* 链接，对卡片 7.79:1 */
  --brand-50: #1a2b45;
  --brand-50-hover: #223751;
  --brand-200: #4d86c9;

  --danger-ink: #ff938a;
  --danger-bg: #3a1f21;
  --danger-border: #5c3033;
  --success-ink: #6ed08f;
  --success-bg: #16321f;
  --success-border: #2a5136;
  --warning-ink: #e8bd63;
  --warning-bg: #33280f;
  --warning-border: #5b4618;
  --tag-room-bg: #3a2530;
  --tag-room-ink: #e3aabb;

  --sidebar-bg: #090f16;
  --sidebar-fg: #ccd7e5;
  --sidebar-fg-muted: #7f8fa4;
  --sidebar-hover: rgba(255, 255, 255, .07);
  --sidebar-active: rgba(255, 255, 255, .12);

  --row-hover: #212f42;              /* 对卡片 1.24，深色下要比亮色更明显才找得到行 */

  --shadow-md: 0 4px 16px -4px rgb(0 0 0 / .55), 0 1px 2px rgb(0 0 0 / .35);
}"""

# ---------------------------------------------------------------- 打印兜底
PRINT = """
/* ---------------------------------------------------------------- 打印 */
/* 深色主题下直接打印会印出一片黑。打印一律锁回亮色、去掉侧栏，
   保证合同台账 / 对账单打出来是白纸黑字。
   注意也要覆盖 :root（亮色态），否则变量沿用屏幕上的灰底。 */
@media print {
  :root,
  html[data-theme="dark"] {
    --bg: #ffffff;
    --card: #ffffff;
    --paper-2: #ffffff;
    --paper-3: #f2f2f2;
    --field-bg: #ffffff;
    --ink: #000000;
    --muted: #333333;
    --line: #bbbbbb;
    --card-border: #999999;
    --row-hover: #ffffff;
    --brand: #1d4ed8;
    --brand-dark: #1d4ed8;
    --brand-strong: #1d4ed8;
    --brand-strong-hover: #1d4ed8;
    --brand-ink: #1d4ed8;
    --brand-50: #f2f2f2;
    --brand-50-hover: #f2f2f2;
    --brand-200: #999999;
    --sidebar-bg: #ffffff;
    --sidebar-fg: #000000;
    --sidebar-fg-muted: #333333;
    --sidebar-hover: transparent;
    --sidebar-active: transparent;
    color-scheme: light;
  }
  .sidebar,
  .sidebar-toggle,
  .scrim { display: none !important; }
  .wrap { max-width: none; padding: 0; }
  .card { box-shadow: none; break-inside: avoid; }
  .kpi { break-inside: avoid; }
  body { background: #fff; }
}
"""

# ---------------------------------------------------------------- 硬编码替换
# (原文, 替换) —— 全部要求命中，命中数不对就报错，防止「改了个寂寞」
REPLACES: list[tuple[str, str, int]] = [
    # 徽章边框写死成亮色值，深色下会跳出一圈浅绿/浅黄
    (".sec-badge.high { color: var(--success-ink); background: var(--success-bg); border-color: #cfdcc8; }",
     ".sec-badge.high { color: var(--success-ink); background: var(--success-bg); border-color: var(--success-border); }", 1),
    (".sec-badge.mid { color: var(--warning-ink); background: var(--warning-bg); border-color: #e3d6b8; }",
     ".sec-badge.mid { color: var(--warning-ink); background: var(--warning-bg); border-color: var(--warning-border); }", 1),
    # 样板间标记
    (".badge.showroom { background: #f0e2e2; color: #8f5a5a; }",
     ".badge.showroom { background: var(--tag-room-bg); color: var(--tag-room-ink); }", 1),
    # 表单控件：底色 + 文字色都要给，否则深色下是「白底黑字」或「深底黑字」
    ("  border-radius: var(--radius-sm); font: inherit; line-height: 1.6; background: #fff;\n}",
     "  border-radius: var(--radius-sm); font: inherit; line-height: 1.6;\n  background: var(--field-bg); color: var(--ink);\n}\ninput::placeholder, textarea::placeholder { color: var(--muted); opacity: .8; }", 1),
    (".checkgrid label.checkline { background: #fff;", ".checkgrid label.checkline { background: var(--field-bg);", 1),
    ("details.ms { border: 1px solid var(--line); border-radius: var(--radius-md); padding: .45rem .6rem; background: #fff; }",
     "details.ms { border: 1px solid var(--line); border-radius: var(--radius-md); padding: .45rem .6rem; background: var(--field-bg); }", 1),
    ("details.ms .ms-panel label.checkline { background: #fff;", "details.ms .ms-panel label.checkline { background: var(--field-bg);", 1),
    (".chip { display: inline-block; padding: .3rem .7rem; border-radius: 999px; background: #fff;",
     ".chip { display: inline-block; padding: .3rem .7rem; border-radius: 999px; background: var(--field-bg);", 1),
    (".task-card { border: 1px solid var(--line); border-radius: var(--radius); padding: .7rem .8rem; background: #fff; }",
     ".task-card { border: 1px solid var(--line); border-radius: var(--radius); padding: .7rem .8rem; background: var(--field-bg); }", 1),
    (".tabs a { padding: .3rem .8rem; border-radius: 999px; background: #fff;",
     ".tabs a { padding: .3rem .8rem; border-radius: 999px; background: var(--field-bg);", 1),
    (".login-card { max-width: 380px; margin: 8vh auto; background: #fff;",
     ".login-card { max-width: 380px; margin: 8vh auto; background: var(--field-bg);", 1),
    (".gallery figure { margin: 0; background: #fff;", ".gallery figure { margin: 0; background: var(--field-bg);", 1),
    (".share-card { max-width: 420px; width: 100%; background: #fff;", ".share-card { max-width: 420px; width: 100%; background: var(--field-bg);", 1),
    (".preview-body { padding: 1.2rem 1.2rem .6rem; background: #fff; }",
     ".preview-body { padding: 1.2rem 1.2rem .6rem; background: var(--field-bg); }", 1),
    (".preview-box .foot-legal { background: #fff;", ".preview-box .foot-legal { background: var(--field-bg);", 1),
    # 移动端卡片化的行底
    ("background: var(--row-bg, #fff);", "background: var(--row-bg, var(--card));", 1),
    # 按钮 hover：原来用 --brand-ink，深色下那是亮蓝，白字会失效
    ("button:hover { background: var(--brand-ink); }", "button:hover { background: var(--brand-strong-hover); }", 1),
    (".btn:hover { background: var(--brand-ink); text-decoration: none; }",
     ".btn:hover { background: var(--brand-strong-hover); text-decoration: none; }", 1),
]

# 故意保留的裸色值：命中即说明是「误改」或需要人眼确认
KEEP_WHITE = [
    (".share-qr img", "二维码必须白底才扫得出来"),
    (".lightbox", "灯箱永远深底，与主题无关"),
    ("color: #fff", "按钮/侧栏上的白字，深色下依然成立"),
]


def main() -> None:
    css = CSS.read_text(encoding="utf-8")
    shutil.copyfile(CSS, CSS.with_suffix(".css.bak"))

    # 1) 替换 :root 整块（含原注释）
    new_css, n = re.subn(r":root \{.*?\n\}", ROOT, css, count=1, flags=re.S)
    assert n == 1, "没找到 :root 块"
    css = new_css

    # 2) 深色块插在 :root 之后
    css = css.replace(ROOT, ROOT + "\n\n" + DARK, 1)

    # 3) color-scheme：让滚动条、日期选择器、下拉面板这些原生控件跟着变
    css = css.replace(
        "* { box-sizing: border-box; }",
        "/* 原生控件（滚动条 / 日期选择器 / 下拉面板）跟随明暗 */\n"
        "html { color-scheme: light; }\n"
        "html[data-theme=\"dark\"] { color-scheme: dark; }\n"
        "* { box-sizing: border-box; }",
        1,
    )

    # 4) 逐条替换硬编码
    report = []
    for old, new, expect in REPLACES:
        got = css.count(old)
        assert got == expect, f"命中数不符（期望 {expect} 实得 {got}）：{old[:60]}…"
        css = css.replace(old, new)
        report.append(f"  ✓ {old[:58]:<58} ×{got}")

    # 5) 打印块追加到末尾
    css = css.rstrip() + "\n" + PRINT
    CSS.write_text(css, encoding="utf-8")

    # ---------------------------------------------------------------- 自检
    print("替换明细：")
    print("\n".join(report))

    light = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", re.search(r":root \{(.*?)\n\}", css, re.S).group(1)))
    dark = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", re.search(r'html\[data-theme="dark"\] \{(.*?)\n\}', css, re.S).group(1)))
    # 圆角三档故意不随明暗变（形状是产品性格，不是光照），深色里不重复定义
    SHARED_OK = {"--radius-sm", "--radius-md", "--radius"}
    print(f"\n变量完整性：亮色 {len(light)} 个 / 深色 {len(dark)} 个")
    miss = sorted(set(light) - set(dark) - SHARED_OK)
    print("深色缺失：", miss or f"无 ✓（圆角 {len(SHARED_OK)} 个为有意共用）")
    assert not miss, f"深色漏定义这些变量，会回落成亮色值：{miss}"

    # 剩余裸色值盘点（排除 :root / 深色 / 打印三块）
    stripped = re.sub(r":root \{.*?\n\}", "", css, flags=re.S)
    stripped = re.sub(r'html\[data-theme="dark"\] \{.*?\n\}', "", stripped, flags=re.S)
    stripped = re.sub(r"@media print \{.*?\n\}\n", "", stripped, flags=re.S)
    left = re.findall(r"^\s*(\S.*#[0-9a-fA-F]{3,6}.*)$", stripped, re.M)
    print(f"\n剩余裸色值 {len(left)} 处（应全部属于「故意保留」）：")
    for ln in left:
        print("   ", ln.strip()[:110])


if __name__ == "__main__":
    main()
