from django.core.management.base import BaseCommand
from production.telegram_reports import send_full_db_backup


class Command(BaseCommand):
    help = "To'liq PostgreSQL ma'lumotlar bazasi zaxira nusxasini (.sql.gz) Telegram guruhga avtomatik yuborish"

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        chat_id = options.get('chat_id')
        token = options.get('token')

        self.stdout.write("Baza zaxira nusxasi tayyorlanmoqda va Telegramga yuborilmoqda...")

        result = send_full_db_backup(
            chat_id=chat_id,
            bot_token=token
        )

        if result.get('success'):
            self.stdout.write(self.style.SUCCESS(f"✅ {result.get('message')}"))
        else:
            self.stderr.write(self.style.WARNING(f"⚠️ {result.get('message')}"))
