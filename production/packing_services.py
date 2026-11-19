"""
Beta qadoqlash tartibi (egasi talabi 2026-11-12; ta'mir cheklari aylanma bo'yicha — 2026-11-14).

Qutining 1-sorti qismlarga bo'linadi, har biri o'z qog'ozi bilan:
- CONTROL qismi = OTK birinchi tekshiruvdagi 1-sort (qutidagi CONTROL stikeri, "CONTROL:<box_code>");
- TAMIRn qismi  = n-ta'mir chekidan tuzalib chiqqan 1-sort ("TAMIR1:<box_code>", "TAMIR2:<box_code>", ...).
OTK ta'mirga yuborsa TAMIR1 cheki chiqadi; ta'mirdan qaytganda ochiq chek yopiladi (tuzalganlar — shu chekning
1-sorti), yana ta'mirga ketganlari uchun keyingi chek (TAMIR2, ...) chiqadi. Qog'ozdagi son emas, bazadagi son olinadi;
bitta qog'ozni qayta urish hech narsa qo'shmaydi. Eski "TAMIR:<box_code>" cheki = 1-chek.
"""
from collections import OrderedDict, defaultdict
from datetime import datetime
from decimal import Decimal
import re

from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Q, Sum
from django.utils import timezone

from production.models import (Article, Box, BoxQualityInspectionLog, Order, OrderItem, PackingBatch, PackingScan,
                               RepairReceipt, SecondSortBatch, SecondSortDispatch, SecondSortDispatchItem,
                               SecondSortDispatchType)
from production.sewing_statistics_views import natural_size_sort_key

CODE_RE = re.compile(r'^(CONTROL|TAMIR(\d*)|BOX|QUTI)\s*:\s*(.+)$', re.IGNORECASE)


def beta_group(box):
    """Quti beta shablon bo'yicha ishlaydimi: artikul shabloni beta va quti belgi yoqilgandan keyin yaratilgan.
    Ha bo'lsa — shu shablon (CONTROL stikeri "OTK Control/Taxlov" narxi va qiyinligi undan), aks holda None."""
    article = box.target_article
    group = article.operation_group if article else None
    if group and group.is_beta and group.beta_since and box.created_at and box.created_at >= group.beta_since:
        return group
    return None


def is_beta_box(box):
    """Yangi qadoqlash tartibidagi quti: beta shablon (belgidan keyin yaratilgan quti) yoki beta zakaz — zakaz belgisi
    yoqilgan vaqtdan (packing_beta_since) oldin OTK dan o'tgan quti eski tartibda qoladi (egasi talabi 2026-11-16)."""
    order = box.order if box.order_id else None
    if order is not None and order.packing_beta:
        since = order.packing_beta_since
        if since is None or not box.quality_inspection_logs.filter(
                action_type=BoxQualityInspectionLog.ActionType.INITIAL, created_at__lt=since).exists():
            return True
    return bool(beta_group(box))


def beta_box_q(prefix='box__'):
    """is_beta_box ning so'rov sharti (prefix='' — Box'ning o'zi). Shablon bo'yicha Box.article orqali tekshiriladi."""
    otk_before_beta = BoxQualityInspectionLog.objects.filter(
        box=OuterRef(f'{prefix}pk'), action_type=BoxQualityInspectionLog.ActionType.INITIAL,
        created_at__lt=OuterRef(f'{prefix}order__packing_beta_since'))
    return ((Q(**{f'{prefix}order__packing_beta': True}) & ~Exists(otk_before_beta))
            | Q(**{f'{prefix}article__operation_group__is_beta': True,
                   f'{prefix}created_at__gte': F(f'{prefix}article__operation_group__beta_since')}))


# Upakovkaga yetgan skanlar: sanoqchi yuborgan ro'yxat yoki ro'yxatsiz (eski taxlovchi tabi) skanlar.
SENT_SCANS = Q(batch__isnull=True) | Q(batch__status=PackingBatch.Status.SENT)


