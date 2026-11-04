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
from decimal import Decimal, InvalidOperation
from django.db import transaction
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
    find_pastal_candidates,
)

logger = logging.getLogger(__name__)

async_verify_pastal_for_sewing = sync_to_async(verify_pastal_for_sewing, thread_sensitive=False)
async_find_pastal_candidates = sync_to_async(find_pastal_candidates, thread_sensitive=False)

WEIGHT_RE = re.compile(r'^\s*(\d+(?:[.,]\d+)?)\s*(?:kg|кг)?\s*$', re.IGNORECASE)
MIN_WEIGHT_KG = Decimal('0.1')
MAX_WEIGHT_KG = Decimal('20000')


def parse_weight_text(raw_text: str):
    """
    Vazn so'ralganda yozilgan matn: Decimal (2 xona), chegaradan tashqari son bo'lsa 'OUT_OF_RANGE', son bo'lmasa None.
    Vazn kutilayotganda butun son ham ("52") vazn hisoblanadi: avval "52" pastal kodi deb tushunilib, vazn saqlanmasdan
    boshqa pastal ochilib qolardi. Pastal kodlari chiziqcha yoki prefiks bilan yoziladi (10-773, PASTAL:12).
    """
    m = WEIGHT_RE.match(raw_text or '')
    if not m:
        return None
    try:
        value = Decimal(m.group(1).replace(',', '.'))
    except InvalidOperation:
        return None
    if not (MIN_WEIGHT_KG <= value <= MAX_WEIGHT_KG):
        return 'OUT_OF_RANGE'
    return value.quantize(Decimal('0.01'))


