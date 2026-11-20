"""
Tayyorlov bo'limi → Stiker chiqarish qismi (/tayyorlov/stiker/...).
Eski /stickers/ bo'limining (production/sticker_views.py) yangi Tayyorlov oqimi uchun 1 ga 1 nusxasi:
- Eski /stickers/ bo'limiga umuman tegilmaydi.
- Farqi: sonlar avtomatik Tasnif bosqichidan (PastalFlowSize.tasnif_qty) keladi;
  stikerchi shu sonni qutilarga bo'ladi va xatolar hisobiga (stiker chop etilguncha) qo'lda o'zgartira oladi.
- Quti va stiker kodlari chop etilgach hech qachon o'zgarmaydi (oltin qoida).
"""
import functools

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from production import meto_flow as flow_logic
from production.models import (
    Article,
    ArticleOperation,
    Box,
    Container,
    CuttingBatch,
    CuttingBatchItem,
    Order,
    PastalFlow,
    Ticket,
)


def tayyorlov_stiker_required(view_func):
    """Tayyorlov stiker bo'limi uchun ruxsat tekshiruvi (STICKER, ADMIN, SUPER_ADMIN)."""
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('root_login')}?next={request.path}")
        if not flow_logic.can(request.user, 'stiker'):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
                return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat yo'q."}, status=403)
            messages.error(request, "Ushbu bo'limga faqat Stiker chiqaruvchi xodimlar va adminlar kira oladi!")
            return redirect('meto_hub_flow')
        return view_func(request, *args, **kwargs)
    return wrapper


def _tasnif_done_v2_batches(order=None):
    """Tasnifdan o'tgan barcha Tayyorlov (v2) pastallari."""
    qs = flow_logic.v2_batches().filter(flow__tasnif_done_at__isnull=False)
    if order is not None:
        qs = qs.filter(order_item__order=order)
    return qs


def _batch_tasnif_total(batch):
    total = 0
    for bi in batch.items.all():
        fs = getattr(bi, 'flow_size', None)
        if fs and fs.tasnif_qty is not None:
            total += fs.tasnif_qty
        elif bi.real_quantity is not None:
            total += bi.real_quantity
    return total


def _batch_active_boxes(batch):
    return [box for bi in batch.items.all() for box in bi.boxes.all() if box.status != Box.Status.CANCELLED]


