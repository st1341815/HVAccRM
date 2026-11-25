#!/usr/bin/env python3
"""HVAccRM 配色方案预览图生成器。

做法：引入**真实线上 app.css**，只覆盖 :root 变量 —— 渲染结果 = 改完线上后的真实样子。
输出 4 个 html：baseline（现状）+ A/B/C 三个方案，再交给 chromium headless 截图。
"""
import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
CSS_SRC = HERE.parent.parent / "app" / "static" / "app.css"

# ------------------------------------------------------------------ 变量集
BASE = """  --bg: #efebe3; --card: #fbf9f5; --paper-2: #f7f4ee; --paper-3: #f1efe9;
  --ink: #45423c; --muted: #6f6b64; --line: #e5dfd5; --card-border: #e2dbd0;
  --brand: #6c7f91; --brand-dark: #596b7c; --brand-strong: #5b6f83; --brand-ink: #4f6376;
  --brand-50: #e9edf1; --brand-50-hover: #dde4ea; --brand-200: #b9c7d4;
  --danger-ink: #8f4a4a; --danger-bg: #f7eded; --danger-border: #e6c9c9;
  --success-ink: #4f7059; --success-bg: #e6ece1; --success-border: #c3d2c0;
  --warning-ink: #7d6540; --warning-bg: #f4eedf; --warning-border: #dfd0a8;
  --sidebar-bg: #3a3f47; --sidebar-fg: #d6d2ca; --sidebar-fg-muted: #a8adb4;
  --row-hover: #f6f3ed;
  --radius-sm: 6px; --radius-md: 8px; --radius: 10px;
  --shadow-md: 0 4px 14px -2px rgb(74 71 66 / .12), 0 1px 3px rgb(74 71 66 / .07);"""

A = """  --bg: #f4f6f7; --card: #ffffff; --paper-2: #f3f7f8; --paper-3: #edf2f4;
  --ink: #1c2529; --muted: #5b6b71; --line: #d3dde1; --card-border: #d5dee2;
  --brand: #0f6b73; --brand-dark: #0b565d; --brand-strong: #0a5a61; --brand-ink: #0b5c64;
  --brand-50: #e6f2f3; --brand-50-hover: #d7e9eb; --brand-200: #a8ccd0;
  --danger-ink: #a3272b; --danger-bg: #fbeeed; --danger-border: #efc9c6;
  --success-ink: #1f6a41; --success-bg: #e8f3ea; --success-border: #c2ddc9;
  --warning-ink: #7a5a13; --warning-bg: #fbf3dd; --warning-border: #e8d3a0;
  --sidebar-bg: #1e2a2e; --sidebar-fg: #d3dcde; --sidebar-fg-muted: #93a3a7;
  --row-hover: #e8f0f2;
  --radius-sm: 4px; --radius-md: 6px; --radius: 8px;
  --shadow-md: 0 2px 8px -2px rgb(24 40 45 / .10), 0 1px 2px rgb(24 40 45 / .06);"""

B = """  --bg: #f5f7fa; --card: #ffffff; --paper-2: #f3f7fa; --paper-3: #edf3fa;
  --ink: #16202e; --muted: #5c6a80; --line: #d5dfee; --card-border: #dbe3ee;
  --brand: #2563eb; --brand-dark: #1d4ed8; --brand-strong: #1d4ed8; --brand-ink: #1a4bc4;
  --brand-50: #eaf1fe; --brand-50-hover: #dbe7fd; --brand-200: #b6cdf8;
  --danger-ink: #b42318; --danger-bg: #fdeceb; --danger-border: #f6cfcb;
  --success-ink: #146b3a; --success-bg: #e7f5ec; --success-border: #bfe3cd;
  --warning-ink: #8a5300; --warning-bg: #fdf4e0; --warning-border: #f0d9a6;
  --sidebar-bg: #16233a; --sidebar-fg: #dbe4f2; --sidebar-fg-muted: #94a4bd;
  --row-hover: #ecf2fb;
  --brand-strong-hover: #1a4bc4; --field-bg: #ffffff;
  --tag-room-bg: #f0e2e2; --tag-room-ink: #8f5a5a;
  --radius-sm: 6px; --radius-md: 8px; --radius: 10px;
  --shadow-md: 0 4px 16px -4px rgb(20 35 60 / .10), 0 1px 2px rgb(20 35 60 / .06);"""

