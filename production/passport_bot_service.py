"""
Terry Jar — Pastal Pasporti va Stikerlar Sifatini Tekshirish Xizmati (Passport Verification Service)
Telegram bot, Barcode skaner va Web orqali ishlatiladi.

Vazifasi:
Mahsulotlar tikuv patogiga berilishidan oldin har bir quti va undagi barcha QR stikerlarni
bittalab chuqur tekshirish:
1. Pastal yoki qutilar bekor qilinganmi (atmen bo'lganmi)?
2. Barcha operatsiya narxlari va unit summalari to'liq kiritilganmi?
3. Agar hammasi to'g'ri bo'lsa: "✅ PATOKKA BERISH MUMKIN"
4. Agar muammo bo'lsa: "🚫 PATOKKA BERIB BO'LMAYDI! Yangilash uchun stikerchiga murojaat qiling."
"""

import io
import re
from decimal import Decimal
import numpy as np
import cv2
from django.db.models import Q
from django.utils import timezone

from .models import (
    Order,
    Box,
    Ticket,
    CuttingBatch,
    CuttingBatchItem,
    CancelledBatchLog,
    ArticleOperation
)


def decode_qr_from_image_bytes(image_bytes: bytes) -> list[str]:
    """
    Foydalanuvchi Telegram botga yuborgan fotosuratdan (yoki skanerdan)
    OpenCV QRCodeDetector yordamida barcha QR kodlarni o'qib oladi.
    Turli burchaklar, yorug'lik va kontrast holatlariga chidamli.
    """
    if not image_bytes:
        return []

    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        # Rasm juda katta bo'lsa, tezlik va aniqlik uchun masshtabni moslash (max 1600px)
        h, w = img.shape[:2]
        max_dim = max(h, w)
        if max_dim > 1600:
            scale = 1600.0 / max_dim
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        detector = cv2.QRCodeDetector()
        results = []

        # 1-urinish: Rangli tasvirdan bevosita aniqlash
        val, pts, _ = detector.detectAndDecode(img)
        if val and val.strip():
            results.append(val.strip())
            return results

        # Multi-detect urinishi
        retval, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
        if retval and decoded_info:
            for s in decoded_info:
                if s and s.strip():
                    results.append(s.strip())
            if results:
                return results

        # 2-urinish: Grayscale (oq-qora)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        val, pts, _ = detector.detectAndDecode(gray)
        if val and val.strip():
            results.append(val.strip())
            return results

        # 3-urinish: Kontrastni oshirish (Histogram Equalization)
        eq = cv2.equalizeHist(gray)
        val, pts, _ = detector.detectAndDecode(eq)
        if val and val.strip():
            results.append(val.strip())
            return results

        # 4-urinish: Adaptive threshold (yorug'lik notekis bo'lganda)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10
        )
        val, pts, _ = detector.detectAndDecode(thresh)
        if val and val.strip():
            results.append(val.strip())
            return results

        return []
    except Exception:
        return []


