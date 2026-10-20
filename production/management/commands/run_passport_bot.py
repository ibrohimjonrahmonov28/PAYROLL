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
    batch = CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article'
    ).filter(id=batch_id).first()
    if not batch:
        return None
    order = batch.order_item.order if (batch.order_item and batch.order_item.order) else None
    customer_name = order.customer.name if (order and order.customer and hasattr(order.customer, 'name')) else getattr(order, 'client_name', '—') if order else '—'
    return {
        'batch_id': batch.id,
        'batch_number': batch.batch_number,
        'pastal_code': batch.pastal_code or str(batch.batch_number),
        'article_name': batch.order_item.article.name if batch.order_item and batch.order_item.article else 'Model',
        'order_number': order.order_number if order else '—',
        'customer_name': customer_name,
        'units': batch.total_quantity,
        'weight': float(batch.actual_weight_kg) if batch.actual_weight_kg else 0.0,
        'dispatch_status': batch.dispatch_status,
        'assigned_screen': batch.assigned_screen,
        'assigned_branch': batch.assigned_branch,
        'rejection_reason': batch.rejection_reason or '',
    }


@sync_to_async
def is_batch_code_exists_async(raw_str: str) -> bool:
    if not raw_str.isdigit():
        return False
    val = int(raw_str)
    return CuttingBatch.objects.filter(Q(id=val) | Q(pastal_code=raw_str) | Q(batch_number=val)).exists()


@sync_to_async
def dispatch_batch_async(batch_id: int, branch: str, screen_number: int, user_name: str):
    batch = CuttingBatch.objects.select_related('order_item__order', 'order_item__article').filter(id=batch_id).first()
    if not batch:
        return None

    # Xavfsizlik tekshiruvi: Agar pastal allaqachon kutilayotgan yoki qabul qilingan bo'lsa
    if batch.dispatch_status == CuttingBatch.DispatchStatus.DISPATCHED:
        p_code = get_patok_code(batch.assigned_screen) or "Patok"
        return {
            'error': True,
            'message': f"Diqqat! Ushbu pastal hozirda {p_code}-Patokda kutilmoqda. Qayta jo'natib bo'lmaydi."
        }
    if batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED:
        p_code = get_patok_code(batch.assigned_screen) or "Patok"
        return {
            'error': True,
            'message': f"Diqqat! Ushbu pastal allaqachon {p_code}-Patok tomonidan qabul qilingan. Qayta jo'natib bo'lmaydi."
        }

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
        'error': False,
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


def build_pastal_card_text(batch_info: dict) -> str:
    """Pastal haqida chiroyli va tushunarli kartochka matni"""
    p_code = batch_info.get('pastal_code') or str(batch_info.get('batch_number'))
    b_num = batch_info.get('batch_number')
    art_name = _esc_md(batch_info.get('article_name', 'Model'))
    ord_num = _esc_md(batch_info.get('order_number', '—'))
    cust_name = _esc_md(batch_info.get('customer_name', '—'))
    units = batch_info.get('units', 0)
    weight = batch_info.get('weight', 0.0)
    wt_text = f"`{weight} kg` ✅" if weight > 0 else "_(Kiritilmagan)_ ⚠️"
    rej_reason = batch_info.get('rejection_reason')

    rej_block = ""
    if rej_reason:
        rej_block = f"\n🔄 *Master qaytargan:* _{_esc_md(rej_reason)}_\n"

    return (
        f"📋 *PASTAL PASPORTI:* #{b_num} (`{p_code}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Zakaz:* {ord_num} ({cust_name})\n"
        f"👕 *Model:* {art_name}\n"
        f"🔢 *Miqdori:* {units:,} dona\n"
        f"⚖️ *Haqiqiy vazni:* {wt_text}\n"
        f"🛡 *Holati:* Jo'natishga tayyor ✅\n"
        f"{rej_block}"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 *Kerakli amalni tanlang:*"
    ).replace(",", " ")