def _details(repair_details):
    details = []
    for index, item in enumerate(repair_details or [], start=1):
        if not isinstance(item, dict):
            continue
        details.append({
            'number': item.get('item_number', index),
            'operations': [str(x) for x in (item.get('operation_names') or [])],
            'workers': [str(x) for x in (item.get('workers') or [])],
            'notes': str(item.get('notes') or ''),
        })
    return details


def create_repair_receipt(box, quantity, repair_details, inspector, operator_name='', log=None):
    """Navbatdagi ta'mir cheki (TAMIR1, TAMIR2, ...); har dona uchun operatsiya va tikuvchi (OTK tanlagan) saqlanadi."""
    cycle = (box.repair_receipts.aggregate(m=Max('cycle'))['m'] or 0) + 1
    return RepairReceipt.objects.create(box=box, cycle=cycle, quantity=quantity, details=_details(repair_details),
                                        inspector=inspector, operator_name=(operator_name or '')[:150], created_log=log)


def close_repair_receipt(box, log, fixed_qty, re_repair_qty, repair_details, inspector, operator_name=''):
    """Ta'mirdan qaytish: ochiq chek yopiladi (tuzalganlar uning 1-sorti), yana ta'mirga ketganlari uchun keyingi chek.
    Keyingi chek (bo'lsa) qaytariladi."""
    receipt = box.repair_receipts.filter(returned_at__isnull=True).order_by('-cycle').first()
    if receipt is not None:
        receipt.first_sort_qty += fixed_qty          # eski chek (qutiga bitta) bir necha qaytishni yig'ishi mumkin
        receipt.returned_log, receipt.returned_at = log, timezone.now()
        receipt.save(update_fields=['first_sort_qty', 'returned_log', 'returned_at'])
    if re_repair_qty > 0:
        return create_repair_receipt(box, re_repair_qty, repair_details, inspector, operator_name, log)
    return None


def parse_packing_code(raw):
    """
    'CONTROL:A10-394' -> (CONTROL, 'A10-394', None); 'TAMIR2:A10-394' -> (TAMIR, 'A10-394', 2);
    eski 'TAMIR:A10-394' -> (TAMIR, ..., 1). Skaner '+' xatosi ham. Qo'lda yozilgan quti kodi ('A10-394', 'BOX:'/'QUTI:'
    bilan ham) = CONTROL stikeri. Tikuvchi stikeri (TICKET:, TK-) yoki bo'sh -> (None, None, None).
    """
    text = (raw or '').strip()
    if '+' in text:
        text = re.sub(r'^(CONTROL|TAMIR\d*|BOX|QUTI)\+', r'\1:', text, flags=re.IGNORECASE).replace('+', '-')
    text = re.sub(r'^(TAMIR\d*)-', r'\1:', text, flags=re.IGNORECASE)   # "+" ni "-" ga aylantirgan sahifadan kelsa
    if not text or re.match(r'^(TICKET:|TK-)', text, flags=re.IGNORECASE):
        return None, None, None
    match = CODE_RE.match(text)
    if not match:
        return PackingScan.Source.CONTROL, text, None
    if match.group(1).upper().startswith('TAMIR'):
        return PackingScan.Source.TAMIR, match.group(3).strip(), int(match.group(2) or 1)
    return PackingScan.Source.CONTROL, match.group(3).strip(), None


def find_receipt(box, cycle):
    return box.repair_receipts.filter(cycle=cycle).first()


def portion_total(box, source, receipt=None):
    """Qism bo'yicha jami 1-sort: CONTROL — birlamchi tekshiruv, TAMIRn — shu chekdan tuzalib chiqqanlar."""
    if source == PackingScan.Source.TAMIR:
        return receipt.first_sort_qty if receipt else 0
    return (box.quality_inspection_logs.filter(action_type=BoxQualityInspectionLog.ActionType.INITIAL)
            .aggregate(s=Sum('first_sort_qty'))['s'] or 0)


