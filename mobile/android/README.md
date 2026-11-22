# HVAccRM 移动端（Android）

HVAccRM 的安卓客户端，测试版 **v0.1.5**。纯 WebView 壳 + 原生能力增强，**零 AndroidX / 零 Gradle / 零 npm 依赖**，一个 `./build.sh` 出 APK。

---

## 一、这是什么，为什么这么做

服务端已经是「FastAPI + Jinja2 服务端渲染 + HTMX」的多页应用，且早期已做过移动端响应式（宽表格卡片化、侧栏抽屉化、safe-area）。所以手机端**没有必要重写一套前端**：

| 方案 | 工作量 | 结论 |
|---|---|---|
| 原生/Flutter 重写 | 30 个页面 + 补一整套 JSON API，数周 | 测试阶段不划算 |
| uni-app/Taro 跨端 | 前端重写一套 | 同上 |
| **WebView 壳 + 原生增强** | 一个 Activity + 一个设置页 | **本方案**：一次做好，之后随服务端自动更新 |

壳负责把浏览器做不到/做得差的部分补上：**系统相机拍照直传、文件选择、返回键、下载、离线兜底、服务器地址可切换**。业务界面仍然由服务端渲染，改一个模板手机上立刻生效，不需要重新发版。

> 这也意味着：**服务端一改，手机端自动是新版**——测试期迭代快，这点比原生 APK 省事得多。

---

## 二、目录结构

```
mobile/android/
├── AndroidManifest.xml          # 权限（只要 INTERNET）、<queries>（相机可见性）、provider
├── build.sh                     # 构建脚本：aapt2 → javac → d8 → zipalign → apksigner
├── .gitignore                   # build/ dist/ *.keystore
├── res/
│   ├── values/{strings,colors,styles}.xml
│   └── mipmap-*/ic_launcher.png # 复用 PWA 图标（app/static/icons/icon-512.png 缩放）
├── assets/
│   ├── welcome.html             # 首次启动引导（未配置服务器时）
│   └── offline.html             # 加载失败兜底页（可重试 / 改地址）
└── src/cn/ii2/hvaccrm/
    ├── MainActivity.java        # WebView + 进度条 + 悬浮球 + 拍照/下载/返回键
    ├── SettingsActivity.java    # 服务器地址 + 现场「测试连接」
    ├── SimpleFileProvider.java  # 最小 ContentProvider，给系统相机写照片用
    ├── EdgeToEdge.java          # 系统栏 inset 适配（Android 15 强制 edge-to-edge 的兜底）
    └── Prefs.java               # 服务器地址持久化
```

---

## 三、构建

### 依赖

- **JDK 17**（`javac` / `keytool`）
- **Android SDK**：build-tools 34 + platform android-35

```bash
# Debian/Ubuntu 上装构建链（一次性）
sudo apt-get install -y --no-install-recommends openjdk-17-jdk-headless

export SDK=/opt/android-sdk && sudo mkdir -p $SDK && sudo chown $USER $SDK
curl -L -o /tmp/bt.zip  https://dl.google.com/android/repository/build-tools_r34-linux.zip
curl -L -o /tmp/pf.zip  https://dl.google.com/android/repository/platform-35_r02.zip
unzip -q /tmp/bt.zip -d $SDK/bt        # 解压后目录名是 android-14，属正常
unzip -q /tmp/pf.zip -d $SDK/platforms # → platforms/android-35
```

> SDK 路径不同的话：`ANDROID_SDK=/your/sdk ./build.sh`

### 打包

```bash
./build.sh            # debug 签名 → dist/hvaccrm-mobile-0.1.0-debug.apk
./build.sh release    # release.keystore 签名（首次会自动生成，口令 KS_PASS，默认 hvaccrm2026）
```

**为什么不用 Gradle**：AGP 要从 `maven.google.com` 拉依赖，内网/国内常连不通；这个壳只有 5 个 Java 文件，用 `aapt2 + javac + d8 + apksigner` 手工串起来，10 秒出包，且**完全离线可重复构建**。