def stiker_dashboard_context(request):
    """
    /tayyorlov/stiker/ bosh sahifasi konteksti (/stickers/ dashboardining 1 ga 1 nusxasi):
    Tasnifdan o'tgan pastallari bor buyurtmalar ro'yxati, KPI kartalar, holat filtri va qidiruv.
    """
    flow_logic.ensure_flows()
    search_q = request.GET.get('q', '').strip()
    filter_status = request.GET.get('status', 'unprinted')  # unprinted, printed, all
    if filter_status not in ('unprinted', 'printed', 'all'):
        filter_status = 'unprinted'

    batches_qs = (
        _tasnif_done_v2_batches()
        .select_related('order_item__order__customer', 'order_item__order__article', 'order_item__article', 'flow')
        .prefetch_related('items__order_item_size', 'items__flow_size', 'items__boxes')
        .order_by('-flow__tasnif_done_at', '-id')
    )

    if search_q:
        batches_qs = batches_qs.filter(
            Q(order_item__order__order_number__icontains=search_q)
            | Q(order_item__order__client_name__icontains=search_q)
            | Q(order_item__order__customer__name__icontains=search_q)
            | Q(order_item__article__name__icontains=search_q)
            | Q(order_item__article__code__icontains=search_q)
            | Q(pastal_code__icontains=search_q)
            | Q(name__icontains=search_q)
            | Q(items__boxes__box_code__icontains=search_q)
        ).distinct()

    # Buyurtmalar bo'yicha guruhlash (eng yangi tasniflangan zakaz tepada)
    orders_map = {}
    for batch in batches_qs:
        ord_obj = batch.order_item.order
        entry = orders_map.get(ord_obj.id)
        if entry is None:
            entry = {'order': ord_obj, 'batches': []}
            orders_map[ord_obj.id] = entry
        entry['batches'].append(batch)

    orders_data = []
    total_unsplit_pastals = 0
    total_unprinted_boxes = 0
    total_printed_boxes = 0
    unprinted_orders_count = 0
    printed_orders_count = 0
    total_orders_count = len(orders_map)

    for entry in orders_map.values():
        ord_obj = entry['order']
        ord_batches = entry['batches']
        all_boxes = []
        unsplit_batches = []
        total_tasnif_qty = 0

        for b in ord_batches:
            b_boxes = _batch_active_boxes(b)
            b_tasnif = _batch_tasnif_total(b)
            total_tasnif_qty += b_tasnif
            all_boxes.extend(b_boxes)
            if not b_boxes and b.flow.stage == PastalFlow.Stage.SKLAD and b_tasnif > 0:
                unsplit_batches.append(b)

        unprinted = [b for b in all_boxes if not b.is_printed]
        printed = [b for b in all_boxes if b.is_printed]

        total_unsplit_pastals += len(unsplit_batches)
        total_unprinted_boxes += len(unprinted)
        total_printed_boxes += len(printed)

        needs_work = len(unsplit_batches) > 0 or len(unprinted) > 0
        all_done = len(unsplit_batches) == 0 and len(all_boxes) > 0 and len(unprinted) == 0

        if needs_work:
            unprinted_orders_count += 1
        if all_done:
            printed_orders_count += 1

        if filter_status == 'unprinted' and not needs_work:
            continue
        if filter_status == 'printed' and len(printed) == 0:
            continue

        first_unsplit_batch_id = unsplit_batches[0].id if unsplit_batches else None
        first_unprinted_batch_id = None
        if not first_unsplit_batch_id and unprinted:
            first_unprinted_batch_id = unprinted[0].cutting_batch_item.batch_id if unprinted[0].cutting_batch_item_id else None

        articles = []
        for b in ord_batches:
            art = b.order_item.article
            if art and art.id not in [a['id'] for a in articles]:
                img_url = ''
                if art.image:
                    try:
                        img_url = art.image.url
                    except Exception:
                        img_url = ''
                articles.append({'id': art.id, 'name': art.name, 'code': art.code, 'image': img_url})

        printed_pct = int(round((len(printed) / len(all_boxes) * 100))) if all_boxes else 0

        orders_data.append({
            'order': ord_obj,
            'articles': articles,
            'pastals_count': len(ord_batches),
            'unsplit_count': len(unsplit_batches),
            'total_boxes': len(all_boxes),
            'unprinted_count': len(unprinted),
            'printed_count': len(printed),
            'printed_pct': printed_pct,
            'total_qty': total_tasnif_qty,
            'has_unprinted': needs_work,
            'first_open_batch_id': first_unsplit_batch_id or first_unprinted_batch_id,
        })

    return {
        'orders_data': orders_data,
        'search_q': search_q,
        'filter_status': filter_status,
        'total_unsplit_pastals': total_unsplit_pastals,
        'total_unprinted_boxes': total_unprinted_boxes,
        'total_printed_boxes': total_printed_boxes,
        'total_orders_count': total_orders_count,
        'unprinted_orders_count': unprinted_orders_count,
        'printed_orders_count': printed_orders_count,
    }


