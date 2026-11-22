# HVAccRM 安卓壳层三页 · 界面规格（v1 / 双方向）

| 项 | 值 |
|---|---|
| 适用版本 | App 0.1.3（versionCode 4）· 服务端 v1.12.x |
| 范围 | `assets/welcome.html`、`assets/offline.html`、`SettingsActivity.java` 三页 |
| 不在范围 | 业务页（服务端渲染）、悬浮球、拍照/下载链路 |
| 依赖 | 仅 `android.widget.*` + `android.graphics.drawable.*`（含 RippleDrawable / StateListDrawable / LayerDrawable） |
| 出图 | 360×780dp 与 412×915dp 两档，dp 直接当 px 渲染，状态栏 24dp / 手势条 24dp |

---

## 0. 共通约束（两方向都适用）

### 0.0 现状核对：先说清楚改的是哪儿

| # | 现状 | 问题 | 规格里的处理 |
|---|---|---|---|
| 1 | 站点按钮 `radius:6px`、卡片 `radius:10px`、`--shadow-md` 只给弹层 | 壳层用 `18px + 0 8px 30px`，比业务页更圆更浮 | A：卡片 12 / 控件 10 / 去重阴影；B：卡片 12 / 控件 14 / 全程无阴影 |
| 2 | 站点 `--card:#FBF9F5` `--ink:#45423C` | 壳层用 `#FFF` + `#353A41` | A 跟随站点；B 保持 `#FFF`（B 无阴影，需要白度撑层级） |
| 3 | 站点已有 danger/success 三档 | 壳层三页完全没用到 | A 直接引用站点值；B 收敛成 2 个新色 |
| 4 | `EdgeToEdge.apply(content, cb)` 把 insert 四边直接铺在 **root** 上 | A 要加墨色顶栏，inset 必须落在顶栏而不是 root | 见 §0.3，给出改法 |
| 5 | `AppTheme`：`windowLightStatusBar=false`；`SettingsTheme`：`true` | A 保持 false（墨顶栏）；**B 必须把 AppTheme 改成 true**（纸底要深色图标） | 见 §0.3 |
| 6 | `welcome.html` 示例写 `192.168.1.100:8090`，`Prefs.DEFAULT_SERVER=https://crm.example.com` | 三页示例值不统一 | 全部改用 **8090**，并只在 `Prefs` 里硬编码一次 |
| 7 | `offline.html` 的 `id="reason"` / `id="server"`，`crmapp://settings` / `crmapp://reload` | — | **全部保留**，新页面继续用同名 id 与深链 |

### 0.1 GradientDrawable 能力对照表（写规格前必须先认这个上限）

| 效果 | 能否 | 写法 / 替代 |
|---|---|---|
| 纯色圆角矩形 | ✅ | `setShape(RECTANGLE)` + `setColor()` + `setCornerRadius(dp)` |
| 四角不同半径 | ✅ | `setCornerRadii(new float[]{tl,tl,tr,tr,br,br,bl,bl})` |
| 单色描边 | ✅ | `setStroke(px, color)` |
| 描边宽度/颜色**随状态变** | ✅ | 两个 GradientDrawable 塞进 `StateListDrawable`（宽度也能变）；或 `setStroke(w, ColorStateList)`（API 21+，宽度不变） |
| 虚线描边 | ✅ | `setStroke(w, color, dashWidth, dashGap)` |
| 纯色圆/胶囊 | ✅ | `setShape(OVAL)` 或 `cornerRadius = height/2` |
| 内置 ripple | ✅ | `new RippleDrawable(ColorStateList, content, mask)`，mask 必须是同半径的 GradientDrawable，否则水波溢出成方形 |
| 渐变底 | ✅ 但**本方案禁用** | `setGradientType(LINEAR_GRADIENT)` |
| 柔和投影（blur shadow） | ❌ | ①**首选**：1dp 描边 + 底色差代替；②`View.setElevation()` 阴影色不可控（API 28+ 才有 `outlineAmbientShadowColor`），本方案不用；③`Paint.setShadowLayer` 要 software layer，滚动态掉帧，禁用 |
| 内阴影 / 外发光 | ❌ | 叠一层更小的 GradientDrawable（`LayerDrawable` inset） |
| 圆角裁切图片 | ✅ | `setClipToOutline(true)` + `ViewOutlineProvider` |
| 环形进度 / 圆弧 | ❌ | 自绘 `Canvas.drawArc` |
| 文字混排字重 | ❌ | `SpannableString` 分段 / 两个 TextView |
| 等宽字体 | ✅ | `Typeface.MONOSPACE`（免字体文件） |
| 中粗字重（600） | ✅ | `Typeface.create("sans-serif-medium", NORMAL)`；700 用 `setFakeBoldText` 或 `sans-serif` + `BOLD` |

> **结论**：本方案里 100% 的视觉都能落地。唯一妥协是「柔光投影」→ 全部改成 1dp 描边，两方向都不例外。

### 0.2 系统控件「驯服」清单（不照做就会露出默认长相）

```java
// Button：系统 Button 自带 minWidth/minHeight、灰底、全大写、点击抬升动画
b.setAllCaps(false);            // 中文无用但别漏，万一 label 混了英文
b.setMinWidth(0); b.setMinHeight(0);
b.setPadding(0, 0, 0, 0);       // 内边距交给 GradientDrawable + gravity
b.setStateListAnimator(null);   // ★ 不去掉会有点击时的 elevation 抖动
b.setBackground(ripple);        // 一定要 RippleDrawable，别直接塞 GradientDrawable（会失去按下反馈）
b.setGravity(Gravity.CENTER);
b.setIncludeFontPadding(false);

// EditText：默认背景是下划线 + 左侧 4dp 内建 padding
e.setBackground(statefulRounded);   // 盖掉下划线
e.setPadding(dp(14), 0, dp(14), 0);  // ★ 必须重设，否则文字偏左 4dp
e.setSingleLine(true);
e.setIncludeFontPadding(false);
e.setGravity(Gravity.CENTER_VERTICAL);
e.setTextCursorDrawable(null);      // 用系统默认光标（别自绘，省事且不违和）

// ScrollView
s.setFillViewport(true);
s.setClipToPadding(false);          // 内容可以滑到状态栏/手势条底下
s.setOverScrollMode(View.OVER_SCROLL_NEVER);

// TextView 行高：Android 没有 line-height
t.setIncludeFontPadding(false);
t.setLineSpacing(dp(extra), 1f);    // 目标行高 18sp、字号 12sp → extra ≈ 2dp
```

### 0.3 Edge-to-edge（API 35 强制）适配

**HTML 页策略**：WebView 全屏铺满（保持「内容滑到状态栏底下」的观感），inset 通过 CSS 变量注入给页面自己用；页面底色与状态栏同色，滚动时视觉上就是「纸继续往上延伸」。

```java
// MainActivity 里给 web 挂一次即可
web.setOnApplyWindowInsetsListener((v, insets) -> {
    Insets b = insets.getInsets(WindowInsets.Type.systemBars()
            | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
    v.evaluateJavascript(
        "document.documentElement.style.setProperty('--sat','" + b.top + "px');"
      + "document.documentElement.style.setProperty('--sab','" + b.bottom + "px');", null);
    return WindowInsets.CONSUMED;   // ★ CONSUMED：不把 inset 变成 padding，否则页面顶不到底
});
```

```css
/* HTML 侧：注入优先，独立出图时退回 env() */
:root{ --sat: env(safe-area-inset-top, 0px); --sab: env(safe-area-inset-bottom, 0px); }
```

> Android WebView 的 `env(safe-area-inset-top)` 在多数机型返回 **0**（不跟状态栏高度），所以必须靠注入兜底。两者都写，Chrome 里出图也不会错位。

**原生 SettingsActivity 策略**：现有 `EdgeToEdge.apply()` 把四边 padding 铺在 root 上，A 方向要加墨色顶栏，必须改成落点分离：

```java
final LinearLayout root = new LinearLayout(this);        // 竖向：header + ScrollView
root.setOrientation(LinearLayout.VERTICAL);
root.setBackgroundColor(0xFFEFEBE3);
// root 自己不要 padding
EdgeToEdge.apply(root, new EdgeToEdge.OnInsets() {
    @Override public void onInsets(int l, int t, int r, int b) {
        header.setPadding(l, t, r, 0);                   // ① 顶部 inset 给墨色顶栏（状态栏被顶栏染色）
        scroll.setPadding(0, 0, 0, b + dp(24));          // ② 底部 inset + ime 给滚动区
        scroll.setClipToPadding(false);
    }
});
```
`EdgeToEdge` 已经把 `ime()` 并进 inset 了，所以键盘弹起时 `b` 会变大，输入框自动被顶上来——**不用改 `EdgeToEdge.java` 一行**。

**主题与状态栏图标**（关键差异，选方向时必看）：

| | AppTheme（WebView 页） | SettingsTheme（原生页） |
|---|---|---|
| A | 保持 `windowLightStatusBar=false`：状态栏 = 墨色顶栏延伸，白色图标。**API 34 及以下与 35+ 观感一致** | 改回 `false` + 加墨顶栏 |
| B | **改为 `true`**：纸色状态栏 + 深色图标 | 保持 `true`（已是） |