def resolve_code_to_entity(raw_code: str):
    """
    Kiritilgan QR matnini (yoki raqamini) tahlil qilib, tegishli obyektlarni aniqlaydi:
    Qaytadi: (batch, box, ticket, resolved_type, clean_code)
    """
    if not raw_code:
        return None, None, None, 'UNKNOWN', ''

    raw = str(raw_code).strip()
    clean = raw

    # 1. PASTAL / BATCH prefiksi
    # Masalan: PASTAL:45, BATCH:45, PC:12, PASTAL: 45
    m_pastal = re.match(r'^(?:PASTAL|BATCH)\s*:\s*(\d+)$', raw, re.IGNORECASE)
    if m_pastal:
        batch_id = int(m_pastal.group(1))
        batch = CuttingBatch.objects.select_related(
            'order_item__order__customer',
            'order_item__article__model'
        ).filter(id=batch_id).first()
        return batch, None, None, 'PASTAL_ID', str(batch_id)

    # 2. TICKET prefiksi
    # Masalan: TICKET:TK-ORD-1-...
    if raw.upper().startswith('TICKET:'):
        tkt_code = raw[7:].strip()
        ticket = Ticket.objects.select_related(
            'box__order__customer',
            'box__article__model',
            'box__cutting_batch_item__batch__order_item__article',
            'article_operation__operation'
        ).filter(ticket_code=tkt_code).first()
        if ticket:
            box = ticket.box
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, ticket, 'TICKET_CODE', tkt_code

    # 3. CONTROL prefiksi
    # Masalan: CONTROL:A9-234
    if raw.upper().startswith('CONTROL:'):
        bx_code = raw[8:].strip()
        box = Box.objects.select_related(
            'order__customer',
            'article__model',
            'cutting_batch_item__batch'
        ).filter(box_code=bx_code).first()
        if box:
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, None, 'BOX_CODE', bx_code

    # 4. To'g'ridan-to'g'ri ticket_code (TK-...)
    if raw.upper().startswith('TK-'):
        ticket = Ticket.objects.select_related(
            'box__order__customer',
            'box__article__model',
            'box__cutting_batch_item__batch__order_item__article',
            'article_operation__operation'
        ).filter(ticket_code=raw).first()
        if ticket:
            box = ticket.box
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, ticket, 'TICKET_CODE', raw

    # 5. Stiker kodi (8 xonali, masalan: 489DA37F yoki #489DA37F)
    stiker_candidate = raw.lstrip('#').strip().upper()
    if len(stiker_candidate) == 8 and re.match(r'^[A-Z0-9]{8}$', stiker_candidate):
        ticket = Ticket.objects.select_related(
            'box__order__customer',
            'box__article__model',
            'box__cutting_batch_item__batch__order_item__article',
            'article_operation__operation'
        ).filter(stiker_code=stiker_candidate).first()
        if ticket:
            box = ticket.box
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, ticket, 'STIKER_CODE', stiker_candidate

    # 6. Quti kodi (masalan: A9-234, B3-102)
    if re.match(r'^[A-Z]\d+-\d+$', raw.upper()):
        box = Box.objects.select_related(
            'order__customer',
            'article__model',
            'cutting_batch_item__batch'
        ).filter(box_code__iexact=raw).first()
        if box:
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, None, 'BOX_CODE', raw.upper()

    # 7. Pastal kodi (masalan: PC: 12 yoki to'g'ridan-to'g'ri pastal kodi)
    pc_clean = raw
    if pc_clean.upper().startswith('PC:'):
        pc_clean = pc_clean[3:].strip()
    
    batches = list(CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).filter(pastal_code__iexact=pc_clean).order_by('-created_at')[:2])
    if len(batches) == 1:
        return batches[0], None, None, 'PASTAL_CODE', pc_clean

    # 8. Sof son bo'lsa (masalan: "45")
    if raw.isdigit():
        num = int(raw)
        # Avval Batch ID deb tekshiramiz
        batch = CuttingBatch.objects.select_related(
            'order_item__order__customer',
            'order_item__article__model'
        ).filter(id=num).first()
        if batch:
            return batch, None, None, 'PASTAL_ID', str(num)

        # Quti ID deb tekshiramiz
        box = Box.objects.select_related(
            'order__customer',
            'article__model',
            'cutting_batch_item__batch'
        ).filter(id=num).first()
        if box:
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, None, 'BOX_ID', str(num)

        # Ticket ID deb tekshiramiz
        ticket = Ticket.objects.select_related(
            'box__order__customer',
            'box__article__model',
            'box__cutting_batch_item__batch__order_item__article',
            'article_operation__operation'
        ).filter(id=num).first()
        if ticket:
            box = ticket.box
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, ticket, 'TICKET_ID', str(num)

    # 9. BOX: yoki QUTI: prefiksi
    if raw.upper().startswith(('BOX:', 'QUTI:')):
        bx_clean = raw.split(':', 1)[1].strip()
        box = Box.objects.select_related(
            'order__customer',
            'article__model',
            'cutting_batch_item__batch'
        ).filter(box_code__iexact=bx_clean).first()
        if box:
            batch = box.cutting_batch_item.batch if (box.cutting_batch_item and box.cutting_batch_item.batch) else None
            return batch, box, None, 'BOX_CODE', bx_clean

    # 10. Zakaz raqami (masalan: ORD-101, ZAK-001, ORDER:ZAK-001)
    cand_ord_num = raw
    if cand_ord_num.upper().startswith(('ORDER:', 'ZAKAZ:')):
        cand_ord_num = cand_ord_num.split(':', 1)[1].strip()
    cand_order = Order.objects.filter(order_number__iexact=cand_ord_num).first()
    if cand_order:
        b_first = CuttingBatch.objects.select_related(
            'order_item__order__customer',
            'order_item__article__model'
        ).filter(order_item__order=cand_order).first()
        bx_first = cand_order.boxes.first()
        return b_first, bx_first, None, 'ORDER_NUMBER', cand_order.order_number

    return None, None, None, 'UNKNOWN', raw