def batch_dispatch_state(dispatch_status, assigned_screen) -> str:
    """
    Tasdiqlash tugmasiga yoziladigan pastal holati (masalan 'W0' — jo'natilmagan, 'D3' — K3 da kutilmoqda).
    Tugma chiqqandan keyin pastal boshqa patokka jo'natilgan yoki qaytarilgan bo'lsa, eski tugma uni jimgina
    qayta yo'naltirmaydi.
    """
    return f"{(dispatch_status or 'W')[0]}{assigned_screen or 0}"


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
def dispatch_batch_async(batch_id: int, branch: str, screen_number: int, user_name: str, expected_state: str = None):
    """
    Pastalni patokka jo'natish. Master qabul qilguncha (DISPATCHED) boshqa patokka qayta yo'naltirish mumkin;
    qabul qilingan (ACCEPTED) pastal bloklanadi. expected_state — tasdiqlash tugmasi chiqqandagi holat
    (batch_dispatch_state): u o'zgargan bo'lsa (chatda qolib ketgan eski tugma) jo'natilmaydi.
    """
    with transaction.atomic():
        batch = CuttingBatch.objects.select_for_update().select_related(
            'order_item__order', 'order_item__article'
        ).filter(id=batch_id).first()
        if not batch:
            return None

        # Xavfsizlik tekshiruvi: Agar pastal allaqachon qabul qilingan bo'lsa
        if batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED:
            p_code = get_patok_code(batch.assigned_screen) or "Patok"
            return {
                'error': True,
                'message': f"Diqqat! Ushbu pastal allaqachon {p_code}-Patok tomonidan qabul qilingan. Qayta jo'natib bo'lmaydi."
            }

        current_state = batch_dispatch_state(batch.dispatch_status, batch.assigned_screen)
        if expected_state is not None and expected_state != current_state:
            if batch.dispatch_status == CuttingBatch.DispatchStatus.DISPATCHED:
                now_text = f"{get_patok_code(batch.assigned_screen) or 'Patok'}-Patokka jo'natilgan, qabul kutilmoqda"
            else:
                now_text = "jo'natilmagan (yoki master qaytargan)"
            return {
                'error': True,
                'message': f"Pastal holati o'zgargan: hozir {now_text}. Pastalni qaytadan tekshirib, keyin jo'nating."
            }

        previous_screen = batch.assigned_screen if batch.dispatch_status == CuttingBatch.DispatchStatus.DISPATCHED else None

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
        'previous_patok_code': get_patok_code(previous_screen) if (previous_screen and previous_screen != screen_number) else None,
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
    disp_status = batch_info.get('dispatch_status')

    status_line = "Jo'natishga tayyor ✅"
    if disp_status == CuttingBatch.DispatchStatus.DISPATCHED:
        p_c = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
        status_line = f"⏳ Qabul kutilmoqda ({p_c}-Patok) ⚠️"
    elif disp_status == CuttingBatch.DispatchStatus.REJECTED:
        status_line = "🔄 Master qaytargan (Qayta jo'natish mumkin) ⚠️"

    rej_block = ""
    if rej_reason:
        rej_block = f"\n🔄 *Master qaytargan sababi:* _{_esc_md(rej_reason)}_\n"

    return (
        f"📋 *PASTAL PASPORTI:* #{b_num} (`{p_code}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Zakaz:* {ord_num} ({cust_name})\n"
        f"👕 *Model:* {art_name}\n"
        f"🔢 *Miqdori:* {units:,} dona\n"
        f"⚖️ *Haqiqiy vazni:* {wt_text}\n"
        f"🛡 *Holati:* {status_line}\n"
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
    - UYCHI: 14..40 (U-1 .. U-27)
    """
    keyboard = []
    if branch == 'HQ':
        # 1..13
        screen_numbers = list(range(1, 14))
    else:
        # 14..40 (U1..U27)
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


def _candidate_status_label(candidate: dict) -> str:
    """Bir xil kodli pastallar ro'yxatida pastal holati qisqa ko'rinishda"""
    status = candidate.get('dispatch_status')
    p_c = get_patok_code(candidate.get('assigned_screen')) if candidate.get('assigned_screen') else None
    if status == CuttingBatch.DispatchStatus.DISPATCHED:
        return f"{p_c or 'Patok'} da kutilmoqda"
    if status == CuttingBatch.DispatchStatus.ACCEPTED:
        return f"{p_c or 'Patok'} qabul qilgan"
    if status == CuttingBatch.DispatchStatus.REJECTED:
        return "qaytarilgan"
    return "jo'natilmagan"


def build_candidates_text(raw_text: str, candidates: list) -> str:
    """Qo'lda yozilgan kod bir nechta pastalga mos kelganda chiqadigan ro'yxat matni"""
    lines = [
        f"🔎 *«{_esc_md(raw_text)}» kodi bilan {len(candidates)} ta pastal topildi.*",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. `{_esc_md(c['pastal_code'])}` — {_esc_md(c['article_name'] or c['article_code'])} "
            f"({_esc_md(c['article_code'])}), zakaz {_esc_md(c['order_number'])} • {_candidate_status_label(c)}"
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("👇 *Qaysi pastal kerak? Tanlang* (pasport QR kodini yuborsangiz, aniq pastal o'zi ochiladi):")
    return "\n".join(lines)


def build_candidates_keyboard(candidates: list) -> InlineKeyboardMarkup:
    """Bir xil kodli pastallardan birini tanlash tugmalari (dp_pick)"""
    rows = [
        [InlineKeyboardButton(
            f"{i}. {c['pastal_code']} • {c['article_code'] or c['article_name']} • {_candidate_status_label(c)}"[:60],
            callback_data=f"dp_pick:{c['batch_id']}"
        )]
        for i, c in enumerate(candidates, 1)
    ]
    rows.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="dp_cnc:0")])
    return InlineKeyboardMarkup(rows)


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


async def _handle_verification_result(update: Update, context: ContextTypes.DEFAULT_TYPE, result: dict, message=None):
    """
    Tekshiruv natijasini yuborish va pastal kartochkasini chiqarish (message — javob yoziladigan xabar,
    tugma bosilganda query.message; aks holda update.message):
    1. Agar pastal DISPATCHED (Kutilmoqda) holatida bo'lsa -> master qabul qilguncha boshqa patokka qayta
       yo'naltirish mumkin (14cf5ee qarori); tasdiqlashda eski tugma bilan jimgina yo'naltirish bloklanadi.
    2. Agar pastal allaqachon ACCEPTED (Qabul qilingan) holatida bo'lsa -> Qayta jo'natish BLOKLANADI!
       (Faqat master planshetda Qaytarish (Rad etish) tugmasini bosgandagina qayta jo'natish mumkin bo'ladi)
    3. Agar pastal WAITING (yangi yoki master qaytargan) holatida bo'lsa:
       - Pastal kartochkasi chiqariladi
       - Foydalanuvchi talabi bo'yicha:
         * Agar kg kiritilmagan bo'lsa: [⚖️ Kg kiritish] va [🚀 Jo'natish], [🔍 Tekshirish]
         * Agar kg kiritilgan bo'lsa: 'bu yerda bitta kg kiritilgan bolsa bu button chiqmaydi'
           faqat [🚀 Jo'natish] va [🔍 Tekshirish] chiqadi!
    """
    message = message or update.message
    message_text = result.get('message', '')
    details = result.get('details', {}) or {}
    batch_id = await resolve_batch_from_details_async(details)

    if not result.get('success') or not batch_id or not result.get('can_release'):
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(message, message_text)
        return

    batch_info = await get_batch_info_async(batch_id)
    if not batch_info:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(message, message_text)
        return

    disp_status = batch_info.get('dispatch_status')

    # 1. AGAR PASTAL ALLAQACHON QABUL QILINGAN BO'LSA (ACCEPTED)
    if disp_status == CuttingBatch.DispatchStatus.ACCEPTED:
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        await _safe_reply_text(message, message_text)
        return

    # 2. PASTAL JO'NATISHGA TAYYOR YOKI HOZIRDA KUTILMOQDA (WAITING / DISPATCHED / REJECTED):
    # Foydalanuvchi talabi:
    # "agar bu pastal hali kutulmoqda bolsa button chiqadi
    #  agar kg hali bolmasa kg kiritish boladi agar jonatish uchun bolsa jonatish
    #  agar tekshrish uchun bolsa tekshirish biladi
    #  bu yerda bitta kg kiritilgan bolsa bu button chiqmaydi bolmasa chiqadi
    #  jonatish bosganda malumot beradi va jonatadigan filial chiqadi va qolgan oldin qilganimizdek boladi"
    weight = batch_info.get('weight') or 0.0
    has_weight = (weight > 0)

    context.user_data['dispatch_batch_id'] = batch_id
    context.user_data['dispatch_state'] = 'WAITING_WEIGHT' if not has_weight else 'WAITING_BRANCH'
    if has_weight:
        context.user_data['dispatch_weight'] = weight

    card_text = build_pastal_card_text(batch_info)
    kb = build_pastal_action_keyboard(batch_id, has_weight=has_weight)
    await _safe_reply_text(message, card_text, reply_markup=kb)


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

    # 1. Agar vazn kutilayotgan bo'lsa: har qanday son (52, 48.5, 48,5, 52 kg) vazn hisoblanadi
    if dispatch_state == 'WAITING_WEIGHT' and dispatch_batch_id:
        parsed_weight = parse_weight_text(raw_text)
        if parsed_weight == 'OUT_OF_RANGE':
            await _safe_reply_text(
                update.message,
                f"⚠️ *Vazn {MIN_WEIGHT_KG} – {MAX_WEIGHT_KG} kg oralig'ida bo'lishi kerak.*\n"
                f"⚖️ Pastalning haqiqiy vaznini qaytadan kiriting (masalan: `48.5` yoki `52`):"
            )
            return
        if parsed_weight is not None:
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

        # Vazn kiritilmagan bo'lsa, foydalanuvchi yangi pastal yubordi deb hisoblab eski holatni tozalaymiz
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)

    # 2. Qo'lda yozilgan kod bir nechta pastalga mos kelsa (bir xil kodli pijama ustki/shorti va h.k.) — tanlatamiz
    candidates = await async_find_pastal_candidates(raw_text)
    if len(candidates) > 1:
        await _safe_reply_text(
            update.message,
            build_candidates_text(raw_text, candidates),
            reply_markup=build_candidates_keyboard(candidates)
        )
        return

    # 3. Oddiy QR kod yoki pastal kodi/raqami tekshiruvi
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

    # 0.1. Bir xil kodli pastallardan biri tanlandi -> to'liq tekshiruv va kartochka (yangi xabar bilan)
    if action == 'dp_pick':
        batch_id = int(parts[1])
        context.user_data.pop('dispatch_state', None)
        context.user_data.pop('dispatch_batch_id', None)
        context.user_data.pop('dispatch_weight', None)
        res = await async_verify_pastal_for_sewing(f"PASTAL:{batch_id}")
        await _handle_verification_result(update, context, res, message=query.message)
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

        # Holat tekshiruvi: faqat patok allaqachon qabul qilgan bo'lsa bloklanadi
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

        if batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.ACCEPTED:
            p_code = get_patok_code(batch_info.get('assigned_screen')) or "Patok"
            context.user_data.clear()
            await _safe_edit_text(query, f"📌 *Ushbu pastal allaqachon {p_code}-Patok tomonidan qabul qilingan!*\n🚫 Uni qayta jo'nata olmaysiz.")
            return

        p_code = get_patok_code(screen_number)
        p_name = get_patok_name(screen_number)
        branch_name = "Kosonsoy" if branch == 'HQ' else "Uychi"

        # Kutilayotgan (DISPATCHED) pastal: master qabul qilguncha boshqa patokka qayta yo'naltirish mumkin (14cf5ee).
        # Avval shu blok qolib ketgani uchun "Jo'natish" bosilsa ham patok tanlangach to'xtab qolardi.
        current_screen = batch_info.get('assigned_screen')
        is_dispatched = (batch_info.get('dispatch_status') == CuttingBatch.DispatchStatus.DISPATCHED and current_screen)
        if is_dispatched and current_screen == screen_number:
            await _safe_edit_text(
                query,
                f"⏳ *Ushbu pastal allaqachon {p_code}-Patokka jo'natilgan va planshetda qabul kutilmoqda.*\n"
                f"Boshqa patokka o'tkazish uchun boshqa patokni tanlang.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Boshqa patok tanlash", callback_data=f"dp_br:{batch_id}:{branch}"),
                    InlineKeyboardButton("❌ Bekor qilish", callback_data=f"dp_cnc:{batch_id}")
                ]])
            )
            return
        move_line = (
            f"🔄 *Hozir:* {get_patok_code(current_screen)}-Patokda qabul kutilmoqda → *{p_code}-Patokka ko'chiriladi*\n"
            if is_dispatched else ""
        )
        state = batch_dispatch_state(batch_info.get('dispatch_status'), current_screen)

        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ha, jo'natish", callback_data=f"dp_cfm:{batch_id}:{branch}:{screen_number}:{state}"),
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
            f"{move_line}"
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
        # Tugma chiqqandagi pastal holati (batch_dispatch_state). Yangilanishdan oldingi tugmalarda yo'q —
        # ular chatda qolib ketgan eski tugmalar, jimgina jo'natmaymiz
        expected_state = parts[4] if len(parts) > 4 else None
        if expected_state is None:
            context.user_data.clear()
            await _safe_edit_text(
                query,
                "⚠️ *Bu tasdiqlash tugmasi eskirgan.* Pastalni qaytadan tekshiring (pasport QR kodini yuboring)."
            )
            return

        user = query.from_user
        user_name = _esc_md(user.full_name or user.username or f"TG-{user.id}")

        res = await dispatch_batch_async(batch_id, branch, screen_number, user_name, expected_state=expected_state)
        if not res:
            await _safe_edit_text(query, "⚠️ Xatolik: Pastal bazadan topilmadi.")
            return

        if res.get('error'):
            context.user_data.clear()
            await _safe_edit_text(query, f"🚫 *{res['message']}*")
            return

        context.user_data.clear()

        now_str = timezone.localtime().strftime("%d.%m.%Y, %H:%M")
        moved_line = f"🔄 *{res['previous_patok_code']}-Patokdan ko'chirildi*\n" if res.get('previous_patok_code') else ""
        success_text = (
            f"🚀 *MUVAFFAQIYATLI JO'NATILDI!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 *Pastal:* #{res['batch_number']} ({_esc_md(res['article_name'])})\n"
            f"🔢 *Miqdori:* {res['units']} dona\n"
            f"⚖️ *Vazni:* {res['weight']} kg\n"
            f"🏢 *Filial:* {res['branch_display']}\n"
            f"🎯 *Mo'ljal Patok:* *{res['patok_code']}-Patok*\n"
            f"{moved_line}"
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
