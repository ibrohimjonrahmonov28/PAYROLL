package uz.terryjar.print;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Planshet yoqilganda yoki ilova yangilanganda (patok kiritilgan bo'lsa) orqa fon xizmatini ishga tushiradi. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!PrintService.prefs(context).getString("patok", "").isEmpty()) {
            PrintService.start(context);
        }
    }
}
