"""配色主题（清锋蓝 + 明暗双主题）的回归测试。

**背景**：v1.13.1 把全站配色从「莫兰迪暖纸灰」换成「清锋蓝」，并加了深色模式。
深色主题和亮色主题是两套独立的 CSS 变量，它们的失效方式**全是静默的**——
没有报错、没有异常，只有肉眼看才能发现页面花掉了。所以这里把四条钉死：

1. **两套变量键名必须完全一致**。深色漏定义某个变量时，浏览器会回落到亮色的值，
   表现是「深色模式里某一块是亮的」，但 CSS 语法完全合法，测试以外的任何检查都发现不了。
   例外：圆角三档有意共用（形状是产品性格，不随光照变）。
2. **表单控件不继承 color**。只改 `background` 会出现「深色底 + 黑字」，
   必须同时给 `color: var(--ink)`。
3. **深色下不锁打印就印出一片黑**。`@media print` 里必须把变量强制回亮色。
4. **硬编码 `#fff` 在深色下变刺眼白块**。凡是「白块」都必须走 `--field-bg`。

另外锁住主题切换的三件套：首屏防闪白脚本（必须在 CSS 之后、`<body>` 之前跑到）、
三态切换按钮、`localStorage` 键名。改这几处之前先读一遍 layout.html 的注释。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "app" / "static" / "app.css"
LAYOUT = ROOT / "app" / "templates" / "layout.html"

# 圆角不随明暗变，深色里不重复定义
SHARED_OK = {"--radius-sm", "--radius-md", "--radius"}


def _vars(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert m, f"app.css 里找不到 {selector} 块"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def test_dark_declares_every_light_variable():
    """深色必须覆盖亮色的每一个变量（圆角除外），否则会静默回落成亮色值。"""
    css = CSS.read_text()
    light = _vars(css, ":root")
    dark = _vars(css, 'html[data-theme="dark"]')
    assert len(light) > 30, f"亮色变量只有 {len(light)} 个，是不是 :root 被误删了？"
    missing = sorted(set(light) - set(dark) - SHARED_OK)
    assert not missing, (
        "深色主题漏定义这些变量，它们在深色下会回落到亮色值（页面会花，但不会报错）：\n  "
        + "\n  ".join(missing)
    )


def test_dark_does_not_invent_unknown_variables():
    """反过来也查：深色里出现亮色没有的变量，说明拼错了名字（同样静默失效）。"""
    css = CSS.read_text()
    light = _vars(css, ":root")
    dark = _vars(css, 'html[data-theme="dark"]')
    extra = sorted(set(dark) - set(light))
    assert not extra, f"深色主题里定义了亮色没有的变量（多半是拼错）：{extra}"


def test_form_controls_set_color_not_only_background():
    """表单控件不继承 color —— 只改背景就是「深色底 + 黑字」。"""
    css = CSS.read_text()
    m = re.search(r"input, select, textarea \{(.*?)\n\}", css, re.S)
    assert m, "app.css 里找不到 input, select, textarea 规则"
    block = m.group(1)
    assert "background: var(--field-bg)" in block, "输入框底色应走 --field-bg（否则深色下是白块）"
    assert "color: var(--ink)" in block, "输入框必须显式给 color，否则深色下是黑字配深底"
    assert re.search(r"input::placeholder[^{]*\{[^}]*var\(--muted\)", css), "placeholder 要走 --muted"


def test_print_forces_light_background():
    """深色主题下打印会印出一片黑，打印块必须把底色锁回白。"""
    css = CSS.read_text()
    m = re.search(r"@media print \{(.*)", css, re.S)
    assert m, "app.css 里没有 @media print，深色下打印会一页黑底"
    block = m.group(1)
    for need in ("--bg: #ffffff", "--card: #ffffff", "--ink: #000000"):
        assert need in block, f"打印块里缺 {need}"
    assert re.search(r"\.sidebar[^{]*\{[^}]*display: none", block), "打印时应隐藏侧栏，否则单据会带导航"


def test_white_blocks_use_field_bg_variable():
    """「白块」一律走 --field-bg。留着硬编码 #fff 的地方必须在白名单里。"""
    css = CSS.read_text()
    # 打印块里故意写死白色（它就是要把深色锁回白纸），不参与本项检查
    css = css.split("@media print")[0]
    allowed_snippets = (
        "color: #fff",
        "background: #0b1220",   # 灯箱遮罩：永远是深底
        ".share-qr img",         # 二维码图片底：必须真白，否则部分扫码器识别不出
    )
    offenders = []
    for line in css.splitlines():
        if "background: #fff" not in line and "background: #ffffff" not in line:
            continue
        if any(s in line for s in allowed_snippets):
            continue
        offenders.append(line.strip()[:90])
    assert not offenders, (
        "这些地方还是硬编码白底，深色模式下会变成刺眼的白块（改用 --field-bg）：\n  "
        + "\n  ".join(offenders)
    )