⚠️ **`.debug.keystore` 必须留在工程根目录、绝不进 build/**。build/ 每次 `rm -rf`，密钥一旦跟着重建，签名就变了，用户手机上会 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，只能卸载重装（配置和登录态全丢）。

---

## 四、安装

```bash
adb install -r dist/hvaccrm-mobile-0.1.0-debug.apk
```

或把 APK 传到手机点击安装（需允许「安装未知来源应用」）。最低 Android 7.0（API 24）。

**首次启动**会直接进「服务器地址」页：

| 场景 | 填什么 |
|---|---|
| 家里/公司局域网 | `http://<内网IP>:8090` |
| 外网（链路正常时） | `https://<你的域名>[:端口]` |

点「测试连接」会当场请求 `/health`：绿色 ✓ 表示通，红色 ✗ 会给出原因（连不上 / 跳转 / HTTP 错误码）。保存后可在 **右下角悬浮球 → 服务器地址** 随时改回来。

---

## 五、已实现的能力

| 能力 | 说明 |
|---|---|
| 登录态保持 | WebView CookieManager 持久化，`onPause` 时 `flush()`，杀进程不掉登录 |
| 系统相机拍照直传 | `<input type=file accept=image/*>` → `onShowFileChooser` → 「相机 / 照片」二选一；照片写进 App 私有目录（自实现 ContentProvider），不污染系统相册 |
| 相册多选上传 | 支持 `multiple`，与 Web 端行为一致 |
| 返回键 | 优先网页后退，退无可退时「再按一次退出」 |
| 文件下载 | 导出 PDF/Excel 等走系统 DownloadManager，**并把登录 Cookie 一起带上**，否则会下载到登录页 |
| 外链/电话 | 非本站链接交给系统浏览器，`tel:` / `mailto:` / 微信 scheme 交给对应 App |
| 离线兜底 | 主文档加载失败 → 本地 offline.html，带「重新连接」「改服务器地址」 |
| 悬浮球 | 刷新 / 服务器地址 / 清除缓存（保留登录）/ 关于；3 秒无操作自动变淡，可拖动 |
| 安全入口 | 服务端开了「系统 → 安全入口」时，App 启动先自动走 `/s/<入口>` 换 30 天 Cookie，之后无感；入口串可单独填，也可直接把 `…/s/xxx` 粘进地址栏 |
| 明文 HTTP | 已开 `usesCleartextTraffic`，内网 `http://IP:8090` 可用 |

### 三个踩过的坑（改代码前务必看）

1. **`<queries>` 不可省**。targetSdk ≥ 30 有包可见性限制，manifest 里不声明 `IMAGE_CAPTURE` 的话，`intent.resolveActivity()` 永远返回 null，App 会误判「设备没有相机」→ **拍照入口直接消失**。模拟器实测过，真机一样。
2. **WebView 焦点**。未获焦点的 WebView 会把第一次点击吃掉（只用来取焦），表现是「点上传没反应，要点两下」。`onPageFinished` / `onWindowFocusChanged` 里都要 `requestFocus()`。
3. **Android 15 强制 edge-to-edge（状态栏压住导航）**。`targetSdk=35` 起，API 35 系统**强制**把窗口铺到状态栏和导航栏底下，主题里的 `android:statusBarColor` / `navigationBarColor` 直接失效（这两个属性在 35 上被废弃）。不自己处理 inset 的话，网页顶部的汉堡菜单/主导航就被信号栏压住——本机 API 34 模拟器测不出来，必须用 API 35 镜像复现。
   - 处理方式见 `EdgeToEdge.java`：监听 `systemBars | displayCutout | ime`，把 inset 落到 WebView 的 **margin**（不是 padding，WebView 会用自己的背景涂掉 padding 区域）。
   - 根布局底色设 `#353A41`，让状态栏/手势条区域透出深色，配合 `windowLightStatusBar=false` 的白色图标。
   - 设置页是浅底，单独用 `SettingsTheme` 把系统图标切成深色。
   - **inset 里必须带上 `ime`**：edge-to-edge 下 `windowSoftInputMode=adjustResize` 不再生效，不自己让位，网页输入框会被键盘盖住。
   - API 34 上系统已让位、inset 回调为 0，所以同一份代码在两端都不会出现双重留白（已实测）。

---

## 六、已知限制（测试版）

- 没有底部 Tab 导航，仍是服务端的侧栏抽屉；单手高频操作不如原生 App 顺手。
- 不支持离线录入（断网只能看兜底页）。
- 应用名「暖通CRM」、包名 `cn.ii2.hvaccrm` 都是测试用的，正式分发前要定。
- debug 签名，不能上应用商店；换正式 keystore 后**必须卸载重装一次**（签名变了）。
- 拍照后临时文件留在 `cacheDir/camera/`，App 启动时会清理 3 天前的（`cleanOldCameraFiles`）。

---

## 七、版本记录

| 版本 | 日期 | 内容 |
|---|---|---|
| 0.1.3 | 2026-11-21 | 脱敏：界面里的示例地址、占位符、安全入口示例全部换成 IANA 保留的 `example.com` 与 `192.168.1.100`，真实域名/内网 IP/入口串不再进 APK；关于页版本号改为读 APK 元数据 |
| 0.1.2 | 2026-11-21 | 支持服务端「安全入口」：设置页新增入口串输入（也可从 `…/s/xxx` 自动拆解），启动自动过门牌换 30 天 Cookie，测试连接同步走门牌；入口串错误时明确提示，主文档 404 自动弹窗引导 |
| 0.1.1 | 2026-11-21 | 修 Android 15 全屏/edge-to-edge 下状态栏遮挡主导航：新增 `EdgeToEdge` inset 适配（含 IME）、WebView 改 margin 让位、根布局深色配白图标、设置页改深色系统图标。API 34 无回归 |
| 0.1.0 | 2026-11-21 | 首个测试版：WebView 壳 + 原生拍照/选图上传 + 服务器地址配置与测试连接 + 返回键/下载/外链/离线兜底 + 悬浮球菜单 |
：设置页新增入口串输入（也可从 `…/s/xxx` 自动拆解），启动自动过门牌换 30 天 Cookie，测试连接同步走门牌；入口串错误时明确提示，主文档 404 自动弹窗引导 |
| 0.1.1 | 2026-11-21 | 修 Android 15 全屏/edge-to-edge 下状态栏遮挡主导航：新增 `EdgeToEdge` inset 适配（含 IME）、WebView 改 margin 让位、根布局深色配白图标、设置页改深色系统图标。API 34 无回归 |
| 0.1.0 | 2026-11-21 | 首个测试版：WebView 壳 + 原生拍照/选图上传 + 服务器地址配置与测试连接 + 返回键/下载/外链/离线兜底 + 悬浮球菜单 |
