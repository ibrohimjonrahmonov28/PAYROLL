from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max, Count, Sum
from django.http import JsonResponse
from django.utils import timezone
from .models import Order, OrderItem, Box, CuttingBatch, CuttingBatchItem, ArticleOperation, Article


def sticker_required(view_func):
    """Stiker chiqaruvchi yoki Superadmin/Admin uchun ruxsat tekshiruvi"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        if not (getattr(request.user, 'is_sticker', lambda: False)() or request.user.is_superadmin() or request.user.is_admin_user()):
            messages.error(request, "Ushbu bo'limga faqat Stiker chiqaruvchi xodimlar va adminlar kira oladi!")
            return redirect('production:order_list')
        return view_func(request, *args, **kwargs)
    return wrapper


@sticker_required
def sticker_dashboard(request):
    """
    Stiker Chiqarish Bo'limi Boshqaruv Paneli:
    - Meto tomonidan tasdiqlanib, avtomatik stikerlari generatsiya bo'lgan buyurtmalar
    - Chop etilishi kutilayotgan qutilar va stikerlar (Yengil va tezkor hisoblash)
    """
    search_q = request.GET.get('q', '').strip()
    filter_status = request.GET.get('status', 'unprinted')  # unprinted, printed, all

    orders_qs = Order.objects.filter(
        boxes__isnull=False
    ).select_related('customer', 'article').prefetch_related(
        'boxes__article',
        'boxes__cutting_batch_item'
    ).distinct().order_by('-created_at')

    if search_q:
        orders_qs = orders_qs.filter(
            Q(order_number__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(customer__name__icontains=search_q) |
            Q(article__name__icontains=search_q) |
            Q(article__code__icontains=search_q) |
            Q(boxes__box_code__icontains=search_q)
        ).distinct()

    orders_data = []
    total_unprinted_boxes = 0
    total_printed_boxes = 0
    unprinted_orders_count = 0
    printed_orders_count = 0
    all_orders_list = list(orders_qs)
    total_orders_count = len(all_orders_list)

    for ord_obj in all_orders_list:
        all_boxes = [b for b in ord_obj.boxes.all() if b.status != Box.Status.CANCELLED]
        unprinted = [b for b in all_boxes if not b.is_printed]
        printed = [b for b in all_boxes if b.is_printed]

        total_unprinted_boxes += len(unprinted)
        total_printed_boxes += len(printed)

        if len(unprinted) > 0:
            unprinted_orders_count += 1
        if len(printed) == len(all_boxes) and len(all_boxes) > 0:
            printed_orders_count += 1

        if filter_status == 'unprinted' and len(unprinted) == 0:
            continue
        if filter_status == 'printed' and len(printed) == 0:
            continue

        orders_data.append({
            'order': ord_obj,
            'total_boxes': len(all_boxes),
            'unprinted_count': len(unprinted),
            'printed_count': len(printed),
            'total_qty': sum(b.quantity for b in all_boxes),
            'has_unprinted': len(unprinted) > 0,
        })

    return render(request, 'stickers/dashboard.html', {
        'orders_data': orders_data,
        'search_q': search_q,
        'filter_status': filter_status,
        'total_unprinted_boxes': total_unprinted_boxes,
        'total_printed_boxes': total_printed_boxes,
        'total_orders_count': total_orders_count,
        'unprinted_orders_count': unprinted_orders_count,
        'printed_orders_count': printed_orders_count,
    })


@sticker_required
def sticker_order_boxes(request, order_id: int):
    """
    Buyurtma Qutilari va QR Stikerlarni Chop Etish Oynasi (Lazy Loading / Pastallar bo'yicha):
    - Tezkor va yengil yuklash: Barcha yuzlab qutilar va minglab stikerlar birdaniga yuklanmaydi!
    - Pastallar (CuttingBatches) ro'yxati va ularning statistikasi bir zumda ko'rsatiladi.
    - Pastal bosilganda uning qutilari bazadan yuklanadi (Lazy loading - xuddi Meto/Kroy kabi).
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article'),
        id=order_id
    )

    open_batch_id = request.GET.get('open_batch')
    try:
        open_batch_id = int(open_batch_id) if open_batch_id else None
    except (ValueError, TypeError):
        open_batch_id = None

    order_items = order.items.all().select_related('article__model').prefetch_related(
        'article__article_operations',
        'cutting_batches__items__order_item_size',
        'cutting_batches__items__boxes'
    )

    items_data = []
    articles_without_ops = []
    batches_without_boxes = []

    for item in order_items:
        art = item.article
        ops_count = art.article_operations.count() if art else 0
        if art and ops_count == 0:
            articles_without_ops.append(art)

        batches_data = []
        for batch in item.cutting_batches.all().order_by('-batch_number'):
            b_items = list(batch.items.all())
            active_boxes = [box for bi in b_items for box in bi.boxes.all() if box.status != Box.Status.CANCELLED]
            total_boxes_count = len(active_boxes)
            unprinted_count = sum(1 for b in active_boxes if not b.is_printed)
            printed_count = sum(1 for b in active_boxes if b.is_printed)
            total_qty = sum(b.quantity for b in active_boxes)

            box_numbers = [b.box_number for b in active_boxes]
            if box_numbers:
                min_box = min(box_numbers)
                max_box = max(box_numbers)
                box_range = f"#{min_box} — #{max_box}" if min_box != max_box else f"#{min_box}"
            else:
                box_range = "—"

            # Razmerlar va meto oraliqlari xulosasi (masalan: M: 45 dona (#1-#90))
            sizes_summary = []
            for bi in b_items:
                meto_str = ""
                if bi.meto_number_start and bi.meto_number_end:
                    meto_str = f"#{bi.meto_number_start}-#{bi.meto_number_end}"
                sizes_summary.append({
                    'size_name': bi.order_item_size.size_name,
                    'qty': bi.effective_quantity,
                    'meto_range': meto_str,
                })

            pending_meto_count = sum(1 for bi in b_items if bi.status == CuttingBatchItem.Status.CUT_ENTERED)
            missing_boxes_count = sum(1 for bi in b_items if len([b for b in bi.boxes.all() if b.status != Box.Status.CANCELLED]) == 0)
            confirmed_missing_count = sum(1 for bi in b_items if bi.status != CuttingBatchItem.Status.CUT_ENTERED and len([b for b in bi.boxes.all() if b.status != Box.Status.CANCELLED]) == 0)

            needs_gen = missing_boxes_count > 0
            can_generate = pending_meto_count == 0 and confirmed_missing_count > 0

            if needs_gen:
                batches_without_boxes.append({
                    'batch': batch,
                    'pastal_code': batch.pastal_code or str(batch.batch_number),
                    'missing_count': missing_boxes_count,
                    'pending_meto_count': pending_meto_count,
                    'confirmed_missing_count': confirmed_missing_count,
                    'total_count': len(b_items),
                    'can_generate': can_generate,
                })

            batches_data.append({
                'batch': batch,
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
                'total_boxes_count': total_boxes_count,
                'unprinted_count': unprinted_count,
                'printed_count': printed_count,
                'total_qty': total_qty,
                'box_range': box_range,
                'sizes_summary': sizes_summary,
                'has_boxes': total_boxes_count > 0,
                'has_unprinted': unprinted_count > 0,
                'needs_boxes_generation': needs_gen,
                'has_pending_meto': pending_meto_count > 0,
                'can_generate': can_generate,
            })

        items_data.append({
            'item': item,
            'operations_count': ops_count,
            'batches': batches_data,
        })

    # Buyurtma umumiy qutilari (tezkor hisoblash)
    all_order_boxes = order.boxes.exclude(status=Box.Status.CANCELLED)
    total_boxes_count = all_order_boxes.count()
    unprinted_boxes_count = all_order_boxes.filter(is_printed=False).count()
    printed_boxes_count = all_order_boxes.filter(is_printed=True).count()
    boxes_without_tickets_count = all_order_boxes.filter(tickets__isnull=True).count()
    available_source_articles = Article.objects.filter(article_operations__isnull=False).distinct()

    # Pastalsiz (alohida) qutilar bor bo'lsa (backward compatibility)
    unassigned_boxes = list(all_order_boxes.filter(cutting_batch_item__isnull=True).order_by('box_number'))

    return render(request, 'stickers/order_boxes.html', {
        'order': order,
        'items_data': items_data,
        'total_boxes_count': total_boxes_count,
        'unprinted_boxes_count': unprinted_boxes_count,
        'printed_boxes_count': printed_boxes_count,
        'boxes_without_tickets_count': boxes_without_tickets_count,
        'articles_without_ops': articles_without_ops,
        'available_source_articles': available_source_articles,
        'batches_without_boxes': batches_without_boxes,
        'unassigned_boxes': unassigned_boxes,
        'open_batch_id': open_batch_id,
        'boxes': all_order_boxes,
    })