> B 必须动 `AppTheme`，否则 API 34 及以下会出现「深色状态栏 + 纸色页面」的割裂；A 反而一套主题通吃两代系统。这是 A 最大的隐性优势。

### 0.4 HTML 页公共基线（两方向共用）

```html
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>…</title>
<!-- 离线页可加：<meta http-equiv="Content-Security-Policy"
     content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"> -->
<style>
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; min-height:100dvh;              /* 老 WebView 不支持 dvh 时退回 100vh，见下 */
  background:var(--paper); color:var(--t1);
  font:400 15px/1.6 -apple-system,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif;
  -webkit-tap-highlight-color:transparent;   /* ★ 不去掉，安卓 WebView 点击会闪灰块 */
  -webkit-user-select:none; user-select:none;
  overscroll-behavior:none;
}
@supports not (min-height:100dvh){ body{min-height:100vh} }
.btn,.card{ -webkit-tap-highlight-color:transparent }
/* 内容不足时垂直居中，超长时从顶部开始滚（不要用 align-items:center 硬顶） */
.stage{ min-height:100dvh; display:flex; flex-direction:column;
        padding:calc(var(--sat) + 0px) 16px calc(var(--sab) + 24px); }
.stage > .grow{ margin:auto 0; }
/* 圆角出图：桌面 Chrome 里模拟 360 宽 */
@media (min-width:520px){ .stage{max-width:420px; margin:0 auto} }
</style>
```
WebView 侧对应设置：
```java
WebSettings ws = web.getSettings();
ws.setJavaScriptEnabled(true);     // offline.html 要读 reason/server 参数，必须开
ws.setTextZoom(100);               // ★ 壳层页文字量极小，锁 100% 保渲染确定性；原生页仍跟 sp
ws.setSupportZoom(false);
ws.setBuiltInZoomControls(false);
web.setBackgroundColor(0xFFEFEBE3); // 防止加载瞬间白闪
```

### 0.5 无障碍与对比度基线（实测 sRGB 相对亮度，±0.1）

| 组合 | 比值 | 结论 |
|---|---|---|
| `#353A41` on `#EFEBE3` | 9.6 | ✅ 正文/标题 |
| `#353A41` on `#FBF9F5` | 11.2 | ✅ |
| `#5E5A52` on `#EFEBE3` | 5.7 | ✅ 纸底说明文字（**新增，替代 `#6B7280`**） |
| `#6B7280` on `#EFEBE3` | **4.1** | ❌ **不达 AA**（现状 `welcome.html` 的 `p` 就是这个组合，必须换） |
| `#6B7280` on `#FFFFFF` | 4.8 | ⚠️ 仅白卡内可用（站点 `--muted:#6F6B64` 同理 4.5 边缘） |
| `#9A8F80` on `#EFEBE3` | **2.7** | ❌ 只能当装饰/placeholder，禁止承载唯一信息（现状 `hint` 违反） |
| `#8C7A66` on `#EFEBE3` | 3.5 | ⚠️ 只够图形/大标题；11px 小字用 `#6F6152`（5.1） |
| `#8F4A4A` on `#EFEBE3` | 4.9 | ✅ 站点 danger-ink |
| `#4F7059` on `#EFEBE3` | 4.6 | ✅ 站点 success-ink |
| `#3E6152` on `#EFEBE3` / 白字 on `#3E6152` | 5.9 / 7.0 | ✅ 方向 B 主色 |
| `#9C5245` on `#EFEBE3` | 4.8 | ✅ 方向 B 危险色 |

其他硬指标：触控目标 ≥44dp（本规格所有按钮 48dp、chip 40dp+行高补足）、无 emoji 图标、所有图标 `contentDescription`（装饰性图 `importantForAccessibility="no"`）、HTML 图标 `<svg aria-hidden="true" focusable="false">`。

---

# 方向 A ·「延续暖纸」

一句话：**不加一个色相，把三页收进站点 v1.4 的同一套语言里**——墨色顶栏、发丝描边、无重阴影、站点已有的 danger/success 语义色。

## A.1 Token 表

### A.1.1 颜色

| token | 值 | 用途 | 来源 |
|---|---|---|---|
| `--ink` | `#353A41` | 顶栏底、主按钮底、标题 | 站点 brand_bar 原值 |
| `--ink-press` | `#2F3339` | 主按钮按下 | 派生（压暗 8%） |
| `--paper` | `#EFEBE3` | 页面底 | 站点 `--bg` |
| `--card` | `#FBF9F5` | 卡片（**不再是纯白**，跟随站点） | 站点 `--card` |
| `--soft` | `#F3F0EA` | chip 底、图标圆底 | 站点 `--paper-2` 系 |
| `--soft-2` | `#F1EFE9` | 信息块底（含左竖线） | 站点 `--paper-3` |
| `--line` | `#E2DBD0` | 组件描边 | 站点 `--card-border` |
| `--line-soft` | `#E9E3D9` | 卡内分隔线、清单分隔 | 站点 `--line` 邻档 |
| `--accent` | `#8C7A66` | 图标描边、焦点环、短横线（图形用） | 壳层 brand_accent |
| `--accent-t` | `#6F6152` | 11px 强调小字 | 派生（压暗，过 AA） |
| `--t1` | `#353A41` | 正文/标题 | — |
| `--t2` | `#5E5A52` | 纸底说明（**替代 `#6B7280`**） | 新增派生 |
| `--t3` | `#6F6B64` | 白卡内次要文字 | 站点 `--muted` |
| `--placeholder` | `#9A8F80` | 仅 placeholder / 装饰 | 站点 `--hint` |
| `--danger-ink` | `#8F4A4A` | 失败原因、错误文字 | 站点 `--danger-ink` |
| `--danger-bg` | `#F7EDED` | 错误图标圆底 | 站点 `--danger-bg` |
| `--success-ink` | `#4F7059` | 测试成功文字 | 站点 `--success-ink` |

> **零新增色相**：A 表里所有值要么是站点/壳层现有 token，要么是同一色相压暗一档（`--ink-press`/`--accent-t`/`--t2`）。

### A.1.2 字号 / 行高

| token | HTML(px) | 原生(sp) | 字重 | 行高 | 用途 |
|---|---|---|---|---|---|
| `--fs-title` | 22 | 22 | 700 | 30 | welcome 主标题 |
| `--fs-h1` | 19 | 19 | 600 | 26 | offline 标题 |
| `--fs-h2` | 15 | 16 | 600 | 22 | 区块标题 / 顶栏 |
| `--fs-body` | 14 | 15 | 400 | 22 | 正文 |
| `--fs-label` | 13 | 13 | 500 | 18 | 字段标签 |
| `--fs-meta` | 12 | 12 | 400 | 18 | 说明文字 |
| `--fs-micro` | 11 | 11 | 600 | 14 | 眉标（letter-spacing .5px / `setLetterSpacing(0.045f)`） |
| `--fs-mono` | 13 | 14 | 400 | 18 | 地址（`monospace` / `Typeface.MONOSPACE`） |

### A.1.3 间距 / 圆角 / 阴影

| token | 值 | 说明 |
|---|---|---|
| 间距刻度 | 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 56 | 4 基准，只准用这 9 档 |
| `--r-card` | 12 | 卡片级（站点 10 → 手机放大一档） |
| `--r-ctl` | 10 | 按钮 / 输入框 / 信息块 |
| `--r-logo` | 18 | 72dp logo 方块 |
| `--r-pill` | 999 | chip / 圆点 |
| `--shadow-card` | `0 1px 2px rgba(53,58,65,.05)` | **只保留 1px 落地感，删掉 `0 8px 30px`** |
| `--shadow-btn` | `none` | 主按钮不投影 |
| 原生 elevation | 全部 `0` | 层级靠 1px 描边 + 底色差 |

## A.2 页 1 · welcome.html

总高预算（360×780，inset 24/24）：**≈ 646dp**，一屏放下不滚。

