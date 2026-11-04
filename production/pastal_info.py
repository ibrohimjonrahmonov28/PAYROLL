"""
Pastal bo'yicha to'liq ma'lumot — Qutilar statistikasi (/statistics/) sahifasidagi "Pastal bo'yicha qidirish".

Kesim, razmerlar va meto, qutilar va stikerlar, patokka jo'natish va qabul, Uychi yuk xati, tikuv, yakuniy dazmol,
OTK va voqealar tarixi. Faqat o'qiydi: biletlar, stiker va quti kodlariga tegmaydi.
"""
from collections import defaultdict

from django.db.models import Count, Max, Min, Q, Sum

from production.daily_dazmol import main_dazmol_operation_ids
from production.models import Box, BoxQualityInspectionLog, CuttingBatch, CuttingBatchItem, Ticket
from production.services import get_patok_code

BATCH_SELECT = (
    'order_item__order__customer',
    'order_item__article__model',
    'order_item__article__operation_group',
    'created_by',
    'accepted_by',
    'waybill__received_by',
)
# Pasport QR, quti va stiker kodlari aniq bitta pastalni ko'rsatadi
EXACT_RESOLVE_TYPES = ('PASTAL_ID', 'BOX_CODE', 'BOX_ID', 'TICKET_CODE', 'TICKET_ID', 'STIKER_CODE')


def _user_name(user):
    if not user:
        return ''
    return user.get_full_name() or user.username


def find_pastal_batches(raw_code, limit=8):
    """
    Qidiruv matnidan pastal(lar)ni topadi: (topilganlar, taklif qilinganlar).
    - Pastal kodi aniq mos kelsa — shu kodli barcha pastallar (bir xil kodli pijama ustki va shorti — ikkalasi).
    - Pasport QR (PASTAL:12), quti kodi yoki stiker — o'sha qutining pastali.
    - Aks holda o'xshash pastallar taklif qilinadi; bittasi bo'lsa — darhol o'sha.
    """
    from production.passport_bot_service import resolve_code_to_entity, _cutting_batch_candidates_qs

    code = (raw_code or '').strip()
    if not code:
        return [], []

    base = CuttingBatch.objects.select_related(*BATCH_SELECT)
    exact = list(base.filter(pastal_code__iexact=code).order_by('-created_at', '-id'))
    if exact:
        return exact, []

    batch, _box, _ticket, res_type, _clean = resolve_code_to_entity(code)
    if batch and res_type in EXACT_RESOLVE_TYPES:
        return [base.get(id=batch.id)], []

    qs = _cutting_batch_candidates_qs(code)
    candidates = list(qs.select_related(*BATCH_SELECT)[:limit]) if qs is not None else []
    if len(candidates) == 1:
        return candidates, []
    return [], candidates


def _pastal_state(items, active, closed_count, controlled_count, scanned_count, batch):
    """Pastalning hozirgi holati: (yozuv, rang)."""
    if not items or all(it.status == CuttingBatchItem.Status.CUT_ENTERED for it in items):
        return "Meto tasdiqlashi kutilmoqda", 'amber'
    if active and closed_count == len(active):
        return "Yopilgan — barcha qutilar OTK dan o'tgan", 'emerald'
    if controlled_count:
        return "OTK jarayonida", 'cyan'
    if scanned_count:
        return "Tikuvda", 'blue'
    if batch.dispatch_status == CuttingBatch.DispatchStatus.DISPATCHED:
        return "Patokka jo'natilgan — qabul kutilmoqda", 'amber'
    if batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED:
        return "Patok qabul qilgan — tikuv boshlanmagan", 'indigo'
    if any(b.is_printed for b in active):
        return "Stikerlar chop etilgan — patokka berilmagan", 'slate'
    return "Meto tasdiqlagan — stiker chop etilmagan", 'slate'


