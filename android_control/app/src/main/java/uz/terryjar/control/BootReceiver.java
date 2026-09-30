package uz.terryjar.control;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Planshet qayta o'chib-yonganda (Boot completed)
 * Sifat Nazorati (OTK / Control) ilovasini avtomatik ishga tushiruvchi qabul qiluvchi.
 */
public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "ControlBootReceiver";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;

        String action = intent.getAction();
        Log.i(TAG, "Tizim harakati qabul qilindi: " + action);

        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || Intent.ACTION_LOCKED_BOOT_COMPLETED.equals(action)
                || "android.intent.action.QUICKBOOT_POWERON".equals(action)
                || "com.htc.intent.action.QUICKBOOT_POWERON".equals(action)) {

            Log.i(TAG, "Planshet yoqildi. Sifat Nazorati ilovasi avtomatik ochilmoqda...");

            Intent launchIntent = new Intent(context, MainActivity.class);
            launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK 
                    | Intent.FLAG_ACTIVITY_CLEAR_TOP 
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            
            try {
                context.startActivity(launchIntent);
            } catch (Exception e) {
                Log.e(TAG, "Ilovani ishga tushirishda xatolik: ", e);
            }
        }
    }
}