```
y=0    ┃ ① 状态栏区  h=insetTop  bg #353A41                      ← 墨色向上延伸
y=24   ┃ ② header  h=56  bg #353A41  padding 0 16
       ┃     ├ [22×22 品牌弯管]  stroke #F7F5F1 宽2.2  [SVG-A1]
       ┃     ├ gap 10
       ┃     └ “HVAccRM” 15/600 #FFF  ls .2  [NW]
y=80   ┃ ③ 主区 padding: 32 16 0 16      （.stage > .grow → 垂直居中，空间不足时顶部对齐）
       ┃ ④ 卡片  bg #FBF9F5 · 1px #E2DBD0 · r12 · shadow-card · padding 24 20 · 内容居中
       ┃     [72×72 logo]  r18  bg #353A41  内嵌 32×32 弯管 stroke #EFEBE3 宽2.6
       ┃     ↕ 20
       ┃     “暖通 CRM”  22/700 lh30  #353A41  [NW]
       ┃     ↕ 8
       ┃     “连接你的 HVAccRM 服务器”  13/400 lh20  #5E5A52  max-w 240 居中(≤2行)
       ┃     ↕ 20
       ┃     ⑤ 地址示例块  full · bg #F1EFE9 · r10 · 左内嵌 2×16 #8C7A66 竖线 · padding 12 14 · 左对齐
       ┃           “地址格式” 11/600 ls.5 #6F6152  [NW]
       ┃           ↕ 6
       ┃           [ http://192.168.1.100:8090  13 mono #353A41  ellipsis ] gap 8 [复制 icon 18×18 #8C7A66]
       ┃           整块可点 → ClipboardManager 复制 + Toast“已复制”
       ┃     ↕ 24
       ┃     ⑥ 主按钮  h48 · full · r10 · bg #353A41 · “填写服务器地址” 15/600 #FFF ls.3  [NW]
       ┃     ↕ 12
       ┃     ⑦ 次按钮  h48 · full · r10 · bg #FBF9F5 · 1px #E2DBD0 · “重新连接” 15/500 #353A41 [NW]
y=…    ┃ ↕ 20（出卡）
       ┃ ⑧ 页脚  “手机和 NAS 在同一个 Wi-Fi 时，用内网 IP 最快”  12/400 lh18 #5E5A52 居中
       ┃ 底部留白 = sab + 24
```

**结构改动（对照现状）**
- `⌂` → 弯管图形（Canvas/SVG 双实现，见 A.6）。
- 两个按钮的权重靠**形态**区分，不靠深浅：主 = 墨实心无描边，次 = 卡色 + 1px 描边。现状次按钮是 `#F3F0EA` 浅底，和主按钮在缩略图上几乎同权重。
- 地址格式从「底部灰字脚注」升级为**卡内带左竖线的信息块**（沿用站点 `.module-notes` 的语言），并支持点按复制。
- 卡片改 `#FBF9F5`（与站点一致），删掉重阴影 → 页面立刻"平"下来。

**nowrap 清单**：`HVAccRM`、`暖通 CRM`、两个按钮文案、地址行（超长省略号 `overflow:hidden;text-overflow:ellipsis`）。
**可折行**：副标题（≤2 行）、页脚（≤1 行，`nowrap` 会溢出，允许折）。

## A.3 页 2 · offline.html

总高预算：**≈ 700dp**（小屏 667dp 需滚动 → `.stage` 允许滚，`overflow-y:auto`）。

```
y=0    ┃ ① 状态栏区 h=insetTop  bg #353A41
y=24   ┃ ② header h=56（与页 1 完全同构：弯管 + HVAccRM）      ← 三页体系感的关键
y=80   ┃ ③ 主区 padding 24 16 0 16
       ┃ ④ 卡片 bg #FBF9F5 · 1px #E2DBD0 · r12 · padding 24 20 · 居中
       ┃     [56×56 圆 r28 bg #F7EDED] 内 [26×26 断管 icon stroke #8F4A4A 宽2.2] [SVG-A2]
       ┃     ↕ 16
       ┃     “连不上服务器”  19/600 lh26 #353A41  [NW]
       ┃     ↕ 8
       ┃     ⑤ 原因  13/400 lh20 #5E5A52 居中  ≤2行  -webkit-line-clamp:2
       ┃           （读 ?reason=，空则「服务器没有响应，可能是地址写错或服务没启动。」）
       ┃     ↕ 20
       ┃     ⑥ 地址块 full · bg #F1EFE9 · r10 · 左竖线 2×16 #8C7A66 · padding 12 14 · 左对齐
       ┃           上排: “当前地址” 11/600 ls.5 #6F6152 [NW]  ⋯⋯ 右侧 [复制 18×18 #8C7A66]
       ┃           ↕ 6
       ┃           下排: http://…:8090  13 mono #353A41  ellipsis  [NW]
       ┃     ↕ 20
       ┃     ⑦ 主按钮 h48 “重试连接”
       ┃     ↕ 12
       ┃     ⑧ 次按钮 h48 描边 “修改地址”
y=…    ┃ ↕ 16（出卡）
       ┃ ⑨ 自查卡 bg #FBF9F5 · 1px #E2DBD0 · r12 · padding 16 16
       ┃     眉标 “按这三条自查” 11/600 ls.5 #6F6152 [NW]
       ┃     ↕ 12
       ┃     · 行 ×3： [18×18 圆 bg #F3F0EA，内 10/600 #6F6152 数字] gap 10 [12/400 lh19 #5E5A52]
       ┃       行间距 10，行内可折 2 行
```

**层级修正（这是本页核心）**

| 信息 | 旧处理 | 新处理 | 理由 |
|---|---|---|---|
| 失败原因 | 与清单同色同字号，夹在中间 | 标题正下方、仅它用 13px `--t2`、2 行截断 | 原因=「发生了什么」，只该有一处 |
| 当前地址 | 灰底块里一行「当前地址：xxx」 | 独立信息块 + 等宽 + 复制按钮 | 用户排查时要**念出来/贴给别人**，等宽字和复制键是刚需 |
| 排查清单 | 卡片内 `<ul>`，与原因同级 | **独立第二张卡**，编号列表 | 原因≠办法，物理分开才不打架 |

**nowrap 清单**：`连不上服务器`、`当前地址`、`按这三条自查`、地址行。
**清单文案（新）**
1. 手机和 NAS 在同一个 Wi-Fi？
2. 地址里的端口是不是 8090（或你改过的那个）？
3. NAS 上的 `hvaccrm` 容器还在运行吗？

## A.4 页 3 · SettingsActivity（原生）

内容区宽 360 - 16×2 = 328dp；卡片内可用 296dp。总高 **≈ 720dp** → 必须可滚。

```
① 状态栏区 = header 的 paddingTop（inset）· bg #353A41
② header  h56 + insetTop · bg #353A41 · padding 0 16
      ├ [48×48 触控区] 内 22×22 chevron-left stroke #F7F5F1 宽2.2 [SVG-A3]  ← 左边缘溢出到 padding 外 8dp
      ├ gap 4
      └ “服务器设置” 17sp/600 #FFF [NW] setSingleLine(true)
③ ScrollView · bg #EFEBE3 · clipToPadding(false) · paddingBottom = insetBottom + ime + 24
   内容 LinearLayout padding 20 16 0 16
   ┌ ④ 卡片 1「服务器地址」 bg #FBF9F5 · 1dp #E2DBD0 · r12 · padding 18 16
   │     “服务器地址” 12sp/600 #353A41 ls.3 [NW]
   │     ↕ 4
   │     “填 NAS 地址，带 http:// 和端口号。” 12sp lh18 #5E5A52 (≤2行)
   │     ↕ 12
   │     [ EditText ] h48 · bg #F1EFE9 · 1dp #E2DBD0 · r10 · padding 0 14 · 15sp mono
   │         text #353A41 · hint “http://192.168.1.100:8090” 14sp #9A8F80
   │         焦点: 底变 #FBF9F5 + 2dp #8C7A66   （见 A.5 输入框四态）
   │     ↕ 10
   │     [chip 用内网 IP] 40 高 r20 bg #F3F0EA 13sp/500 #5E5A52 padding 0 14 [NW]
   │     gap 8
   │     [chip 用域名]   同上
   └ ↕ 16
   ┌ ⑤ 卡片 2「安全入口」 同卡片样式
   │     “安全入口” 12sp/600 #353A41  +8+ [pill “可选” h18 r9 bg #F3F0EA 10sp #6F6152 padding 0 6]  ← 整行 [NW]
   │     ↕ 4
   │     “服务端设过入口串才需要填。” 12sp #5E5A52
   │     ↕ 12
   │     [ EditText ] 同规格 · hint “没开门牌就留空” 14sp
   └ ↕ 20
   ⑥ 操作区（**不套卡**，按钮直接落在纸底上；卡片套按钮会变成"卡里卡"）
      [ 测试连接 ]   h48 · r10 · bg #FBF9F5 · 1dp #E2DBD0 · 15sp/600 #353A41
      ↕ 12
      [ 保存并进入 ] h48 · r10 · bg #353A41 · 15sp/600 #FFF
      ↕ 12
      ⑦ “保存后会立刻尝试连接一次” 12sp lh18 #5E5A52 居中 [NW]
```

**⑧ 测试结果区（现有 `hint` TextView 的替代，必须重做）**
现状把 `✓/✗/△` 和长段技术文案塞进一个 13sp 灰字 TextView，是这一页最乱的地方。改为：

```
┌ 结果块（默认 GONE，出结果时显示）· r10 · padding 12 14 · 左竖线 2×16
│   成功: bg #E6ECE1 · 竖线 #4F7059 · 标题“连接成功” 13/600 #4F7059 
│        副行 “HTTP 200 · 安全入口已通过” 12/400 lh18 #5E5A52  [NW]
│   失败: bg #F7EDED · 竖线 #8F4A4A · 标题“连不上” 13/600 #8F4A4A
│        副行 具体原因（≤3 行，等宽显示 URL）
│   ↕ 12 → 再往下才是操作区
```
- `✓ ✗ △` 字符全部删掉，用**颜色 + 标题词**表达（色盲用户靠标题词，不靠颜色）。
- 地址一律 `Typeface.MONOSPACE`，避免 l/1/0/O 念错——用户是要照着念给客服/家人的。