def packed_total(box, source, receipt=None):
    scans = box.packing_scans.filter(source=source, is_cancelled=False)
    if source == PackingScan.Source.TAMIR:
        scans = scans.filter(repair_receipt=receipt)
    return scans.aggregate(s=Sum('quantity'))['s'] or 0


def available_for_packing(box, source, receipt=None):
    return max(0, portion_total(box, source, receipt) - packed_total(box, source, receipt))


def find_box(box_code):
    return Box.objects.select_related('order', 'article').filter(box_code__iexact=box_code).first()


def paper_label(source, receipt=None):
    return f"{receipt.cycle}-ta'mir" if receipt is not None else "CONTROL"


def take_paper(raw_code, worker, terminal_user, batch=None, screen_number=None):
    """Qog'ozni (CONTROL stikeri, TAMIRn cheki yoki qo'lda yozilgan quti kodi) taxlovchi hisobiga oladi: shu qismning
    hali olinmagani yoziladi (qog'ozdagi emas, bazadagi son), har qog'oz bir marta. (scan, None) yoki (None, xato)."""
    source, box_code, cycle = parse_packing_code(raw_code)
    if not source:
        return None, ('BAD_CODE', "Bu tikuvchi stikeri — CONTROL stikeri, TAMIR cheki yoki quti kodini uring.")
    with transaction.atomic():
        box = (Box.objects.select_for_update(of=('self',)).select_related('order', 'article')
               .filter(box_code__iexact=box_code).first())
        if not box:
            return None, ('NOT_FOUND', f"«{box_code}» quti topilmadi.")
        if not is_beta_box(box):
            return None, ('NOT_BETA', "Bu quti yangi qadoqlash tartibida emas.")
        if box.status == Box.Status.CANCELLED:
            return None, ('CANCELLED', "Bu quti bekor qilingan.")
        receipt = find_receipt(box, cycle) if source == PackingScan.Source.TAMIR else None
        if source == PackingScan.Source.TAMIR and receipt is None:
            return None, ('NOT_FOUND', f"«TAMIR{cycle}:{box.box_code}» cheki yo'q.")
        available = available_for_packing(box, source, receipt)
        if available <= 0:
            if source == PackingScan.Source.CONTROL and not portion_total(box, source):
                message = "OTK bu qutini hali tekshirmagan (yoki 1-sort yo'q)."
            elif receipt is not None and receipt.is_open:
                message = f"{receipt.cycle}-ta'mir cheki hali ta'mirdan qaytmagan."
            elif receipt is not None and not receipt.first_sort_qty:
                message = f"{receipt.cycle}-ta'mir chekidan 1-sort chiqmagan."
            else:
                message = f"Bu qog'oz allaqachon urilgan: {packed_total(box, source, receipt)} dona."
            return None, ('NOTHING', message)
        scan_screen = screen_number if screen_number is not None else (batch.screen_number if batch else None)
        scan = PackingScan.objects.create(box=box, source=source, repair_receipt=receipt, worker=worker,
                                          quantity=available, terminal_user=terminal_user, batch=batch,
                                          screen_number=scan_screen)
        from production.meto_flow import release_for_scan   # yangi meto oqimi: qog'oz urilsa konteyner bo'shaydi
        release_for_scan(box, source, receipt, terminal_user)
    return scan, None


