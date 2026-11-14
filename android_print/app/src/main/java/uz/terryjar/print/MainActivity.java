package uz.terryjar.print;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.hardware.usb.UsbManager;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Bir martalik sozlash ekrani: patok (K1..K13, U1..U27 yoki 1..40) kiritiladi va saqlanadi.
 * Keyin ilova orqa fonda ishlaydi; bu ekran faqat holatni ko'rsatadi va sinov chekini chiqaradi.
 */
public class MainActivity extends Activity {
    private static final Pattern PATOK = Pattern.compile("^(K([1-9]|1[0-3])|U([1-9]|1[0-9]|2[0-7])|([1-9]|[1-3][0-9]|40))$");
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView statusView;
    private EditText patokInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences prefs = PrintService.prefs(this);
        if (printerAttached(getIntent()) && !prefs.getString("patok", "").isEmpty()) {
            finish();   // printer qayta ulandi — xizmat ishga tushdi, DLT ekrani ustida qolmaymiz
            return;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(48, 48, 48, 48);
        root.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("Terry Jar Print");
        title.setTextSize(28);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(Color.BLACK);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("OTK ta'mir cheklari shu planshetga ulangan printerdan chiqadi. Patokni bir marta kiriting.");
        hint.setTextColor(Color.DKGRAY);
        hint.setPadding(0, 8, 0, 32);
        root.addView(hint);

        patokInput = new EditText(this);
        patokInput.setHint("Patok, masalan K13 yoki U5");
        patokInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        patokInput.setTextSize(24);
        patokInput.setText(prefs.getString("patok", ""));
        root.addView(patokInput);

        root.addView(button("Saqlash va ishga tushirish", v -> save()));
        root.addView(button("Printerga USB ruxsati", v -> UsbPrinter.askPermission(this)));
        root.addView(button("Sinov cheki", v -> testPrint()));
        root.addView(button("Batareya cheklovini o'chirish", v -> askBatteryExemption()));

        statusView = new TextView(this);
        statusView.setTextSize(20);
        statusView.setTypeface(Typeface.DEFAULT_BOLD);
        statusView.setPadding(0, 32, 0, 0);
        statusView.setTextColor(Color.rgb(5, 120, 85));
        root.addView(statusView);

        setContentView(root);

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        }
        if (!prefs.getString("patok", "").isEmpty()) {
            PrintService.start(this);
        }
        refresh();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (printerAttached(intent) && !PrintService.prefs(this).getString("patok", "").isEmpty()) {
            moveTaskToBack(true);
        }
    }

    /** USB printer ulanganda Android shu ekranni ochadi: xizmatni ishga tushiramiz (ruxsat shu yerda beriladi). */
    private boolean printerAttached(Intent intent) {
        if (intent == null || !UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(intent.getAction())) {
            return false;
        }
        PrintService.start(this);
        return true;
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    private Button button(String text, android.view.View.OnClickListener onClick) {
        Button b = new Button(this);
        b.setText(text);
        b.setTextSize(18);
        b.setAllCaps(false);
        b.setGravity(Gravity.CENTER);
        b.setOnClickListener(onClick);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = 16;
        b.setLayoutParams(lp);
        return b;
    }

    private void save() {
        String patok = patokInput.getText().toString().trim().toUpperCase(Locale.US);
        if (!PATOK.matcher(patok).matches()) {
            Toast.makeText(this, "Patok noto'g'ri: K1..K13, U1..U27 yoki 1..40", Toast.LENGTH_LONG).show();
            return;
        }
        PrintService.prefs(this).edit().putString("patok", patok).apply();
        PrintService.status = "Ishga tushmoqda…";
        PrintService.start(this);
        UsbPrinter.askPermission(this);
        Toast.makeText(this, patok + " saqlandi", Toast.LENGTH_SHORT).show();
    }

    private void testPrint() {
        String patok = PrintService.prefs(this).getString("patok", "?");
        String text = "\u001b@\u001c.\u001ba\u0001\u001bE\u0001\u001d!\u0011SINOV " + patok + "\n\u001d!\u0000\u001bE\u0000"
                + "Terry Jar Print ishlayapti\n\u001dVB\u0000";
        new Thread(() -> {
            String error = UsbPrinter.print(this, text.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1));
            runOnUiThread(() -> Toast.makeText(this, error == null ? "Sinov cheki yuborildi" : error,
                    Toast.LENGTH_LONG).show());
        }).start();
    }

    private void askBatteryExemption() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm.isIgnoringBatteryOptimizations(getPackageName())) {
            Toast.makeText(this, "Batareya cheklovi allaqachon o'chirilgan", Toast.LENGTH_SHORT).show();
            return;
        }
        startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                Uri.parse("package:" + getPackageName())));
    }

    private void refresh() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        String battery = pm.isIgnoringBatteryOptimizations(getPackageName()) ? "" : "\n⚠ Batareya cheklovini o'chiring";
        statusView.setText(PrintService.status + battery);
        handler.postDelayed(this::refresh, 2000);
    }
}
