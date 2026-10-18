"""
Terry Jar — Pastal Pasporti va Stikerlarni Tekshirish Telegram Boti
Management Command: python manage.py run_passport_bot

Vazifalari:
1. Pasportdagi QR kodni yoki qutidagi stikerni tekshiradi.
2. Narxlar, summalar va atmen (bekor qilinganlik) holatini tekshiradi.
3. Ruxsat berilgach:
   - Pastalning haqiqiy vaznini (kg) so'raydi.
   - Filialni (Kosonsoy / Uychi) tanlatadi.
   - 3 ustunli patoklar ro'yxatidan patokni tanlatadi.
   - Tasdiqlash so'raladi va patokka jo'natiladi (Planshetda qabul qilish uchun).
"""

import logging
import re
from decimal import Decimal
from django.db.models import Q
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from production.models import CuttingBatch, Box
from production.services import get_patok_code, get_patok_name
from production.passport_bot_service import (
    decode_qr_from_image_bytes,
    verify_pastal_for_sewing,
)

logger = logging.getLogger(__name__)

async_verify_pastal_for_sewing = sync_to_async(verify_pastal_for_sewing, thread_sensitive=False)


@sync_to_async
def save_batch_weight_async(batch_id: int, weight: Decimal):
    batch = CuttingBatch.objects.filter(id=batch_id).first()
    if batch:
        batch.actual_weight_kg = weight
        batch.save(update_fields=['actual_weight_kg'])
        return {
            'batch_id': batch.id,
            'batch_number': batch.batch_number,
            'pastal_code': batch.pastal_code or str(batch.batch_number),
            'article_name': batch.order_item.article.name if batch.order_item and batch.order_item.article else '',
            'units': batch.total_quantity,
            'weight': float(batch.actual_weight_kg)
        }
    return None


@sync_to_async
def get_batch_info_async(batch_id: int):
    batch = CuttingBatch.objects.select_related('order_item__order', 'order_item__article').filter(id=batch_id).first()
    if not batch:
        return None
    return {
        'batch_id': batch.id,
        'batch_number': batch.batch_number,
        'pastal_code': batch.pastal_code or str(batch.batch_number),
        'article_name': batch.order_item.article.name if batch.order_item and batch.order_item.article else 'Model',
        'units': batch.total_quantity,
        'weight': float(batch.actual_weight_kg) if batch.actual_weight_kg else 0.0,
        'dispatch_status': batch.dispatch_status,
        'assigned_screen': batch.assigned_screen,
        'assigned_branch': batch.assigned_branch,
    }


@sync_to_async
def dispatch_batch_async(batch_id: int, branch: str, screen_number: int, user_name: str):
    batch = CuttingBatch.objects.select_related('order_item__order', 'order_item__article').filter(id=batch_id).first()
    if not batch:
        return None
    batch.assigned_branch = branch
    batch.assigned_screen = screen_number
    batch.dispatch_status = CuttingBatch.DispatchStatus.DISPATCHED
    batch.dispatched_at = timezone.now()
    batch.dispatched_by = user_name or "Telegram Bot"
    batch.rejection_reason = ""
    batch.rejected_at = None
    batch.save(update_fields=['assigned_branch', 'assigned_screen', 'dispatch_status', 'dispatched_at', 'dispatched_by', 'rejection_reason', 'rejected_at'])

    # Faqat AKTIV qutilarga biriktirilgan patok raqamini belgilash (bo'sh pastal_code xatosiz)
    from production.master_views import _get_batch_active_boxes
    _get_batch_active_boxes(batch).update(assigned_screen=screen_number)

    return {
        'batch_id': batch.id,
        'batch_number': batch.batch_number,
        'pastal_code': batch.pastal_code or str(batch.batch_number),
        'article_name': batch.order_item.article.name if batch.order_item and batch.order_item.article else 'Model',
        'units': batch.total_quantity,
        'weight': float(batch.actual_weight_kg) if batch.actual_weight_kg else 0.0,
        'patok_code': get_patok_code(screen_number),
        'patok_name': get_patok_name(screen_number),
        'branch_display': "Kosonsoy (Bosh bino)" if branch == 'HQ' else "Uychi filiali",
    }