C = """  --bg: #f7f2e8; --card: #ffffff; --paper-2: #f9f4e7; --paper-3: #f1e9d9;
  --ink: #332c22; --muted: #6b6152; --line: #e7dbc9; --card-border: #ddcfb7;
  --brand: #a8562f; --brand-dark: #7f3d1f; --brand-strong: #9c4a26; --brand-ink: #8a3f1e;
  --brand-50: #f7e9e0; --brand-50-hover: #f0dccd; --brand-200: #e0b79c;
  --danger-ink: #9c1f33; --danger-bg: #fbeaee; --danger-border: #efc7d0;
  --success-ink: #2f6b3f; --success-bg: #e9f0e2; --success-border: #c8d8bb;
  --warning-ink: #7a5200; --warning-bg: #f9efd6; --warning-border: #e6cf9c;
  --sidebar-bg: #2e2820; --sidebar-fg: #e2d8c9; --sidebar-fg-muted: #a89c8a;
  --row-hover: #f5ebd8;
  --radius-sm: 8px; --radius-md: 10px; --radius: 12px;
  --shadow-md: 0 6px 18px -4px rgb(74 58 40 / .14), 0 1px 3px rgb(74 58 40 / .08);"""

# 深色模式：不是把亮色反相，而是重新定一套语义。
#   · 卡片（#161e2a）比页面底（#0e141c）**亮** —— 和亮色同一条分层逻辑，只是幅度收窄
#   · 正文不用纯白（#e6ecf5），避免长时间盯屏的眩光
#   · --brand-dark / --brand-ink 在深色下必须**变亮**（它们承载 ghost 按钮文字与链接）
#   · 实心按钮单独拆出 --brand-strong-hover：深色下不能靠 --brand-ink 做 hover（那是亮蓝，白字会失效）
DARK = """  --bg: #0e141c; --card: #161e2a; --paper-2: #121a24; --paper-3: #1c2634;
  --ink: #e6ecf5; --muted: #94a3b8; --line: #2b3746; --card-border: #2f3c4c;
  --brand: #2b66d9; --brand-dark: #8ab4ff; --brand-strong: #2b66d9; --brand-strong-hover: #1e50b8;
  --brand-ink: #7db3ff;
  --brand-50: #1a2b45; --brand-50-hover: #223751; --brand-200: #4d86c9;
  --danger-ink: #ff938a; --danger-bg: #3a1f21; --danger-border: #5c3033;
  --success-ink: #6ed08f; --success-bg: #16321f; --success-border: #2a5136;
  --warning-ink: #e8bd63; --warning-bg: #33280f; --warning-border: #5b4618;
  --sidebar-bg: #090f16; --sidebar-fg: #ccd7e5; --sidebar-fg-muted: #7f8fa4;
  --row-hover: #212f42;
  --field-bg: #1a2431; --tag-room-bg: #3a2530; --tag-room-ink: #e3aabb;
  --radius-sm: 6px; --radius-md: 8px; --radius: 10px;
  --shadow-md: 0 4px 16px -4px rgb(0 0 0 / .55), 0 1px 2px rgb(0 0 0 / .35);"""

SCHEMES = [
    ("B-light", "方案 B · 清锋蓝（亮色）", "#f5f7fa", False),
    ("B-dark", "方案 B · 清锋蓝（深色）", "#0b1119", True),
]