def paper_status(box):
    """Qutining hamma qog'ozlari: CONTROL va har ta'mir cheki — qismi, olingani, kutilayotgani va kim olgani.
    Qutining ta'mir tarixi (necha marta) va yo'qolgan (hali urilmagan) qog'ozni topish uchun."""
    scans = list(box.packing_scans.filter(is_cancelled=False).select_related('worker', 'batch').order_by('scanned_at'))

    def describe(code, label, portion, own_scans, state_if_waiting='waiting'):
        taken = sum(sc.quantity for sc in own_scans)
        waiting = max(0, portion - taken)
        if waiting:
            state = state_if_waiting
        elif portion:
            state = 'done'
        else:
            state = 'empty'
        return {
            'code': code, 'label': label, 'portion': portion, 'taken': taken, 'waiting': waiting, 'state': state,
            'scans': [{'worker': sc.worker.full_name, 'quantity': sc.quantity,
                       'at': timezone.localtime(sc.scanned_at).strftime('%d.%m %H:%M'),
                       'sent': sc.batch_id is None or sc.batch.status == PackingBatch.Status.SENT} for sc in own_scans],
        }

    inspected = box.quality_inspection_logs.filter(action_type=BoxQualityInspectionLog.ActionType.INITIAL).exists()
    control = describe(f"CONTROL:{box.box_code}", "CONTROL", portion_total(box, PackingScan.Source.CONTROL),
                       [sc for sc in scans if sc.source == PackingScan.Source.CONTROL])
    if not inspected:
        control['state'] = 'not_inspected'
    papers = [control]
    receipts = list(box.repair_receipts.order_by('cycle'))
    for receipt in receipts:
        paper = describe(receipt.qr_text, paper_label(PackingScan.Source.TAMIR, receipt), receipt.first_sort_qty,
                         [sc for sc in scans if sc.repair_receipt_id == receipt.id])
        paper['sent_to_repair'] = receipt.quantity
        if receipt.is_open:
            paper['state'] = 'in_repair'
        papers.append(paper)
    return {'box_code': box.box_code, 'repair_cycles': len(receipts), 'papers': papers,
            'waiting_total': sum(p['waiting'] for p in papers)}


def _format_taxlov_difficulty(value) -> str:
    if value is None:
        return "1"
    try:
        val = float(value)
        return str(int(val)) if val.is_integer() else f"{val:g}"
    except (TypeError, ValueError):
        return str(value)


def _format_taxlov_price(value) -> str:
    amount = value if value is not None else Decimal('0.00')
    try:
        dec = Decimal(amount)
    except Exception:
        return str(amount)
    if dec == dec.to_integral_value():
        return f"{int(dec):,}".replace(",", " ")
    s = f"{dec.normalize():f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    int_part, _, dec_part = s.partition('.')
    int_fmt = f"{int(int_part):,}".replace(",", " ")
    return f"{int_fmt}.{dec_part}" if dec_part else int_fmt


def control_sticker_meta(box, initial_before_beta_ids=None):
    """Qutining CONTROL stikerini chop etish uchun beta holati, taxlov qiyinligi va narxi (summa)."""
    if initial_before_beta_ids is None:
        is_beta = is_beta_box(box)
    else:
        order = box.order if box.order_id else None
        if order is not None and order.packing_beta and (order.packing_beta_since is None or box.id not in initial_before_beta_ids):
            is_beta = True
        else:
            is_beta = bool(beta_group(box))
    if not is_beta:
        return {
            'is_beta': False,
            'difficulty_display': '',
            'price_display': '',
        }
    article = box.target_article
    group = article.operation_group if article else None
    difficulty = group.taxlov_difficulty if (group and group.taxlov_difficulty is not None) else 1.0
    price = group.taxlov_price if (group and group.taxlov_price is not None) else Decimal('0.00')
    return {
        'is_beta': True,
        'difficulty_display': _format_taxlov_difficulty(difficulty),
        'price_display': _format_taxlov_price(price),
    }


# ------------------------------------------------------------------ 2-Sort balans, jo'natish va oylik hisobot

SECOND_SORT_LOG_RELATED = (
    'box__order',
    'box__order__article',
    'box__article',
    'box__cutting_batch_item__batch__order_item__article',
    'second_sort_batch',
)


def normalize_second_sort_size(razmer) -> str:
    """Razmer nomini bir xil formatga keltirish (bo'sh/None -> '—')."""
    return (razmer or '').strip() or '—'


def _article_image_url(article) -> str:
    if article and article.image:
        try:
            return article.image.url
        except ValueError:
            return ''
    return ''


