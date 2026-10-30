"""
Tikim statistikasi bosh sahifasi ("Fabrika bo'yicha jami") uchun zakazlarning quvur balansi — tezkor hisob.

Natija /sewing-statistics/orders/<id>/ sahifasidagi ``order_totals`` bilan bir xil (evaluate_box_progress,
calculate_pipeline_balance, get_model_pastals_analysis va "oldingi + shu kunda" qoidalari), lekin har bir biletni
Python obyektiga aylantirmaydi: qutilar bo'yicha kerakli belgilar bitta guruhlangan SQL so'rovda olinadi.
Tenglik production/test_pipeline_summary.py da tekshiriladi.
"""
from collections import defaultdict

from django.db.models import Count, Min, Q

from production.models import Box, BoxQualityInspectionLog, Ticket

# Zakaz jamlari: fabrika jami shu kalitlarning yig'indisi
ORDER_SUM_KEYS = (
    'planned', 'cut_prior', 'cut_today', 'cut_total', 'cut', 'boxed', 'boxes_count',
    'waiting_sewing_qty', 'waiting_sewing_boxes', 'waiting_pastals_count',
    'sewing_prior', 'sewing_today', 'sewing_total', 'entered_sewing', 'entered_sewing_boxes',
    'in_sewing_pre_dazmol', 'in_sewing_pre_dazmol_boxes', 'real_wip_sewing', 'real_wip_sewing_boxes',
    'dazmol_prior', 'dazmol_today', 'dazmol_total', 'dazmol', 'dazmol_boxes',
    'unreached_control', 'unreached_control_boxes', 'waiting_control', 'waiting_control_boxes',
    'waiting_control_ready', 'waiting_control_ready_boxes', 'waiting_control_missing_ops',
    'waiting_control_missing_ops_boxes', 'unprinted_boxes_count', 'in_repair', 'in_repair_boxes',
    're_repair', 're_repair_boxes', 'first_sort_prior', 'first_sort_today', 'first_sort_total', 'first_sort',
    'second_sort_prior', 'second_sort_today', 'second_sort_total', 'second_sort',
    'controlled_boxes_count', 'control_variance',
)

LIVE_TICKET_STATUSES = (Ticket.Status.PENDING, Ticket.Status.SCANNED)   # bekor qilinmagan biletlar


def _box_rows(order_ids, dazmol_ao_ids):
    dz_ids = list(dazmol_ao_ids) or [-1]
    scanned = Q(tickets__status=Ticket.Status.SCANNED)
    active_op = Q(tickets__article_operation__isnull=True) | Q(tickets__article_operation__is_active=True)
    return list(
        Box.objects.filter(order_id__in=order_ids).exclude(status=Box.Status.CANCELLED).values(
            'id', 'order_id', 'article_id', 'quantity', 'created_at', 'is_printed', 'is_controlled', 'controlled_at',
            'controlled_first_sort_qty', 'controlled_second_sort_qty', 'controlled_defect_qty', 'controlled_repair_qty',
            'pastal_number', 'cutting_batch_item__batch__pastal_code', 'cutting_batch_item__batch__batch_number',
        ).annotate(
            # Kunlik bo'linish va "tikim kutmoqda" (har qanday operatsiya biletlari)
            scanned_any=Count('tickets', filter=scanned, distinct=True),
            min_scan=Min('tickets__scanned_at', filter=scanned),
            min_dazmol=Min('tickets__scanned_at', filter=scanned & Q(tickets__article_operation_id__in=dz_ids)),
            # evaluate_box_progress: faqat faol operatsiyalar biletlari
            total_active=Count('tickets', filter=Q(tickets__status__in=LIVE_TICKET_STATUSES) & active_op, distinct=True),
            scanned_active=Count('tickets', filter=scanned & active_op, distinct=True),
            dazmol_active_scanned=Count(
                'tickets', distinct=True,
                filter=scanned & Q(tickets__article_operation_id__in=dz_ids, tickets__article_operation__is_active=True),
            ),
        )
    )