# ------------------------------------------------------------------ 图标
ICONS = {
    "dash": '<rect x="3" y="3" width="7.5" height="8" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="5" rx="1.5"/><rect x="13.5" y="11" width="7.5" height="10" rx="1.5"/><rect x="3" y="14" width="7.5" height="7" rx="1.5"/>',
    "cust": '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5c.7-3.1 2.9-4.7 5.5-4.7s4.8 1.6 5.5 4.7"/><circle cx="17" cy="9" r="2.4"/><path d="M15.8 14.8c2.4.3 4 1.7 4.4 4.2"/>',
    "room": '<rect x="5" y="3" width="14" height="18" rx="1.5"/><path d="M9 7h2M13 7h2M9 11h2M13 11h2M9 15h2M13 15h2"/>',
    "contract": '<path d="M6 2.5h8.5L19 7v14.5H6z"/><path d="M14.5 2.5V7H19"/><path d="M9 12h6M9 15.5h6"/>',
    "money": '<circle cx="12" cy="12" r="9"/><path d="M8.5 8.5l3.5 4 3.5-4"/><path d="M12 12.5V17M9.8 14.5h4.4"/>',
    "cost": '<rect x="6" y="3" width="12" height="18" rx="1.5"/><path d="M9 7h6"/><path d="M9.5 12h.01M12 12h.01M14.5 12h.01M9.5 15.5h.01M12 15.5h.01M14.5 15.5h.01"/>',
    "task": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "acct": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8"/><path d="M8 11h2M12 11h2M16 11h.01M8 15h2M12 15h2M16 15h.01M8 19h6"/>',
    "case": '<path d="M8 21h8M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4"/>',
    "report": '<path d="M4 20h16"/><rect x="6" y="12" width="3.2" height="5" rx="0.6"/><rect x="10.4" y="8" width="3.2" height="9" rx="0.6"/><rect x="14.8" y="4" width="3.2" height="13" rx="0.6"/>',
    "users": '<circle cx="12" cy="8" r="3.5"/><path d="M5.5 20c.8-3.4 3.3-5 6.5-5s5.7 1.6 6.5 5"/>',
    "sys": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
}

NAV = [
    ("dash", "看板", True), ("cust", "客户", False), ("room", "楼盘/房号", False),
    ("contract", "合同", False), ("money", "收款", False), ("cost", "成本", False),
    ("task", "施工", False), ("acct", "核算", False), ("case", "案例", False),
    ("report", "报表", False), ("users", "用户", False), ("sys", "系统", False),
]


def nav_html():
    out = []
    for key, label, active in NAV:
        cls = ' class="active"' if active else ""
        ico = ICONS[key]
        out.append(
            f'<a href="#"{cls}><span class="ico"><svg viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{ico}</svg></span>{label}</a>'
        )
    return "\n      ".join(out)


# 客户/合同混合列表：覆盖斑马纹、金额加粗、徽章、alert 行、合计行
ROWS = [
    ("阳光美地 12-2-1801", "周建国", "大金", "中央空调+地暖", "186,000", "186,000", "0", "已完工"),
    ("橡树湾 3-1-0602", "李美玲", "日立", "中央空调", "94,800", "60,000", "34,800", "施工中"),
    ("中海城 8-2-1103", "王海涛", "格力", "风管机", "62,400", "20,000", "42,400", "施工中"),
    ("金地天府 5-1-2004", "陈晓红", "三菱", "中央空调+新风", "258,000", "258,000", "0", "已完工"),
    ("锦绣山河 9-3-0801", "赵志强", "东芝", "地暖", "118,500", "0", "118,500", "待开工"),
    ("万科金域 2-1-1602", "孙丽娟", "美的", "风管机+净水", "47,600", "30,000", "17,600", "施工中"),
]
STATUS_CLS = {"已完工": "s-done", "施工中": "s-doing", "待开工": "s-wait"}