def _order_first_articles(order_ids):
    """box.article va order.article bo'sh bo'lganda OrderItem dan birinchi artikulni bitta so'rovda olish."""
    if not order_ids:
        return {}
    mapping = {}
    for oi in (OrderItem.objects.filter(order_id__in=order_ids, article__isnull=False)
               .select_related('article').order_by('order_id', 'id')):
        if oi.order_id not in mapping:
            mapping[oi.order_id] = oi.article
    return mapping


def _resolve_box_article(box, fallback_articles=None):
    """Box.target_article bilan bir xil ustuvorlik, N+1 so'rovlarsiz."""
    if box.article_id:
        return box.article
    if box.cutting_batch_item_id and box.cutting_batch_item.batch_id and box.cutting_batch_item.batch.order_item_id:
        art = box.cutting_batch_item.batch.order_item.article
        if art:
            return art
    if box.order_id and box.order.article_id:
        return box.order.article
    if fallback_articles is not None:
        return fallback_articles.get(box.order_id)
    return box.target_article


def _second_sort_in_timestamp(log):
    """2-sort kirim vaqti: master qabul qilgan vaqt (yoki avvalgi savat jo'natilgan/yaratilgan vaqt)."""
    batch = log.second_sort_batch
    if batch is not None:
        return batch.accepted_at or batch.sent_at or batch.created_at or log.created_at
    return log.created_at


def _accepted_second_sort_logs(order_ids=None, before=None):
    qs = BoxQualityInspectionLog.objects.filter(
        beta_box_q(),
        second_sort_qty__gt=0,
        second_sort_batch__status=SecondSortBatch.Status.ACCEPTED,
    ).select_related(*SECOND_SORT_LOG_RELATED)
    if order_ids is not None:
        qs = qs.filter(box__order_id__in=order_ids)
    logs = list(qs)
    missing_order_ids = {
        log.box.order_id for log in logs
        if log.box.article_id is None and log.box.order.article_id is None
    }
    fallback_articles = _order_first_articles(missing_order_ids)
    result = []
    for log in logs:
        ts = _second_sort_in_timestamp(log)
        if before is not None and ts >= before:
            continue
        article = _resolve_box_article(log.box, fallback_articles)
        result.append((log, article, ts))
    return result


def second_sort_balance_data(order_ids=None):
    """Upakovkadagi amaldagi 2-sort balansi: qabul qilingan (ACCEPTED) minus bekor qilinmagan jo'natmalar."""
    logs_meta = _accepted_second_sort_logs(order_ids=order_ids)
    dispatch_qs = SecondSortDispatchItem.objects.filter(dispatch__is_cancelled=False)
    if order_ids is not None:
        dispatch_qs = dispatch_qs.filter(order_id__in=order_ids)
    dispatch_items = list(dispatch_qs.values('order_id', 'article_id', 'razmer', 'quantity'))

    in_by_key = defaultdict(int)
    out_by_key = defaultdict(int)
    cards_meta = OrderedDict()

    for log, article, _ts in logs_meta:
        box = log.box
        aid = article.id if article else 0
        size = normalize_second_sort_size(box.razmer)
        key = (box.order_id, aid, size)
        in_by_key[key] += log.second_sort_qty

        card_key = (box.order_id, aid)
        meta = cards_meta.setdefault(card_key, {
            'order': box.order,
            'article': article,
            'image': _article_image_url(article),
            'sizes': OrderedDict(),
            'boxes_map': OrderedDict(),
            'accepted_total': 0,
        })
        meta['sizes'][size] = meta['sizes'].get(size, 0) + log.second_sort_qty
        meta['accepted_total'] += log.second_sort_qty
        b_entry = meta['boxes_map'].setdefault(box.id, {
            'box_code': box.box_code, 'razmer': size, 'qty': 0, 'notes': [],
        })
        b_entry['qty'] += log.second_sort_qty

    for item in dispatch_items:
        aid = item['article_id'] or 0
        size = normalize_second_sort_size(item['razmer'])
        out_by_key[(item['order_id'], aid, size)] += item['quantity']

    balance_map = {}
    cards = []
    for (order_id, aid), meta in cards_meta.items():
        sizes_rem = OrderedDict()
        size_rows = []
        dispatched_total = 0
        ordered_sizes = sorted(meta['sizes'].keys(), key=natural_size_sort_key)
        for size in ordered_sizes:
            acc = meta['sizes'][size]
            disp = out_by_key.get((order_id, aid, size), 0)
            rem = max(0, acc - disp)
            dispatched_total += disp
            if rem > 0:
                sizes_rem[size] = rem
                balance_map[(order_id, aid, size)] = rem
                size_rows.append({
                    'size': size,
                    'remaining': rem,
                    'accepted': acc,
                    'dispatched': disp,
                })
        total_rem = sum(sizes_rem.values())
        if total_rem <= 0:
            continue
        boxes = sorted(meta['boxes_map'].values(), key=lambda b: (natural_size_sort_key(b['razmer']), b['box_code']))
        cards.append({
            'order': meta['order'],
            'article': meta['article'],
            'image': meta['image'],
            'sizes': sizes_rem,
            'size_rows': size_rows,
            'total': total_rem,
            'accepted_total': meta['accepted_total'],
            'dispatched_total': dispatched_total,
            'boxes': boxes,
            'box_count': len(boxes),
        })

    cards.sort(key=lambda c: (c['order'].order_number, c['article'].code if c['article'] else ''))
    return {
        'cards': cards,
        'total': sum(card['total'] for card in cards),
        'balance_map': balance_map,
    }