@tayyorlov_stiker_required
def tayyorlov_stiker_order_boxes(request, order_id: int):
    """
    Buyurtma Qutilari va QR Stikerlarni Chop Etish Oynasi (/stickers/orders/<id>/ ning 1 ga 1 nusxasi):
    - Faqat ushbu buyurtmaning Tasnifdan o'tgan Tayyorlov pastallarini ko'rsatadi.
    - Har bir pastal bosilganda Lazy Loading orqali:
        * Hali qutiga bo'linmagan bo'lsa — Tasnifdan kelgan son bilan qutiga bo'lish va o'zgartirish oynasi chiqadi.
        * Bo'lingan bo'lsa — qutilar jadvali, chop etish va tikuvga berildi belgilash (+ chop etilguncha o'zgartirish) chiqadi.
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article'),
        id=order_id,
    )

    open_batch_id = request.GET.get('open_batch')
    try:
        open_batch_id = int(open_batch_id) if open_batch_id else None
    except (ValueError, TypeError):
        open_batch_id = None

    v2_done_ids = set(_tasnif_done_v2_batches(order=order).values_list('id', flat=True))

    order_items = order.items.all().select_related('article__model').prefetch_related(
        'article__article_operations',
        'cutting_batches__flow__tasnif_done_by',
        'cutting_batches__items__order_item_size',
        'cutting_batches__items__flow_size',
        'cutting_batches__items__boxes',
    )

    items_data = []
    articles_without_ops = []
    batches_without_boxes = []
    all_v2_boxes = []
    unsplit_pastals_count = 0

    for item in order_items:
        art = item.article
        ops_count = art.article_operations.count() if art else 0

        batches_data = []
        v2_item_batches = [
            b for b in item.cutting_batches.all().order_by('-batch_number')
            if b.id in v2_done_ids
        ]
        if not v2_item_batches:
            continue

        if art and ops_count == 0:
            articles_without_ops.append(art)

        for batch in v2_item_batches:
            flow = batch.flow
            b_items = sorted(batch.items.all(), key=lambda it: (it.order_item_size_id, it.id))
            active_boxes = [box for bi in b_items for box in bi.boxes.all() if box.status != Box.Status.CANCELLED]
            active_boxes.sort(key=lambda b: b.box_number)
            all_v2_boxes.extend(active_boxes)

            total_boxes_count = len(active_boxes)
            unprinted_count = sum(1 for b in active_boxes if not b.is_printed)
            printed_count = sum(1 for b in active_boxes if b.is_printed)
            boxed_qty = sum(b.quantity for b in active_boxes)
            tasnif_total_qty = _batch_tasnif_total(batch)
            total_qty = boxed_qty if active_boxes else tasnif_total_qty

            if active_boxes:
                first_code = active_boxes[0].display_code
                last_code = active_boxes[-1].display_code
                box_range = f"{first_code} — {last_code}" if first_code != last_code else f"{first_code}"
            else:
                box_range = "Bo'linmagan"

            sizes_summary = []
            for bi in b_items:
                fs = getattr(bi, 'flow_size', None)
                t_qty = fs.tasnif_qty if (fs and fs.tasnif_qty is not None) else bi.effective_quantity
                m_qty = fs.meto_qty if fs else None
                meto_str = ""
                if bi.meto_number_start and bi.meto_number_end:
                    meto_str = f"#{bi.meto_number_start}-#{bi.meto_number_end}"
                sizes_summary.append({
                    'size_name': bi.order_item_size.size_name,
                    'qty': t_qty,
                    'meto_qty': m_qty,
                    'cut_qty': bi.quantity,
                    'meto_range': meto_str,
                })

            needs_split = (total_boxes_count == 0 and flow.stage == PastalFlow.Stage.SKLAD and tasnif_total_qty > 0)
            if needs_split:
                unsplit_pastals_count += 1
                batches_without_boxes.append({
                    'batch': batch,
                    'pastal_code': batch.pastal_code or str(batch.batch_number),
                    'tasnif_total_qty': tasnif_total_qty,
                    'sizes_count': len(b_items),
                    'tasnif_done_at': flow.tasnif_done_at,
                })

            batches_data.append({
                'batch': batch,
                'flow': flow,
                'id': batch.id,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'pastal_code': batch.pastal_code or str(batch.batch_number),
                'partiya_number': batch.partiya_number,
                'cutter_name': batch.cutter_name,
                'fabric_weight_kg': batch.fabric_weight_kg,
                'fabric_batch_code': batch.fabric_batch_code,
                'notes': batch.notes,
                'created_at': batch.created_at,
                'tasnif_done_at': flow.tasnif_done_at,
                'tasnif_done_by_name': flow_logic.user_name(flow.tasnif_done_by),
                'total_boxes_count': total_boxes_count,
                'unprinted_count': unprinted_count,
                'printed_count': printed_count,
                'total_qty': total_qty,
                'tasnif_total_qty': tasnif_total_qty,
                'box_range': box_range,
                'sizes_summary': sizes_summary,
                'has_boxes': total_boxes_count > 0,
                'has_unprinted': unprinted_count > 0 or needs_split,
                'needs_split': needs_split,
            })

        items_data.append({
            'item': item,
            'operations_count': ops_count,
            'batches': batches_data,
        })

    v2_box_ids = [b.id for b in all_v2_boxes]
    total_boxes_count = len(all_v2_boxes)
    unprinted_boxes_count = sum(1 for b in all_v2_boxes if not b.is_printed)
    printed_boxes_count = sum(1 for b in all_v2_boxes if b.is_printed)
    boxes_without_tickets_count = (
        Box.objects.filter(id__in=v2_box_ids, tickets__isnull=True).count() if v2_box_ids else 0
    )
    available_source_articles = Article.objects.filter(article_operations__isnull=False).distinct()

    return render(request, 'tayyorlov/stiker_order_boxes.html', {
        'order': order,
        'items_data': items_data,
        'total_boxes_count': total_boxes_count,
        'unprinted_boxes_count': unprinted_boxes_count,
        'printed_boxes_count': printed_boxes_count,
        'unsplit_pastals_count': unsplit_pastals_count,
        'boxes_without_tickets_count': boxes_without_tickets_count,
        'articles_without_ops': articles_without_ops,
        'available_source_articles': available_source_articles,
        'batches_without_boxes': batches_without_boxes,
        'open_batch_id': open_batch_id,
    })


@tayyorlov_stiker_required
def tayyorlov_stiker_batch_boxes(request, order_id: int, batch_id: int):
    """
    Pastal ichidagi qutilar yoki Tasnif sonini qutilarga bo'lish oynasi (Lazy Loading partial).
    """
    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(
        _tasnif_done_v2_batches(order=order).select_related(
            'order_item__article', 'flow__tasnif_done_by', 'flow__meto_done_by', 'flow__boxes_split_by'
        ).prefetch_related('items__order_item_size', 'items__flow_size'),
        id=batch_id,
    )
    flow = batch.flow
    boxes = list(
        Box.objects.filter(cutting_batch_item__batch=batch)
        .exclude(status=Box.Status.CANCELLED)
        .select_related('article', 'cutting_batch_item__order_item_size')
        .prefetch_related('tickets')
        .order_by('box_number')
    )
    unprinted_count = sum(1 for b in boxes if not b.is_printed)
    printed_count = len(boxes) - unprinted_count
    linked_count = Container.objects.filter(box__in=boxes).count() if boxes else 0

    article = batch.order_item.article
    operations_count = article.article_operations.count() if article else 0

    sizes = []
    for item in sorted(batch.items.all(), key=lambda it: (it.order_item_size_id, it.id)):
        fs = getattr(item, 'flow_size', None)
        t_qty = fs.tasnif_qty if (fs and fs.tasnif_qty is not None) else item.effective_quantity
        sizes.append({
            'id': item.id,
            'name': item.order_item_size.size_name,
            'cut': item.quantity,
            'meto': fs.meto_qty if fs else None,
            'tasnif': t_qty,
        })

    can_split = len(boxes) == 0 and flow.stage == PastalFlow.Stage.SKLAD
    can_edit_tasnif = (
        flow.stage == PastalFlow.Stage.SKLAD
        and printed_count == 0
        and linked_count == 0
    )

    return render(request, 'tayyorlov/partials/stiker_batch_boxes.html', {
        'order': order,
        'batch': batch,
        'flow': flow,
        'boxes': boxes,
        'sizes': sizes,
        'total_tasnif': sum(s['tasnif'] or 0 for s in sizes),
        'unprinted_count': unprinted_count,
        'printed_count': printed_count,
        'total_boxes_count': len(boxes),
        'operations_count': operations_count,
        'can_split': can_split,
        'can_edit_tasnif': can_edit_tasnif,
        'tasnif_done_by_name': flow_logic.user_name(flow.tasnif_done_by),
        'split_by_name': flow_logic.user_name(flow.boxes_split_by),
    })


@tayyorlov_stiker_required
def tayyorlov_stiker_mark_batch_printed(request, order_id: int, batch_id: int):
    """Pastaldagi barcha qutilarni chop etilgan (Tikuvga berilgan) deb belgilash."""
    if request.method != 'POST':
        return redirect('tayyorlov_stiker_order_boxes', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(_tasnif_done_v2_batches(order=order), id=batch_id)
    boxes = Box.objects.filter(
        cutting_batch_item__batch=batch,
        is_printed=False,
    ).exclude(status=Box.Status.CANCELLED)

    count = boxes.count()
    now = timezone.now()
    with transaction.atomic():
        boxes.update(is_printed=True, printed_at=now)
        batch.items.filter(
            status=CuttingBatchItem.Status.METO_CONFIRMED
        ).update(status=CuttingBatchItem.Status.STICKERS_PRINTED)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({'status': 'ok', 'count': count, 'batch_id': batch.id})

    messages.success(
        request,
        f"Pastal '{batch.pastal_code or batch.name}' dagi barcha ({count} ta) quti Tikuvga berildi deb belgilandi!",
    )
    return redirect(f"{reverse('tayyorlov_stiker_order_boxes', args=[order.id])}?open_batch={batch.id}")


@tayyorlov_stiker_required
def tayyorlov_stiker_mark_box_printed(request, box_id: int):
    """Bitta Tayyorlov qutisini chop etilgan deb belgilash."""
    if request.method != 'POST':
        return redirect('meto_hub_flow_stiker')

    box = get_object_or_404(Box.objects.select_related('order', 'cutting_batch_item__batch__order_item__order'), id=box_id)
    if not flow_logic.is_v2_box(box):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
            return JsonResponse({'status': 'ERROR', 'message': "Bu quti Tayyorlov oqimida emas."}, status=400)
        messages.error(request, "Bu quti Tayyorlov oqimida emas.")
        return redirect('meto_hub_flow_stiker')

    box.is_printed = True
    box.printed_at = timezone.now()
    box.save(update_fields=['is_printed', 'printed_at'])

    batch_id = None
    if box.cutting_batch_item:
        batch_id = box.cutting_batch_item.batch_id
        active_remaining = Box.objects.filter(
            cutting_batch_item=box.cutting_batch_item,
            is_printed=False,
        ).exclude(status=Box.Status.CANCELLED).count()
        if active_remaining == 0:
            box.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
            box.cutting_batch_item.save(update_fields=['status'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({'status': 'ok', 'box_id': box.id, 'batch_id': batch_id})

    messages.success(request, f"Quti #{box.box_number} [{box.box_code}] stikerlari chop etildi deb belgilandi!")
    url = reverse('tayyorlov_stiker_order_boxes', args=[box.order_id])
    return redirect(f"{url}?open_batch={batch_id}" if batch_id else url)


@tayyorlov_stiker_required
def tayyorlov_stiker_mark_all_printed(request, order_id: int):
    """Buyurtmaning Tayyorlov oqimidagi barcha chop etilmagan qutilarini chop etilgan deb belgilash."""
    if request.method != 'POST':
        return redirect('tayyorlov_stiker_order_boxes', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    v2_batch_ids = list(_tasnif_done_v2_batches(order=order).values_list('id', flat=True))
    now = timezone.now()
    boxes = Box.objects.filter(
        order=order,
        cutting_batch_item__batch_id__in=v2_batch_ids,
        is_printed=False,
    ).exclude(status=Box.Status.CANCELLED)
    count = boxes.count()

    with transaction.atomic():
        for b in boxes.select_related('cutting_batch_item'):
            b.is_printed = True
            b.printed_at = now
            b.save(update_fields=['is_printed', 'printed_at'])
            if b.cutting_batch_item:
                b.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
                b.cutting_batch_item.save(update_fields=['status'])

    messages.success(request, f"Jami {count} ta quti stikerlari chop etildi va Tikuvga berildi deb belgilandi!")
    return redirect('tayyorlov_stiker_order_boxes', order_id=order.id)


@tayyorlov_stiker_required
def tayyorlov_stiker_regenerate_tickets(request, order_id: int):
    """Chop etilmagan va skanerlanmagan Tayyorlov qutilari uchun QR biletlarni generatsiya qilish (oltin qoida saqlanadi)."""
    from production.services import generate_box_tickets

    order = get_object_or_404(Order, id=order_id)
    v2_batch_ids = list(_tasnif_done_v2_batches(order=order).values_list('id', flat=True))
    boxes = Box.objects.filter(
        order=order,
        cutting_batch_item__batch_id__in=v2_batch_ids,
        is_printed=False,
    ).exclude(status=Box.Status.CANCELLED)
    created_tickets_count = 0

    with transaction.atomic():
        for item in order.items.select_related('article__operation_group'):
            if item.article and item.article.operation_group:
                item.article.sync_operations_from_group(sync_unscanned_boxes=False)
        if order.article and order.article.operation_group:
            order.article.sync_operations_from_group(sync_unscanned_boxes=False)

        for box in boxes:
            if not box.tickets.filter(status=Ticket.Status.SCANNED).exists():
                tickets = generate_box_tickets(box)
                created_tickets_count += len(tickets)

    if created_tickets_count > 0:
        messages.success(
            request,
            f"Muvaffaqiyatli! Jami {created_tickets_count} ta QR stiker Narxlar matritsasi bo'yicha generatsiya qilindi va chop etishga tayyor!",
        )
    else:
        has_ops = any(it.article and it.article.article_operations.exists() for it in order.items.all())
        if not has_ops:
            messages.error(request, "Stikerlar generatsiya qilinmadi, chunki modelga hali operatsiyalar biriktirilmagan!")
        else:
            messages.info(request, "Barcha qutilarda stikerlar chop etilgan yoki allaqachon so'nggi holatda.")

    return redirect('tayyorlov_stiker_order_boxes', order_id=order.id)


@tayyorlov_stiker_required
def tayyorlov_stiker_copy_operations(request, order_id: int):
    """Boshqa modeldan operatsiyalarni nusxalab olish va stikersiz Tayyorlov qutilariga stiker yaratish."""
    from production.services import generate_box_tickets

    if request.method != 'POST':
        return redirect('tayyorlov_stiker_order_boxes', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    source_article_id = request.POST.get('source_article_id')
    source_art = get_object_or_404(Article, id=source_article_id)

    v2_batch_ids = list(_tasnif_done_v2_batches(order=order).values_list('id', flat=True))
    copied_total = 0
    tickets_total = 0

    with transaction.atomic():
        for item in order.items.all():
            target_art = item.article
            if target_art and target_art.article_operations.count() == 0:
                for src_ao in source_art.article_operations.all():
                    _, created = ArticleOperation.objects.get_or_create(
                        article=target_art,
                        operation=src_ao.operation,
                        defaults={
                            'price_per_unit': src_ao.price_per_unit,
                            'sequence': src_ao.sequence,
                            'difficulty': src_ao.difficulty,
                        },
                    )
                    if created:
                        copied_total += 1

        for box in Box.objects.filter(
            order=order,
            cutting_batch_item__batch_id__in=v2_batch_ids,
            is_printed=False,
        ).exclude(status=Box.Status.CANCELLED):
            if box.tickets.count() == 0:
                tickets = generate_box_tickets(box)
                tickets_total += len(tickets)

    messages.success(
        request,
        f"'{source_art.code}' modelidan {copied_total} ta operatsiya biriktirildi va {tickets_total} ta QR stiker generatsiya qilindi!",
    )
    return redirect('tayyorlov_stiker_order_boxes', order_id=order.id)