**状态与骨架**
- 测试中：`测试连接` → 文字“测试中…”+ `setEnabled(false)`（走禁用态），结果块显示“正在连接 http://…”。
- 保存前校验：`new URL()` 失败 → 输入框进**错误态**（2dp `#8F4A4A` + 下方 12sp 错误行“地址格式不对，要以 http:// 开头”），不再只弹 Toast（Toast 会被键盘挡住）。
- 键盘：`input` → `IME_ACTION_NEXT`，`gateInput` → `IME_ACTION_DONE`；done 时收起键盘不提交（避免误保存）。

## A.5 组件四态规格

> `GD(r, fill, stroke)` 简写 = `GradientDrawable{RECTANGLE, cornerRadius=r, setColor(fill), setStroke(w, stroke)}`

### A.5.1 主按钮（HTML `.btn-primary` / 原生 `Button`）

| 态 | HTML | 原生 GradientDrawable 写法 |
|---|---|---|
| 常态 | `background:#353A41;color:#fff;border:0;height:48px;border-radius:10px;font:600 15px/48px …;letter-spacing:.3px` | `GD(10dp, #353A41, 无)` |
| 按下 | `background:#2F3339`（无 transform、无阴影变化） | `new RippleDrawable(ColorStateList.valueOf(0x26FFFFFF), content=GD(10,#353A41), mask=GD(10,#FFF))` |
| 焦点 | `:focus-visible{outline:2px solid #8C7A66;outline-offset:2px}` | `StateListDrawable`: `state_focused → GD(10,#353A41,#8C7A66 2dp)`，默认 `GD(10,#353A41,无)`（描边向内长，不位移） |
| 禁用 | `background:#E2DBD0;color:#9A8F80;cursor:default` | `StateListDrawable`: `{-state_enabled} → GD(10,#E2DBD0,无)` + `setTextColor(#9A8F80)` |
| 加载中 | 文字换“连接中…” | 同禁用态 + 右侧 12dp `ProgressBar`（`indeterminateTintList=#9A8F80`） |

### A.5.2 次按钮（描边型）

| 态 | HTML | 原生 |
|---|---|---|
| 常态 | `background:#FBF9F5;border:1px solid #E2DBD0;color:#353A41;height:48px;border-radius:10px` | `GD(10dp, #FBF9F5, #E2DBD0 1dp)` |
| 按下 | `background:#F3F0EA`（只变底，描边不变） | `RippleDrawable(0x14353A41, GD(10,#FBF9F5,#E2DBD0), mask=GD(10,#FFF))` |
| 焦点 | 同主按钮 outline 环 | `state_focused → GD(10,#FBF9F5,#8C7A66 2dp)` |
| 禁用 | `background:#F3F0EA;border-color:#E9E3D9;color:#9A8F80` | `{-state_enabled} → GD(10,#F3F0EA,#E9E3D9 1dp)` |

### A.5.3 输入框（原生为主；HTML 出图版同值）

| 态 | 原生写法 | 备注 |
|---|---|---|
| 常态 | `GD(10dp, #F1EFE9, #E2DBD0 1dp)` | **浅底凹陷**，靠底色而不是描边跟卡片区分（白卡里画白框是看不见的） |
| 焦点 | `GD(10dp, #FBF9F5, #8C7A66 2dp)` | 底变亮 + 描边变粗变棕 = "浮起来"。用 `StateListDrawable` 两个 GD，宽度才能变 |
| 错误 | `GD(10dp, #FBF9F5, #8F4A4A 2dp)` + 下方 12sp `#8F4A4A` 行 | 错误行高 18dp，出现时**不挤压**输入框（用 `View.VISIBLE` + `layout_marginTop 6dp`） |
| 禁用 | `GD(10dp, #F3F0EA, #E9E3D9 1dp)` + `setTextColor(#9A8F80)` | |
| 按下 | 不适用 | EditText 不加 ripple，避免输入时闪 |

```java
StateListDrawable bg = new StateListDrawable();
bg.addState(new int[]{ android.R.attr.state_focused },  gd(10, 0xFFFBF9F5, 2f, 0xFF8C7A66));
bg.addState(new int[]{ -android.R.attr.state_enabled }, gd(10, 0xFFF3F0EA, 1f, 0xFFE9E3D9));
bg.addState(new int[]{},                                gd(10, 0xFFF1EFE9, 1f, 0xFFE2DBD0));
// ★ 顺序：最具体的在前，{} 必须最后
```

### A.5.4 信息块（地址块）

| 态 | 规格 |
|---|---|
| 常态 | `bg #F1EFE9; r10; border-left:2px solid #8C7A66; padding 12 14`；原生：`LayerDrawable(GD(10,#F1EFE9), InsetDrawable(GD(2,#8C7A66), 左14dp))` 或直接用 `View` 做竖线（更省事） |
| 按下（可复制时） | `bg #E9E3D9`；原生 `RippleDrawable(0x148C7A66, content, mask)` |
| 焦点（键盘可达） | `outline 2px #8C7A66 offset 2px`；原生 `GD(10,#F1EFE9,#8C7A66 1dp)` |
| 禁用（`server` 为空） | 不显示复制图标，`color:#9A8F80`，值显示 `—` |

### A.5.5 错误块

见 A.4 ⑧：`bg #F7EDED` / 竖线 `#8F4A4A` / 标题 13sp 600 同色 / 副行 12sp `#5E5A52`。HTML 版只用于 `offline.html` 的「原因」——但**原因不用色块**（用色块会让人以为要修东西），只留文字；错误块专属设置页的测试结果。

## A.6 图标方案

三页共 4 个图形，**全部单色描边、24×24 网格、`stroke-linecap:round`、`stroke-linejoin:round`**。原生用 `Canvas`（`Path` + `Paint.STROKE`），HTML 用内联 SVG，同一套 path。

### 图标 1 · 品牌弯管（`duct-elbow`）
`24×24`，主弯管 1 条 + 出风口鳍线 2 条（仅在 ≥28dp 时画鳍线，22dp 以下省略）。

```html
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <path d="M4.5 20V10.5A6 6 0 0 1 10.5 4.5H20"
        stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
  <!-- 鳍线变体 -->
  <path d="M14.6 2.4v4M18.2 2.4v4"
        stroke="currentColor" stroke-width="1.6" stroke-linecap="round" opacity=".55"/>
</svg>
```
```java
// 24 单位空间建路径，再按目标尺寸缩放；u() = 单位→px
Path duct = new Path();
duct.moveTo(u(4.5f), u(20f));
duct.lineTo(u(4.5f), u(10.5f));
duct.arcTo(new RectF(u(4.5f), u(4.5f), u(16.5f), u(16.5f)), 180f, 90f, false); // 180°→270° = 左→上
duct.lineTo(u(20f), u(4.5f));
// u(v) = v * size / 24f；stroke 宽度 = size * 2.4f / 24f
```
> 语义：一根 90° 直角风管。比 `⌂` 强的地方是它同时暗示"管路/接进来"，和产品做的事对得上，且在 16dp 仍可辨。

### 图标 2 · 断管（`pipe-break`，offline 页）
`24×24`，左右两段 L 管 + 中间断开，两个端头法兰竖线。

```html
<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <path d="M4 16.5v-5.2A3.3 3.3 0 0 1 7.3 8h2.3" stroke-width="2.2" stroke-linecap="round"/>
  <path d="M9.6 5.4v5.2"                          stroke-width="2.2" stroke-linecap="round"/>
  <path d="M14.4 5.4v5.2"                         stroke-width="2.2" stroke-linecap="round"/>
  <path d="M14.4 8h2.3A3.3 3.3 0 0 1 20 11.3v5.2" stroke-width="2.2" stroke-linecap="round"/>
</svg>
```
用法：`56×56` 圆底 `#F7EDED`，图标 26×26，`stroke #8F4A4A`。
> 为什么不用 `⚠️`：警告三角 ≠ 连接失败；断管是**这个产品自己的语言**，用户看一眼就知道「管子没接上」。

### 图标 3 · 返回 chevron（原生 header）
```java
Path chev = new Path();
chev.moveTo(u(14.2f), u(5.4f)); chev.lineTo(u(7.6f), u(12f)); chev.lineTo(u(14.2f), u(17.4f));
// stroke 2.2dp, round, 反白 #F7F5F1
```
`<svg>` 等价：`<path d="M14.2 5.4 7.6 12l6.6 6.6" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`
触控区 48×48，箭头 22×22 居中；`contentDescription="返回"`。

### 图标 4 · 复制（地址块右侧）
```html
<svg viewBox="0 0 24 24" fill="none">
  <rect x="8.6" y="8.6" width="10.8" height="10.8" rx="2.4" stroke-width="1.8"/>
  <path d="M15.4 5.6H6.6a2 2 0 0 0-2 2v8.8" stroke-width="1.8" stroke-linecap="round"/>
</svg>
```
18×18，`#8C7A66`，`contentDescription="复制地址"`。

