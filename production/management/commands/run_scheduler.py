"""
Management Command: python manage.py run_scheduler

Har kuni tunda (Toshkent vaqti bilan 00:00 da) avtomatik ravishda:
1. O'tgan kun uchun barcha xodimlarning ish haqini yopish (close_daily_payroll)
2. Kunlik to'liq Excel hisobotini Telegram guruhga yuborish (send_daily_report)

Doimiy ravishda fonda (daemon) ishlaydi va Docker orqali nazorat qilinadi.
Server vaqti UTC bo'lishidan qat'i nazar, Django'ning Asia/Tashkent vaqti bilan ishlaydi.
"""

import time
import datetime
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.management.commands.close_daily_payroll import perform_daily_closing
from production.telegram_reports import send_daily_excel_report

logger = logging.getLogger(__name__)


def execute_nightly_jobs(target_date: datetime.date) -> dict:
    """
    O'tgan kun uchun tungi operatsiyalarni ketma-ket bajarish:
    1. Ish haqini yopish va balansga muhrlash
    2. Excel hisobotini generatsiya qilib Telegram guruhga jo'natish
    """
    results = {}

    # 1. Kunlik ish haqini yopish
    try:
        closing_res = perform_daily_closing(target_date)
        results['closing'] = {
            'success': True,
            'workers_count': closing_res.get('workers_count', 0),
            'total_units': closing_res.get('total_units', 0),
            'total_amount': closing_res.get('total_amount', 0),
        }
    except Exception as e:
        logger.error(f"Kunlik hisobni yopishda xatolik: {e}", exc_info=True)
        results['closing'] = {'success': False, 'error': str(e)}

    # 2. Telegramga Excel hisobotini yuborish
    try:
        report_res = send_daily_excel_report(target_date=target_date)
        results['report'] = report_res
    except Exception as e:
        logger.error(f"Telegram hisobotini yuborishda xatolik: {e}", exc_info=True)
        results['report'] = {'success': False, 'message': str(e)}

    return results


class Command(BaseCommand):
    help = "Kunlik tungi avtomatik hisobot va ish haqi yopish rejalashtiruvchisi (Scheduler)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--now',
            action='store_true',
            help="Kutmasdan darhol hisobotni generatsiya qilib Telegramga jo'natish (Test uchun)"
        )
        parser.add_argument(
            '--date',
            type=str,
            help="Muayyan sana bo'yicha darhol jo'natish (YYYY-MM-DD)"
        )

    def handle(self, *args, **options):
        run_now = options.get('now')
        date_str = options.get('date')

        # TEST / DARHOL ISHGA TUSHIRISH REJIMI
        if run_now or date_str:
            if date_str:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            else:
                now = timezone.localtime()
                # Agar 00:00-05:00 oralig'ida bo'lsa kechagi kun, aks holda bugungi kun
                target_date = now.date() - datetime.timedelta(days=1) if now.hour < 5 else now.date()

            self.stdout.write(self.style.SUCCESS(f"🚀 Tungi hisobot darhol ishga tushirilmoqda (Sana: {target_date})..."))
            res = execute_nightly_jobs(target_date)
            self.stdout.write(f"1. Ish haqini yopish natijasi: {res.get('closing')}")
            self.stdout.write(f"2. Telegram hisoboti natijasi: {res.get('report')}")
            return

        # DOIMIY SCHEDULER REJIMI
        self.stdout.write(self.style.SUCCESS("⏰ TERRY JAR Tungi Hisobot Scheduler ishga tushdi..."))
        self.stdout.write("📅 Har kuni 00:00 da (Toshkent vaqti bilan) avtomatik ishga tushadi.")

        last_executed_date = None

        while True:
            try:
                now = timezone.localtime()
                yesterday = now.date() - datetime.timedelta(days=1)

                # Har kuni tunda 00:00 dan 00:15 oralig'ida va bugun hali bajarilmagan bo'lsa
                if now.hour == 0 and now.minute <= 15 and last_executed_date != yesterday:
                    self.stdout.write(self.style.NOTICE(
                        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Tungi hisobot vaqti keldi! "
                        f"Kechagi kun ({yesterday}) uchun hisobot yuborilmoqda..."
                    ))

                    res = execute_nightly_jobs(yesterday)
                    report_status = res.get('report', {})

                    if report_status.get('success'):
                        last_executed_date = yesterday
                        self.stdout.write(self.style.SUCCESS(
                            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ✅ Hisobot muvaffaqiyatli yuborildi: {report_status.get('message')}"
                        ))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Hisobot yuborishda muammo bo'ldi: {report_status.get('message')}. "
                            f"Keyingi daqiqada qayta urinib ko'riladi..."
                        ))
                        # 60 sekund kutib qayta urinadi
                        time.sleep(60)
                        continue

                # 30 soniya uxlab turish
                time.sleep(30)

            except Exception as e:
                logger.error(f"Scheduler siklida kutilmagan xatolik: {e}", exc_info=True)
                time.sleep(30)
