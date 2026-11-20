from django.core.management.base import BaseCommand

from production.media_backup import send_media_backup


class Command(BaseCommand):
    help = ("Rasmlar (media) zaxirasini Telegram guruhga yuborish: dushanba to'liq, boshqa kunlar dushanbadan beri "
            "o'zgarganlar; --full — hozir hamma rasmlar")

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true', help="Kun qaysi bo'lishidan qat'i nazar to'liq arxiv")
        parser.add_argument('--chat-id', type=str, help="Telegram chat ID (settings.TELEGRAM_REPORT_CHAT_ID o'rniga)")
        parser.add_argument('--token', type=str, help="Telegram bot tokeni (settings.TELEGRAM_BOT_TOKEN o'rniga)")

    def handle(self, *args, **options):
        self.stdout.write("Rasmlar zaxirasi tayyorlanmoqda va Telegramga yuborilmoqda...")
        result = send_media_backup(full=True if options['full'] else None, chat_id=options.get('chat_id'),
                                   bot_token=options.get('token'))
        if result.get('success'):
            self.stdout.write(self.style.SUCCESS(f"✅ {result['message']}"))
        else:
            self.stderr.write(self.style.WARNING(f"⚠️ {result['message']}"))