def build_pastal_info(batch):
    """Bitta pastal (CuttingBatch) haqida kartochka uchun to'liq ma'lumot."""
    order = batch.order_item.order if batch.order_item_id else None
    article = batch.order_item.article if batch.order_item_id else None

    items = list(batch.items.select_related('order_item_size', 'meto_completed_by').order_by('order_item_size__id'))
    boxes = list(Box.objects.filter(cutting_batch_item__batch=batch).order_by('box_number'))
    active = [b for b in boxes if b.status != Box.Status.CANCELLED]
    cancelled = [b for b in boxes if b.status == Box.Status.CANCELLED]
    active_ids = [b.id for b in active]

    # 1. Razmerlar: kesimchi soni, meto soni, qutilar
    boxes_by_item = defaultdict(list)
    for b in active:
        boxes_by_item[b.cutting_batch_item_id].append(b)
    sizes = []
    for it in items:
        it_boxes = boxes_by_item.get(it.id, [])
        sizes.append({
            'name': it.order_item_size.size_name,
            'cut_qty': it.quantity,
            'meto_qty': it.real_quantity,
            'boxes_count': len(it_boxes),
            'boxes_qty': sum(b.quantity for b in it_boxes),
            'status': it.status,
            'status_display': it.get_status_display(),
            'meto_worker': it.meto_worker_name or _user_name(it.meto_completed_by),
            'meto_completed_at': it.meto_completed_at,
        })

    printed_times = [b.printed_at for b in active if b.printed_at]
    totals = {
        'cut': sum(it.quantity for it in items),
        'meto': sum(it.effective_quantity for it in items),
        'boxed': sum(b.quantity for b in active),
        'boxes': len(active),
        'cancelled_boxes': len(cancelled),
        'printed_boxes': sum(1 for b in active if b.is_printed),
        'first_printed_at': min(printed_times) if printed_times else None,
        'last_printed_at': max(printed_times) if printed_times else None,
        'first_box_at': min(b.created_at for b in boxes) if boxes else None,
    }

    # 2. Tikuv: faol qutilardagi bekor qilinmagan biletlar
    tickets = Ticket.objects.filter(box_id__in=active_ids).exclude(status=Ticket.Status.CANCELLED)
    scanned_q = Q(status=Ticket.Status.SCANNED)
    t = tickets.aggregate(
        total=Count('id'),
        scanned=Count('id', filter=scanned_q),
        first=Min('scanned_at', filter=scanned_q),
        last=Max('scanned_at', filter=scanned_q),
        workers=Count('worker', filter=scanned_q, distinct=True),
        earned=Sum('total_amount', filter=scanned_q),
    )
    by_patok = [
        {
            'code': get_patok_code(r['screen_number']) if r['screen_number'] else '—',
            'tickets': r['n'],
            'pct': round(r['n'] * 100 / t['scanned']) if t['scanned'] else 0,
        }
        for r in tickets.filter(scanned_q).values('screen_number').annotate(n=Count('id')).order_by('-n')
    ]
    sewing = {
        'tickets_total': t['total'] or 0,
        'tickets_scanned': t['scanned'] or 0,
        'progress_pct': round((t['scanned'] or 0) * 100 / t['total']) if t['total'] else 0,
        'first_scan': t['first'],
        'last_scan': t['last'],
        'workers': t['workers'] or 0,
        'earned': int(t['earned'] or 0),
        'earned_display': f"{int(t['earned'] or 0):,}".replace(',', ' '),
        'by_patok': by_patok,
    }

    # 3. Yakuniy dazmol (faqat aynan "DAZMOL" operatsiyasi)
    dz = tickets.filter(scanned_q, article_operation__operation_id__in=main_dazmol_operation_ids())
    d = dz.aggregate(qty=Sum('quantity'), first=Min('scanned_at'), last=Max('scanned_at'))
    dazmol = {
        'qty': d['qty'] or 0,
        'first_at': d['first'],
        'last_at': d['last'],
        'by_patok': [
            {'code': get_patok_code(r['screen_number']) if r['screen_number'] else '—', 'qty': r['q']}
            for r in dz.values('screen_number').annotate(q=Sum('quantity')).order_by('-q')
        ],
    }

    # 4. OTK
    controlled = [b for b in active if b.controlled_at]
    closed = [b for b in active if b.is_controlled and b.controlled_repair_qty == 0]
    logs = BoxQualityInspectionLog.objects.filter(box_id__in=active_ids)
    lg = logs.aggregate(n=Count('id'), first=Min('created_at'), last=Max('created_at'), inspectors=Count('inspector', distinct=True))
    otk = {
        'controlled_boxes': len(controlled),
        'closed_boxes': len(closed),
        'repair_boxes': sum(1 for b in active if b.controlled_repair_qty > 0),
        'first_sort': sum(b.controlled_first_sort_qty for b in active),
        'second_sort': sum(b.controlled_second_sort_qty for b in active),
        'repair_now': sum(b.controlled_repair_qty for b in active),
        'defect': sum(b.controlled_defect_qty for b in active),
        'inspections': lg['n'] or 0,
        'inspectors': lg['inspectors'] or 0,
        'first_at': lg['first'],
        'last_at': lg['last'],
    }

    # 5. Patokka jo'natish va qabul, Uychi yuk xati
    dispatch = {
        'status': batch.dispatch_status,
        'status_display': batch.get_dispatch_status_display(),
        'branch_display': batch.get_assigned_branch_display() if batch.assigned_branch else '',
        'patok_code': get_patok_code(batch.assigned_screen) if batch.assigned_screen else '',
        'dispatched_at': batch.dispatched_at,
        'dispatched_by': batch.dispatched_by,
        'accepted_at': batch.accepted_at,
        'accepted_by': _user_name(batch.accepted_by),
        'rejected_at': batch.rejected_at,
        'rejection_reason': batch.rejection_reason,
        'received_boxes': sum(1 for b in active if b.is_received_by_patok),
    }
    waybill = None
    if batch.waybill_id:
        wb = batch.waybill
        waybill = {
            'number': wb.waybill_number,
            'status_display': wb.get_status_display(),
            'route': f"{wb.get_source_branch_display()} → {wb.get_destination_branch_display()}",
            'created_at': wb.created_at,
            'dispatched_at': wb.dispatched_at,
            'received_at': wb.received_at,
            'received_by': _user_name(wb.received_by),
        }

    state_label, state_tone = _pastal_state(items, active, len(closed), len(controlled), sewing['tickets_scanned'], batch)

    # 6. Voqealar tarixi (vaqti bor bosqichlar, vaqt bo'yicha)
    events = [(batch.created_at, "Kesim kiritildi", batch.cutter_name or _user_name(batch.created_by))]
    meto_times = [s['meto_completed_at'] for s in sizes if s['meto_completed_at']]
    if meto_times:
        events.append((max(meto_times), "Meto tasdiqladi", f"{len(meto_times)} ta razmer"))
    if totals['first_box_at']:
        events.append((totals['first_box_at'], "Qutilar yaratildi", f"{len(boxes)} ta quti"))
    if totals['first_printed_at']:
        events.append((totals['first_printed_at'], "Stikerlar chop etildi", f"{totals['printed_boxes']} ta quti"))
    if waybill and waybill['dispatched_at']:
        events.append((waybill['dispatched_at'], "Yuk xati bilan jo'natildi", f"{waybill['number']} · {waybill['route']}"))
    if waybill and waybill['received_at']:
        events.append((waybill['received_at'], "Uychi ombori qabul qildi", waybill['received_by']))
    if batch.dispatched_at:
        events.append((batch.dispatched_at, "Patokka jo'natildi", f"{dispatch['patok_code']} · {batch.dispatched_by}".strip(' ·')))
    if batch.accepted_at:
        events.append((batch.accepted_at, "Patok qabul qildi", f"{dispatch['patok_code']} · {dispatch['accepted_by']}".strip(' ·')))
    if batch.rejected_at:
        events.append((batch.rejected_at, "Master qaytardi", batch.rejection_reason))
    if sewing['first_scan']:
        events.append((sewing['first_scan'], "Birinchi stiker urildi", ''))
    if dazmol['first_at']:
        events.append((dazmol['first_at'], "Birinchi dazmol", ''))
    if otk['first_at']:
        events.append((otk['first_at'], "Birinchi OTK tekshiruvi", ''))
    if sewing['last_scan'] and sewing['last_scan'] != sewing['first_scan']:
        events.append((sewing['last_scan'], "Oxirgi stiker urildi", ''))
    if active and len(closed) == len(active):
        last_close = max((b.controlled_at for b in closed if b.controlled_at), default=None)
        if last_close:
            events.append((last_close, "Pastal yopildi", "barcha qutilar OTK dan o'tdi"))
    timeline = [{'at': at, 'title': title, 'detail': detail} for at, title, detail in sorted(events, key=lambda e: e[0])]

    image_url = ''
    if article and getattr(article, 'image', None):
        try:
            image_url = article.image.url
        except Exception:
            image_url = ''

    return {
        'batch_id': batch.id,
        'pastal_code': batch.pastal_code or str(batch.batch_number),
        'batch_number': batch.batch_number,
        'name': batch.name,
        'order': {
            'number': order.order_number if order else '—',
            'customer': (order.customer.name if order and order.customer else (order.client_name if order else '')) or '',
            'status_display': order.get_status_display() if order else '',
        },
        'article': {
            'code': article.code if article else '',
            'name': article.name if article else '',
            'model_name': article.model.name if article and article.model_id else '',
            'template_name': article.operation_group.name if article and article.operation_group_id else '',
            'image_url': image_url,
        },
        'cutter_name': batch.cutter_name,
        'created_at': batch.created_at,
        'created_by': _user_name(batch.created_by),
        'partiya_number': batch.partiya_number,
        'fabric_batch_code': batch.fabric_batch_code,
        'fabric_weight_kg': batch.fabric_weight_kg,
        'actual_weight_kg': batch.actual_weight_kg,
        'notes': batch.notes,
        'sizes': sizes,
        'totals': totals,
        'sewing': sewing,
        'dazmol': dazmol,
        'otk': otk,
        'dispatch': dispatch,
        'waybill': waybill,
        'state_label': state_label,
        'state_tone': state_tone,
        'timeline': timeline,
    }