def verify_pastal_for_sewing(raw_code: str) -> dict:
    """
    Asosiy tekshiruv algoritmi:
    Har bir quti va stikerni bittalab tekshiradi:
    1. Zakaz holati (CANCELLED, ARCHIVED, DRAFT, COMPLETED)
    2. Atmen bo'lgan/bekor qilingan qutilar yoki stikerlar
    3. Meto bo'limi holati (CUT_ENTERED qaytarilganlar)
    4. Operatsiya narxlari va summalar to'liqligi
    5. Tikuvga berishga ruxsat
    """
    clean_input = str(raw_code or '').strip('`"\' \t\r\n')
    if not clean_input:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'EMPTY_INPUT',
            'title': "⚠️ Kod kiritilmadi",
            'message': (
                "⚠️ *Iltimos, QR kodni yoki stiker kodini yuboring!*\n\n"
                "Siz pasportdagi QR kodni, qutidagi stikerni rasmga olib yuborishingiz "
                "yoki uning kodini (masalan: `PASTAL:12` yoki `#489DA37F`) matn sifatida yozishingiz mumkin."
            ),
            'details': {}
        }

    batch, scanned_box, scanned_ticket, res_type, ref_code = resolve_code_to_entity(clean_input)

    # --------------------------------------------------------------------------
    # 1. AGAR TOPILMASA: Bekor qilingan jurnaldan tekshirish
    # --------------------------------------------------------------------------
    if not batch and not scanned_box and not scanned_ticket:
        # Batch ID bo'lishi mumkinligini tekshirish
        batch_id_match = re.search(r'\d+', clean_input)
        if batch_id_match:
            cand_id = int(batch_id_match.group(0))
            cancelled_log = CancelledBatchLog.objects.filter(batch_id=cand_id).first()
            if cancelled_log:
                return {
                    'success': False,
                    'can_release': False,
                    'status_code': 'CANCELLED',
                    'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                    'message': (
                        f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ *USHBU PASTAL KESIMDA BEKOR QILINIB (ATMEN), O'CHIRILGAN!*\n\n"
                        f"📋 *Zakaz:* {cancelled_log.order_number}\n"
                        f"👕 *Model:* {cancelled_log.article_code}\n"
                        f"🏷 *Pastal kodi:* {cancelled_log.pastal_code or '—'}\n"
                        f"✂️ *Kesim ID:* #{cancelled_log.batch_id}\n"
                        f"📅 *Bekor qilingan vaqti:* {cancelled_log.cancelled_at.strftime('%d.%m.%Y %H:%M')}\n"
                        f"👤 *Bekor qiluvchi:* {cancelled_log.cancelled_by.username if cancelled_log.cancelled_by else 'Admin'}\n\n"
                        f"❗ *Ushbu partiyaning barcha qog'oz pasport va stikerlari o'z kuchini yo'qotgan!*\n"
                        f"Tikuv patogiga kiritish qat'iyan man etiladi.\n\n"
                        f"🔄 Yangilash uchun *stikerchiga murojaat qiling*."
                    ),
                    'details': {'cancelled_log_id': cancelled_log.id}
                }

        # Umumiy topilmadi xabari
        return {
            'success': False,
            'can_release': False,
            'status_code': 'NOT_FOUND',
            'title': "❌ Bunday kod topilmadi",
            'message': (
                f"❌ *Bunday kod tizimda topilmadi:* `{clean_input}`\n\n"
                f"Iltimos, pasportdagi QR kodni yoki qutidagi stikerni to'g'ri va aniq skanerlang.\n"
                f"Agar bu eski ish bo'lsa, stikerchiga murojaat qiling."
            ),
            'details': {}
        }

    # --------------------------------------------------------------------------
    # 2. ZAKAZ HOLATI TEKSHIRUVI (Menejer / Admin tomonidan bekor qilinganmi?)
    # --------------------------------------------------------------------------
    order = None
    if batch and batch.order_item and batch.order_item.order:
        order = batch.order_item.order
    elif scanned_box and scanned_box.order:
        order = scanned_box.order
    elif scanned_ticket and scanned_ticket.box and scanned_ticket.box.order:
        order = scanned_ticket.box.order

    if order:
        if order.status == Order.Status.CANCELLED:
            return {
                'success': False,
                'can_release': False,
                'status_code': 'ORDER_CANCELLED',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU ZAKAZ TIZIMDA BEKOR QILINGAN (ATMEN BO'LGAN)!*\n\n"
                    f"📋 *Zakaz:* {order.order_number}\n"
                    f"👤 *Mijoz:* {order.customer.name if (order.customer and hasattr(order.customer, 'name')) else getattr(order, 'client_name', '—')}\n"
                    f"❌ *Holati:* BEKOR QILINDI (CANCELLED)\n\n"
                    f"❗ *Ushbu zakazga tegishli barcha partiya va stikerlar o'z kuchini yo'qotgan!*\n"
                    f"Tikuv patogiga tarqatish qat'iyan man etiladi.\n\n"
                    f"🔄 Yangilash yoki ma'lumot olish uchun *menejer yoki stikerchiga murojaat qiling*."
                ),
                'details': {'order_id': order.id, 'order_status': order.status}
            }

        if order.status == Order.Status.ARCHIVED:
            return {
                'success': False,
                'can_release': False,
                'status_code': 'ORDER_ARCHIVED',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU ZAKAZ ARXIVLANGAN (YOPILGAN)!*\n\n"
                    f"📋 *Zakaz:* {order.order_number}\n"
                    f"📁 *Holati:* ARXIVLANGAN (ARCHIVED)\n\n"
                    f"❗ *Eski yoki arxivlangan zakaz stikerlarini tikuvga kiritish taqiqlanadi!*\n\n"
                    f"🔄 Ma'lumot olish uchun *menejerga murojaat qiling*."
                ),
                'details': {'order_id': order.id, 'order_status': order.status}
            }

        if order.status == Order.Status.COMPLETED:
            return {
                'success': False,
                'can_release': False,
                'status_code': 'ORDER_COMPLETED',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU ZAKAZ ALLAQACHON TUGATILGAN (COMPLETED)!*\n\n"
                    f"📋 *Zakaz:* {order.order_number}\n"
                    f"🏁 *Holati:* TUGATILDI (COMPLETED)\n\n"
                    f"❗ *Bajarilgan zakaz stikerlarini qaytadan tikuv patogiga tarqatish taqiqlanadi!*\n\n"
                    f"🔄 Ma'lumot olish uchun *menejerga murojaat qiling*."
                ),
                'details': {'order_id': order.id, 'order_status': order.status}
            }

        if order.status == Order.Status.DRAFT:
            return {
                'success': False,
                'can_release': False,
                'status_code': 'ORDER_DRAFT',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU ZAKAZ HALI TAYYORLANMOQDA (DRAFT / QORALAMA)!*\n\n"
                    f"📋 *Zakaz:* {order.order_number}\n"
                    f"📝 *Holati:* DRAFT (Tasdiqlanmagan)\n\n"
                    f"❗ *Ushbu zakazga ishlab chiqarishga hali ruxsat berilmagan!*"
                ),
                'details': {'order_id': order.id, 'order_status': order.status}
            }

    # --------------------------------------------------------------------------
    # 3. AGAR SKANERLANGAN ALOHIDA QUTI YOKI TICKET BEKOR QILINGAN / MUZLATILGAN BO'LSA
    # --------------------------------------------------------------------------
    if scanned_box and scanned_box.status == Box.Status.CANCELLED:
        # Ushbu razmer bo'yicha yangi to'g'ri aktiv qutilarni topish
        active_replacement_boxes = []
        if scanned_box.cutting_batch_item:
            active_replacement_boxes = list(scanned_box.cutting_batch_item.boxes.exclude(status=Box.Status.CANCELLED).order_by('box_number'))
        elif scanned_box.order and scanned_box.pastal_number and scanned_box.razmer:
            active_replacement_boxes = list(Box.objects.filter(
                order=scanned_box.order,
                pastal_number=scanned_box.pastal_number,
                razmer=scanned_box.razmer
            ).exclude(status=Box.Status.CANCELLED).order_by('box_number'))

        replacement_info = ""
        if active_replacement_boxes:
            box_range_str = f"#{active_replacement_boxes[0].box_number} dan #{active_replacement_boxes[-1].box_number} gacha" if len(active_replacement_boxes) > 1 else f"#{active_replacement_boxes[0].box_number}"
            replacement_info = (
                f"\n👉 *Ushbu razmer ({scanned_box.razmer}) uchun tizimdagi YANGI TO'G'RI QUTILAR:*\n"
                f"• {box_range_str} (jami {len(active_replacement_boxes)} ta quti)\n"
            )

        return {
            'success': False,
            'can_release': False,
            'status_code': 'CANCELLED',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *USHBU QUTI / STIKER TIZIMDA BEKOR QILINGAN (ATMEN BO'LGAN)!*\n\n"
                f"📦 *Quti:* #{scanned_box.box_number} ({scanned_box.display_code}) | {scanned_box.quantity} dona\n"
                f"📏 *Razmer:* {scanned_box.razmer or '—'}\n"
                f"📋 *Zakaz:* {scanned_box.order.order_number if scanned_box.order else '—'}\n"
                f"🏷 *Pastal:* {scanned_box.pastal_number or '—'}\n"
                f"❌ *Holati:* BEKOR QILINDI (CANCELLED)\n\n"
                f"❗ *Sababi:* Ushbu razmer Meto yoki Kesim bo'limida qayta taqsimlangan. Qo'lingizdagi qog'oz stiker eskirgan va bekor bo'lgan! Tikuvchilar buni ekranda ura olmaydi.\n"
                f"{replacement_info}\n"
                f"🔄 *NIMA QILISH KERAK:*\n"
                f"Ushbu eski stikerni tikuvga bermang! Stikerchiga murojaat qilib, yuqoridagi yangi to'g'ri qutilar stikerlarini oling!"
            ),
            'details': {'box_id': scanned_box.id, 'status': scanned_box.status}
        }

    if scanned_ticket:
        if scanned_ticket.status == Ticket.Status.CANCELLED or (scanned_ticket.box and scanned_ticket.box.status == Box.Status.CANCELLED):
            bx = scanned_ticket.box
            active_replacement_boxes = []
            if bx and bx.cutting_batch_item:
                active_replacement_boxes = list(bx.cutting_batch_item.boxes.exclude(status=Box.Status.CANCELLED).order_by('box_number'))

            replacement_info = ""
            if active_replacement_boxes:
                box_range_str = f"#{active_replacement_boxes[0].box_number} dan #{active_replacement_boxes[-1].box_number} gacha" if len(active_replacement_boxes) > 1 else f"#{active_replacement_boxes[0].box_number}"
                replacement_info = (
                    f"\n👉 *Ushbu razmer ({bx.razmer if bx else '—'}) uchun tizimdagi YANGI TO'G'RI QUTILAR:*\n"
                    f"• {box_range_str} (jami {len(active_replacement_boxes)} ta quti)\n"
                )

            return {
                'success': False,
                'can_release': False,
                'status_code': 'CANCELLED',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU STIKER BEKOR QILINGAN (ATMEN BO'LGAN / ESKIRGAN)!*\n\n"
                    f"🎫 *Stiker ID:* #{scanned_ticket.stiker_code or scanned_ticket.id}\n"
                    f"⚙️ *Operatsiya:* {scanned_ticket.article_operation.operation.name if scanned_ticket.article_operation else '—'}\n"
                    f"📦 *Quti:* #{bx.box_number if bx else '—'} ({bx.display_code if bx else '—'})\n"
                    f"📏 *Razmer:* {bx.razmer if bx else '—'}\n"
                    f"❌ *Holati:* BEKOR QILINDI (CANCELLED)\n\n"
                    f"❗ *Ushbu stiker bekor qilingan! Tikuv patogiga tarqatish taqiqlanadi.*\n"
                    f"{replacement_info}\n"
                    f"🔄 *NIMA QILISH KERAK:*\n"
                    f"Stikerchiga murojaat qiling va faqat yangi stikerlarni chiqarib oling!"
                ),
                'details': {'ticket_id': scanned_ticket.id, 'status': scanned_ticket.status}
            }

        if getattr(scanned_ticket, 'is_frozen', False):
            return {
                'success': False,
                'can_release': False,
                'status_code': 'FROZEN_TICKET',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU STIKER MUZLATILGAN (QULFLANGAN)!*\n\n"
                    f"🎫 *Stiker ID:* #{scanned_ticket.stiker_code or scanned_ticket.id}\n"
                    f"📦 *Quti:* {scanned_ticket.box.display_code if scanned_ticket.box else '—'}\n"
                    f"🔒 *Holati:* MUZLATILGAN (IS_FROZEN)\n\n"
                    f"❗ *Ushbu stiker bo'yicha hisob-kitoblar vaqtinchalik to'xtatilgan!*\n"
                    f"Tikuv patogiga tarqatish taqiqlanadi.\n\n"
                    f"🔄 Ma'lumot olish uchun *menejerga murojaat qiling*."
                ),
                'details': {'ticket_id': scanned_ticket.id}
            }

    # --------------------------------------------------------------------------
    # 4. METO HOLATI TEKSHIRUVI (Meto tasdiqlaganmi yoki qaytarilganmi?)
    # --------------------------------------------------------------------------
    if batch:
        batch_items = list(batch.items.all())
        if batch_items and all(bi.status == CuttingBatchItem.Status.CUT_ENTERED for bi in batch_items):
            return {
                'success': False,
                'can_release': False,
                'status_code': 'METO_NOT_CONFIRMED',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU PASTAL METO TOMONIDAN TASDIQLANMAGAN (YOKI QAYTARILGAN)!*\n\n"
                    f"📋 *Pastal:* {batch.name or '—'} ({batch.pastal_code or '—'})\n"
                    f"✂️ *Holati:* Kesim kiritilgan, lekin Meto hali qutilarni tasdiqlamagan yoki qaytargan.\n\n"
                    f"❗ *Tikuv patogiga tarqatish taqiqlanadi!*\n\n"
                    f"🔄 Yangilash uchun *Meto bo'limiga yoki stikerchiga murojaat qiling*."
                ),
                'details': {'batch_id': batch.id}
            }

    # --------------------------------------------------------------------------
    # 5. PASTALNI VA BARCHA QUTILARNI TO'PLASH
    # --------------------------------------------------------------------------
    if not batch and scanned_box:
        if scanned_box.cutting_batch_item and scanned_box.cutting_batch_item.batch:
            batch = scanned_box.cutting_batch_item.batch
        elif scanned_box.pastal_number:
            batch = CuttingBatch.objects.select_related(
                'order_item__order__customer',
                'order_item__article__model'
            ).filter(
                order_item__order=scanned_box.order,
                order_item__article=scanned_box.target_article,
                pastal_code=scanned_box.pastal_number
            ).first()

    # Barcha tegishli qutilarni olish
    boxes_query = Q()
    if batch:
        boxes_query |= Q(cutting_batch_item__batch=batch)
        if batch.pastal_code:
            boxes_query |= Q(
                order=batch.order_item.order,
                article=batch.order_item.article,
                pastal_number=batch.pastal_code
            )
    elif scanned_box:
        if scanned_box.pastal_number and scanned_box.order:
            boxes_query = Q(
                order=scanned_box.order,
                article=scanned_box.target_article,
                pastal_number=scanned_box.pastal_number
            )
        else:
            boxes_query = Q(id=scanned_box.id)
    elif scanned_ticket and scanned_ticket.box:
        if scanned_ticket.box.pastal_number and scanned_ticket.box.order:
            boxes_query = Q(
                order=scanned_ticket.box.order,
                article=scanned_ticket.box.target_article,
                pastal_number=scanned_ticket.box.pastal_number
            )
        else:
            boxes_query = Q(id=scanned_ticket.box_id)

    all_related_boxes = list(Box.objects.filter(boxes_query).distinct().order_by('box_number'))

    # Qutilar umuman topilmasa
    if not all_related_boxes:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'NO_BOXES',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *Ushbu pastal bo'yicha qutilar hali Meto bo'limi tomonidan yaratilmagan!*\n\n"
                f"📋 *Pastal:* {batch.name if batch else '—'} ({batch.pastal_code if batch else '—'})\n"
                f"Meto bo'limi sonlarni tasdiqlab, qutilarga ajratishi kerak.\n\n"
                f"🔄 Yangilash uchun *stikerchiga yoki Metoga murojaat qiling*."
            ),
            'details': {}
        }

    # --------------------------------------------------------------------------
    # 6. CHUQUR TEKSHIRUV: HAR BIR QUTI VA STIKERNI BITTALAB TEKSHIRISH
    # --------------------------------------------------------------------------
    cancelled_boxes = [b for b in all_related_boxes if b.status == Box.Status.CANCELLED]
    active_boxes = [b for b in all_related_boxes if b.status != Box.Status.CANCELLED]

    # Model va Zakaz ma'lumotlari
    order = batch.order_item.order if (batch and batch.order_item) else (scanned_box.order if scanned_box else all_related_boxes[0].order)
    customer_name = order.customer.name if (order and order.customer and hasattr(order.customer, 'name')) else getattr(order, 'client_name', '—')
    article = (batch.order_item.article if (batch and batch.order_item) else None) or (scanned_box.target_article if scanned_box else all_related_boxes[0].target_article)
    pastal_code = (batch.pastal_code if batch else None) or (scanned_box.pastal_number if scanned_box else all_related_boxes[0].pastal_number) or '—'
    cutter_name = batch.cutter_name if (batch and batch.cutter_name) else '—'
    batch_num = batch.batch_number if batch else '—'

    # Razmerlar bo'yicha tahlil tayyorlash
    size_reports = []
    problematic_sizes = []
    if batch:
        for item in batch.items.all().order_by('order_item_size__id'):
            s_name = item.order_item_size.size_name
            c_b = list(item.boxes.filter(status=Box.Status.CANCELLED).order_by('box_number'))
            a_b = list(item.boxes.exclude(status=Box.Status.CANCELLED).order_by('box_number'))

            if c_b:
                problematic_sizes.append(s_name)
                c_range = f"#{c_b[0].box_number} – #{c_b[-1].box_number}" if len(c_b) > 1 else f"#{c_b[0].box_number}"
                a_range = f"#{a_b[0].box_number} – #{a_b[-1].box_number}" if len(a_b) > 1 else (f"#{a_b[0].box_number}" if a_b else "yo'q")
                size_reports.append(
                    f"❌ *Razmer {s_name}* ({item.quantity} dona):\n"
                    f"   • Eski atmen qutilar: {c_range} ({len(c_b)} ta quti ATMEN ❌)\n"
                    f"   • Yangi to'g'ri qutilar: {a_range} ({len(a_b)} ta quti ✅)"
                )
            else:
                a_range = f"#{a_b[0].box_number} – #{a_b[-1].box_number}" if len(a_b) > 1 else (f"#{a_b[0].box_number}" if a_b else "yo'q")
                size_reports.append(
                    f"✅ *Razmer {s_name}* ({item.quantity} dona, {len(a_b)} ta quti):\n"
                    f"   • Qutilar: {a_range} (Hammasi to'g'ri ✅)"
                )

    # 6.A: AGAR BARCHA QUTILAR BEKOR BO'LGAN BO'LSA
    if len(active_boxes) == 0 and len(cancelled_boxes) > 0:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'CANCELLED',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *USHBU ISH TO'LIQ ATMEN BO'LGAN (Bekor qilingan)!*\n\n"
                f"📋 *Zakaz:* {order.order_number} ({customer_name})\n"
                f"👕 *Model:* {article.name if article else '—'} ({article.code if article else '—'})\n"
                f"🏷 *Pastal kodi:* {pastal_code}\n\n"
                f"Tizimda ushbu pastalning barcha {len(cancelled_boxes)} ta qutisi bekor qilingan.\n"
                f"❗ *Eski stikerlarni tikuv patogiga berish qat'iyan man etiladi!*\n\n"
                f"🔄 Yangilash uchun *stikerchiga (meto/kesimga) murojaat qiling*."
            ),
            'details': {'cancelled_boxes_count': len(cancelled_boxes)}
        }

    # 6.B: AGAR FOYDALANUVCHI ALOHIDA AKTIV QUTI / STIKERNI SKANERLAGAN BO'LSA
    is_single_box_scan = (scanned_box is not None and res_type in ['BOX_CODE', 'TICKET_CODE', 'STIKER_CODE', 'BOX_ID', 'TICKET_ID'])
    if is_single_box_scan and scanned_box.status != Box.Status.CANCELLED:
        # Alohida aktiv qutining stikerlari narxini tekshirish
        single_box_tickets = list(scanned_box.tickets.select_related('article_operation__operation').all())
        box_zero_prices = [t for t in single_box_tickets if t.price_per_unit is None or t.price_per_unit <= Decimal('0.00')]
        if box_zero_prices:
            return {
                'success': False,
                'can_release': False,
                'status_code': 'MISSING_PRICE',
                'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
                'message': (
                    f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚠️ *USHBU QUTIDA NARX VA SUMMALAR KIRITILMAGAN!*\n\n"
                    f"📦 *Quti:* #{scanned_box.box_number} ({scanned_box.display_code})\n"
                    f"📏 *Razmer:* {scanned_box.razmer}\n"
                    f"❗ Tikuvchilarga ish haqi yozilishi uchun narxlar kiritilishi shart!\n\n"
                    f"🔄 Stikerchi yoki texnologga murojaat qiling."
                ),
                'details': {'box_id': scanned_box.id}
            }

        box_total_val = sum((t.total_amount or Decimal('0.00')) for t in single_box_tickets)
        re_split_note = ""
        if len(cancelled_boxes) > 0:
            re_split_note = (
                f"\nℹ️ *Eslatma:* Ushbu pastalning ba'zi boshqa razmerlarida qayta taqsimlangan eski qutilar bor. "
                f"Lekin siz skanerlagan #{scanned_box.box_number}-quti — YANGI VA TO'G'RI ✅.\n"
            )

        return {
            'success': True,
            'can_release': True,
            'status_code': 'APPROVED',
            'title': "✅ PATOKKA BERISH MUMKIN!",
            'message': (
                f"✅ *USHBU QUTI VA STIKER TO'G'RI: AKTIV!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 *Quti:* #{scanned_box.box_number} ({scanned_box.display_code}) | {scanned_box.quantity} dona\n"
                f"📏 *Razmer:* {scanned_box.razmer}\n"
                f"📋 *Zakaz:* {order.order_number} ({customer_name})\n"
                f"👕 *Model:* {article.name if article else '—'} ({article.code if article else '—'})\n"
                f"🏷 *Pastal:* {pastal_code}\n\n"
                f"💰 *Operatsiyalar va narxlar (100% to'g'ri):*\n"
                f"• Barcha {len(single_box_tickets)} ta operatsiya narxi mavjud ✅\n"
                f"• Qutining umumiy tikuv summasi: {box_total_val:,.0f} UZS\n"
                f"{re_split_note}\n"
                f"🛡 *Holati:* Ushbu quti to'liq AKTIV va tasdiqlangan.\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 *USHBU QUTINI TIKUV PATOGIGA BERISHGA RUXSAT ETILADI!*"
            ).replace(",", " "),
            'details': {
                'box_number': scanned_box.box_number,
                'box_code': scanned_box.box_code,
                'razmer': scanned_box.razmer,
                'quantity': scanned_box.quantity
            }
        }

    # 6.C: PASPORT SKANERLANGANDA VA PASTALDA BEKOR BO'LGAN QUTILAR MAVJUD BO'LSA
    if len(cancelled_boxes) > 0:
        sizes_str = "\n\n".join(size_reports) if size_reports else f"• Bekor bo'lgan qutilar: {len(cancelled_boxes)} ta"
        prob_str = ", ".join(problematic_sizes) if problematic_sizes else "ba'zi razmerlar"
        return {
            'success': False,
            'can_release': False,
            'status_code': 'PARTIALLY_CANCELLED',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI! (Qayta taqsimlangan razmerlar bor)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 *Zakaz:* {order.order_number} ({customer_name})\n"
                f"👕 *Model:* {article.name if article else '—'} ({article.code if article else '—'})\n"
                f"🏷 *Pastal kodi:* {pastal_code} | ✂️ *Kesim:* #{batch_num}\n\n"
                f"📊 *RAZMERLAR BO'YICHA HOLAT:*\n"
                f"{sizes_str}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *DIQQAT: Tizimda {prob_str} bo'yicha eski qutilar bekor qilingan!*\n\n"
                f"👉 *QO'LINGIZDAGI PASPORT VA STIKERLARNI TEKSHIRING:*\n"
                f"1. Agar qo'lingizdagi qog'oz pasportda yuqoridagi *ESKI bekor bo'lgan* qutilar yozilgan bo'lsa — bu ESKI pasport! Eski stikerlarni tikuv patogiga BERMANG ❌!\n"
                f"2. Agar qo'lingizda *YANGI to'g'ri* qutilar bo'lsa — bu to'g'ri ✅!\n"
                f"3. Muammoli razmerlar ({prob_str}) uchun stikerchidan yangi stiker va yangi pasport oling.\n"
                f"4. Razmerlar hal bo'lgach, yangi pasportni botga qayta tashlang. Hammasi to'g'ri bo'lsa darhol yashil ruxsat beriladi!"
            ),
            'details': {
                'cancelled_boxes_count': len(cancelled_boxes),
                'active_boxes_count': len(active_boxes),
                'problematic_sizes': problematic_sizes
            }
        }

    # Barcha aktiv qutilarning biletlarini tekshirish
    all_tickets = list(Ticket.objects.select_related(
        'article_operation__operation',
        'box'
    ).filter(box__in=active_boxes))

    # Agar aktiv qutilar bor, ammo bironta ham stiker yo'q bo'lsa
    if not all_tickets:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'NO_TICKETS',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *Qutilarga hali QR stikerlar va operatsiyalar biriktirilmagan!*\n\n"
                f"Modelda operatsiyalar va narxlar kiritilmagan bo'lishi mumkin.\n\n"
                f"🔄 Stikerlar chiqarish uchun *stikerchiga murojaat qiling*."
            ),
            'details': {}
        }

    # Biletlar orasida CANCELLED (atmen bo'lganlari) bormi?
    cancelled_tickets = [t for t in all_tickets if t.status == Ticket.Status.CANCELLED]
    if len(cancelled_tickets) > 0:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'CANCELLED',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *USHBU PASTALDAGI {len(cancelled_tickets)} TA STIKER BEKOR QILINGAN (ATMEN BO'LGAN)!*\n\n"
                f"Qutilar qaytadan bo'lingan yoki o'zgartirilgan. Eski stikerlar o'z kuchini yo'qotgan.\n"
                f"❗ Tikuv patogiga tarqatish taqiqlanadi!\n\n"
                f"🔄 Yangilash uchun *stikerchiga murojaat qiling*."
            ),
            'details': {'cancelled_tickets_count': len(cancelled_tickets)}
        }

    # Biletlar orasida muzlatilganlari bormi?
    frozen_tickets = [t for t in all_tickets if getattr(t, 'is_frozen', False)]
    if len(frozen_tickets) > 0:
        return {
            'success': False,
            'can_release': False,
            'status_code': 'FROZEN_TICKETS',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *USHBU PASTALDA {len(frozen_tickets)} TA STIKER MUZLATILGAN (QULFLANGAN)!*\n\n"
                f"Biletlar ma'muriyat tomonidan muzlatilgan (salary freeze yoki nazoratda).\n"
                f"❗ *Patokka berish qat'iyan man etiladi!*\n\n"
                f"🔄 Ma'lumot olish uchun *menejerga murojaat qiling*."
            ),
            'details': {'frozen_tickets_count': len(frozen_tickets)}
        }

    # --------------------------------------------------------------------------
    # 5. NARXLAR VA SUMMALAR TEKSHIRUVI (Foydalanuvchi talabi: "AGAR SUMMA KELSA")
    # --------------------------------------------------------------------------
    unpriced_operations = set()
    zero_amount_tickets = []
    total_batch_amount = Decimal('0.00')
    total_tickets_count = len(all_tickets)
    unit_total_price = Decimal('0.00')

    # Model bo'yicha 1 dona mahsulotning umumiy tikuv narxini hisoblash
    first_box = active_boxes[0]
    target_art = first_box.target_article
    if target_art:
        art_ops = ArticleOperation.objects.filter(article=target_art)
        unit_total_price = sum((op.price_per_unit or Decimal('0.00')) for op in art_ops)

    for tkt in all_tickets:
        # Dona narxi bormi?
        if tkt.price_per_unit is None or tkt.price_per_unit <= Decimal('0.00'):
            op_name = tkt.article_operation.operation.name if (tkt.article_operation and tkt.article_operation.operation) else "Noma'lum operatsiya"
            unpriced_operations.add(op_name)

        # Jami summa hisoblanganmi?
        if tkt.total_amount is None or tkt.total_amount <= Decimal('0.00'):
            zero_amount_tickets.append(tkt)
        else:
            total_batch_amount += tkt.total_amount

    # Agar biron-bir operatsiya narxi yoki summa 0 bo'lsa -> BLOKLANADI!
    if unpriced_operations or zero_amount_tickets:
        op_list_str = "\n".join([f"• {op}" for op in sorted(unpriced_operations)])
        return {
            'success': False,
            'can_release': False,
            'status_code': 'MISSING_PRICE',
            'title': "🚫 PATOKKA BERIB BO'LMAYDI!",
            'message': (
                f"🚫 *PATOKKA BERIB BO'LMAYDI!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ *OPERATSIYA NARXLARI VA SUMMASI BELGILANMAGAN!*\n\n"
                f"📋 *Model:* {target_art.name if target_art else '—'} ({target_art.code if target_art else '—'})\n"
                f"📦 *Qutilar soni:* {len(active_boxes)} ta\n\n"
                f"❌ *Narxi 0 so'm bo'lgan operatsiyalar:*\n"
                f"{op_list_str if op_list_str else '• Biletlar summasi 0 UZS'}\n\n"
                f"❗ *Narxi kiritilmagan stikerlar bilan tikuvga berilsa, tikuvchilarga ish haqi yozilmaydi!*\n"
                f"Tikuv patogiga tarqatish qat'iyan man etiladi.\n\n"
                f"🔄 Narxlarni to'g'irlash uchun *stikerchi yoki texnologga murojaat qiling*."
            ),
            'details': {
                'unpriced_operations': list(unpriced_operations),
                'zero_amount_count': len(zero_amount_tickets)
            }
        }

    # --------------------------------------------------------------------------
    # 6. SKANERLANISH HOLATI (ALLAQACHON TIKUVDA BO'LSA OGOHLANTIRISH)
    # --------------------------------------------------------------------------
    scanned_tickets_count = sum(1 for t in all_tickets if t.status == Ticket.Status.SCANNED)
    pending_tickets_count = sum(1 for t in all_tickets if t.status == Ticket.Status.PENDING)

    # --------------------------------------------------------------------------
    # 7. HAMMASI 100% TO'G'RI: PATOKKA BERISH MUMKIN! (APPROVED)
    # --------------------------------------------------------------------------
    order = batch.order_item.order if batch else first_box.order
    customer_name = order.customer.name if (order.customer and hasattr(order.customer, 'name')) else getattr(order, 'client_name', '—')
    article = target_art or (batch.order_item.article if batch else first_box.article)
    pastal_code = batch.pastal_code if batch else (first_box.pastal_number or '—')
    cutter_name = batch.cutter_name if (batch and batch.cutter_name) else '—'
    batch_num = batch.batch_number if batch else '—'

    total_units = sum(b.quantity for b in active_boxes)
    total_boxes_count = len(active_boxes)

    # Razmerlar bo'yicha taqsimot
    size_stats = {}
    for b in active_boxes:
        s_name = b.razmer or "Noma'lum"
        if s_name not in size_stats:
            size_stats[s_name] = {'boxes': 0, 'qty': 0}
        size_stats[s_name]['boxes'] += 1
        size_stats[s_name]['qty'] += b.quantity

    size_lines = []
    for s_name, data in sorted(size_stats.items()):
        size_lines.append(f"• *{s_name}:* {data['boxes']} ta quti ({data['qty']} dona) — Narxlar to'liq ✅")
    sizes_text = "\n".join(size_lines)

    in_progress_notice = ""
    if scanned_tickets_count > 0:
        in_progress_notice = (
            f"\nℹ️ *Eslatma:* Ushbu pastaldan allaqachon {scanned_tickets_count} ta stiker tikuvda urilgan "
            f"({pending_tickets_count} ta qolgan).\n"
        )

    approved_message = (
        f"✅ *HAMMASI TO'G'RI: PATOKKA BERISH MUMKIN!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 *Zakaz:* {order.order_number} ({customer_name})\n"
        f"👕 *Model:* {article.name if article else '—'} ({article.code if article else '—'})\n"
        f"🏷 *Pastal kodi:* {pastal_code}\n"
        f"✂️ *Kesim:* #{batch_num} | *Bichuvchi:* {cutter_name}\n"
        f"📦 *Jami qutilar:* {total_boxes_count} ta quti\n"
        f"🔢 *Jami mahsulot:* {total_units:,} dona\n\n"
        f"📊 *Razmerlar va qutilar taqsimoti:*\n"
        f"{sizes_text}\n\n"
        f"💰 *Operatsiyalar va narxlar (100% to'g'ri):*\n"
        f"• Tekshirilgan jami stikerlar: {total_tickets_count} ta\n"
        f"• Barcha qutilardagi barcha stikerlar narxi va summasi mavjud ✅\n"
        f"• 1 dona kiyim tikuv qiymati: {unit_total_price:,.0f} UZS\n"
        f"• Partiyaning umumiy tikuv qiymati: {total_batch_amount:,.0f} UZS\n"
        f"{in_progress_notice}\n"
        f"🛡 *Holati:* Barcha {total_boxes_count} ta quti to'liq AKTIV, bekor qilingan (atmen) qutilar yo'q.\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *TIKUV PATOGIGA TARQATISHGA RUXSAT ETILADI!*"
    ).replace(",", " ")

    return {
        'success': True,
        'can_release': True,
        'status_code': 'APPROVED',
        'title': "✅ PATOKKA BERISH MUMKIN!",
        'message': approved_message,
        'details': {
            'order_number': order.order_number,
            'customer_name': customer_name,
            'article_code': article.code if article else '',
            'pastal_code': pastal_code,
            'total_boxes': total_boxes_count,
            'total_units': total_units,
            'total_tickets': total_tickets_count,
            'total_amount': float(total_batch_amount),
            'unit_price': float(unit_total_price),
            'scanned_tickets': scanned_tickets_count,
            'pending_tickets': pending_tickets_count,
        }
    }