### 不用的方案（写下来免得后面又走回头路）
- `android.R.drawable.ic_menu_*` / `ic_media_*`：Holo 时代线条图标，与暖纸分明度、线宽完全不搭，只用 1~2 个会显得"混搭"。**本方案 0 个系统图标**。
- emoji（现状 `⌂ ⚠️ ✓ ✗ △`）：字体决定长相，各机型不一致，且无法着色。
- 引 `VectorDrawable`：技术上可行（框架自带支持），但你现有工程没有 XML 资源目录习惯；Canvas 路径与 SVG 同源，改一处两边同步，更划算。

## A.7 文案打磨

| 位置 | 现状 | 改为 | 为什么 |
|---|---|---|---|
| welcome 标题 | 暖通CRM 移动端 | **暖通 CRM** | "移动端"在手机上无信息量 |
| welcome 副标题 | 连接你自托管的 HVAccRM 服务器 | **连接你的 HVAccRM 服务器** | 去掉"自托管"，App 名已含 CRM，重复 |
| welcome 主按钮 | 填写服务器地址 | **填写服务器地址** | 保留（动词+对象，最清晰） |
| welcome 次按钮 | 我已填好，重新连接 | **重新连接** | 前半句是自言自语 |
| welcome 页脚 | 示例格式：局域网 http://… 外网 https://… | **手机和 NAS 在同一个 Wi-Fi 时，用内网 IP 最快** | 示例已移入信息块；页脚只放"怎么选"这一条决策 |
| offline 标题 | 连不上服务器 | **连不上服务器** | 保留 |
| offline 原因空值 | 网络异常 | **服务器没有响应，可能是地址写错或服务没启动** | "网络异常"会让人去查手机网络，方向是错的 |
| offline 主按钮 | 重试 | **重试连接** | 见下文"重试"歧义 |
| offline 次按钮 | 修改服务器地址 | **修改地址** | 页面语境已明确 |
| offline 清单 | 手机是否连着 Wi-Fi / 流量？ | **手机和 NAS 在同一个 Wi-Fi？** | 原句在 4G 下也"连着流量"，判据不唯一 |
| offline 清单 | 服务器（NAS）是否开机、容器是否在跑？ | **NAS 上的 hvaccrm 容器还在运行吗？** | 给出可执行的检查对象 |
| offline 清单 | 在外网时要用外网域名… | **地址里的端口是不是 8090（或你改过的那个）？** | 第三条与第一条重复；端口错是实测最高频失败原因 |
| settings 标题 | 连接哪台服务器（20sp） | **服务器设置** | 疑问句不适合当页标题；且 shell 页标题应短 |
| settings 说明 | 4 行（含两条格式示例 + "以上只是示例"） | **填 NAS 地址，带 http:// 和端口号。**（1 行）+ 示例进 hint | 4 行说明没人读，示例放 hint 里"边打边看" |
| settings chip | 用内网地址 / 用外网域名 | **用内网 IP** / **用域名** | 4 字 vs 5 字，短一半；"外网域名"里"外网"是冗余限定 |
| settings 标签 | 安全入口（可选） | **安全入口** + pill「可选」 | 括号里的"可选"该是视觉标记，不是文字 |
| settings 照片 | — | hint **没开门牌就留空** | 比"例如 your-gate"更有判断力 |
| settings 主按钮 | 保存并进入 | **保存并进入** | 保留（"并进入"说明了后果，是好的） |
| settings 次按钮 | 测试连接 | **测试连接** | 保留 |
| settings 尾部提示 | 提示：保存后可在 App 右下角悬浮球里随时改回来。 | **保存后会立刻尝试连接一次** | 原句是"说明功能入口"，用户此刻要的是"按下去会发生什么" |
| settings 结果 | ✗ 连不上：UnknownHostException… | **连不上** / 副行 **域名解析失败（UnknownHostException）** | 异常名做副行小字，主行给人话 |

### 本方向新增的通用规则
- 按钮文案**动词开头**，最长 6 个汉字（48dp 高、15sp，6 字 = 90dp，留足左右留白）。
- 错误文案结构：**标题（发生了什么）+ 副行（技术细节/怎么办）**，两级之间用字号和颜色区分，不靠 emoji。

---

# 方向 B ·「品牌向前一步」

一句话：**纸页 + 墨绿 + 盘管螺旋**。去掉顶栏和卡片阴影，靠左对齐的大字排版、发丝分隔线、等宽数字建立"工程图纸"气质；主操作换成一个能被记住的墨松绿。

不安静的东西一律不做：无渐变、无玻璃、无霓虹、无大色块。新增颜色只有 2 个，且都只用于**功能性动作与状态**。

## B.1 Token 表

### B.1.1 颜色

| token | 值 | 用途 | 关系 |
|---|---|---|---|
| `--ink` | `#353A41` | 标题、正文、次要按钮文字 | 沿用 |
| `--paper` | `#EFEBE3` | 页面底 | 沿用 |
| `--card` | `#FFFFFF` | 卡片/输入框（B 无阴影，靠白度撑层级） | 沿用壳层现状 |
| **`--primary`** | **`#3E6152`** | 主按钮、链接、成功态、状态点 | **新色 1**（源自站点 `--success-ink #4F7059` 压深加饱和） |
| `--primary-press` | `#375548` | 主按钮按下 | 派生 |
| `--primary-soft` | `#E8EFEA` | 成功态底、绿色浅底 | 派生 |
| **`--danger`** | **`#9C5245`** | 失败、错误描边、危险图标 | **新色 2**（源自站点 `--danger-ink #8F4A4A` 提亮提暖） |
| `--danger-press` | `#8A463A` | 危险按下 | 派生 |
| `--danger-soft` | `#F7EBE6` | 失败图标底 | 派生 |
| `--accent` | `#8C7A66` | 短横线、眉标线、装饰 | 沿用 |
| `--meta` | `#6F6152` | 11px 眉标小字 | 派生 |
| `--t1` | `#353A41` | 主文字 | — |
| `--t2` | `#5E5A52` | 纸底说明 | 派生 |
| `--line` | `#E3DDD1` | 输入框描边、卡片描边 | 派生 |
| `--line-soft` | `#EDE8DF` | 分隔线（列表项之间） | 派生 |
| `--chip` | `#F1EDE4` | chip 底 | 派生 |

对比度：`#3E6152` on paper **5.9**、白字 on `#3E6152` **7.0**；`#9C5245` on paper **4.8**。两个新色都能直接当文字色用，不需要再设"文字专用档"。

### B.1.2 字号 / 行高

| token | HTML(px) | 原生(sp) | 字重 | 行高 | 用途 |
|---|---|---|---|---|---|
| `--fs-display` | 30 | 26 | 700 | 38 / 34 | welcome 大标题 / 设置页标题 |
| `--fs-h1` | 24 | 24 | 700 | 32 | offline 标题 |
| `--fs-h2` | 16 | 17 | 600 | 24 | 卡片标题 |
| `--fs-body` | 14 | 15 | 400 | 22 | 正文 |
| `--fs-label` | 13 | 13 | 500 | 18 | 字段标签 |
| `--fs-meta` | 12 | 12 | 400 | 18 | 说明 |
| `--fs-micro` | 11 | 11 | 600 | 14 | 眉标，`ls 1.6px` / `setLetterSpacing(0.15f)` |
| `--fs-mono` | 13.5 | 14 | 400 | 20 | 地址、编号（`tabular-nums` 可选） |

> B 的字号整体比 A 大一档（display 30 vs 22、h1 24 vs 19），这是 B 的"钩子"之一：**字号对比拉大**，靠排版而不是颜色产生记忆点。因此 B 的正文不放大，避免整页变吵。

### B.1.3 间距 / 圆角 / 阴影

| token | 值 |
|---|---|
| 间距刻度 | 4 · 8 · 10 · 12 · 16 · 20 · 24 · 32 · 40 · 56（比 A 多 10 与 56，用于大字块呼吸） |
| `--r-card` | 12 |
| `--r-ctl` | 14（按钮/输入框，比 A 更圆一档 → 更"当代"，与 A 区分） |
| `--r-logo` | 14（48dp 方块） |
| `--r-pill` | 999 |
| `--shadow-*` | **全部 `none`**，连 1px 落地阴影都不要；层级只靠 `1px 描边 + 白底 + 留白` |
| 原生 elevation | 全部 0 |

## B.2 页 1 · welcome.html

总高预算（360×780）：**≈ 652dp**。**无顶栏**，内容左对齐，从状态栏下方 40dp 开始。

