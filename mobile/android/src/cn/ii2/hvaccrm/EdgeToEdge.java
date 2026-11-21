package cn.ii2.hvaccrm;

import android.graphics.Insets;
import android.os.Build;
import android.view.View;
import android.view.WindowInsets;

/**
 * 系统栏 inset 适配。
 *
 * 背景：targetSdk 35（Android 15）起系统**强制 edge-to-edge**，窗口一律铺到状态栏和
 * 导航栏底下，主题里的 android:statusBarColor / navigationBarColor 直接失效。
 * 结果就是网页顶部主导航被信号栏压住。
 *
 * 解法：把 systemBars + displayCutout 的 inset 交给页面根容器当 padding，
 * 内容自然让到状态栏和手势条下面；留白区域由根背景色填充（配合白色系统图标）。
 */
final class EdgeToEdge {

    interface OnInsets {
        void onInsets(int left, int top, int right, int bottom);
    }

    private EdgeToEdge() {
    }

    /** 监听 root 的窗口 inset，回调系统栏四边尺寸（px）。 */
    static void apply(final View root, final OnInsets cb) {
        root.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
            @Override
            public WindowInsets onApplyWindowInsets(View v, WindowInsets insets) {
                int l = 0, t = 0, r = 0, b = 0;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    // ime 也要算：edge-to-edge 下 windowSoftInputMode=adjustResize 不再生效，
                    // 不自己让位的话网页里的输入框会被键盘盖住。
                    Insets i = insets.getInsets(WindowInsets.Type.systemBars()
                            | WindowInsets.Type.displayCutout()
                            | WindowInsets.Type.ime());
                    l = i.left;
                    t = i.top;
                    r = i.right;
                    b = i.bottom;
                } else {
                    l = insets.getSystemWindowInsetLeft();
                    t = insets.getSystemWindowInsetTop();
                    r = insets.getSystemWindowInsetRight();
                    b = insets.getSystemWindowInsetBottom();
                }
                cb.onInsets(l, t, r, b);
                return insets;
            }
        });
        root.requestApplyInsets();
    }
}
