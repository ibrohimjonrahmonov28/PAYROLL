"""
Terry Jar — Pastal Pasporti va Stikerlarni Tekshirish Telegram Boti
Management Command: python manage.py run_passport_bot

Ushbu bot mahsulotlar tikuv patogiga chiqishidan oldin:
- Pasportdagi QR kodni yoki qutidagi stikerni tekshiradi
- Har bir quti va stikerni bittalab chuqur tahlil qiladi
- Narxlar, summalar va atmen (bekor qilinganlik) holatini tekshiradi
- "PATOKKA BERISH MUMKIN" yoki "BERIB BO'LMAYDI" xulosasini beradi
"""

import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from production.passport_bot_service import (
    decode_qr_from_image_bytes,
    verify_pastal_for_sewing,
)

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start buyrug'i uchun salomlashish va qo'llanma matni
    """
    welcome_text = (
        "👋 *Assalomu alaykum!*\n"
        "🏭 *TERRY JAR — Pastal va Stikerlarni Tekshirish Boti*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Ushbu bot mahsulotlarni tikuv patogiga berishdan oldin ularning "
        "haqiqiyligi, aktivligi va operatsiya narxlarini bittalab tekshirish uchun xizmat qiladi.\n\n"
        "📲 *Qanday ishlatiladi?*\n"
        "1️⃣ Pastal pasportidagi QR kodni yoki qutidagi istalgan stikerni *rasmga olib yuboring*.\n"
        "2️⃣ Yoki kodni to'g'ridan-to'g'ri *matn ko'rinishida yozing* (masalan: `PASTAL:12` yoki `#489DA37F`).\n\n"
        "🔍 *Bot nimalarni tekshiradi?*\n"
        "• Ushbu pastal yoki qutilar bekor qilinganmi (atmen bo'lganmi)?\n"
        "• Barcha qutilardagi barcha stikerlar narxi va summasi to'liq kelganmi?\n"
        "• Meto va Kesim bo'yicha ma'lumotlar to'g'rimi?\n\n"
        "🟢 Agar barcha qutilar va narxlar to'g'ri bo'lsa: *✅ PATOKKA BERISH MUMKIN* ruxsati beriladi.\n"
        "🔴 Agar muammo bo'lsa: *🚫 PATOKKA BERIB BO'LMAYDI* ogohlantirishi chiqadi.\n\n"
        "📸 _Hozir tekshirish uchun QR kodni rasmga olib yuboring!_"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /help buyrug'i
    """
    help_text = (
        "ℹ️ *YORDAM VA FORMATLAR:*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Bot quyidagi barcha formatdagi QR kodlarni taniydi:\n"
        "• *Pastal pasporti QR kodi:* `PASTAL:12` yoki `BATCH:12`\n"
        "• *Stiker QR kodi:* `TICKET:TK-...` yoki `#489DA37F`\n"
        "• *Quti kodi:* `CONTROL:A9-234` yoki `A9-234`\n"
        "• *Fotosurat:* Telefon kamerasi orqali QR kodni rasmga olib yuboring.\n\n"
        "Agar stiker bekor qilingan yoki narxi kiritilmagan bo'lsa, "
        "tizim stikerchi yoki texnologga murojaat qilishni so'raydi."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Matnli xabarlarni qabul qilish va tekshirish
    """
    raw_text = update.message.text
    if not raw_text:
        return

    result = verify_pastal_for_sewing(raw_text)
    await update.message.reply_text(result['message'], parse_mode='Markdown')


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fotosuratlardan QR kodni aniqlash va tekshirish
    """
    if not update.message.photo:
        return

    # Eng yuqori sifatli fotosuratni tanlash
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    # OpenCV yordamida QR kodni o'qish
    decoded_codes = decode_qr_from_image_bytes(image_bytes)

    if not decoded_codes:
        error_msg = (
            "⚠️ *Rasmdan QR kod aniqlanmadi!*\n\n"
            "Iltimos, quyidagilarga e'tibor bering:\n"
            "1. QR kod yaxshi yoritilgan va xira (blur) bo'lmasligi kerak.\n"
            "2. QR kodni kameraga yaqinroq tutib, to'g'ridan rasmga oling.\n"
            "3. Yoki QR kod ostidagi kodni matn shaklida yozib yuboring (masalan: `PASTAL:12` yoki `#489DA37F`)."
        )
        await update.message.reply_text(error_msg, parse_mode='Markdown')
        return

    # Aniqlangan har bir QR kodni tekshirish (odatda 1 ta)
    for code in decoded_codes[:2]:
        result = verify_pastal_for_sewing(code)
        await update.message.reply_text(result['message'], parse_mode='Markdown')


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fayl (hujjat) ko'rinishida yuborilgan rasmlarni qabul qilish
    """
    doc = update.message.document
    if not doc:
        return

    mime = (doc.mime_type or '').lower()
    if not (mime.startswith('image/') or doc.file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))):
        await update.message.reply_text(
            "⚠️ Iltimos, faqat rasm fayllarini (JPG, PNG) yoki oddiy foto yuboring.",
            parse_mode='Markdown'
        )
        return

    doc_file = await doc.get_file()
    image_bytes = await doc_file.download_as_bytearray()

    decoded_codes = decode_qr_from_image_bytes(image_bytes)
    if not decoded_codes:
        await update.message.reply_text(
            "⚠️ Ushbu fayldan QR kod aniqlanmadi. Iltimos, aniqroq rasm yuboring.",
            parse_mode='Markdown'
        )
        return

    for code in decoded_codes[:2]:
        result = verify_pastal_for_sewing(code)
        await update.message.reply_text(result['message'], parse_mode='Markdown')


class Command(BaseCommand):
    help = "Pastal Pasporti va Stikerlar Sifatini Tekshirish Telegram Botini Ishga Tushirish"

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            help="Telegram Bot Tokeni (settings.TELEGRAM_PASSPORT_BOT_TOKEN o'rniga)"
        )

    def handle(self, *args, **options):
        token = (
            options.get('token')
            or getattr(settings, 'TELEGRAM_PASSPORT_BOT_TOKEN', None)
            or '8679375634:AAF5bd9mCMpkfpVJhhFuoiAoCLLZa_LYeFU'
        )

        if not token:
            self.stderr.write(self.style.ERROR("TELEGRAM_PASSPORT_BOT_TOKEN topilmadi!"))
            return

        import time
        while True:
            try:
                self.stdout.write(self.style.SUCCESS("🤖 TERRY JAR — Pastal Tekshiruv Boti ishga tushmoqda..."))
                self.stdout.write(f"Token: {token[:12]}...{token[-5:]}")

                app = ApplicationBuilder().token(token).build()

                app.add_handler(CommandHandler("start", start_handler))
                app.add_handler(CommandHandler("help", help_handler))
                app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
                app.add_handler(MessageHandler(filters.Document.IMAGE, document_handler))
                app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

                self.stdout.write(self.style.SUCCESS("✅ Bot muvaffaqiyatli ishga tushdi! Xabarlar kutilmoqda... (Ctrl+C to'xtatish)"))
                app.run_polling(drop_pending_updates=True)
                break
            except (KeyboardInterrupt, SystemExit):
                self.stdout.write("Bot to'xtatildi.")
                break
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Botda xatolik yuz berdi: {e}. 5 soniyadan so'ng qayta uriniladi..."))
                time.sleep(5)