def build_branch_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    """Filial tanlash klaviaturasi"""
    keyboard = [
        [
            InlineKeyboardButton("🏢 Kosonsoy (Bosh bino)", callback_data=f"dp_br:{batch_id}:HQ"),
            InlineKeyboardButton("🏭 Uychi filiali", callback_data=f"dp_br:{batch_id}:UYCHI"),
        ],
        [
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_patoks_keyboard(batch_id: int, branch: str) -> InlineKeyboardMarkup:
    """
    Patoklar ro'yxatini 3 ustun ko'rinishida generatsiya qilish:
    - HQ (Kosonsoy): 1..13 (K-1 .. K-13)
    - UYCHI: 14..40 (U-1 .. U-27)
    """
    keyboard = []
    if branch == 'HQ':
        # 1..13
        screen_numbers = list(range(1, 14))
    else:
        # 14..40
        screen_numbers = list(range(14, 41))

    row = []
    for sn in screen_numbers:
        p_code = get_patok_code(sn)
        btn = InlineKeyboardButton(f"{p_code}-Patok", callback_data=f"dp_pat:{batch_id}:{branch}:{sn}")
        row.append(btn)
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Orqaga / Bekor qilish tugmasi
    keyboard.append([
        InlineKeyboardButton("🔙 Filialni o'zgartirish", callback_data=f"dp_rebr:{batch_id}"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
    ])
    return InlineKeyboardMarkup(keyboard)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start buyrug'i uchun salomlashish va qo'llanma matni
    """
    context.user_data.clear()
    welcome_text = (
        "👋 *Assalomu alaykum!*\n"
        "🏭 *TERRY JAR — Pastal Pasporti va Patokka Biriktirish Boti*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Ushbu bot orqali:\n"
        "1️⃣ Pastal pasportidagi QR kodni skanerlaysiz yoki yozasiz.\n"
        "2️⃣ Tizim stikerlar va narxlarni 100% tekshiradi.\n"
        "3️⃣ Pastalning haqiqiy vaznini (kg) kiritasiz.\n"
        "4️⃣ Kerakli filial va patokni (K1..K13, U1..U27) 3 ustunli tugmalarda tanlaysiz.\n"
        "5️⃣ Tasdiqlab, planshetda qabul qilish uchun patokka jo'natasiz!\n\n"
        "📸 _Hozir tekshirish uchun pasport QR kodini rasmga olib yuboring!_"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /help buyrug'i
    """
    help_text = (
        "ℹ️ *QO'LLANMA:*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• *Pastal pasporti kodi:* `PASTAL:12` yoki `BATCH:12`\n"
        "• *Stiker kodi:* `#489DA37F` yoki `TICKET:TK-...`\n"
        "• Pasport tekshirilib 'RUXSAT BERILDI' chiqqach, bot sizdan kg so'raydi.\n"
        "• Kg kiritgach, filial va patokni tanlab tasdiqlaysiz.\n"
        "• Patok masteri o'z planshetida qabul qilishi bilan ushbu pastal shu patokka to'liq birikadi!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


@sync_to_async
def resolve_batch_from_details_async(details: dict):
    """
    Agar batch_id to'g'ridan-to'g'ri bo'lmasa, box_id yoki boshqa parametrlar orqali CuttingBatch ni topish
    """
    if not details:
        return None
    batch_id = details.get('batch_id')
    if batch_id:
        return batch_id

    box_id = details.get('box_id')
    if box_id:
        box = Box.objects.select_related('cutting_batch_item__batch', 'order').filter(id=box_id).first()
        if box:
            if box.cutting_batch_item and box.cutting_batch_item.batch:
                return box.cutting_batch_item.batch_id
            if box.pastal_number and box.order:
                batch = CuttingBatch.objects.filter(
                    order_item__order=box.order
                ).filter(
                    Q(pastal_code=box.pastal_number) | Q(batch_number=box.pastal_number)
                ).first()
                if batch:
                    return batch.id
            batch = CuttingBatch.objects.filter(order_item__order=box.order).order_by('-id').first()
            if batch:
                return batch.id
    return None


async def _handle_verification_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict):
    """
    Tekshiruv natijasini yuborish va agar muvaffaqiyatli bo'lsa, kg so'rash rejimiga o'tish
    """
    message_text = result['message']
    details = result.get('details', {}) or {}
    batch_id = await resolve_batch_from_details_async(details)

    if result.get('success') and batch_id:
        disp_status = details.get('dispatch_status')
        ass_screen = details.get('assigned_screen')
        scanned_tkts = details.get('scanned_tickets', 0) or 0
        p_code_info = get_patok_code(ass_screen) if ass_screen else 'Patok'

        # Agar pastal allaqachon patokda qabul qilingan va tikuv boshlangan bo'lsa, qayta jo'natishni bloklash
        if disp_status == CuttingBatch.DispatchStatus.ACCEPTED and scanned_tkts > 0:
            context.user_data.pop('dispatch_state', None)
            context.user_data.pop('dispatch_batch_id', None)
            already_msg = (
                f"{message_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *HOLATI:* Ushbu pastal allaqachon *{p_code_info}-Patok* tomonidan QABUL QILINGAN "
                f"va tikuv jarayonida ({scanned_tkts} ta stiker urilgan).\n"
                f"🔒 _Tikuvga kirgan pastalni boshqa patokka qayta jo'natib bo'lmaydi._"
            )
            await update.message.reply_text(already_msg, parse_mode='Markdown')
            return

        status_note = ""
        if disp_status == CuttingBatch.DispatchStatus.DISPATCHED and ass_screen:
            status_note = f"\n⏳ _Eslatma: Hozirda {p_code_info}-Patokda qabul qilinishi kutilmoqda. Qayta jo'natish uchun vazn kiriting:_\n"
        elif disp_status == CuttingBatch.DispatchStatus.ACCEPTED and ass_screen:
            status_note = f"\nℹ️ _Eslatma: Hozirda {p_code_info}-Patokda qabul qilingan (0 stiker urilgan). Boshqa patokka o'tkazish uchun vazn kiriting:_\n"

        # User sessiyasiga batch_id va kutish holatini o'rnatish
        context.user_data['dispatch_batch_id'] = batch_id
        context.user_data['dispatch_state'] = 'WAITING_WEIGHT'

        kg_prompt = (
            f"{message_text}\n"
            f"{status_note}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ *Iltimos, pastalning haqiqiy vaznini (kg) kiriting:*\n"
            f"_(Pastal tortilgandagi haqiqiy kg ni matn shaklida yozing, masalan: `48.5` yoki `52`)_"
        )
        await update.message.reply_text(kg_prompt, parse_mode='Markdown')
    else:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        await update.message.reply_text(message_text, parse_mode='Markdown')


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Matnli xabarlarni qabul qilish:
    - Agar vazn kutilayotgan bo'lsa: kg ni qabul qiladi va filial tanlashga o'tadi
    - Aks holda: QR kod matni sifatida tekshiradi
    """
    raw_text = (update.message.text or '').strip()
    if not raw_text:
        return

    dispatch_state = context.user_data.get('dispatch_state')
    dispatch_batch_id = context.user_data.get('dispatch_batch_id')

    # Agar vazn kutilayotgan bo'lsa va kiritilgan matn son bo'lsa
    if dispatch_state == 'WAITING_WEIGHT' and dispatch_batch_id:
        clean_num_text = raw_text.replace(',', '.')
        is_number = False
        try:
            val = float(clean_num_text)
            if 0.1 <= val <= 20000:
                is_number = True
                parsed_weight = Decimal(f"{val:.2f}")
        except ValueError:
            is_number = False

        if is_number:
            # Vaznni saqlash
            saved_info = await save_batch_weight_async(dispatch_batch_id, parsed_weight)
            if saved_info:
                context.user_data['dispatch_weight'] = float(parsed_weight)
                context.user_data['dispatch_state'] = 'WAITING_BRANCH'

                kb = build_branch_keyboard(dispatch_batch_id)
                await update.message.reply_text(
                    f"⚖️ *Vazn saqlandi:* `{parsed_weight} kg`\n"
                    f"📋 *Pastal:* #{saved_info['batch_number']} ({saved_info['article_name']}, {saved_info['units']} dona)\n\n"
                    f"🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
                    reply_markup=kb,
                    parse_mode='Markdown'
                )
                return

        # Agar son bo'lmasa, lekin yangi QR kod bo'lsa (masalan: PASTAL:15 yoki #8928374), eski holatni tozalaymiz
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)

    # Oddiy QR kod tekshiruvi
    result = await async_verify_pastal_for_sewing(raw_text)
    await _handle_verification_result(update, context, result)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fotosuratlardan QR kodni aniqlash va tekshirish
    """
    if not update.message.photo:
        return

    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    image_bytes = await photo_file.download_as_bytearray()

    decoded_codes = decode_qr_from_image_bytes(image_bytes)

    if not decoded_codes:
        error_msg = (
            "⚠️ *Rasmdan QR kod aniqlanmadi!*\n\n"
            "Iltimos, pasport QR kodini kameraga yaqinroq va aniqroq tutib yuboring "
            "yoki kodni matn ko'rinishida yozing (masalan: `PASTAL:12`)."
        )
        await update.message.reply_text(error_msg, parse_mode='Markdown')
        return

    # Aniqlangan birinchi QR kodni tekshirish
    result = await async_verify_pastal_for_sewing(decoded_codes[0])
    await _handle_verification_result(update, context, result)


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fayl ko'rinishida yuborilgan rasmlarni qabul qilish
    """
    doc = update.message.document
    if not doc:
        return

    mime = (doc.mime_type or '').lower()
    if not (mime.startswith('image/') or doc.file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))):
        await update.message.reply_text("⚠️ Iltimos, faqat rasm fayllarini yuboring.", parse_mode='Markdown')
        return

    doc_file = await doc.get_file()
    image_bytes = await doc_file.download_as_bytearray()

    decoded_codes = decode_qr_from_image_bytes(image_bytes)
    if not decoded_codes:
        await update.message.reply_text("⚠️ Ushbu fayldan QR kod aniqlanmadi.", parse_mode='Markdown')
        return

    result = await async_verify_pastal_for_sewing(decoded_codes[0])
    await _handle_verification_result(update, context, result)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inline tugmalar bosilganda qayta ishlash:
    - dp_br:<batch_id>:<branch> -> Filial tanlandi, patoklar ro'yxatini chiqarish
    - dp_pat:<batch_id>:<branch>:<screen> -> Patok tanlandi, tasdiqlash so'rash
    - dp_cfm:<batch_id>:<branch>:<screen> -> Tasdiqlandi, patokka jo'natish
    - dp_rebr:<batch_id> -> Filialni qayta tanlash
    - dp_cnc:<batch_id> -> Bekor qilish
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(':')
    action = parts[0]

    # 1. Bekor qilish
    if action == 'dp_cnc':
        context.user_data.clear()
        await query.edit_message_text("❌ *Jo'natish bekor qilindi.* Yangi pastalni tekshirish uchun QR kod yuboring.", parse_mode='Markdown')
        return

    # 2. Filialni qayta tanlash
    if action == 'dp_rebr':
        batch_id = int(parts[1])
        kb = build_branch_keyboard(batch_id)
        await query.edit_message_text(
            "🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
            reply_markup=kb,
            parse_mode='Markdown'
        )
        return

    # 3. Filial tanlandi -> Patoklar ro'yxatini chiqarish (3 ustun)
    if action == 'dp_br':
        batch_id = int(parts[1])
        branch = parts[2]
        branch_name = "Kosonsoy (Bosh bino)" if branch == 'HQ' else "Uychi filiali"

        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await query.edit_message_text("⚠️ Pastal ma'lumotlari topilmadi.", parse_mode='Markdown')
            return

        kb = build_patoks_keyboard(batch_id, branch)
        await query.edit_message_text(
            f"📋 *Pastal:* #{batch_info['batch_number']} ({batch_info['article_name']})\n"
            f"🔢 *Miqdor:* {batch_info['units']} dona | ⚖️ *Vazn:* {batch_info['weight']} kg\n"
            f"🏢 *Filial:* {branch_name}\n\n"
            f"🎯 *Qaysi patokka jo'natiladi? Kerakli patokni tanlang:*",
            reply_markup=kb,
            parse_mode='Markdown'
        )
        return

    # 4. Patok tanlandi -> Tasdiqlash so'rash
    if action == 'dp_pat':
        batch_id = int(parts[1])
        branch = parts[2]
        screen_number = int(parts[3])

        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await query.edit_message_text("⚠️ Pastal ma'lumotlari topilmadi.", parse_mode='Markdown')
            return

        p_code = get_patok_code(screen_number)
        p_name = get_patok_name(screen_number)
        branch_name = "Kosonsoy" if branch == 'HQ' else "Uychi"

        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ha, jo'natish", callback_data=f"dp_cfm:{batch_id}:{branch}:{screen_number}"),
                InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
            ],
            [
                InlineKeyboardButton("🔙 Boshqa patok tanlash", callback_data=f"dp_br:{batch_id}:{branch}")
            ]
        ])

        confirm_text = (
            f"❓ *TASDIQLASH:*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 *Pastal:* #{batch_info['batch_number']} ({batch_info['article_name']})\n"
            f"🔢 *Miqdori:* {batch_info['units']} dona\n"
            f"⚖️ *Haqiqiy vazni:* {batch_info['weight']} kg\n"
            f"🏢 *Filial:* {branch_name}\n"
            f"🎯 *Mo'ljal Patok:* *{p_code}-Patok* ({p_name})\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Ushbu pastalni *{p_code}-Patokka* jo'natishni tasdiqlaysizmi?"
        )
        await query.edit_message_text(confirm_text, reply_markup=confirm_kb, parse_mode='Markdown')
        return

    # 5. Tasdiqlandi -> Patokka jo'natish (Planshet kutishiga o'tkazish)
    if action == 'dp_cfm':
        batch_id = int(parts[1])
        branch = parts[2]
        screen_number = int(parts[3])

        user = query.from_user
        user_name = user.full_name or user.username or f"TG_{user.id}"

        res = await dispatch_batch_async(batch_id, branch, screen_number, user_name)
        if not res:
            await query.edit_message_text("⚠️ Xatolik: Pastal bazadan topilmadi.", parse_mode='Markdown')
            return

        context.user_data.clear()

        now_str = timezone.localtime().strftime("%d.%m.%Y, %H:%M")
        success_text = (
            f"🚀 *MUVAFFAQIYATLI JO'NATILDI!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 *Pastal:* #{res['batch_number']} ({res['article_name']})\n"
            f"🔢 *Miqdori:* {res['units']} dona\n"
            f"⚖️ *Vazni:* {res['weight']} kg\n"
            f"🏢 *Filial:* {res['branch_display']}\n"
            f"🎯 *Mo'ljal Patok:* *{res['patok_code']}-Patok*\n"
            f"👤 *Jo'natuvchi:* {user_name}\n"
            f"🕒 *Vaqti:* {now_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ *{res['patok_code']}-Patok masteri planshet orqali ushbu pastalni QABUL QILISHI kerak.*\n\n"
            f"✅ Pastal holati: `DISPATCHED` (Kutilmoqda)"
        )
        await query.edit_message_text(success_text, parse_mode='Markdown')
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Xatoliklarni ushlash va log qilish"""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ So'rovni qayta ishlashda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
            )
        except Exception:
            pass


class Command(BaseCommand):
    help = "Pastal Pasporti va Patokka Biriktirish Telegram Botini Ishga Tushirish"

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            help="Telegram Bot Tokeni (settings.TELEGRAM_PASSPORT_BOT_TOKEN o'rniga)"
        )

    def handle(self, *args, **options):
        token = (
            options.get('token')
            or getattr(settings, 'TELEGRAM_PASSPORT_BOT_TOKEN', '')
        )

        if not token:
            self.stderr.write(self.style.ERROR("TELEGRAM_PASSPORT_BOT_TOKEN topilmadi!"))
            return

        import time
        while True:
            try:
                self.stdout.write(self.style.SUCCESS("🤖 TERRY JAR — Pastal Tekshiruv va Patokka Biriktirish Boti ishga tushmoqda..."))
                self.stdout.write(f"Token: {token[:12]}...{token[-5:]}")

                app = ApplicationBuilder().token(token).build()

                app.add_handler(CommandHandler("start", start_handler))
                app.add_handler(CommandHandler("help", help_handler))
                app.add_handler(CallbackQueryHandler(callback_handler))
                app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
                app.add_handler(MessageHandler(filters.Document.IMAGE, document_handler))
                app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
                app.add_error_handler(error_handler)

                self.stdout.write(self.style.SUCCESS("✅ Bot muvaffaqiyatli ishga tushdi! Xabarlar kutilmoqda... (Ctrl+C to'xtatish)"))
                app.run_polling(drop_pending_updates=True)
                break
            except (KeyboardInterrupt, SystemExit):
                self.stdout.write("Bot to'xtatildi.")
                break
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Botda xatolik yuz berdi: {e}. 5 soniyadan so'ng qayta uriniladi..."))
                time.sleep(5)
