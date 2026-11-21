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
    private static final String KEY_GATE = "gate_entry";

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

    /** 安全入口字符串（服务端「系统 → 安全入口」里设的那个，如 your-gate）。没配返回 null。 */
    static String getGateOrNull(Context ctx) {
        return cleanGate(sp(ctx).getString(KEY_GATE, null));
    }

    static void setGate(Context ctx, String gate) {
        String v = cleanGate(gate);
        sp(ctx).edit().putString(KEY_GATE, v == null ? "" : v).apply();
    }

    /** 去掉空白和斜杠；不合法（空）返回 null。 */
    static String cleanGate(String gate) {
        if (gate == null) return null;
        String v = gate.trim().replace("/", "").replace(" ", "");
        return v.isEmpty() ? null : v;
    }

    /**
     * 从用户粘贴的完整地址里拆出 `/s/<入口>`。
     * 返回 {base, gate|null}，没带入口时 gate 为 null。
     */
    static String[] splitGate(String url) {
        String u = normalize(url);
        int i = u.indexOf("/s/");
        if (i < 0) return new String[]{u, null};
        String gate = u.substring(i + 3);
        int cut = gate.length();
        for (char c : new char[]{'?', '#', '/'}) {
            int k = gate.indexOf(c);
            if (k >= 0 && k < cut) cut = k;
        }
        return new String[]{u.substring(0, i), cleanGate(gate.substring(0, cut))};
    }

    /**
     * 是否内网地址。内网访问服务端会按 IP 免检门牌（gate.bypass_private），
     * 这时候再拼 `/s/xxx` 反而多余。
     */
    static boolean isPrivateHost(String url) {
        try {
            String host = new java.net.URL(normalize(url)).getHost();
            if (host == null) return true;
            if ("localhost".equals(host) || host.endsWith(".local") || host.endsWith(".lan") || host.endsWith(".home.arpa")) {
                return true;
            }
            if ("10.0.2.2".equals(host)) return true; // 模拟器里的宿主机别名
            if (!host.matches("^\\d{1,3}(\\.\\d{1,3}){3}$")) return false; // 域名一律按公网处理
            String[] p = host.split("\\.");
            int a = Integer.parseInt(p[0]);
            int b = Integer.parseInt(p[1]);
            return a == 10 || a == 127
                    || (a == 192 && b == 168)
                    || (a == 172 && b >= 16 && b <= 31)
                    || (a == 169 && b == 254);
        } catch (Exception e) {
            return false;
        }
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
