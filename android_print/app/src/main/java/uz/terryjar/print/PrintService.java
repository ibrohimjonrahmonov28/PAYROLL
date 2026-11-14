package uz.terryjar.print;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Orqa fonda doim ishlaydi: har 3 soniyada serverdan faqat o'z patokining cheklarini so'raydi
 * (/printers/agent/jobs/?patok=K13), USB printerga chiqaradi va natijani qaytaradi.
 * Chek chiqmasa server uni qayta beradi (5 martagacha) — chek yo'qolmaydi.
 */
public class PrintService extends Service {
    static final String PREFS = "print";
    static volatile String status = "Ishga tushmoqda…";
    private static final String CHANNEL = "print";
    private static final int NOTIFICATION_ID = 1;

    private volatile boolean running;
    private Thread worker;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private String lastPrinted = "";

    static void start(Context context) {
        Intent intent = new Intent(context, PrintService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Chek printeri", NotificationManager.IMPORTANCE_LOW));
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "terryjar:print");
        wakeLock.acquire();
        WifiManager wm = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
        wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "terryjar:print");
        wifiLock.acquire();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification notification = buildNotification(status);
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "print-loop");
            worker.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) {
            worker.interrupt();
        }
        if (wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock.isHeld()) {
            wifiLock.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification buildNotification(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_menu_send)
                .setContentTitle("Terry Jar Print · " + prefs(this).getString("patok", "?"))
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .build();
    }

    private void setStatus(String text) {
        if (text.equals(status)) {
            return;
        }
        status = text;
        getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, buildNotification(text));
    }

    private void loop() {
        while (running) {
            long pause = 3000;
            try {
                pause = poll();
            } catch (IllegalStateException e) {
                setStatus("Token noto'g'ri — yangi APK o'rnating");
                pause = 60000;
            } catch (Exception e) {
                setStatus("Serverga ulanib bo'lmadi — internetni tekshiring");
                pause = 10000;
            }
            try {
                Thread.sleep(pause);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    /** Bitta so'rov; keyingi so'rovgacha kutish (ms) qaytaradi. */
    private long poll() throws Exception {
        SharedPreferences p = prefs(this);
        String patok = p.getString("patok", "");
        if (patok.isEmpty()) {
            setStatus("Patok kiritilmagan — ilovani oching");
            return 5000;
        }
        String server = p.getString("server", Config.SERVER);
        JSONObject reply = request(server + "/printers/agent/jobs/?patok=" + URLEncoder.encode(patok, "UTF-8"), null);
        if ("NO_PRINTER".equals(reply.optString("status"))) {
            setStatus(patok + " patokka /printers/ sahifasida \"tablet\" belgilanmagan");
            return 15000;
        }
        JSONArray jobs = reply.optJSONArray("jobs");
        if (jobs == null || jobs.length() == 0) {
            String usb = UsbPrinter.state(this);
            setStatus(usb + (lastPrinted.isEmpty() ? "" : " · oxirgi chek " + lastPrinted));
            return 3000;
        }
        for (int i = 0; i < jobs.length(); i++) {
            JSONObject job = jobs.getJSONObject(i);
            String error = UsbPrinter.print(this, Base64.decode(job.getString("data"), Base64.DEFAULT));
            JSONObject result = new JSONObject();
            result.put("ok", error == null);
            if (error == null) {
                lastPrinted = new SimpleDateFormat("HH:mm", Locale.US).format(new Date());
                setStatus("Printer ulangan · oxirgi chek " + lastPrinted);
            } else {
                result.put("error", patok + ": " + error);
                setStatus(error);
            }
            request(server + "/printers/agent/jobs/" + job.getInt("id") + "/result/", result.toString());
        }
        return 300;
    }

    private static JSONObject request(String url, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(10000);
        c.setReadTimeout(20000);
        c.setRequestProperty("X-Agent-Token", Config.TOKEN);
        c.setRequestProperty("Content-Type", "application/json");
        if (body != null) {
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            try (OutputStream out = c.getOutputStream()) {
                out.write(body.getBytes("UTF-8"));
            }
        }
        int code = c.getResponseCode();
        if (code == 403) {
            throw new IllegalStateException("token noto'g'ri");
        }
        try (InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream()) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while (in != null && (n = in.read(chunk)) > 0) {
                buf.write(chunk, 0, n);
            }
            return new JSONObject(buf.toString("UTF-8"));
        } finally {
            c.disconnect();
        }
    }
}
