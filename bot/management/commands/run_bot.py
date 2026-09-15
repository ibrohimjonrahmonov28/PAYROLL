import os
import sys
from django.core.management.base import BaseCommand
from bot.bot_service import create_bot_app


class Command(BaseCommand):
    help = "Master Telegram Botini ishga tushirish"

    def handle(self, *args, **options):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token or token.strip() == "your-telegram-bot-token-here":
            self.stdout.write(
                self.style.ERROR(
                    "XATO: TELEGRAM_BOT_TOKEN .env faylida ko'rsatilmagan!\n"
                    "Iltimos, .env faylida TELEGRAM_BOT_TOKEN parametrini to'ldiring.\n"
                    "Test qilish uchun veb-interfeysdagi 'Master Bot Simulyatori'dan ham foydalanishingiz mumkin."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("Master Telegram Boti ishga tushmoqda... (To'xtatish uchun Ctrl+C)"))
        app = create_bot_app(token)
        app.run_polling()