@sticker_required
def sticker_batch_boxes_view(request, order_id: int, batch_id: int):
    """
    Pastal (CuttingBatch) ichidagi barcha qutilarni yuklash (Lazy Loading HTML partial):
    Faqat ushbu pastalga tegishli qutilar va biletlar yuklanadi.
    """
    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(
        CuttingBatch.objects.select_related('order_item__article'),
        id=batch_id,
        order_item__order=order
    )
    boxes = Box.objects.filter(
        cutting_batch_item__batch=batch
    ).exclude(
        status=Box.Status.CANCELLED
    ).select_related(
        'article',
        'cutting_batch_item__order_item_size'
    ).prefetch_related(
        'tickets'
    ).order_by('box_number')

    unprinted_count = sum(1 for b in boxes if not b.is_printed)

    return render(request, 'stickers/partials/batch_boxes.html', {
        'order': order,
        'batch': batch,
        'boxes': boxes,
        'unprinted_count': unprinted_count,
        'total_boxes_count': len(boxes),
    })


@sticker_required
def sticker_mark_batch_printed(request, order_id: int, batch_id: int):
    """
    Pastaldagi barcha qutilarni chop etilgan (Tikuvga berilgan) deb belgilash
    """
    if request.method != 'POST':
        return redirect('sticker_order_boxes', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(CuttingBatch, id=batch_id, order_item__order=order)
    boxes = Box.objects.filter(
        cutting_batch_item__batch=batch,
        is_printed=False
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

    messages.success(request, f"Pastal '{batch.pastal_code or batch.name}' dagi barcha ({count} ta) quti Tikuvga berildi deb belgilandi!")
    return redirect(f"/stickers/orders/{order.id}/?open_batch={batch.id}")


@sticker_required
def sticker_regenerate_tickets(request, order_id: int):
    """
    Operatsiyalar kiritilgandan so'ng, stikeri yo'q qutilar uchun barcha QR biletlarni generatsiya qilish:
    """
    order = get_object_or_404(Order, id=order_id)
    boxes = order.boxes.all()
    created_tickets_count = 0

    from .services import generate_box_tickets

    with transaction.atomic():
        for box in boxes:
            if box.tickets.count() == 0:
                tickets = generate_box_tickets(box)
                created_tickets_count += len(tickets)

    if created_tickets_count > 0:
        messages.success(request, f"Muvaffaqiyatli! Jami {created_tickets_count} ta QR stiker generatsiya qilindi va chop etishga tayyor!")
    else:
        has_ops = any(it.article and it.article.article_operations.exists() for it in order.items.all())
        if not has_ops:
            messages.error(request, "Stikerlar generatsiya qilinmadi, chunki ushbu modelga hali operatsiyalar biriktirilmagan! Avval operatsiyalarni qo'shing yoki boshqa modeldan nusxalang.")
        else:
            messages.info(request, "Barcha qutilarda allaqachon stikerlar mavjud.")

    return redirect('sticker_order_boxes', order_id=order.id)


@sticker_required
def sticker_copy_operations_and_generate(request, order_id: int):
    """
    Boshqa modeldan (masalan TKDL090 yoki Mayka) operatsiyalarni nusxalab olish va qutilarga darhol stikerlarni yaratish (POST)
    """
    if request.method != 'POST':
        return redirect('sticker_order_boxes', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    source_article_id = request.POST.get('source_article_id')
    source_art = get_object_or_404(Article, id=source_article_id)

    from .models import ArticleOperation
    from .services import generate_box_tickets

    copied_total = 0
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
                        }
                    )
                    if created:
                        copied_total += 1

        # Qutilarga darhol stikerlarni yaratish
        tickets_total = 0
        for box in order.boxes.all():
            if box.tickets.count() == 0:
                tickets = generate_box_tickets(box)
                tickets_total += len(tickets)

    messages.success(
        request,
        f"'{source_art.code}' modelidan {copied_total} ta operatsiya muvaffaqiyatli biriktirildi va {tickets_total} ta QR stiker darhol generatsiya qilindi!"
    )
    return redirect('sticker_order_boxes', order_id=order.id)


@sticker_required
def sticker_unassigned_boxes_view(request, order_id: int):
    """
    Pastalga biriktirilmagan umumiy qutilarni yuklash (Lazy Loading HTML partial):
    """
    order = get_object_or_404(Order, id=order_id)
    boxes = Box.objects.filter(
        order=order,
        cutting_batch_item__isnull=True
    ).exclude(
        status=Box.Status.CANCELLED
    ).select_related('article').prefetch_related('tickets').order_by('box_number')

    unprinted_count = sum(1 for b in boxes if not b.is_printed)

    return render(request, 'stickers/partials/batch_boxes.html', {
        'order': order,
        'batch': None,
        'boxes': boxes,
        'unprinted_count': unprinted_count,
        'total_boxes_count': len(boxes),
    })


@sticker_required
def sticker_mark_box_printed(request, box_id: int):
    """Bitta qutini chop etilgan deb belgilash va Tikuvga uzatish"""
    box = get_object_or_404(Box, id=box_id)
    box.is_printed = True
    box.printed_at = timezone.now()
    box.save(update_fields=['is_printed', 'printed_at'])

    batch_id = None
    if box.cutting_batch_item:
        batch_id = box.cutting_batch_item.batch_id
        active_remaining = Box.objects.filter(
            cutting_batch_item=box.cutting_batch_item,
            is_printed=False
        ).exclude(status=Box.Status.CANCELLED).count()
        if active_remaining == 0:
            box.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
            box.cutting_batch_item.save(update_fields=['status'])

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({'status': 'ok', 'box_id': box.id, 'batch_id': batch_id})

    messages.success(request, f"Quti #{box.box_number} [{box.box_code}] stikerlari chop etildi va Tikuvga berildi deb belgilandi!")
    if batch_id:
        return redirect(f"/stickers/orders/{box.order.id}/?open_batch={batch_id}")
    return redirect('sticker_order_boxes', order_id=box.order.id)


@sticker_required
def sticker_mark_all_printed(request, order_id: int):
    """Buyurtmaning barcha qutilarini chop etilgan deb belgilash"""
    order = get_object_or_404(Order, id=order_id)
    now = timezone.now()
    boxes = order.boxes.filter(is_printed=False).exclude(status=Box.Status.CANCELLED)
    count = boxes.count()

    with transaction.atomic():
        for b in boxes:
            b.is_printed = True
            b.printed_at = now
            b.save(update_fields=['is_printed', 'printed_at'])
            if b.cutting_batch_item:
                b.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
                b.cutting_batch_item.save(update_fields=['status'])

    messages.success(request, f"Jami {count} ta quti stikerlari muvaffaqiyatli chop etildi va Tikuvga berildi deb belgilandi!")
    return redirect('sticker_order_boxes', order_id=order.id)