```
y=0    ┃ ① 状态栏区 h=insetTop · bg #EFEBE3（深色图标，无内容）
y=24   ┃ ② 主区 padding 24 · 顶间距 40 · 左对齐（不用 .grow 居中，B 是"从上往下读"的排版）
       ┃ ③ [48×48 r14 bg #353A41] 内 [26×26 盘管螺旋 stroke #F7F5F1 宽2.0] [SVG-B1]
       ┃ ↕ 28
       ┃ ④ 眉标 “HVACRM · 自托管”  11/600 ls1.6 #6F6152（大写效果靠全大写字母，中文不打字距）[NW]
       ┃ ↕ 10
       ┃ ⑤ 短横线 24×2 r1 bg #8C7A66
       ┃ ↕ 14
       ┃ ⑥ 大标题 “连上你的” / “暖通工具台”  30/700 lh38 #353A41  两行，左对齐  [NW]×2
       ┃ ↕ 12
       ┃ ⑦ 副标题 “客户、工地、报价都在你自己的 NAS 上，App 只是个窗口。” 
       ┃    14/400 lh22 #5E5A52  max-width 26ch（≤3 行）
       ┃ ↕ 32
       ┃ ⑧ 主按钮 h52 · full · r14 · bg #3E6152 · “填写服务器地址” 15/600 #FFF ls.3 [NW]
       ┃ ↕ 8
       ┃ ⑨ 文字按钮 h48（无底无描边）· 左对齐 · “重新连接” 15/500 #353A41 +[16×16 chevron-right #8C7A66]
       ┃    触控宽 = 内容宽 + 24，高 48
       ┃ ↕ 28
       ┃ ⑩ 地址示例（图纸条）：上下各 1px #E3DDD1，padding 12 0，无圆角
       ┃    上： “地址长这样” 11/600 ls1.2 #6F6152 [NW]
       ┃    ↕ 6
       ┃    下： http://192.168.1.100:8090  13.5 mono #353A41 ellipsis [NW]  gap 8  [复制 16×16 #8C7A66]
       ┃ ↕ 24
       ┃ ⑪ 页脚 “内网用 IP，出门用域名。” 12/400 lh18 #5E5A52 [NW]
       ┃ 底部 = sab + 24
```

**B 的钩子（三处，缺一不可）**
1. **盘管螺旋 logo**：地暖盘管的俯视图形，一笔画完、无文字依赖，缩到 24dp 仍是"一个圈里往外转"，辨识度远高于房子/齿轮。
2. **大字 + 眉标 + 短横线**的三段式页头：这是 B 三页共用的"版式签名"（设置页也用它），比加颜色更省、更像品牌。
3. **等宽地址 + 上下发丝线**：把"技术参数"当版式元素用，和"暖通小老板自己跑 Docker"的身份契合。

## B.3 页 2 · offline.html

总高预算：**≈ 706dp**（小屏滚动）。

```
y=0    ┃ ① 状态栏区 h=insetTop · bg #EFEBE3
y=24   ┃ ② 主区 padding 24 · 顶间距 32
       ┃ ③ [44×44 r22 bg #F7EBE6] 内 [22×22 断管 icon stroke #9C5245 宽2.2] [SVG-B2]
       ┃ ↕ 20
       ┃ ④ 眉标 “连接失败” 11/600 ls1.6 #9C5245 [NW]
       ┃ ↕ 10  ┃ 短横线 24×2 #9C5245
       ┃ ↕ 14
       ┃ ⑤ 标题 “连不上服务器” 24/700 lh32 #353A41 [NW]
       ┃ ↕ 10
       ┃ ⑥ 原因 14/400 lh21 #5E5A52 ≤3行
       ┃ ↕ 24
       ┃ ⑦ 地址卷 bg #FFF · 1px #E3DDD1 · r12 · padding 12 14
       ┃    上排 [“当前地址” 11/600 ls.6 #6F6152 NW] ⋯ [“复制” 12/600 #3E6152 NW，触控 44×44]
       ┃    ↕ 6
       ┃    下排 http://…:8090  13.5 mono #353A41 ellipsis [NW]
       ┃ ↕ 16
       ┃ ⑧ 主按钮 h52 r14 #3E6152 “重试连接”
       ┃ ↕ 8
       ┃ ⑨ 文字按钮 h48 “修改地址” + chevron
       ┃ ↕ 32
       ┃ ⑩ 自查清单（**无卡片，发丝线列表**）
       ┃    眉标 “按这三条自查” 11/600 ls1.6 #6F6152 [NW]
       ┃    ↕ 12
       ┃    项 ×3： padding 12 0 · 上边 1px #EDE8DF（首项无）
       ┃          [ “01/02/03” 11 mono #8C7A66 固定宽 22 ] gap 10 [ 13/400 lh20 #5E5A52 ]
       ┃ 底部 = sab + 24
```

**B 的层级手法**：失败原因不再是"卡片里的一段灰字"，而是**眉标(红) → 标题(大黑) → 原因(中灰)** 的三级递进；排查清单彻底脱离卡片，变成纸上的编号条目——这正是"工具台"的观感，也顺手解决了"原因和清单混在一起"。

## B.4 页 3 · SettingsActivity（原生）

**无顶栏**。用「眉标 + 短横线 + 26sp 标题」当页头，与 welcome 同构。**字段不套卡**，输入框自己是白卡；组与组之间用满宽发丝线分隔。

```
① 状态栏区 = ScrollView 内容的 paddingTop（insetTop）
② ScrollView · bg #EFEBE3 · clipToPadding(false) · paddingBottom = insetBottom + ime + 24
   内容 padding 24 20 0 20
   ┌ ③ 页头
   │   “连接” 11sp/600 ls0.15em #6F6152 [NW]  ← setLetterSpacing(0.15f)
   │   ↕ 8  ┃ 短横线 View 24×2dp bg #8C7A66
   │   ↕ 14
   │   “服务器” 26sp/700 #353A41 [NW]  setSingleLine(true)
   │   ↕ 10
   │   “填 NAS 地址。手机和 NAS 同一 Wi-Fi 时用内网 IP。” 13sp lh20 #5E5A52 (≤2行)
   └ ↕ 24
   ┌ ④ 组 1
   │   “服务器地址” 12sp/600 #353A41 [NW]
   │   ↕ 8
   │   [ EditText ] h50 · bg #FFF · 1dp #E3DDD1 · r12 · padding 0 14 · 14sp MONO
   │       hint “http://192.168.1.100:8090” 13sp #9A8F80
   │       焦点: 2dp #3E6152（白底不变）
   │   ↕ 8
   │   [chip 用内网 IP] h40 r20 bg #FFF 1dp #E3DDD1 13sp/500 #3E6152 padding 0 14 [NW]
   │   gap 8
   │   [chip 用域名]
   └ ↕ 20  ┃ 满宽发丝线 1dp #E3DDD1（左右各溢出 -20，贴到屏幕边）
   ┌ ⑤ 组 2
   │   “安全入口” 12sp/600 + 8 + [pill “可选” h18 r9 bg #F1EDE4 10sp #6F6152 padding 0 6] 整行 [NW]
   │   ↕ 8
   │   [ EditText ] h50 同上 · hint “没开门牌就留空” 13sp
   └ ↕ 24
   ┌ ⑥ 结果块（默认 GONE）
   │   成功 bg #E8EFEA · 左竖线 2×16 #3E6152 · “连接成功”13/600 #3E6152 · 副行 12/400 #5E5A52
   │   失败 bg #F7EBE6 · 左竖线 2×16 #9C5245 · “连不上”13/600 #9C5245 · 副行 12/400 #5E5A52（异常名用 MONO）
   └ ↕ 16
   ┌ ⑦ 操作区（B 把主操作放**上**、次操作放下：51dp 高度的绿按钮落在拇指区正中）
   │   [ 保存并进入 ] h52 · r14 · #3E6152
   │   ↕ 10
   │   [ 仅测试连接 ] h50 · r14 · bg #FFF · 1dp #E3DDD1 · 15sp/500 #353A41
   └ ↕ 12
   ⑧ 状态行 [6dp 圆点 #3E6152 | #9C5245 | #8C7A66] gap 8
      “当前生效 · http://192.168.1.100:8090” 11sp MONO #5E5A52 ellipsis [NW]
      （点=已连通 / 失败 / 未测试；未测试时整行 #8C7A66 且文案“尚未连接”）
   ⑨ 页脚 “保存后会立刻尝试连接一次” 12sp lh18 #5E5A52 [NW] · paddingBottom 24
```

**⑧ 状态行是 B 独有的**：三页里只有设置页能"告诉你现在连的是哪儿"，用 6dp 圆点 + 等宽地址一行说完，用户改地址前后都有参照。零额外面积，信息量最大。

**按钮上图的原因**：设置页是小屏单手场景，主操作（保存）放在拇指自然落点比放在屏幕最底部（易被手势条挤压、需跨手）更好点；"测试连接"作为探索性动作放下面，误触成本也低。若你更看重"主按钮永远最下"的一致性，把 ⑦ 两块对调即可，其余规格不变。

## B.5 组件四态规格

### B.5.1 主按钮（墨绿）

| 态 | HTML | 原生 |
|---|---|---|
| 常态 | `background:#3E6152;color:#fff;height:52px;border-radius:14px;font:600 15px/52px;letter-spacing:.3px` | `GD(14dp, #3E6152, 无)` |
| 按下 | `background:#375548`（**不缩放、不投影**） | `RippleDrawable(0x26FFFFFF, GD(14,#3E6152), mask=GD(14,#FFF))` |
| 焦点 | `outline:2px solid #353A41;outline-offset:2px`（B 用墨色环，不用绿色环：绿环在浅底上不够清楚） | `state_focused → GD(14,#3E6152,#353A41 2dp)` |
| 禁用 | `background:#CFD8D2;color:#FFFFFF`（60% 白感） | `{-state_enabled} → GD(14,#CFD8D2,无)` + `setTextColor(#FFFFFF)` |
| 加载 | 文字“连接中…” | 同禁用 + 12dp ProgressBar(`#FFFFFF`) |

