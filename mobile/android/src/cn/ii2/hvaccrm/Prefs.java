package cn.ii2.hvaccrm;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * 应用本地配置：服务器地址等。
 * 故意用 SharedPreferences 而非 AccountManager —— 这里只存地址，不存任何凭据；
 * 登录态由 WebView 的 CookieManager 自己持久化。
 */
final class Prefs {

    private static final String FILE = "hvaccrm_app";
    private static final String KEY_SERVER = "server_url";

    /** 默认指向外网域名（记忆里确认过的正式入口），内网可自行改成 http://192.168.1.8:8090 */
    static final String DEFAULT_SERVER = "https://crm.example.com:8443";

    private Prefs() {}

    private static SharedPreferences sp(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    /** 返回已保存的地址；从未配置过则返回 null（用于「首次启动」判定）。 */
    static String getServerOrNull(Context ctx) {
        String v = sp(ctx).getString(KEY_SERVER, null);
        if (v == null) return null;
        v = v.trim();
        return v.isEmpty() ? null : v;
    }

    static String getServer(Context ctx) {
        String v = getServerOrNull(ctx);
        return v == null ? DEFAULT_SERVER : v;
    }

    static void setServer(Context ctx, String url) {
        sp(ctx).edit().putString(KEY_SERVER, normalize(url)).apply();
    }

    /** 补全 scheme、去掉尾部斜杠和空白 —— 用户手输的东西什么样都有。 */
    static String normalize(String raw) {
        if (raw == null) return DEFAULT_SERVER;
        String v = raw.trim();
        if (v.isEmpty()) return DEFAULT_SERVER;
        if (!v.startsWith("http://") && !v.startsWith("https://")) {
            v = "https://" + v;
        }
        while (v.endsWith("/")) {
            v = v.substring(0, v.length() - 1);
        }
        return v;
    }
}