def _pastal_key(r):
    """get_model_pastals_analysis dagi kalit: (box.pastal_code or box.pastal_number or "NOMA'LUM")."""
    code = r['pastal_number'] or ''
    if not code and r['cutting_batch_item__batch__batch_number'] is not None:
        code = r['cutting_batch_item__batch__pastal_code'] or str(r['cutting_batch_item__batch__batch_number'])
    return (code or r['pastal_number'] or "NOMA'LUM").strip()


def _add_balance(t, rows, repair_cycles):
    """calculate_pipeline_balance (evaluate_box_progress bo'yicha) — t ga qo'shadi."""
    for r in rows:
        q = r['quantity']
        total, scanned = r['total_active'], r['scanned_active']
        has_entered = scanned > 0
        in_repair = (r['controlled_repair_qty'] or 0) > 0
        finished_all = total > 0 and scanned == total
        terminal = r['dazmol_active_scanned'] > 0 or finished_all
        waiting = terminal and not r['is_controlled'] and not in_repair

        if has_entered:
            t['entered_sewing_boxes'] += 1
            if not terminal and not in_repair and not r['is_controlled']:
                t['in_sewing_pre_dazmol'] += q
                t['in_sewing_pre_dazmol_boxes'] += 1
        if terminal:
            t['dazmol_boxes'] += 1
        if waiting:
            t['waiting_control'] += q
            t['waiting_control_boxes'] += 1
            if finished_all:
                t['waiting_control_ready'] += q
                t['waiting_control_ready_boxes'] += 1
            else:
                t['waiting_control_missing_ops'] += q
                t['waiting_control_missing_ops_boxes'] += 1
                t['unreached_control'] += q
                t['unreached_control_boxes'] += 1
        if in_repair:
            rq = r['controlled_repair_qty'] or q
            t['in_repair'] += rq
            t['in_repair_boxes'] += 1
            if repair_cycles.get(r['id'], 0) >= 2:
                t['re_repair'] += rq
                t['re_repair_boxes'] += 1
        if r['is_controlled']:
            t['controlled_boxes_count'] += 1
            closed = (r['controlled_first_sort_qty'] or 0) + (r['controlled_second_sort_qty'] or 0) + (r['controlled_defect_qty'] or 0)
            t['control_variance'] += closed - q


def _add_daily(t, rows, day_start, day_end):
    """Oldingi + shu kunda: tikim (birinchi skan), dazmol (birinchi dazmol skani), OTK (controlled_at)."""
    for r in rows:
        q = r['quantity']
        for key, ts in (('sewing', r['min_scan']), ('dazmol', r['min_dazmol'])):
            if ts:
                if ts < day_start:
                    t[f'{key}_prior'] += q
                elif ts <= day_end:
                    t[f'{key}_today'] += q
        if r['is_controlled'] and r['controlled_at']:
            part = 'prior' if r['controlled_at'] < day_start else ('today' if r['controlled_at'] <= day_end else None)
            if part:
                t[f'first_sort_{part}'] += r['controlled_first_sort_qty'] or 0
                t[f'second_sort_{part}'] += r['controlled_second_sort_qty'] or 0


def _finalize(t):
    for key in ('sewing', 'dazmol', 'first_sort', 'second_sort'):
        t[f'{key}_total'] = t[f'{key}_prior'] + t[f'{key}_today']
    t['cut_total'] = t['cut_prior'] + t['cut_today']
    t['cut'] = t['cut_total']
    t['entered_sewing'] = t['sewing_total']
    t['dazmol'] = t['dazmol_total']
    t['first_sort'] = t['first_sort_total']
    t['second_sort'] = t['second_sort_total']
    t['real_wip_sewing'] = t['in_sewing_pre_dazmol'] + t['in_repair']
    t['real_wip_sewing_boxes'] = t['in_sewing_pre_dazmol_boxes'] + t['in_repair_boxes']
    add_rates(t)


