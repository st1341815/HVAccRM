package cn.ii2.hvaccrm;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;

/**
 * HVAccRM 移动端主界面。
 *
 * 设计取向：不重写业务前端，用系统 WebView 承载现有服务端渲染页面（已具备手机响应式），
 * 把「网页在手机浏览器里差的那一口气」用原生补上：
 *   1. 拍照/相册上传（<input type=file> 走 onShowFileChooser）
 *   2. 文件下载（合同/报表导出）交给 DownloadManager，并带上 Cookie
 *   3. 返回键按页面历史退，到底再按两次才退出
 *   4. 断网/服务不可达时落到本地离线页，可一键重试或改地址
 *   5. 悬浮球提供刷新 / 改服务器 / 清缓存 / 关于
 */
public class MainActivity extends Activity {

    private static final int REQ_FILE = 1001;
    private static final int REQ_SETTINGS = 1002;

    private WebView web;
    private ProgressBar bar;
    private TextView fab;
    private ValueCallback<Uri[]> filePathCallback;
    private Uri cameraOutUri;
    private String serverUrl;
    private long lastBackPressedAt = 0;

    /** 系统栏 inset（px）：Android 15 强制 edge-to-edge，内容必须自己让位。 */
    private int insetsLeft = 0;
    private int insetsTop = 0;
    private int insetsRight = 0;
    private int insetsBottom = 0;
    private boolean fabPlaced = false;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Runnable dimFab = new Runnable() {
        @Override public void run() { if (fab != null) fab.setAlpha(0.35f); }
    };

    // ---------------------------------------------------------------- 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        configureWeb();