def create_second_sort_dispatch(user, dispatch_type_id, raw_items, note='', dispatched_at=None):
    """2-sort balansidan chiqim (1 ta razmer yoki bir nechta zakaz/model yig'masi) yaratadi."""
    try:
        dtype_id = int(dispatch_type_id)
    except (TypeError, ValueError):
        return None, "Jo'natish turini tanlang."
    dispatch_type = SecondSortDispatchType.objects.filter(id=dtype_id).first()
    if not dispatch_type:
        return None, "Tanlangan jo'natish turi topilmadi."
    if not dispatch_type.is_active:
        return None, f"«{dispatch_type.name}» turi nofaol — faol turni tanlang."

    if not isinstance(raw_items, list) or not raw_items:
        return None, "Jo'natish uchun kamida bitta qator tanlang."

    merged = OrderedDict()
    for idx, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            return None, f"{idx}-qator formati noto'g'ri."
        try:
            order_id = int(item.get('order_id'))
            article_id = int(item.get('article_id') or 0)
            qty = int(item.get('quantity') or 0)
        except (TypeError, ValueError):
            return None, f"{idx}-qator soni noto'g'ri."
        if qty < 0:
            return None, "Soni manfiy bo'lishi mumkin emas."
        if qty == 0:
            continue
        size = normalize_second_sort_size(item.get('size'))[:50]
        key = (order_id, article_id, size)
        merged[key] = merged.get(key, 0) + qty

    if not merged:
        return None, "Jo'natish uchun kamida 1 dona miqdor kiriting."

    order_ids = sorted({k[0] for k in merged})
    article_ids = sorted({k[1] for k in merged if k[1] > 0})

    with transaction.atomic():
        orders = {o.id: o for o in Order.objects.select_for_update().filter(id__in=order_ids).order_by('id')}
        for oid in order_ids:
            if oid not in orders:
                return None, "Buyurtma topilmadi."
        articles = {a.id: a for a in Article.objects.filter(id__in=article_ids)}
        for aid in article_ids:
            if aid not in articles:
                return None, "Model (artikul) topilmadi."

        balance_map = second_sort_balance_data(order_ids=order_ids)['balance_map']
        for (oid, aid, size), qty in merged.items():
            available = balance_map.get((oid, aid, size), 0)
            if qty > available:
                ord_num = orders[oid].order_number
                art_code = articles[aid].code if aid in articles else '—'
                return None, (
                    f"«{ord_num} · {art_code} ({size})» balansda {available} dona qolgan "
                    f"(jo'natishga {qty} dona so'raldi)."
                )

        dispatch = SecondSortDispatch.objects.create(
            dispatch_type=dispatch_type,
            note=(note or '').strip()[:300],
            dispatched_by=user,
            dispatched_at=dispatched_at or timezone.now(),
        )
        SecondSortDispatchItem.objects.bulk_create([
            SecondSortDispatchItem(
                dispatch=dispatch,
                order_id=oid,
                article_id=(aid if aid > 0 else None),
                razmer=size,
                quantity=qty,
            )
            for (oid, aid, size), qty in merged.items()
        ])
    return dispatch, None