def rows_html():
    out = []
    for i, (room, name, brand, scope, amt, paid, debt, st) in enumerate(ROWS):
        cls = ""
        if st == "待开工":
            cls = ' class="row-alert"'
        debt_cell = f'<td class="nowrap {"red" if debt != "0" else ""}">{debt}</td>'
        out.append(
            f'<tr{cls}><td class="nowrap"><a href="#">{room}</a></td><td>{name}</td>'
            f'<td class="nowrap">{brand}</td><td class="nowrap small muted">{scope}</td>'
            f'<td class="nowrap"><strong>{amt}</strong></td><td class="nowrap">{paid}</td>'
            f'{debt_cell}<td class="nowrap"><span class="badge">{st}</span></td></tr>'
        )
    return "\n      ".join(out)


TEMPLATE = """<!doctype html>
<html lang="zh-CN"__HTMLATTR__><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="__THEME__">
<title>__TITLE__ · 配色预览</title>
<link rel="stylesheet" href="app.css">
<style>
/* 亮色 = 默认；深色 = html[data-theme="dark"] 覆盖。
   两套变量同时写进来是刻意的：深色页里若有变量漏定义，会直接露出亮色的值当场翻车，
   等于一个自动自检。落地时线上也是这个结构。 */
:root {
__LIGHT__
}
html[data-theme="dark"] {
__DARK__
}
/* ⚠️ 主题补丁：线上 app.css 里有 32 处硬编码色值，深色模式下会变成刺眼白块 / 错色徽章。
   下面这些必须从硬编码改成变量，否则深色模式是残的。落地时一并替换进 app.css。 */
html { color-scheme: light; }
html[data-theme="dark"] { color-scheme: dark; }
input, select, textarea { background: var(--field-bg); color: var(--ink); }
.tabs a { background: var(--field-bg); }
.checkgrid label.checkline { background: var(--field-bg); }
details.ms { background: var(--field-bg); }
.chip { background: var(--field-bg); }
.task-card { background: var(--field-bg); }
.sec-badge.high { border-color: var(--success-border); }
.sec-badge.mid { border-color: var(--warning-border); }
.badge.showroom { background: var(--tag-room-bg); color: var(--tag-room-ink); }
/* 实心按钮 hover 必须走独立变量：深色下 --brand-ink 是亮蓝（链接要用），拿来当 hover 底色白字会失效 */
button:hover, .btn:hover { background: var(--brand-strong-hover); }
/* 预览页专用：让徽章/胶囊用真实语义色（线上靠 .badge.s-* 类，见 app.css 第 400+ 行） */
h1 span.muted { font-size: .95rem; font-weight: 400; }
</style>
</head>
<body class="has-sidebar">
<div class="app">
  <aside class="sidebar">
    <div class="brand"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 4a2 2 0 0 1 4 0v9.3a4.5 4.5 0 1 1-4 0z"/><circle cx="12" cy="17.5" r="1.7"/></svg><span>HVAccRM</span><button type="button" class="collapse-btn" aria-label="折叠侧边栏">⟨</button></div>
    <nav>
      __NAV__
    </nav>
    <div class="me">
      <div class="who">13叔<div class="role">管理员</div></div>
      <div class="me-links"><a href="#">账户</a><button type="button" class="linklike">退出</button></div>
    </div>
  </aside>
  <main class="wrap">
    <div class="page-head">
      <h1 style="margin:0">客户管理 <span class="muted small">共 6 条 · 2026 年度</span></h1>
      <div class="actions">
        <a class="btn ghost" href="#">导出 Excel</a>
        <a class="btn" href="#">+ 新增客户</a>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi"><span class="kpi-num">12</span><span class="kpi-label">在施工地</span></div>
      <div class="kpi"><span class="kpi-num">2,846,500</span><span class="kpi-label">年销售额</span></div>
      <div class="kpi"><span class="kpi-num">2,193,000</span><span class="kpi-label">年收款</span></div>
      <div class="kpi"><span class="kpi-num">1,524,800</span><span class="kpi-label">年成本</span></div>
      <div class="kpi"><span class="kpi-num">1,321,700</span><span class="kpi-label">年利润</span></div>
      <div class="kpi alert"><span class="kpi-num">653,500</span><span class="kpi-label">未收总额</span></div>
    </div>

    <div class="tabs">
      <a href="#" class="active">全部 6</a><a href="#">施工中 3</a><a href="#">待开工 1</a><a href="#">已完工 2</a>
    </div>

    <section class="card">
      <div class="card-head">
        <h2>客户与合同</h2>
        <div class="actions">
          <span class="head-sum">合计应收 <strong>767,700</strong></span>
          <a class="btn ghost small" href="#">批量催款</a>
        </div>
      </div>
      <table>
        <thead><tr>
          <th class="nowrap">房号</th><th>客户</th><th class="nowrap">空调品牌</th>
          <th class="nowrap">业务范围</th><th class="nowrap">合同额</th>
          <th class="nowrap">已收</th><th class="nowrap">欠款</th><th class="nowrap">状态</th>
        </tr></thead>
        <tbody>
      __ROWS__
      <tr class="sum-row"><td class="sum-label">合计</td><td></td><td></td><td></td><td>767,300</td><td>554,000</td><td>213,300</td><td></td></tr>
        </tbody>
      </table>
    </section>

    <div class="grid2">
      <section class="card">
        <h2>新增客户</h2>
        <form class="stack">
          <label>客户姓名<input value="周建国"></label>
          <label>联系电话<input value="138 0000 1234"></label>
          <label>空调品牌<select><option>大金</option><option>日立</option><option>三菱</option></select></label>
          <label>意向等级<select><option>高意向 · 本月可签</option><option>中意向</option></select></label>
          <div class="checkline"><input type="checkbox" checked> 已现场勘测</div>
          <div style="display:flex;gap:.5rem;margin-top:.2rem">
            <button type="button" class="btn">保存客户</button>
            <button type="button" class="btn ghost">存为草稿</button>
            <button type="button" class="danger">删除</button>
          </div>
        </form>
      </section>
      <section class="card">
        <h2>本月待办</h2>
        <div class="card note-card" style="margin-bottom:.7rem">
          <strong>3 个工地欠款超 30 天</strong>
          <div class="small muted">建议本周内完成电话回访并登记跟进记录。</div>
        </div>
        <table class="tight">
          <thead><tr><th>房号</th><th>工序</th><th>状态</th></tr></thead>
          <tbody>
            <tr><td><a href="#">阳光美地 12-2-1801</a></td><td>内机吊装</td><td><span class="badge s-doing">进行中</span></td></tr>
            <tr><td><a href="#">中海城 8-2-1103</a></td><td>冷媒管焊接</td><td><span class="badge s-doing">进行中</span></td></tr>
            <tr><td><a href="#">万科金域 2-1-1602</a></td><td>隐蔽验收</td><td><span class="badge warn">待开始</span></td></tr>
            <tr><td><a href="#">金地天府 5-1-2004</a></td><td>调试交付</td><td><span class="badge s-done">已完成</span></td></tr>
          </tbody>
        </table>
        <p style="margin:.6rem 0 0"><a href="#">查看全部施工任务 →</a></p>
      </section>
    </div>

    <footer class="foot">
      <span class="sec-badge high"><i></i>安全等级：高</span><span class="foot-sep">·</span>v1.9.19 · HVAccRM — 暖通空调客户管理系统
    </footer>
  </main>
</div>
</body></html>
"""


def main():
    shutil.copyfile(CSS_SRC, HERE / "app.css")
    nav, rows = nav_html(), rows_html()
    for slug, title, theme, dark in SCHEMES:
        html = (
            TEMPLATE.replace("__LIGHT__", B)
            .replace("__DARK__", DARK)
            .replace("__HTMLATTR__", ' data-theme="dark"' if dark else "")
            .replace("__NAV__", nav)
            .replace("__ROWS__", rows)
            .replace("__TITLE__", title)
            .replace("__THEME__", theme)
        )
        (HERE / f"preview-{slug}.html").write_text(html, encoding="utf-8")
        print("wrote", f"preview-{slug}.html")


if __name__ == "__main__":
    main()