def build_pastal_action_keyboard(batch_id: int, has_weight: bool) -> InlineKeyboardMarkup:
    """
    Foydalanuvchi talabi:
    - Agar bu pastal hali kutilmoqda bo'lsa button chiqadi:
      - Agar kg hali bo'lmasa: [⚖️ Kg kiritish], [🚀 Jo'natish], [🔍 Tekshirish]
      - Agar kg kiritilgan bo'lsa: 'bu yerda bitta kg kiritilgan bolsa bu button chiqmaydi'
        faqat [🚀 Jo'natish], [🔍 Tekshirish] chiqadi!
    """
    rows = []
    if not has_weight:
        rows.append([
            InlineKeyboardButton("⚖️ Kg kiritish", callback_data=f"dp_ask_wt:{batch_id}"),
            InlineKeyboardButton("🚀 Jo'natish", callback_data=f"dp_send:{batch_id}")
        ])
    else:
        rows.append([
            InlineKeyboardButton("🚀 Jo'natish", callback_data=f"dp_send:{batch_id}")
        ])

    rows.append([
        InlineKeyboardButton("🔍 Tekshirish", callback_data=f"dp_check:{batch_id}"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
    ])
    return InlineKeyboardMarkup(rows)


def build_branch_keyboard(batch_id: int) -> InlineKeyboardMarkup:
    """Filial tanlash klaviaturasi"""
    keyboard = [
        [
            InlineKeyboardButton("🏢 Kosonsoy (Bosh bino)", callback_data=f"dp_br:{batch_id}:HQ"),
            InlineKeyboardButton("🏭 Uychi filiali", callback_data=f"dp_br:{batch_id}:UYCHI"),
        ],
        [
            InlineKeyboardButton("🔙 Orqaga", callback_data=f"dp_back_card:{batch_id}"),
            InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_patoks_keyboard(batch_id: int, branch: str) -> InlineKeyboardMarkup:
    """
    Patoklar ro'yxatini 3 ustun ko'rinishida generatsiya qilish:
    - HQ (Kosonsoy): 1..13 (K-1 .. K-13)
    - UYCHI: 14..24 (U-1 .. U-11)
    """
    keyboard = []
    if branch == 'HQ':
        # 1..13
        screen_numbers = list(range(1, 14))
    else:
        # 14..24 (U1..U11)
        screen_numbers = list(range(14, 25))

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


def _esc_md(val) -> str:
    """Telegram Markdown v1 da xatolik bermasligi uchun _, *, `, [ belgilarini zararsizlantirish"""
    if val is None:
        return ""
    return str(val).replace('_', ' ').replace('*', '').replace('`', "'").replace('[', '(').replace(']', ')')


async def _safe_reply_text(message, text: str, **kwargs):
    try:
        return await message.reply_text(text, parse_mode='Markdown', **kwargs)
    except Exception:
        clean_text = text.replace('*', '').replace('_', '').replace('`', '')
        return await message.reply_text(clean_text, **kwargs)


async def _safe_edit_text(query, text: str, **kwargs):
    try:
        return await query.edit_message_text(text, parse_mode='Markdown', **kwargs)
    except Exception:
        clean_text = text.replace('*', '').replace('_', '').replace('`', '')
        return await query.edit_message_text(clean_text, **kwargs)


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
    await _safe_reply_text(update.message, welcome_text)


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
    await _safe_reply_text(update.message, help_text)


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
            if box.pastal_number:
                from production.passport_bot_service import _find_best_cutting_batch
                b = _find_best_cutting_batch(box.pastal_number)
                if b:
                    return b.id
            batch = CuttingBatch.objects.filter(order_item__order=box.order).order_by('-id').first()
            if batch:
                return batch.id
    return None


async def _handle_verification_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict):
    """
    Tekshiruv natijasini yuborish va pastal kartochkasini chiqarish:
    1. Agar pastal allaqachon DISPATCHED (Kutilmoqda) holatida bo'lsa -> Qayta jo'natish BLOKLANADI!
    2. Agar pastal allaqachon ACCEPTED (Qabul qilingan) holatida bo'lsa -> Qayta jo'natish BLOKLANADI!
       (Faqat master planshetda Qaytarish (Rad etish) tugmasini bosgandagina qayta jo'natish mumkin bo'ladi)
    3. Agar pastal WAITING (yangi yoki master qaytargan) holatida bo'lsa:
       - Pastal kartochkasi chiqariladi
       - Foydalanuvchi talabi bo'yicha:
         * Agar kg kiritilmagan bo'lsa: [⚖️ Kg kiritish] va [🚀 Jo'natish], [🔍 Tekshirish]
         * Agar kg kiritilgan bo'lsa: 'bu yerda bitta kg kiritilgan bolsa bu button chiqmaydi'
           faqat [🚀 Jo'natish] va [🔍 Tekshirish] chiqadi!
    """
    message_text = result.get('message', '')
    details = result.get('details', {}) or {}
    batch_id = await resolve_batch_from_details_async(details)

    if not result.get('success') or not batch_id or not result.get('can_release'):
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(update.message, message_text)
        return

    batch_info = await get_batch_info_async(batch_id)
    if not batch_info:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(update.message, message_text)
        return

    disp_status = batch_info.get('dispatch_status')

    # 1. AGAR PASTAL HOZIRDA KUTILAYOTGAN BO'LSA (DISPATCHED)
    if disp_status == CuttingBatch.DispatchStatus.DISPATCHED:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(update.message, message_text)
        return

    # 2. AGAR PASTAL ALLAQACHON QABUL QILINGAN BO'LSA (ACCEPTED)
    if disp_status == CuttingBatch.DispatchStatus.ACCEPTED:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(update.message, message_text)
        return

    # 3. PASTAL JO'NATISHGA TAYYOR (WAITING / REJECTED):
    weight = batch_info.get('weight') or 0.0
    has_weight = (weight > 0)

    context.user_data['dispatch_batch_id'] = batch_id
    context.user_data['dispatch_state'] = 'WAITING_WEIGHT' if not has_weight else 'WAITING_BRANCH'
    if has_weight:
        context.user_data['dispatch_weight'] = weight

    card_text = build_pastal_card_text(batch_info)
    kb = build_pastal_action_keyboard(batch_id, has_weight=has_weight)
    await _safe_reply_text(update.message, card_text, reply_markup=kb)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Matnli xabarlarni qabul qilish:
    - Agar vazn kutilayotgan bo'lsa va son kiritilsa (masalan: 48.5, 50, 52 kg): vaznni qabul qiladi
    - Aks holda: QR kod yoki pastal kodi/raqami sifatida tekshiradi
    """
    raw_text = (update.message.text or '').strip()
    if not raw_text:
        return

    dispatch_state = context.user_data.get('dispatch_state')
    dispatch_batch_id = context.user_data.get('dispatch_batch_id')

    # 1. Agar vazn kutilayotgan bo'lsa va son kiritilsa
    if dispatch_state == 'WAITING_WEIGHT' and dispatch_batch_id:
        m = re.search(r'^\s*(\d+(?:[.,]\d+)?)\s*(?:kg|кг)?\s*$', raw_text, re.IGNORECASE)
        if m:
            clean_num_text = m.group(1).replace(',', '.')
            try:
                val = float(clean_num_text)
                if 0.1 <= val <= 20000:
                    parsed_weight = Decimal(f"{val:.2f}")
                    saved_info = await save_batch_weight_async(dispatch_batch_id, parsed_weight)
                    if saved_info:
                        context.user_data['dispatch_weight'] = float(parsed_weight)
                        context.user_data['dispatch_state'] = 'WAITING_BRANCH'

                        kb = build_branch_keyboard(dispatch_batch_id)
                        await _safe_reply_text(
                            update.message,
                            f"⚖️ *Vazn saqlandi:* `{parsed_weight} kg`\n"
                            f"📋 *Pastal:* #{saved_info['batch_number']} ({_esc_md(saved_info['article_name'])}, {saved_info['units']} dona)\n\n"
                            f"🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
                            reply_markup=kb
                        )
                        return
            except (ValueError, TypeError):
                pass

        # Agar vazn emas, balki yangi QR kod yuborilmagan bo'lsa, foydalanuvchini ogohlantirish
        looks_like_qr = bool(re.search(
            r'^(PASTAL|BATCH|TICKET|TK|CONTROL|BOX|QUTI|PC|ORDER|ZAKAZ|#|[A-Z0-9]{8})',
            raw_text,
            re.IGNORECASE
        ))
        if not looks_like_qr:
            await _safe_reply_text(
                update.message,
                "⚠️ *Iltimos, pastal vaznini to'g'ri sonda kiriting!* (Masalan: `48.5` yoki `52`)\n"
                "Yoki yangi pastal tekshirish uchun uning QR kodini yuboring."
            )
            return

        # Yangi QR kod kelsa eski holatni tozalab yangisini tekshirish
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)

    # 2. Oddiy QR kod yoki pastal kodi/raqami tekshiruvi
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
        await _safe_reply_text(update.message, error_msg)
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
        await _safe_reply_text(update.message, "⚠️ Iltimos, faqat rasm fayllarini yuboring.")
        return

    doc_file = await doc.get_file()
    image_bytes = await doc_file.download_as_bytearray()

    decoded_codes = decode_qr_from_image_bytes(image_bytes)
    if not decoded_codes:
        await _safe_reply_text(update.message, "⚠️ Ushbu fayldan QR kod aniqlanmadi.")
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

    # 0. Orqaga kartochkaga qaytish
    if action == 'dp_back_card':
        batch_id = int(parts[1])
        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        weight = batch_info.get('weight') or 0.0
        has_weight = (weight > 0)
        card_text = build_pastal_card_text(batch_info)
        kb = build_pastal_action_keyboard(batch_id, has_weight=has_weight)
        await _safe_edit_text(query, card_text, reply_markup=kb)
        return

    # 1. Vazn (kg) kiritish tugmasi
    # Foydalanuvchi talabi: "agar kg hali bolmasa kg kiritish boladi"
    if action == 'dp_ask_wt':
        batch_id = int(parts[1])
        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        context.user_data['dispatch_batch_id'] = batch_id
        context.user_data['dispatch_state'] = 'WAITING_WEIGHT'

        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data=f"dp_back_card:{batch_id}"),
             InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")]
        ])
        await _safe_edit_text(
            query,
            f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])})\n\n"
            f"⚖️ *Iltimos, pastalning haqiqiy vaznini (kg) kiriting:*\n"
            f"_(Pastal tortilgandagi haqiqiy kg ni matn shaklida yozing, masalan: `48.5` yoki `52`)_",
            reply_markup=back_kb
        )
        return

    # 2. Jo'natish tugmasi
    # Foydalanuvchi talabi: "agar jonatish uchun bolsa jonatish... jonatish bosganda malumot beradi va jonatadigan filial chiqadi va qolgan oldin qilganimizdek boladi"
    if action == 'dp_send':
        batch_id = int(parts[1])
        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        # Holat tekshiruvi
        if batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.DISPATCHED:
            p_code = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
            context.user_data.clear()
            await _safe_edit_text(query, f"⏳ *Ushbu pastal hozirda {p_code}-Patokda kutilmoqda!*\n🚫 Uni qayta jo'nata olmaysiz.")
            return

        if batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.ACCEPTED:
            p_code = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
            context.user_data.clear()
            await _safe_edit_text(query, f"📌 *Ushbu pastal allaqachon {p_code}-Patok tomonidan qabul qilingan!*\n🚫 Uni qayta jo'nata olmaysiz.")
            return

        weight = batch_info.get('weight') or 0.0
        if weight <= 0:
            context.user_data['dispatch_batch_id'] = batch_id
            context.user_data['dispatch_state'] = 'WAITING_WEIGHT'
            back_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Orqaga", callback_data=f"dp_back_card:{batch_id}"),
                 InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")]
            ])
            await _safe_edit_text(
                query,
                f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])}, {batch_info['units']} dona)\n"
                f"⚠️ *Vazn hali kiritilmagan!*\n\n"
                f"⚖️ *Iltimos, pastalni patokka jo'natish uchun uning haqiqiy vaznini (kg) kiriting:*\n"
                f"_(Pastal tortilgandagi haqiqiy kg ni matn shaklida yozing, masalan: `48.5` yoki `52`)_",
                reply_markup=back_kb
            )
            return

        # Vazn kiritilgan bo'lsa: ma'lumot beradi va jo'natadigan filial chiqadi
        context.user_data['dispatch_batch_id'] = batch_id
        context.user_data['dispatch_weight'] = weight
        context.user_data['dispatch_state'] = 'WAITING_BRANCH'

        kb = build_branch_keyboard(batch_id)
        await _safe_edit_text(
            query,
            f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])}, {batch_info['units']} dona)\n"
            f"⚖️ *Haqiqiy vazni:* `{weight} kg`\n\n"
            f"🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
            reply_markup=kb
        )
        return

    # 3. To'liq tekshirish tugmasi
    # Foydalanuvchi talabi: "agar tekshrish uchun bolsa tekshirish biladi"
    if action == 'dp_check':
        batch_id = int(parts[1])
        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        res = await async_verify_pastal_for_sewing(f"PASTAL:{batch_id}")
        message_text = res.get('message', '')
        weight = batch_info.get('weight') or 0.0

        check_buttons = []
        if res.get('can_release'):
            check_buttons.append([
                InlineKeyboardButton("🚀 Jo'natish", callback_data=f"dp_send:{batch_id}")
            ])
            if weight <= 0:
                check_buttons[0].insert(0, InlineKeyboardButton("⚖️ Kg kiritish", callback_data=f"dp_ask_wt:{batch_id}"))

        check_buttons.append([
            InlineKeyboardButton("🔙 Orqaga", callback_data=f"dp_back_card:{batch_id}"),
            InlineKeyboardButton("❌ Yopish", callback_data=f"dp_cnc:{batch_id}")
        ])

        await _safe_edit_text(query, message_text, reply_markup=InlineKeyboardMarkup(check_buttons))
        return

    # 4. Oldingi vaznni qoldirish
    if action == 'dp_keep_wt':
        batch_id = int(parts[1])
        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        weight = batch_info.get('weight') or 0.0
        context.user_data['dispatch_weight'] = weight
        context.user_data['dispatch_state'] = 'WAITING_BRANCH'
        kb = build_branch_keyboard(batch_id)
        await _safe_edit_text(
            query,
            f"⚖️ *Vazn saqlandi:* `{weight} kg`\n"
            f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])}, {batch_info['units']} dona)\n\n"
            f"🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
            reply_markup=kb
        )
        return

    # 1. Bekor qilish
    if action == 'dp_cnc':
        context.user_data.clear()
        await _safe_edit_text(query, "❌ *Jo'natish bekor qilindi.* Yangi pastalni tekshirish uchun QR kod yuboring.")
        return

    # 2. Filialni qayta tanlash
    if action == 'dp_rebr':
        batch_id = int(parts[1])
        kb = build_branch_keyboard(batch_id)
        await _safe_edit_text(
            query,
            "🏢 *Ushbu pastal qaysi filialga jo'natiladi? Tanlang:*",
            reply_markup=kb
        )
        return

    # 3. Filial tanlandi -> Patoklar ro'yxatini chiqarish (3 ustun)
    if action == 'dp_br':
        batch_id = int(parts[1])
        branch = parts[2]
        branch_name = "Kosonsoy (Bosh bino)" if branch == 'HQ' else "Uychi filiali"

        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        kb = build_patoks_keyboard(batch_id, branch)
        await _safe_edit_text(
            query,
            f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])})\n"
            f"🔢 *Miqdor:* {batch_info['units']} dona | ⚖️ *Vazn:* {batch_info['weight']} kg\n"
            f"🏢 *Filial:* {branch_name}\n\n"
            f"🎯 *Qaysi patokka jo'natiladi? Kerakli patokni tanlang:*",
            reply_markup=kb
        )
        return

    # 4. Patok tanlandi -> Tasdiqlash so'rash
    if action == 'dp_pat':
        batch_id = int(parts[1])
        branch = parts[2]
        screen_number = int(parts[3])

        batch_info = await get_batch_info_async(batch_id)
        if not batch_info:
            await _safe_edit_text(query, "⚠️ Pastal ma'lumotlari topilmadi.")
            return

        # Qayta jo'natish xavfsizlik tekshiruvi
        if batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.DISPATCHED:
            p_code = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
            context.user_data.clear()
            await _safe_edit_text(query, f"⏳ *Ushbu pastal hozirda {p_code}-Patokda kutilmoqda!*\n🚫 Uni qayta jo'nata olmaysiz. Master qabul qilishi yoki qaytarishi kerak.")
            return

        if batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.ACCEPTED:
            p_code = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
            context.user_data.clear()
            await _safe_edit_text(query, f"📌 *Ushbu pastal allaqachon {p_code}-Patok tomonidan qabul qilingan!*\n🚫 Uni qayta jo'nata olmaysiz.")
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
            f"📋 *Pastal:* #{batch_info['batch_number']} ({_esc_md(batch_info['article_name'])})\n"
            f"🔢 *Miqdori:* {batch_info['units']} dona\n"
            f"⚖️ *Haqiqiy vazni:* {batch_info['weight']} kg\n"
            f"🏢 *Filial:* {branch_name}\n"
            f"🎯 *Mo'ljal Patok:* *{p_code}-Patok* ({_esc_md(p_name)})\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Ushbu pastalni *{p_code}-Patokka* jo'natishni tasdiqlaysizmi?"
        )
        await _safe_edit_text(query, confirm_text, reply_markup=confirm_kb)
        return

    # 5. Tasdiqlandi -> Patokka jo'natish (Planshet kutishiga o'tkazish)
    if action == 'dp_cfm':
        batch_id = int(parts[1])
        branch = parts[2]
        screen_number = int(parts[3])

        user = query.from_user
        user_name = _esc_md(user.full_name or user.username or f"TG-{user.id}")

        res = await dispatch_batch_async(batch_id, branch, screen_number, user_name)
        if not res:
            await _safe_edit_text(query, "⚠️ Xatolik: Pastal bazadan topilmadi.")
            return

        if res.get('error'):
            context.user_data.clear()
            await _safe_edit_text(query, f"🚫 *{res['message']}*")
            return

        context.user_data.clear()

        now_str = timezone.localtime().strftime("%d.%m.%Y, %H:%M")
        success_text = (
            f"🚀 *MUVAFFAQIYATLI JO'NATILDI!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 *Pastal:* #{res['batch_number']} ({_esc_md(res['article_name'])})\n"
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
        await _safe_edit_text(query, success_text)
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