        String saved = Prefs.getServerOrNull(this);
        if (saved == null) {
            // 首次启动：先给个本地欢迎页，同时把用户推到设置界面填地址
            serverUrl = Prefs.DEFAULT_SERVER;
            web.loadUrl("file:///android_asset/welcome.html");
            openSettings();
        } else {
            serverUrl = saved;
            loadHome();
        }
        cleanOldCameraFiles();
    }

    /** 窗口重新拿到焦点时把焦点还给 WebView（从设置页/相册选择器返回后同样生效）。 */
    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && web != null && !web.hasFocus()) {
            web.requestFocus();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush(); // 确认登录 Cookie 落盘，避免杀进程后掉登录
    }

    @Override
    protected void onDestroy() {
        ui.removeCallbacksAndMessages(null);
        if (web != null) {
            web.setWebChromeClient(null);
            web.destroy();
        }
        super.onDestroy();
    }

    // ---------------------------------------------------------------- UI 搭建

    private int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        // 深色底：edge-to-edge 后状态栏/手势条区域露出这个颜色，配主题里的白色系统图标
        root.setBackgroundColor(0xFF353A41);

        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(100);
        bar.setProgressTintList(ColorStateList.valueOf(0xFF8C7A66));
        bar.setBackgroundColor(Color.TRANSPARENT);
        bar.setVisibility(View.GONE);
        FrameLayout.LayoutParams blp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3));
        blp.gravity = Gravity.TOP;
        root.addView(bar, blp);

        fab = new TextView(this);
        fab.setText("≡");
        fab.setTextSize(20);
        fab.setTextColor(Color.WHITE);
        fab.setGravity(Gravity.CENTER);
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(0xFF353A41);
        fab.setBackground(bg);
        fab.setAlpha(0.85f);
        fab.setElevation(dp(6));
        FrameLayout.LayoutParams flp = new FrameLayout.LayoutParams(dp(44), dp(44));
        flp.gravity = Gravity.TOP | Gravity.START;
        flp.leftMargin = dp(8);
        flp.topMargin = dp(120);
        root.addView(fab, flp);
        setupFab();

        setContentView(root);
        EdgeToEdge.apply(root, new EdgeToEdge.OnInsets() {
            @Override public void onInsets(int l, int t, int r, int b) {
                applySystemInsets(l, t, r, b);
            }
        });
    }

    /** 把系统栏尺寸落到各子控件上：网页留白、进度条下移、悬浮球避开状态栏。 */
    private void applySystemInsets(int l, int t, int r, int b) {
        insetsLeft = l;
        insetsTop = t;
        insetsRight = r;
        insetsBottom = b;

        // 用 margin 而不是 padding：WebView 会用自己的背景把 padding 区域涂掉，
        // 只有缩小它的 frame 才能让 root 的深色透出来，白色系统图标才看得见。
        if (web != null) {
            FrameLayout.LayoutParams p = (FrameLayout.LayoutParams) web.getLayoutParams();
            p.leftMargin = l;
            p.topMargin = t;
            p.rightMargin = r;
            p.bottomMargin = b;
            web.setLayoutParams(p);
        }

        if (bar != null) {
            FrameLayout.LayoutParams p = (FrameLayout.LayoutParams) bar.getLayoutParams();
            p.topMargin = t;
            bar.setLayoutParams(p);
        }

        if (fab != null && !fabPlaced) {
            // 只摆一次；之后位置归用户拖动
            FrameLayout.LayoutParams p = (FrameLayout.LayoutParams) fab.getLayoutParams();
            p.topMargin = t + dp(56);
            fab.setLayoutParams(p);
            fabPlaced = true;
        }
    }

    /** 悬浮球：可拖动，轻点弹菜单，静止 4 秒后变淡免得挡内容。 */
    private void setupFab() {
        final int slop = dp(6);
        fab.setOnTouchListener(new View.OnTouchListener() {
            float downRawX, downRawY;
            int startLeft, startTop;
            boolean moved;

            @Override
            public boolean onTouch(View v, android.view.MotionEvent e) {
                FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) v.getLayoutParams();
                switch (e.getActionMasked()) {
                    case android.view.MotionEvent.ACTION_DOWN:
                        downRawX = e.getRawX();
                        downRawY = e.getRawY();
                        startLeft = lp.leftMargin;
                        startTop = lp.topMargin;
                        moved = false;
                        v.setAlpha(1f);
                        ui.removeCallbacks(dimFab);
                        return true;
                    case android.view.MotionEvent.ACTION_MOVE:
                        int dx = (int) (e.getRawX() - downRawX);
                        int dy = (int) (e.getRawY() - downRawY);
                        if (Math.abs(dx) > slop || Math.abs(dy) > slop) moved = true;
                        if (moved) {
                            View parent = (View) v.getParent();
                            int minL = insetsLeft, minT = insetsTop;
                            int maxL = Math.max(minL, parent.getWidth() - insetsRight - v.getWidth());
                            int maxT = Math.max(minT, parent.getHeight() - insetsBottom - v.getHeight());
                            lp.leftMargin = Math.max(minL, Math.min(maxL, startLeft + dx));
                            lp.topMargin = Math.max(minT, Math.min(maxT, startTop + dy));
                            v.setLayoutParams(lp);
                        }
                        return true;
                    case android.view.MotionEvent.ACTION_UP:
                        if (!moved) showMenu();
                        ui.postDelayed(dimFab, 4000);
                        return true;
                    case android.view.MotionEvent.ACTION_CANCEL:
                        ui.postDelayed(dimFab, 4000);
                        return true;
                }
                return false;
            }
        });
        ui.postDelayed(dimFab, 4000);
    }

    private void showMenu() {
        PopupMenu menu = new PopupMenu(this, fab);
        menu.getMenu().add(0, 1, 0, "刷新页面");
        menu.getMenu().add(0, 2, 1, "服务器地址");
        menu.getMenu().add(0, 3, 2, "清除网页缓存");
        menu.getMenu().add(0, 4, 3, "关于");
        menu.setOnMenuItemClickListener(item -> {
            switch (item.getItemId()) {
                case 1:
                    reloadPage();
                    return true;
                case 2:
                    openSettings();
                    return true;
                case 3:
                    clearWebCache();
                    return true;
                case 4:
                    showAbout();
                    return true;
            }
            return false;
        });
        menu.show();
    }

    // ---------------------------------------------------------------- WebView 配置

    private void configureWeb() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        s.setSupportMultipleWindows(false); // target=_blank 就地打开，别弹新窗口
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        if (Build.VERSION.SDK_INT >= 26) {
            s.setSafeBrowsingEnabled(true);
        }

        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(web, true);

        WebView.setWebContentsDebuggingEnabled(true); // 测试版保留远程调试（chrome://inspect）

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleUrl(request.getUrl());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                bar.setVisibility(View.GONE);
                // WebView 没拿到焦点时，第一次点击只会用来"取焦"，页面根本收不到 ——
                // 现场表现就是「点了拍照没反应，要点两下」。这里每次加载完成都主动取焦。
                view.setFocusable(true);
                view.setFocusableInTouchMode(true);
                view.requestFocus();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    showOffline(error != null ? String.valueOf(error.getDescription()) : "网络不可用");
                }
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int p) {
                if (p >= 100) {
                    bar.setVisibility(View.GONE);
                } else {
                    bar.setVisibility(View.VISIBLE);
                    bar.setProgress(p);
                }
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = cb;
                return pickFile(params);
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            // 用系统 DownloadManager，并把 WebView 的登录 Cookie 一起带上，否则会被踢回登录页
            try {
                String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
                DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null && !cookie.isEmpty()) {
                    req.addRequestHeader("Cookie", cookie);
                }
                if (userAgent != null) {
                    req.addRequestHeader("User-Agent", userAgent);
                }
                req.setMimeType(mimeType);
                req.setTitle(name);
                req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                try {
                    req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                } catch (Exception e) {
                    req.setDestinationInExternalFilesDir(MainActivity.this, null, name);
                }
                DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                if (dm != null) {
                    dm.enqueue(req);
                    toast("已开始下载：" + name);
                }
            } catch (Exception e) {
                // 兜底：交给系统浏览器处理
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception ignore) {
                    toast("下载失败，请重试");
                }
            }
        });
    }

    // ---------------------------------------------------------------- 导航

    /** 统一处理 URL：自定义 scheme、站内放行、站外/电话/邮件交给外部。 */
    private boolean handleUrl(Uri u) {
        if (u == null) return false;
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase();

        if ("crmapp".equals(scheme)) {
            String host = u.getHost() == null ? "" : u.getHost();
            if ("reload".equals(host)) reloadPage();
            else if ("settings".equals(host)) openSettings();
            else if ("about".equals(host)) showAbout();
            return true;
        }
        if ("tel".equals(scheme) || "sms".equals(scheme) || "mailto".equals(scheme)
                || "weixin".equals(scheme) || "alipays".equals(scheme) || "geo".equals(scheme)) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, u));
            } catch (Exception ignore) {
            }
            return true;
        }
        if ("http".equals(scheme) || "https".equals(scheme)) {
            if (isSameSite(u)) return false;        // 站内：交给 WebView
            try {                                    // 站外：系统浏览器
                startActivity(new Intent(Intent.ACTION_VIEW, u));
            } catch (Exception ignore) {
            }
            return true;
        }
        return true; // file:// 等其它 scheme 一并拦下
    }

    private boolean isSameSite(Uri u) {
        try {
            Uri base = Uri.parse(serverUrl);
            String h1 = u.getHost();
            String h2 = base.getHost();
            if (h1 == null || h2 == null) return false;
            if (!h1.equalsIgnoreCase(h2)) return false;
            return effPort(u) == effPort(base);
        } catch (Exception e) {
            return false;
        }
    }

    private int effPort(Uri u) {
        if (u.getPort() > 0) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }

    private void loadHome() {
        if (!networkUp()) {
            showOffline("当前无网络连接");
            return;
        }
        web.loadUrl(serverUrl + "/");
    }

    private void reloadPage() {
        String cur = web.getUrl();
        if (cur == null || cur.startsWith("file://")) loadHome();
        else {
            if (!networkUp()) { showOffline("当前无网络连接"); return; }
            web.reload();
        }
    }

    private boolean networkUp() {
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) return true;
            NetworkInfo ni = cm.getActiveNetworkInfo();
            return ni != null && ni.isConnected();
        } catch (Exception e) {
            return true;
        }
    }

    private void showOffline(String reason) {
        bar.setVisibility(View.GONE);
        String page = "file:///android_asset/offline.html?reason="
                + Uri.encode(reason == null ? "" : reason)
                + "&server=" + Uri.encode(serverUrl);
        web.loadUrl(page);
    }

    private void clearWebCache() {
        web.clearCache(true);
        web.clearFormData();
        web.clearHistory();
        toast("网页缓存已清除（登录状态保留）");
    }

    private void openSettings() {
        Intent i = new Intent(this, SettingsActivity.class);
        i.putExtra(SettingsActivity.EXTRA_CURRENT, serverUrl);
        startActivityForResult(i, REQ_SETTINGS);
    }

    private void showAbout() {
        String ua = web.getSettings().getUserAgentString();
        new AlertDialog.Builder(this)
                .setTitle("暖通CRM 移动端")
                .setMessage("版本：0.1.0（测试版）\n"
                        + "当前服务器：" + serverUrl + "\n\n"
                        + "承载内核：" + System.getProperty("http.agent") + "\n\n"
                        + "提示：长按/轻点左侧悬浮球可刷新页面或切换服务器。")
                .setPositiveButton("知道了", null)
                .setNeutralButton("改服务器", (d, w) -> openSettings())
                .show();
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }

    // ---------------------------------------------------------------- 返回键

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastBackPressedAt < 2000) {
            super.onBackPressed();
        } else {
            lastBackPressedAt = now;
            toast("再按一次退出");
        }
    }

    // ---------------------------------------------------------------- 文件选择 / 拍照

    /** 判断这次 <input type=file> 想要什么，据此决定给「拍照 + 图库」还是「文件管理器」。 */
    private boolean pickFile(WebChromeClient.FileChooserParams params) {
        boolean wantsImage = false;
        boolean allowMultiple = false;
        if (params != null) {
            try {
                allowMultiple = params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE;
                String[] accepts = params.getAcceptTypes();
                if (accepts != null) {
                    for (String a : accepts) {
                        if (a == null) continue;
                        String low = a.toLowerCase();
                        if (low.contains("image") || low.contains(".jpg") || low.contains(".png")
                                || low.contains(".jpeg") || low.contains(".webp") || low.contains(".heic")) {
                            wantsImage = true;
                        }
                    }
                }
            } catch (Exception ignore) {
            }
        }
        if (params == null) wantsImage = true;

        Intent content = new Intent(Intent.ACTION_GET_CONTENT);
        content.addCategory(Intent.CATEGORY_OPENABLE);
        content.setType(wantsImage ? "image/*" : "*/*");
        if (wantsImage) content.putExtra("android.content.extra.SHOW_ADVANCED", true);
        if (allowMultiple) content.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);

        Intent chooser = Intent.createChooser(content, wantsImage ? "选择照片来源" : "选择文件");

        Intent capture = null;
        if (wantsImage) {
            capture = buildCaptureIntent();
            if (capture != null) {
                chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{capture});
            }
        }

        try {
            startActivityForResult(chooser, REQ_FILE);
            return true;
        } catch (Exception e) {
            filePathCallback = null;
            cameraOutUri = null;
            toast("无法打开文件选择器");
            return false;
        }
    }

    /** 构造拍照 intent；输出走自家的 content:// provider（file:// 在 Android 7+ 会被系统拒绝）。 */
    private Intent buildCaptureIntent() {
        try {
            if (!getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)) {
                return null;
            }
            File dir = SimpleFileProvider.cameraDir(this);
            File out = new File(dir, "crm_" + System.currentTimeMillis() + ".jpg");
            cameraOutUri = SimpleFileProvider.newCameraUri(this, out);
            Intent i = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
            i.putExtra(MediaStore.EXTRA_OUTPUT, cameraOutUri);
            i.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            if (i.resolveActivity(getPackageManager()) == null) {
                cameraOutUri = null;
                return null;
            }
            return i;
        } catch (Exception e) {
            cameraOutUri = null;
            return null;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);

        if (requestCode == REQ_SETTINGS) {
            String saved = Prefs.getServerOrNull(this);
            if (saved != null) {
                serverUrl = saved;
                loadHome();
            }
            return;
        }

        if (requestCode == REQ_FILE) {
            Uri[] results = null;
            if (resultCode == RESULT_OK) {
                if (data != null && data.getData() != null) {
                    // 来自图库/文件管理器
                    ClipData clip = data.getClipData();
                    if (clip != null && clip.getItemCount() > 0) {
                        results = new Uri[clip.getItemCount()];
                        for (int i = 0; i < clip.getItemCount(); i++) {
                            results[i] = clip.getItemAt(i).getUri();
                        }
                    } else {
                        results = new Uri[]{data.getData()};
                    }
                } else if (cameraOutUri != null) {
                    // 来自相机：文件已由相机写入 cameraOutUri 指向的位置
                    results = new Uri[]{cameraOutUri};
                }
            }
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(results);
                filePathCallback = null;
            }
            cameraOutUri = null;
        }
    }

    /** 相机照片留在 cache，清掉三天前的，别让缓存无限长。 */
    private void cleanOldCameraFiles() {
        new Thread(() -> {
            try {
                File dir = SimpleFileProvider.cameraDir(this);
                File[] files = dir.listFiles();
                if (files == null) return;
                long cut = System.currentTimeMillis() - 3L * 24 * 3600 * 1000;
                for (File f : files) {
                    if (f.isFile() && f.lastModified() < cut) {
                        //noinspection ResultOfMethodCallIgnored
                        f.delete();
                    }
                }
            } catch (Exception ignore) {
            }
        }).start();
    }
}