### B.5.2 次按钮

| 态 | HTML | 原生 |
|---|---|---|
| 常态 | `background:#FFF;border:1px solid #E3DDD1;color:#353A41;height:50px;border-radius:14px` | `GD(14dp,#FFFFFF,#E3DDD1 1dp)` |
| 按下 | `background:#F1EDE4` | `RippleDrawable(0x14353A41, GD(14,#FFF,#E3DDD1), mask=GD(14,#FFF))` |
| 焦点 | `outline:2px solid #353A41;offset:2px` | `state_focused → GD(14,#FFF,#353A41 2dp)` |
| 禁用 | `background:#F7F5F1;color:#9A8F80;border-color:#EDE8DF` | `{-state_enabled} → GD(14,#F7F5F1,#EDE8DF 1dp)` |

### B.5.3 文字按钮（ghost，B 特有）

| 态 | HTML | 原生 |
|---|---|---|
| 常态 | 无底无框，`color:#353A41;height:48px;display:inline-flex;align-items:center;gap:4px` | `setBackground(null)` + `setTextColor(#353A41)` |
| 按下 | `color:#3E6152` + `background:rgba(53,58,65,.05)`（出现一层极淡底，只做反馈） | `RippleDrawable(0x0D353A41, null, null)`（无 mask，水波不限形状） |
| 焦点 | `outline:2px solid #353A41;offset:2px` | `state_focused → GD(999,#00000000,#353A41 2dp)` |
| 禁用 | `color:#9A8F80` | `setAlpha(0.38f)` |

### B.5.4 输入框

| 态 | 原生写法 | 说明 |
|---|---|---|
| 常态 | `GD(12dp, #FFFFFF, #E3DDD1 1dp)` | 白卡浮在纸上 = 天然有容器感，这就是"内容铺在背景上"却不再乱的原因 |
| 焦点 | `GD(12dp, #FFFFFF, #3E6152 2dp)` | 只变色不变底，比 A 更克制 |
| 错误 | `GD(12dp, #FFFFFF, #9C5245 2dp)` + 12sp 错误行 `#9C5245` | |
| 禁用 | `GD(12dp, #F7F5F1, #EDE8DF 1dp)` | |
| 按下 | 不适用 | |

### B.5.5 信息块（地址卷 / 结果块）

| 变体 | 常态 | 按下 | 焦点 | 禁用 |
|---|---|---|---|---|
| 地址卷 | `bg #FFF; 1px #E3DDD1; r12` | `bg #F7F5F1`（仅可复制时） | `1px #353A41` | 复制键隐藏，值 `—`，文字 `#9A8F80` |
| 结果块·成功 | `bg #E8EFEA; 竖线 2dp #3E6152; 无描边` | 不可点 | — | — |
| 结果块·失败 | `bg #F7EBE6; 竖线 2dp #9C5245` | 不可点 | — | — |

### B.5.6 chip（快捷填入，两方向通用结构，仅配色不同）

| 态 | A | B |
|---|---|---|
| 常态 | `bg #F3F0EA; 无描边; 13sp/500 #5E5A52` | `bg #FFF; 1dp #E3DDD1; 13sp/500 #3E6152` |
| 按下 | `bg #E9E3D9` | `bg #F1EDE4` |
| 焦点 | `outline 2px #8C7A66` | `outline 2px #353A41` |
| 禁用 | `bg #F3F0EA; color #9A8F80` | `bg #F7F5F1; color #9A8F80` |
| 尺寸 | h40 · r20 · padding 0 14 · 高 40 已满足触控 | |

> chip 与按钮的区别必须**一眼可见**：A 无描边 + 灰字（"这是标签"），B 有描边 + 绿字（"这是快捷动作"）。现状两个 chip 是系统 Button，跟"测试连接"完全同款——这才是"分不清哪个是填、哪个是提交"的根因：**不是颜色问题，是形态问题**。40dp 高 vs 48dp 高 + 圆角 20 vs 14 + 字号 13 vs 15，三重区分。

## B.6 图标方案

### 图标 1 · 盘管螺旋（`coil-spiral`，品牌标记）
`24×24` 视野，3 圈阿基米德螺旋，`r0=1.6 → r1=8.4`，`stroke 2.0`，round cap。**不要手写 SVG 螺旋 path**（A 命令拼不准），用生成器：

```java
// 原生 Canvas
Path coil = new Path();
int N = 180;
for (int i = 0; i <= N; i++) {
    float t = i / (float) N;
    float ang = (float) (t * 3f * 2 * Math.PI);          // 3 圈
    float r = 1.6f + (8.4f - 1.6f) * t;                  // 半径线性外扩
    float x = 12f + r * (float) Math.cos(ang);
    float y = 12f + r * (float) Math.sin(ang);
    if (i == 0) coil.moveTo(u(x), u(y)); else coil.lineTo(u(x), u(y));
}
```

```js
// HTML 端同一份逻辑（内联在单文件里，无外链）
const d = [...Array(181)].map((_,i)=>{
  const t=i/180, a=t*6*Math.PI, r=1.6+6.8*t;
  return (i?'L':'M')+(12+r*Math.cos(a)).toFixed(2)+' '+(12+r*Math.sin(a)).toFixed(2);
}).join('');
document.getElementById('coil').setAttribute('d', d);
```
尺寸规则：**≥40dp 用 3 圈**；24~40dp 用 2 圈（`turns=2f`, `r1=8f`）；<24dp 直接换"三格暖气片"简化标（3 个圆角竖条，见下），否则会糊成一团。

### 图标 2 · 断管（同 A.6 图标 2，换色）
`stroke #9C5245`，`44×44 r22` 底 `#F7EBE6`，图标 22×22。B 里它是"失败"的唯一强色，所以尺寸比 A 小一档、颜色更饱和，形成对比。

### 图标 3 · chevron-right（内联在文字按钮里）
`<path d="M9.8 5.4 16.4 12l-6.6 6.6" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>`，16×16，`#8C7A66`，`aria-hidden="true"`。

### 图标 4 · 暖气片（`radiator`，小尺寸降级方案）
`24×24`：外框 `rect x=3.2 y=6 w=17.6 h=12 rx=2.4`（stroke 2），内 3 竖条 `M9.4 6v12  M12 6v12  M14.6 6v12`（stroke 1.8）。用于 16~24dp 场景（如通知栏、favicon、悬浮球）。

### 图标 5 · 状态点
不是图标：`6dp` `GradientDrawable(OVAL)` 纯色。三态 `#3E6152` / `#9C5245` / `#8C7A66`。

## B.7 文案打磨

B 的文案比 A 更短、更口语、更少解释（大字排版撑得住留白，不需要靠文字填）。只列与 A 不同的部分：

| 位置 | A 版 | B 版 | 为什么 |
|---|---|---|---|
| welcome 标题 | 暖通 CRM | **连上你的 / 暖通工具台** | 两行 30sp 是版式主体；"工具台"呼应站点调性 |
| welcome 副标题 | 连接你的 HVAccRM 服务器 | **客户、工地、报价都在你自己的 NAS 上，App 只是个窗口。** | 一句说清"自托管"的价值，比"连接服务器"有说服力 |
| welcome 眉标 | （无） | **HVACRM · 自托管** | 版式签名，同时是身份声明 |
| welcome 页脚 | 手机和 NAS 在同一个 Wi-Fi 时，用内网 IP 最快 | **内网用 IP，出门用域名。** | 6+8 字，节奏对得上短横线 |
| offline 眉标 | （无） | **连接失败** | 红眉标承担"错误"语义，标题就能保持中性 |
| offline 标题 | 连不上服务器 | **连不上服务器** | 保留（24sp 单行，6 字刚好） |
| offline 清单标题 | 按这三条自查 | **按这三条自查** | 保留 |
| settings 页标题 | 服务器设置 | **服务器** | B 的眉标已经写了"连接"，标题只需名词 |
| settings 说明 | 填 NAS 地址，带 http:// 和端口号。 | **填 NAS 地址。手机和 NAS 同一 Wi-Fi 时用内网 IP。** | 把"为什么用内网"提前，省一次往返 |
| settings 主按钮 | 保存并进入 | **保存并进入** | 保留 |
| settings 次按钮 | 测试连接 | **仅测试连接** | B 把主按钮放上面，需要强调"这个不保存" |
| settings 状态行 | （无） | **当前生效 · http://…** / **尚未连接** | 新增，B 的独有信息位 |

### 两方向共用的文案红线
- 不用感叹号、不用"哦/啦/哟"。
- 数字与英文两侧不加空格（`8090` / `Wi-Fi` / `NAS` 保持原样），URL 一律等宽。
- 错误信息不出现堆栈、不出现 `Exception` 之外的裸类名（异常名放副行、缩到 11px）。

---

## 3. 选型对照（两方向都做完后，看这张表决定）