def add_rates(t):
    """Foizlar, qutilardagi farq va brak (zakaz sahifasidagi formulalar)."""
    planned = t['planned']
    t['cut_pct'] = round(t['cut_total'] / planned * 100, 1) if planned > 0 else 0.0
    t['dazmol_pct'] = round(t['dazmol_total'] / planned * 100, 1) if planned > 0 else 0.0
    t['boxed_diff'] = t['boxed'] - t['cut_total']
    closed = t['first_sort_total'] + t['second_sort_total']
    t['brak_rate_pct'] = round(t['second_sort_total'] / closed * 100, 1) if closed > 0 else 0.0
    t['is_brak_warning'] = bool(t['brak_rate_pct'] > 10.0)


def summarize_orders_pipeline(orders, day_start, day_end, dazmol_ids_for_article):
    """
    {order_id: order_totals} — /sewing-statistics/orders/<id>/ dagi order_totals bilan bir xil kalitlar va qiymatlar.
    orders: items__article__article_operations__operation va items__sizes__cutting_items__batch prefetch qilingan.
    dazmol_ids_for_article: sewing_statistics_views.get_dazmol_operation_ids_for_article.
    """
    orders = list(orders)
    article_dazmol = {}
    for o in orders:
        for item in o.items.all():
            if item.article_id not in article_dazmol:
                article_dazmol[item.article_id] = dazmol_ids_for_article(item.article)
    all_dazmol_ids = set().union(*article_dazmol.values()) if article_dazmol else set()

    order_ids = [o.id for o in orders]
    rows_by_order = defaultdict(list)
    for r in _box_rows(order_ids, all_dazmol_ids):
        rows_by_order[r['order_id']].append(r)
    repair_cycles = dict(
        BoxQualityInspectionLog.objects.filter(box__order_id__in=order_ids, repair_qty__gt=0)
        .values('box_id').annotate(n=Count('id')).values_list('box_id', 'n')
    )

    result = {}
    for o in orders:
        rows = rows_by_order.get(o.id, [])
        t = {key: 0 for key in ORDER_SUM_KEYS}
        t['boxes_count'] = len(rows)
        t['unprinted_boxes_count'] = sum(1 for r in rows if not r['is_printed'] and not r['is_controlled'])

        # 3. Tikim kutmoqda: birorta ham stikeri skanerlanmagan qutilar (zakaz bo'yicha)
        waiting = [r for r in rows if r['scanned_any'] == 0]
        t['waiting_sewing_qty'] = sum(r['quantity'] for r in waiting)
        t['waiting_sewing_boxes'] = len(waiting)
        t['waiting_pastals_count'] = len({_pastal_key(r) for r in waiting})

        for item in o.items.all():
            art_rows = [r for r in rows if r['article_id'] == item.article_id]
            t['planned'] += item.total_planned_quantity
            t['boxed'] += sum(r['quantity'] for r in art_rows)

            cutting_items = [ci for s in item.sizes.all() for ci in s.cutting_items.all()]
            if cutting_items:
                for ci in cutting_items:
                    created = ci.batch.created_at if ci.batch_id and ci.batch else None
                    if created and created < day_start:
                        t['cut_prior'] += ci.quantity
                    elif created and created <= day_end:
                        t['cut_today'] += ci.quantity
            else:
                for r in art_rows:
                    if r['created_at'] and r['created_at'] < day_start:
                        t['cut_prior'] += r['quantity']
                    elif r['created_at'] and r['created_at'] <= day_end:
                        t['cut_today'] += r['quantity']

            _add_balance(t, art_rows, repair_cycles)
            _add_daily(t, art_rows, day_start, day_end)

        _finalize(t)
        result[o.id] = t
    return result


def sum_factory_totals(order_totals_list):
    """Fabrika bo'yicha jami: zakaz jamlarining yig'indisi, foizlar qayta hisoblanadi."""
    total = {key: 0 for key in ORDER_SUM_KEYS}
    for t in order_totals_list:
        for key in ORDER_SUM_KEYS:
            total[key] += t.get(key, 0)
    add_rates(total)
    return total
