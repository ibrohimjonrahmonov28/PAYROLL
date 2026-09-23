import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from production.telegram_reports import send_daily_excel_report
from production.excel_reports import generate_daily_excel_report


class Command(BaseCommand):
    help = "Kunlik xodimlar ish haqi va stikerlar hisobotini Telegram guruhga yoki botga avtomatik Excel formatda yuborish"

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help="Hisobot sanasi (YYYY-MM-DD formatida). Belgilanmasa, avtomatik aniqlanadi."
        )
        parser.add_argument(
            '--chat-id',
            type=str,
            help="Telegram chat yoki guruh ID si (settings.TELEGRAM_REPORT_CHAT_ID o'rniga)"
        )
        parser.add_argument(
            '--token',
            type=str,
            help="Telegram Bot Tokeni (settings.TELEGRAM_BOT_TOKEN o'rniga)"
        )
        parser.add_argument(
            '--save-local',
            type=str,
            help="Faylni lokal diskka ham saqlash (masalan: /tmp/hisobot.xlsx)"
        )
        parser.add_argument(
            '--with-backup',
            action='store_true',
            help="Excel hisoboti bilan birga to'liq DB zaxira nusxasini ham jo'natish"
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        now = timezone.localtime()

        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Xato sana formati: {date_str}. YYYY-MM-DD formatida kiriting."))
                return
        else:
            # Agar kechasi 00:00 dan 01:00 gacha chaqirilsa, kechagi kunning to'liq hisoboti olinadi!
            if now.hour == 0:
                target_date = now.date() - datetime.timedelta(days=1)
            else:
                target_date = now.date()

        self.stdout.write(self.style.SUCCESS(f"Sana bo'yicha hisobot shakllantirilmoqda: {target_date}"))

        with_backup = options.get('with_backup')
        chat_id = options.get('chat_id')
        token = options.get('token')

        # Agar lokal saqlash so'ralgan bo'lsa
        save_path = options.get('save_local')
        if save_path:
            from production.excel_reports import generate_month_to_date_excel_report
            excel_buf = generate_month_to_date_excel_report(target_date=target_date)
            with open(save_path, 'wb') as f:
                f.write(excel_buf.getvalue())
            self.stdout.write(self.style.SUCCESS(f"Excel fayli lokal saqlandi: {save_path}"))

        # Telegramga jo'natish
        if with_backup:
            from production.telegram_reports import send_month_to_date_telegram_report
            result = send_month_to_date_telegram_report(
                chat_id=chat_id,
                bot_token=token
            )
        else:
            result = send_daily_excel_report(
                target_date=target_date,
                chat_id=chat_id,
                bot_token=token
            )

        if result.get('success'):
            self.stdout.write(self.style.SUCCESS(f"✅ {result.get('message')}"))
        else:
            self.stderr.write(self.style.WARNING(f"⚠️ {result.get('message')}"))