| 维度 | A 延续暖纸 | B 品牌向前 |
|---|---|---|
| 新增颜色 | 0（只用站点已有语义色 + 同色相压暗） | 2（`#3E6152` / `#9C5245`） |
| 与业务页的关系 | **同源**：卡片 `#FBF9F5`、描边 `#E2DBD0`、左竖线信息块、发丝线全部复用站点语言 | **同调不同源**：纸张与莫兰迪一致，但排版与主色是 App 自己的 |
| 页头 | 墨色 56dp 顶栏（三页一致） | 无顶栏，大字排版三页一致 |
| 状态栏 | 墨色底 + 白图标，**API 34/35 观感一致** | 纸色底 + 深图标，**必须改 `AppTheme.windowLightStatusBar`** |
| 阴影 | 1px 落地阴影 | 完全无阴影 |
| 记忆点 | 低（"这就是 HVAccRM 该有的样子"） | 高（螺旋、双行大标题、墨绿主按钮） |
| 落地风险 | 极低（改 CSS + 一个左竖线 View） | 中（改主题、身份色进入 DS、需接受"主色不是站点主色"） |
| 维护成本 | 站点改版时跟着改，不会跑偏 | 站点改版时需人工对齐一次 |
| 适合 | 想"消隐壳层"、让用户感觉不到 App 存在 | 想让人在桌面上认出这个图标、并愿意截图分享 |

**我的建议**：三页是一次性使用（首次启动 / 失败兜底 / 改地址），用户一年看不了几次——**低频界面用 A 更划算**（不引入需要长期维护的第二套主色）。但如果这个 App 要发到应用商店、要有个能当 ICON 的标记，B 的螺旋 + 墨绿是唯一能穿过这个门槛的方案。折中路线也存在：**骨架用 A，只取 B 的 logo 与主按钮色**——两者 token 结构一致，混搭零成本，改 4 个变量即可。

---

## 4. 落地自查清单（两个方向都过一遍）

**HTML 页**
- [ ] `viewport-fit=cover` + `--sat/--sab` 注入（缺一个，状态栏就会压住内容）
- [ ] `-webkit-tap-highlight-color:transparent`（否则安卓点击闪灰块，最显廉价）
- [ ] `100dvh` + `@supports` 退回 `100vh`
- [ ] emoji 全部清除：`grep -nP '[\x{1F300}-\x{1FAFF}\x{2190}-\x{27BF}]' assets/*.html` 应为空
- [ ] 无外链资源：`grep -n 'src=\|@import\|http' assets/*.html` 只允许 `crmapp://` 与示例地址
- [ ] `crmapp://settings` / `crmapp://reload`、`id="reason"` / `id="server"` 原样保留
- [ ] `[NW]` 元素都写了 `white-space:nowrap`；地址行另加 `overflow:hidden;text-overflow:ellipsis`
- [ ] 出图后对照：390dp 宽下无横向滚动（`document.body.scrollWidth === 390`）

**原生页**
- [ ] `setStateListAnimator(null)`、`setAllCaps(false)`、`setMinWidth/Height(0)`
- [ ] EditText `setPadding(14dp,0,14dp,0)` 重置（默认左偏 4dp）
- [ ] 所有 `GradientDrawable` 都是 `new` 出来的独立实例（严禁复用同一个实例赋给两个 View，改一个会连带改另一个——这是本项目最容易踩的坑）
- [ ] inset 落点：header 拿 top、ScrollView 拿 bottom(+ime)，root 不拿
- [ ] 主题 `windowLightStatusBar` 与该页底色匹配（A：两页 false；B：两页 true）
- [ ] 键盘弹出后「保存并进入」仍可点（不靠 `adjustResize`，靠 ime inset）
- [ ] 错误提示不用 Toast（会被键盘挡住），用输入框下方常驻错误行

---

## 附：可直接粘贴的 Java 工具（两方向共用，只换颜色常量）

```java
/** 圆角纯色。radius 单位 dp；fill 为 ARGB。 */
private GradientDrawable gd(float radiusDp, int fill) {
    GradientDrawable d = new GradientDrawable();
    d.setShape(GradientDrawable.RECTANGLE);
    d.setCornerRadius(dp(radiusDp));
    d.setColor(fill);
    return d;
}

/** 圆角 + 描边。strokeW = 0 表示无描边。 */
private GradientDrawable gd(float radiusDp, int fill, float strokeW, int stroke) {
    GradientDrawable d = gd(radiusDp, fill);
    if (strokeW > 0) d.setStroke(Math.max(1, dp(strokeW)), stroke);
    return d;
}

/** 按下水波。mask 必须同半径，否则水波溢出成方形。 */
private Drawable ripple(float radiusDp, int fill, int rippleArgb) {
    GradientDrawable content = gd(radiusDp, fill);
    GradientDrawable mask = gd(radiusDp, 0xFFFFFFFF);
    return new RippleDrawable(ColorStateList.valueOf(rippleArgb), content, mask);
}

/** 按钮背景：常态 / 按下 / 禁用（描边宽度可变 → 用 StateListDrawable 而不是 ColorStateList）。 */
private Drawable btnBg(float radiusDp, int fill, int strokeW, int stroke,
                       int fillDisabled, int strokeDisabled, int rippleArgb) {
    StateListDrawable sl = new StateListDrawable();
    sl.addState(new int[]{-android.R.attr.state_enabled},
                gd(radiusDp, fillDisabled, strokeW, strokeDisabled));
    sl.addState(new int[]{}, gd(radiusDp, fill, strokeW, stroke));
    GradientDrawable mask = gd(radiusDp, 0xFFFFFFFF);
    return new RippleDrawable(ColorStateList.valueOf(rippleArgb), sl, mask);
}

/** 输入框背景：焦点 / 禁用 / 常态（★ 数组顺序：具体在前，{} 最后）。 */
private Drawable fieldBg(float radiusDp, int restFill, int restStroke,
                         int focusFill, int focusStroke) {
    StateListDrawable sl = new StateListDrawable();
    sl.addState(new int[]{ android.R.attr.state_focused },
                gd(radiusDp, focusFill, 2f, focusStroke));
    sl.addState(new int[]{ -android.R.attr.state_enabled },
                gd(radiusDp, 0xFFF3F0EA, 1f, 0xFFE9E3D9));
    sl.addState(new int[]{}, gd(radiusDp, restFill, 1f, restStroke));
    return sl;
}

/** 左侧竖线信息块（B/A 通用的 .module-notes 语言）：底色 + 2dp 竖线，纯 LayerDrawable，不额外加 View。 */
private Drawable noteBlock(float radiusDp, int bgColor, int barColor) {
    GradientDrawable base = gd(radiusDp, bgColor);
    GradientDrawable bar = new GradientDrawable();
    bar.setColor(barColor);
    bar.setCornerRadius(dp(1));
    LayerDrawable layer = new LayerDrawable(new Drawable[]{ base, bar });
    layer.setLayerInset(1, dp(14), dp(12), -1, dp(12)); // 竖线贴左内边 14dp，上下内缩 12dp
    layer.setLayerWidth(1, dp(2));
    return layer;
}

/** 24 单位网格 → 目标尺寸；图标路径全部按 24×24 写。 */
private float u(float v, float sizeDp) { return v * dp(sizeDp) / 24f; }
```

```java
// 方向 A 常量
static final int A_INK = 0xFF353A41, A_INK_PRESS = 0xFF2F3339;
static final int A_PAPER = 0xFFEFEBE3, A_CARD = 0xFFFBF9F5;
static final int A_SOFT = 0xFFF3F0EA, A_SOFT2 = 0xFFF1EFE9;
static final int A_LINE = 0xFFE2DBD0, A_LINE_SOFT = 0xFFE9E3D9;
static final int A_ACCENT = 0xFF8C7A66, A_ACCENT_T = 0xFF6F6152;
static final int A_T1 = 0xFF353A41, A_T2 = 0xFF5E5A52, A_T3 = 0xFF6F6B64;
static final int A_DANGER = 0xFF8F4A4A, A_DANGER_BG = 0xFFF7EDED;
static final int A_OK = 0xFF4F7059, A_OK_BG = 0xFFE6ECE1;

// 方向 B 常量
static final int B_INK = 0xFF353A41, B_PAPER = 0xFFEFEBE3, B_CARD = 0xFFFFFFFF;
static final int B_PRIMARY = 0xFF3E6152, B_PRIMARY_PRESS = 0xFF375548, B_PRIMARY_SOFT = 0xFFE8EFEA;
static final int B_DANGER = 0xFF9C5245, B_DANGER_PRESS = 0xFF8A463A, B_DANGER_SOFT = 0xFFF7EBE6;
static final int B_ACCENT = 0xFF8C7A66, B_META = 0xFF6F6152, B_T2 = 0xFF5E5A52;
static final int B_LINE = 0xFFE3DDD1, B_LINE_SOFT = 0xFFEDE8DF, B_CHIP = 0xFFF1EDE4;
```

> 注意：`res/values/colors.xml` 里现在只有 3 个色。上面这些**建议只在 Java 里留常量**，不要一次性灌进 `colors.xml`——壳层三页用的色和业务主题（`windowBackground`/`colorAccent`）是两件事，混在一起以后分不清哪条是"App 主题色"。