def test_layout_has_theme_bootstrap_and_toggle():
    """首屏必须尽早定明暗（否则深色用户先闪一下白），且要有三态切换入口。"""
    html = LAYOUT.read_text()
    assert "hvaccrm-theme" in html, "localStorage 键名变了？切换逻辑和读取必须用同一个键"
    assert 'data-theme' in html, "首屏脚本应在 <html> 上落 data-theme"
    assert 'id="themeToggle"' in html, "侧栏缺少主题切换按钮"
    # 防闪白脚本必须在样式表之后、</head> 之前（顺序错了就会先白后黑）
    head = html.split("</head>")[0]
    assert "app.css" in head and html.index("app.css") < html.index("hvaccrm-theme"), (
        "主题脚本必须排在 app.css 之后、且在首次绘制前执行，否则会闪白"
    )
    assert "prefers-color-scheme" in html, "auto 模式要跟随系统变化"


def test_manifest_colors_match_theme():
    """PWA 启动屏颜色写死在 manifest 里，换色时最容易忘。"""
    manifest = (ROOT / "app" / "static" / "manifest.webmanifest").read_text()
    assert "#efebe3" not in manifest, "manifest 里还留着旧配色 #efebe3（PWA 启动屏会是旧暖灰）"
    assert "#353a41" not in manifest, "manifest 里还留着旧侧栏色 #353a41"


def test_theme_toggle_sits_under_settings_in_nav():
    """v1.15.8：明暗切换从侧栏底部搬进导航区，紧排在「系统」下方。

    位置本身就是需求（用户要求「调整到侧栏系统下方」），所以直接对 DOM 顺序断言，
    不做像素级检查。三个要点：
    1. 按钮在 `<nav>` 里面，不再是 `.me` 底部那块；
    2. 排在有权限渲染出的「系统」之后 —— 若哪天菜单顺序变了，这里会先报；
    3. 三个图标 + 三份文字常驻 DOM（靠脚本只显示一个），别退回 `textContent = ...`
       的写法：那会把图标一起冲掉，折叠态就只剩一个空壳。
    """
    html = LAYOUT.read_text()
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
    assert 'id="themeToggle"' in nav, "明暗切换按钮应在侧栏导航区（<nav>）内"
    assert nav.index('id="themeToggle"') > nav.index(">系统</a>"), (
        "明暗切换按钮应排在「系统」之后"
    )

    me = html.split('class="me"', 1)[1].split("</aside>", 1)[0]
    assert "themeToggle" not in me, "旧的侧栏底部按钮还在？两处 id 重复会让脚本只认第一个"

    btn = nav.split('id="themeToggle"', 1)[1].split("</button>", 1)[0]
    for mode in ("auto", "light", "dark"):
        assert f'data-theme-icon="{mode}"' in btn, f"缺少 {mode} 模式的图标"
        assert f'data-theme-label="{mode}"' in btn, f"缺少 {mode} 模式的文字"
    assert "textContent = LABEL" not in html, (
        "别再用 textContent 覆盖按钮内容 —— 图标会一起没掉"
    )