def cancel_second_sort_dispatch(dispatch_id, user, reason=''):
    """Xato qilingan 2-sort jo'natmasini bekor qiladi — donalar balansga qaytadi."""
    try:
        pk = int(dispatch_id)
    except (TypeError, ValueError):
        return None, "Jo'natma topilmadi."
    with transaction.atomic():
        dispatch = SecondSortDispatch.objects.select_for_update().filter(id=pk).first()
        if not dispatch:
            return None, "Jo'natma topilmadi."
        if dispatch.is_cancelled:
            return None, f"{dispatch.code} allaqachon bekor qilingan."
        dispatch.is_cancelled = True
        dispatch.cancelled_at = timezone.now()
        dispatch.cancelled_by = user
        dispatch.cancel_reason = (reason or '').strip()[:300]
        dispatch.save(update_fields=['is_cancelled', 'cancelled_at', 'cancelled_by', 'cancel_reason'])
    return dispatch, None


def _month_window(month_str=None):
    """'YYYY-MM' oy chegaralari (mahalliy vaqt mintaqasida)."""
    tz = timezone.get_current_timezone()
    local_now = timezone.localtime()
    year, month = local_now.year, local_now.month
    if month_str:
        match = re.match(r'^(\d{4})-(\d{2})$', month_str.strip())
        if match:
            y, m = int(match.group(1)), int(match.group(2))
            if 2000 <= y <= 2100 and 1 <= m <= 12:
                year, month = y, m
    start_naive = datetime(year, month, 1, 0, 0, 0)
    if month == 12:
        end_naive = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        end_naive = datetime(year, month + 1, 1, 0, 0, 0)
    return f"{year:04d}-{month:02d}", timezone.make_aware(start_naive, tz), timezone.make_aware(end_naive, tz)


