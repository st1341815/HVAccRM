package cn.ii2.hvaccrm;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 服务器地址设置页。
 * 测试版刻意保留「手工填地址 + 现场测试连接」——真机联调时最常见的问题就是地址写错、
 * 内网 IP 写了外网域名、HTTP/HTTPS 混了，这里当场就能看出来。
 */
public class SettingsActivity extends Activity {

    static final String EXTRA_CURRENT = "current";

    private static final int FG = 0xFF353A41;
    private static final int MUTED = 0xFF6B7280;

    private EditText input;
    private TextView hint;
    private Button testBtn;
    private Button saveBtn;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("服务器地址");

        String current = getIntent().getStringExtra(EXTRA_CURRENT);
        if (TextUtils.isEmpty(current)) current = Prefs.getServerOrNull(this);
        if (TextUtils.isEmpty(current)) current = Prefs.DEFAULT_SERVER;

        setContentView(buildView(current));
    }

    private int dp(float v) {
        return (int) (v * getResources().getDisplayMetrics().density + 0.5f);
    }

    private View buildView(String current) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundColor(0xFFEFEBE3);
        box.setPadding(dp(18), dp(18), dp(18), dp(18));

        TextView title = new TextView(this);
        title.setText("连接哪台服务器");
        title.setTextSize(20);
        title.setTextColor(FG);
        title.setGravity(Gravity.START);
        box.addView(title, lp(-1, -2, 0, 0, 0, dp(6)));

        TextView desc = new TextView(this);
        desc.setText("填 CRM 的访问地址即可，http:// 或 https:// 都能用。\n"
                + "· 家里/公司局域网：http://192.168.1.8:8090\n"
                + "· 外网（需域名可用）：https://crm.example.com:8443");
        desc.setTextSize(13);
        desc.setTextColor(MUTED);
        desc.setLineSpacing(dp(3), 1f);
        box.addView(desc, lp(-1, -2, 0, 0, 0, dp(14)));

        input = new EditText(this);
        input.setText(current);
        input.setTextSize(16);
        input.setTextColor(FG);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setImeOptions(EditorInfo.IME_ACTION_DONE);
        input.setHint("https://crm.example.com:8443");
        box.addView(input, lp(-1, -2, 0, 0, 0, dp(10)));

        LinearLayout quick = new LinearLayout(this);
        quick.setOrientation(LinearLayout.HORIZONTAL);
        Button lan = smallButton("用内网地址");
        lan.setOnClickListener(v -> input.setText("http://192.168.1.8:8090"));
        Button wan = smallButton("用外网域名");
        wan.setOnClickListener(v -> input.setText("https://crm.example.com:8443"));
        quick.addView(lan, new LinearLayout.LayoutParams(0, dp(40), 1f));
        quick.addView(wan, lp(0, dp(40), 1f, dp(8), 0, 0, 0));
        box.addView(quick, lp(-1, -2, 0, 0, 0, dp(12)));

        hint = new TextView(this);
        hint.setTextSize(13);
        hint.setTextColor(MUTED);
        hint.setText("");
        hint.setMinimumHeight(dp(20));
        hint.setLineSpacing(dp(2), 1f);
        box.addView(hint, lp(-1, -2, 0, 0, 0, dp(10)));

        testBtn = new Button(this);
        testBtn.setText("测试连接");
        testBtn.setOnClickListener(v -> testConnection());
        box.addView(testBtn, lp(-1, dp(46), 0, 0, 0, dp(8)));

        saveBtn = new Button(this);
        saveBtn.setText("保存并进入");
        saveBtn.setOnClickListener(v -> save());
        box.addView(saveBtn, lp(-1, dp(48), 0, 0, 0, 0));

        TextView tip = new TextView(this);
        tip.setText("\n提示：保存后可在 App 右下角悬浮球里随时改回来。");
        tip.setTextSize(12);
        tip.setTextColor(MUTED);
        box.addView(tip, lp(-1, -2, 0, 0, 0, 0));

        ScrollView sc = new ScrollView(this);
        sc.setBackgroundColor(0xFFEFEBE3);
        sc.addView(box, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return sc;
    }

    private Button smallButton(String text) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(13);
        b.setAllCaps(false);
        return b;
    }

    private LinearLayout.LayoutParams lp(int w, int h, int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h);
        p.setMargins(l, t, r, b);
        return p;
    }

    private LinearLayout.LayoutParams lp(int w, int h, float weight, int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h, weight);
        p.setMargins(l, t, r, b);
        return p;
    }

    // ---------------------------------------------------------------- 动作

    private void save() {
        String raw = input.getText().toString();
        String url = Prefs.normalize(raw);
        try {
            new URL(url);
        } catch (Exception e) {
            Toast.makeText(this, "地址格式不对，请检查", Toast.LENGTH_LONG).show();
            return;
        }
        Prefs.setServer(this, url);
        setResult(RESULT_OK, new Intent().putExtra(EXTRA_CURRENT, url));
        finish();
    }

    private void testConnection() {
        String url = Prefs.normalize(input.getText().toString());
        testBtn.setEnabled(false);
        testBtn.setText("测试中…");
        hint.setTextColor(MUTED);
        hint.setText("正在连接 " + url + " …");

        new Thread(() -> {
            final String[] msg = new String[2]; // [文本, "ok"/"fail"]
            HttpURLConnection conn = null;
            try {
                URL u = new URL(url + "/health");
                conn = (HttpURLConnection) u.openConnection();
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(8000);
                conn.setRequestMethod("GET");
                conn.setInstanceFollowRedirects(false);
                int code = conn.getResponseCode();
                String body = readSnippet(conn.getInputStream(), 200);
                if (code >= 200 && code < 300) {
                    msg[0] = "✓ 连接成功（HTTP " + code + "）\n" + body;
                    msg[1] = "ok";
                } else if (code >= 300 && code < 400) {
                    msg[0] = "△ 服务端返回跳转（HTTP " + code + "）\n地址可能少了 / 或证书/反代有问题。";
                    msg[1] = "fail";
                } else {
                    msg[0] = "✗ 服务端返回 HTTP " + code + "\n" + body;
                    msg[1] = "fail";
                }
            } catch (Exception e) {
                msg[0] = "✗ 连不上：" + e.getClass().getSimpleName()
                        + (e.getMessage() != null ? "\n" + e.getMessage() : "")
                        + "\n请确认手机和服务器在同一网络，或域名/端口写对了。";
                msg[1] = "fail";
            } finally {
                if (conn != null) conn.disconnect();
            }
            runOnUiThread(() -> {
                testBtn.setEnabled(true);
                testBtn.setText("测试连接");
                hint.setText(msg[0]);
                hint.setTextColor("ok".equals(msg[1]) ? 0xFF2E7D32 : 0xFFC62828);
                if ("ok".equals(msg[1])) {
                    input.setText(Prefs.normalize(input.getText().toString()));
                }
            });
        }).start();
    }

    private String readSnippet(InputStream in, int max) {
        if (in == null) return "";
        try {
            StringBuilder sb = new StringBuilder();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0 && sb.length() < max) {
                sb.append(new String(buf, 0, n, "UTF-8"));
            }
            in.close();
            String s = sb.toString().trim();
            return s.length() > max ? s.substring(0, max) + "…" : s;
        } catch (Exception e) {
            return "";
        }
    }
}
