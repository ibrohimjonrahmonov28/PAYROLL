package uz.terryjar.control;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public class MainActivity extends Activity implements View.OnClickListener, View.OnLongClickListener {
    private static final String TAG = "ControlMainActivity";
    private static final String PREFS_NAME = "TerryJarControlPrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String KEY_USERNAME = "username";
    private static final String KEY_PASSWORD = "password";
    private static final String KEY_KEEP_SCREEN_ON = "keep_screen_on";
    private static final String KEY_AUTO_LOGIN = "auto_login";

    private static final String DEFAULT_SERVER_URL = "https://terryjar.uz";

    private WebView webView;
    private ProgressBar progressBar;
    private LinearLayout layoutError;
    private TextView txtTargetUrl;
    private View secretSettingsTrigger;

    private SharedPreferences prefs;
    private int secretClickCount = 0;
    private long lastSecretClickTime = 0;

    private AlertDialog currentSettingsDialog;
    private EditText currentEditServerUrl;
    private EditText currentEditUsername;
    private EditText currentEditPassword;
    private CheckBox currentChkKeepScreenOn;
    private CheckBox currentChkAutoLogin;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        requestWindowFeature(Window.FEATURE_NO_TITLE);
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);

        initViews();
        setupFullscreenAndKeepScreen();
        setupWebView();
        checkPermissions();

        loadControlSystem();
    }

    private void initViews() {
        webView = (WebView) findViewById(R.id.webView);
        progressBar = (ProgressBar) findViewById(R.id.progressBar);
        layoutError = (LinearLayout) findViewById(R.id.layoutError);
        txtTargetUrl = (TextView) findViewById(R.id.txtTargetUrl);
        secretSettingsTrigger = findViewById(R.id.secretSettingsTrigger);

        Button btnRetry = (Button) findViewById(R.id.btnRetry);
        Button btnSettings = (Button) findViewById(R.id.btnSettings);

        btnRetry.setOnClickListener(this);
        btnSettings.setOnClickListener(this);
        secretSettingsTrigger.setOnClickListener(this);
        secretSettingsTrigger.setOnLongClickListener(this);
    }

    @Override
    public void onClick(View v) {
        int id = v.getId();
        if (id == R.id.btnRetry) {
            layoutError.setVisibility(View.GONE);
            webView.setVisibility(View.VISIBLE);
            loadControlSystem();
        } else if (id == R.id.btnSettings) {
            showSettingsDialog();
        } else if (id == R.id.secretSettingsTrigger) {
            long now = System.currentTimeMillis();
            if (now - lastSecretClickTime < 1200) {
                secretClickCount++;
            } else {
                secretClickCount = 1;
            }
            lastSecretClickTime = now;

            if (secretClickCount >= 5) {
                secretClickCount = 0;
                showSettingsDialog();
            }
        } else if (id == R.id.btnCancelSettings) {
            if (currentSettingsDialog != null) {
                currentSettingsDialog.dismiss();
            }
            hideSystemUI();
        } else if (id == R.id.btnSaveSettings) {
            handleSaveSettings();
        }
    }

    @Override
    public boolean onLongClick(View v) {
        if (v.getId() == R.id.secretSettingsTrigger) {
            showSettingsDialog();
            return true;
        }
        return false;
    }

    private void setupFullscreenAndKeepScreen() {
        boolean keepScreenOn = prefs.getBoolean(KEY_KEEP_SCREEN_ON, true);
        if (keepScreenOn) {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } else {
            getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        }

        hideSystemUI();
    }

    public void hideSystemUI() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            View decorView = getWindow().getDecorView();
            decorView.setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_FULLSCREEN
            );
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideSystemUI();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        hideSystemUI();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(false);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
            CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        }
        CookieManager.getInstance().setAcceptCookie(true);

        webView.setWebChromeClient(new AppWebChromeClient(this));
        webView.setWebViewClient(new AppWebViewClient(this));
    }

    public static class AppWebChromeClient extends WebChromeClient {
        private final MainActivity activity;

        public AppWebChromeClient(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public void onProgressChanged(WebView view, int newProgress) {
            if (activity.progressBar != null) {
                if (newProgress < 100) {
                    activity.progressBar.setVisibility(View.VISIBLE);
                    activity.progressBar.setProgress(newProgress);
                } else {
                    activity.progressBar.setVisibility(View.GONE);
                }
            }
        }

        @Override
        public void onPermissionRequest(PermissionRequest request) {
            activity.runOnUiThread(new PermissionGrantRunnable(request));
        }
    }

    public static class PermissionGrantRunnable implements Runnable {
        private final PermissionRequest request;

        public PermissionGrantRunnable(PermissionRequest request) {
            this.request = request;
        }

        @Override
        public void run() {
            try {
                if (request != null) {
                    request.grant(request.getResources());
                }
            } catch (Exception e) {
                Log.e(TAG, "Permission grant error: ", e);
            }
        }
    }

    public static class AppWebViewClient extends WebViewClient {
        private final MainActivity activity;

        public AppWebViewClient(MainActivity activity) {
            this.activity = activity;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (request != null && request.getUrl() != null) {
                view.loadUrl(request.getUrl().toString());
                return true;
            }
            return false;
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            super.onPageStarted(view, url, favicon);
            if (activity.layoutError != null) activity.layoutError.setVisibility(View.GONE);
            if (activity.webView != null) activity.webView.setVisibility(View.VISIBLE);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            CookieManager.getInstance().flush();
            activity.hideSystemUI();
            activity.checkAndPerformAutoLogin(url);
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request != null && request.isForMainFrame()) {
                activity.showErrorScreen(request.getUrl().toString());
            }
        }
    }

    public void checkAndPerformAutoLogin(String url) {
        if (url == null) return;

        boolean autoLogin = prefs.getBoolean(KEY_AUTO_LOGIN, true);
        if (!autoLogin) return;

        final String username = prefs.getString(KEY_USERNAME, "").trim();
        final String password = prefs.getString(KEY_PASSWORD, "").trim();

        if (username.isEmpty() || password.isEmpty()) return;

        if (url.contains("/login/") || url.contains("/accounts/login/")) {
            Log.i(TAG, "Login sahifasi aniqlandi. Avtomatik hisob ma'lumotlari kiritilmoqda...");

            final String js = "javascript:(function() {" +
                    "  try {" +
                    "    var u = document.querySelector('input[name=\"username\"]') || document.querySelector('#id_username');" +
                    "    var p = document.querySelector('input[name=\"password\"]') || document.querySelector('#id_password');" +
                    "    var f = document.querySelector('form');" +
                    "    if (u && p && f) {" +
                    "      u.value = " + escapeJs(username) + ";" +
                    "      p.value = " + escapeJs(password) + ";" +
                    "      u.dispatchEvent(new Event('input', { bubbles: true }));" +
                    "      p.dispatchEvent(new Event('input', { bubbles: true }));" +
                    "      setTimeout(function() { f.submit(); }, 200);" +
                    "    }" +
                    "  } catch(e) { console.error('Auto login error:', e); }" +
                    "})();";

            new Handler(Looper.getMainLooper()).postDelayed(new AutoLoginRunnable(webView, js), 300);
        }
    }

    public static class AutoLoginRunnable implements Runnable {
        private final WebView webView;
        private final String js;

        public AutoLoginRunnable(WebView webView, String js) {
            this.webView = webView;
            this.js = js;
        }

        @Override
        public void run() {
            if (webView != null) {
                webView.evaluateJavascript(js, null);
            }
        }
    }

    private String escapeJs(String str) {
        if (str == null) return "''";
        return "'" + str.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'";
    }

    public void showErrorScreen(String failingUrl) {
        if (webView != null) webView.setVisibility(View.GONE);
        if (layoutError != null) layoutError.setVisibility(View.VISIBLE);
        if (txtTargetUrl != null) txtTargetUrl.setText(failingUrl != null ? failingUrl : getTargetUrl());
    }

    private String getTargetUrl() {
        String base = prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL).trim();
        if (!base.startsWith("http://") && !base.startsWith("https://")) {
            base = "http://" + base;
        }
        if (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        return base + "/production/control/";
    }

    public void loadControlSystem() {
        String target = getTargetUrl();
        Log.i(TAG, "Yuklanmoqda: " + target);
        webView.loadUrl(target);
    }

    private void showSettingsDialog() {
        AlertDialog.Builder builder = new AlertDialog.Builder(this);
        LayoutInflater inflater = LayoutInflater.from(this);
        View dialogView = inflater.inflate(R.layout.dialog_settings, null);
        builder.setView(dialogView);

        currentSettingsDialog = builder.create();
        if (currentSettingsDialog.getWindow() != null) {
            currentSettingsDialog.getWindow().setBackgroundDrawableResource(android.R.color.transparent);
        }

        currentEditServerUrl = (EditText) dialogView.findViewById(R.id.editServerUrl);
        currentEditUsername = (EditText) dialogView.findViewById(R.id.editUsername);
        currentEditPassword = (EditText) dialogView.findViewById(R.id.editPassword);
        currentChkKeepScreenOn = (CheckBox) dialogView.findViewById(R.id.chkKeepScreenOn);
        currentChkAutoLogin = (CheckBox) dialogView.findViewById(R.id.chkAutoLogin);

        Button btnSave = (Button) dialogView.findViewById(R.id.btnSaveSettings);
        Button btnCancel = (Button) dialogView.findViewById(R.id.btnCancelSettings);

        currentEditServerUrl.setText(prefs.getString(KEY_SERVER_URL, DEFAULT_SERVER_URL));
        currentEditUsername.setText(prefs.getString(KEY_USERNAME, ""));
        currentEditPassword.setText(prefs.getString(KEY_PASSWORD, ""));
        currentChkKeepScreenOn.setChecked(prefs.getBoolean(KEY_KEEP_SCREEN_ON, true));
        currentChkAutoLogin.setChecked(prefs.getBoolean(KEY_AUTO_LOGIN, true));

        btnCancel.setOnClickListener(this);
        btnSave.setOnClickListener(this);

        currentSettingsDialog.show();
    }

    private void handleSaveSettings() {
        if (currentEditServerUrl == null) return;

        String url = currentEditServerUrl.getText().toString().trim();
        String user = currentEditUsername.getText().toString().trim();
        String pass = currentEditPassword.getText().toString().trim();
        boolean keepScreen = currentChkKeepScreenOn.isChecked();
        boolean autoLog = currentChkAutoLogin.isChecked();

        if (url.isEmpty()) {
            Toast.makeText(this, "Server URL bo'sh bo'lishi mumkin emas!", Toast.LENGTH_SHORT).show();
            return;
        }

        prefs.edit()
                .putString(KEY_SERVER_URL, url)
                .putString(KEY_USERNAME, user)
                .putString(KEY_PASSWORD, pass)
                .putBoolean(KEY_KEEP_SCREEN_ON, keepScreen)
                .putBoolean(KEY_AUTO_LOGIN, autoLog)
                .apply();

        setupFullscreenAndKeepScreen();
        if (currentSettingsDialog != null) {
            currentSettingsDialog.dismiss();
        }
        hideSystemUI();

        Toast.makeText(this, "Sozlamalar saqlandi. Yuklanmoqda...", Toast.LENGTH_SHORT).show();
        loadControlSystem();
    }

    private void checkPermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.CAMERA}, 101);
            }
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (webView.canGoBack()) {
                String currentUrl = webView.getUrl();
                if (currentUrl != null && currentUrl.contains("/production/control/")) {
                    return true;
                }
                webView.goBack();
                return true;
            }
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