def second_sort_monthly_report(month_str=None, dispatch_type_id=None, query=''):
    """Tanlangan oy uchun 2-sort aylanma hisoboti: oy boshiga + qabul - chiqim (turlar kesimida) = oy oxiriga."""
    selected_month, month_start, month_end = _month_window(month_str)
    q_norm = (query or '').strip().lower()
    try:
        filter_type_id = int(dispatch_type_id) if dispatch_type_id else None
    except (TypeError, ValueError):
        filter_type_id = None

    logs_meta = _accepted_second_sort_logs(before=month_end)
    dispatch_items = list(
        SecondSortDispatchItem.objects
        .filter(dispatch__is_cancelled=False, dispatch__dispatched_at__lt=month_end)
        .select_related('dispatch', 'dispatch__dispatch_type', 'order', 'article')
    )

    groups = {}
    used_type_ids = set()

    def ensure_group(order, article):
        aid = article.id if article else 0
        key = (order.id, aid)
        entry = groups.get(key)
        if entry is None:
            entry = {
                'order': order,
                'article': article,
                'opening_in': 0,
                'opening_out': 0,
                'month_in': 0,
                'month_out': 0,
                'by_type': defaultdict(int),
                'sizes_closing': defaultdict(int),
            }
            groups[key] = entry
        return entry

    for log, article, ts in logs_meta:
        g = ensure_group(log.box.order, article)
        qty = log.second_sort_qty
        size = normalize_second_sort_size(log.box.razmer)
        g['sizes_closing'][size] += qty
        if ts < month_start:
            g['opening_in'] += qty
        else:
            g['month_in'] += qty

    for item in dispatch_items:
        g = ensure_group(item.order, item.article)
        qty = item.quantity
        size = normalize_second_sort_size(item.razmer)
        g['sizes_closing'][size] -= qty
        dt = item.dispatch.dispatched_at
        if dt < month_start:
            g['opening_out'] += qty
        else:
            tid = item.dispatch.dispatch_type_id
            g['month_out'] += qty
            g['by_type'][tid] += qty
            used_type_ids.add(tid)

    report_types = list(
        SecondSortDispatchType.objects.filter(Q(is_active=True) | Q(id__in=used_type_ids))
        .order_by('sort_order', 'id')
    )

    rows = []
    type_totals = defaultdict(int)
    grand = {'opening': 0, 'month_in': 0, 'month_out': 0, 'closing': 0}

    for g in groups.values():
        opening = g['opening_in'] - g['opening_out']
        month_in = g['month_in']
        month_out = g['month_out']
        closing = opening + month_in - month_out
        if opening == 0 and month_in == 0 and month_out == 0 and closing == 0:
            continue
        if q_norm:
            haystack = ' '.join(filter(None, [
                g['order'].order_number,
                g['order'].client_name,
                g['article'].code if g['article'] else '',
                g['article'].name if g['article'] else '',
            ])).lower()
            if q_norm not in haystack:
                continue

        by_type_list = []
        for t in report_types:
            val = g['by_type'].get(t.id, 0)
            by_type_list.append(val)
            type_totals[t.id] += val

        grand['opening'] += opening
        grand['month_in'] += month_in
        grand['month_out'] += month_out
        grand['closing'] += closing

        closing_sizes = [
            {'size': sz, 'qty': qty}
            for sz, qty in sorted(g['sizes_closing'].items(), key=lambda kv: natural_size_sort_key(kv[0]))
            if qty > 0
        ]
        rows.append({
            'order': g['order'],
            'article': g['article'],
            'opening': opening,
            'month_in': month_in,
            'by_type_list': by_type_list,
            'month_out': month_out,
            'closing': closing,
            'closing_sizes': closing_sizes,
        })

    rows.sort(key=lambda r: (r['order'].order_number, r['article'].code if r['article'] else ''))

    dispatches_qs = (
        SecondSortDispatch.objects
        .filter(dispatched_at__gte=month_start, dispatched_at__lt=month_end)
        .select_related('dispatch_type', 'dispatched_by', 'cancelled_by')
        .prefetch_related('items__order', 'items__article')
        .order_by('-dispatched_at', '-id')
    )
    if filter_type_id:
        dispatches_qs = dispatches_qs.filter(dispatch_type_id=filter_type_id)

    dispatches = []
    for d in dispatches_qs:
        items = list(d.items.all())
        if q_norm:
            haystack = ' '.join(filter(None, [
                d.code,
                d.note,
                d.dispatch_type.name,
                *(it.order.order_number for it in items),
                *(it.article.code for it in items if it.article),
                *(it.article.name for it in items if it.article),
            ])).lower()
            if q_norm not in haystack:
                continue
        dispatches.append({
            'dispatch': d,
            'items': items,
            'total_qty': sum(it.quantity for it in items),
        })

    type_summary = [{'type': t, 'total': type_totals.get(t.id, 0)} for t in report_types]
    totals = {
        'opening': grand['opening'],
        'month_in': grand['month_in'],
        'by_type_list': [type_totals.get(t.id, 0) for t in report_types],
        'month_out': grand['month_out'],
        'closing': grand['closing'],
    }
    return {
        'selected_month': selected_month,
        'filter_type_id': filter_type_id,
        'query': (query or '').strip(),
        'report_types': report_types,
        'type_summary': type_summary,
        'rows': rows,
        'totals': totals,
        'dispatches': dispatches,
    }


