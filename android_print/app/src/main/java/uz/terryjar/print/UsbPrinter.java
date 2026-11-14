package uz.terryjar.print;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.os.Build;

/**
 * Planshetga USB (OTG hub) bilan ulangan termo printerga ESC/POS baytlarini yozadi.
 * Avval printer sinfidagi (7) interfeys, bo'lmasa bulk OUT nuqtasi bor istalgan interfeys tanlanadi.
 */
public final class UsbPrinter {
    static final String ACTION_PERMISSION = "uz.terryjar.print.USB_PERMISSION";
    private static final int CHUNK = 4096;
    private static final int TIMEOUT_MS = 5000;
    private static long lastPermissionAsk;

    private UsbPrinter() {
    }

    /** Printer interfeysi va uning bulk OUT nuqtasi; topilmasa null. */
    private static Object[] find(UsbManager manager) {
        Object[] fallback = null;
        for (UsbDevice device : manager.getDeviceList().values()) {
            for (int i = 0; i < device.getInterfaceCount(); i++) {
                UsbInterface iface = device.getInterface(i);
                for (int e = 0; e < iface.getEndpointCount(); e++) {
                    UsbEndpoint ep = iface.getEndpoint(e);
                    if (ep.getType() == UsbConstants.USB_ENDPOINT_XFER_BULK && ep.getDirection() == UsbConstants.USB_DIR_OUT) {
                        Object[] found = new Object[]{device, iface, ep};
                        if (iface.getInterfaceClass() == UsbConstants.USB_CLASS_PRINTER) {
                            return found;
                        }
                        if (fallback == null) {
                            fallback = found;
                        }
                    }
                }
            }
        }
        return fallback;
    }

    /** Printer ulanganmi va ruxsat bormi — holat matni uchun. */
    static String state(Context context) {
        UsbManager manager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
        Object[] found = find(manager);
        if (found == null) {
            return "Printer ulanmagan";
        }
        return manager.hasPermission((UsbDevice) found[0]) ? "Printer ulangan" : "Printerga USB ruxsati kerak";
    }

    static void askPermission(Context context) {
        UsbManager manager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
        Object[] found = find(manager);
        if (found == null || manager.hasPermission((UsbDevice) found[0])) {
            return;
        }
        int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        Intent intent = new Intent(ACTION_PERMISSION).setPackage(context.getPackageName());
        manager.requestPermission((UsbDevice) found[0], PendingIntent.getBroadcast(context, 0, intent, flags));
    }

    /** Chop etadi. Muvaffaqiyatli bo'lsa null, aks holda xato matni (serverga yuboriladi). */
    static synchronized String print(Context context, byte[] data) {
        UsbManager manager = (UsbManager) context.getSystemService(Context.USB_SERVICE);
        Object[] found = find(manager);
        if (found == null) {
            return "Printer ulanmagan (USB kabel yoki hub)";
        }
        UsbDevice device = (UsbDevice) found[0];
        UsbInterface iface = (UsbInterface) found[1];
        UsbEndpoint endpoint = (UsbEndpoint) found[2];
        if (!manager.hasPermission(device)) {
            if (System.currentTimeMillis() - lastPermissionAsk > 60000) {
                lastPermissionAsk = System.currentTimeMillis();
                askPermission(context);
            }
            return "Printerga USB ruxsati berilmagan";
        }
        UsbDeviceConnection connection = manager.openDevice(device);
        if (connection == null) {
            return "Printerni ochib bo'lmadi";
        }
        try {
            if (!connection.claimInterface(iface, true)) {
                return "Printer band";
            }
            for (int offset = 0; offset < data.length; offset += CHUNK) {
                int length = Math.min(CHUNK, data.length - offset);
                byte[] part = new byte[length];
                System.arraycopy(data, offset, part, 0, length);
                if (connection.bulkTransfer(endpoint, part, length, TIMEOUT_MS) < 0) {
                    return "Printerga yozib bo'lmadi (qog'oz yoki qopqoq?)";
                }
            }
            return null;
        } finally {
            connection.releaseInterface(iface);
            connection.close();
        }
    }
}
