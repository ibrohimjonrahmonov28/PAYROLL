from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max, Sum, Count
from django.utils import timezone
from .models import Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box, ArticleOperation, Ticket
from .services import auto_generate_boxes_for_batch_item


def meto_required(view_func):
    """Metochi (Nomerovkachi) yoki Superadmin uchun ruxsat tekshiruvi"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        if not (getattr(request.user, 'is_meto', lambda: False)() or request.user.is_superadmin() or request.user.is_admin_user()):
            messages.error(request, "Ushbu bo'limga faqat Meto (Nomerovka) bo'limi xodimlari kira oladi!")
            return redirect('production:order_list')
        return view_func(request, *args, **kwargs)
    return wrapper


@meto_required
def meto_dashboard(request):
    """
    Meto (Nomerovka) Bo'limi Bosh Sahifasi:
    - Kesimdan kelgan va Meto kutilayotgan partiyalar ro'yxati
    - Qavatlar nomirovkasi va real sonlarni tasdiqlash uchun buyurtmalar
    """
    search_q = request.GET.get('q', '').strip()
    orders_qs = Order.objects.filter(
        status=Order.Status.IN_PROGRESS
    ).select_related('customer', 'article').prefetch_related(
        'items__article__model',
        'items__sizes',
        'items__cutting_batches__items__order_item_size'
    ).order_by('-created_at')

    if search_q:
        orders_qs = orders_qs.filter(
            Q(order_number__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(customer__name__icontains=search_q) |
            Q(items__article__name__icontains=search_q) |
            Q(items__article__code__icontains=search_q)
        ).distinct()

    orders_data = []
    total_pending_meto_count = 0
    total_confirmed_meto_count = 0

    for ord_obj in orders_qs:
        pending_items_count = 0
        confirmed_items_count = 0
        total_cut_qty = 0
        total_real_qty = 0

        for item in ord_obj.items.all():
            for batch in item.cutting_batches.all():
                for b_it in batch.items.all():
                    if b_it.status == CuttingBatchItem.Status.CUT_ENTERED:
                        pending_items_count += 1
                        total_cut_qty += b_it.quantity
                    else:
                        confirmed_items_count += 1
                        total_real_qty += b_it.effective_quantity

        total_pending_meto_count += pending_items_count
        total_confirmed_meto_count += confirmed_items_count

        orders_data.append({
            'order': ord_obj,
            'pending_items_count': pending_items_count,
            'confirmed_items_count': confirmed_items_count,
            'total_cut_qty': total_cut_qty,
            'total_real_qty': total_real_qty,
            'has_pending': pending_items_count > 0,
        })

    return render(request, 'meto/dashboard.html', {
        'orders_data': orders_data,
        'search_q': search_q,
        'total_pending_meto_count': total_pending_meto_count,
        'total_confirmed_meto_count': total_confirmed_meto_count,
    })


@meto_required
def meto_order_detail(request, order_id: int):
    """
    Meto Nomerovka va Tasdiqlash Oynasi:
    - Tezkor va yengil yuklash: Barcha quti va biletlar oldindan yuklanmaydi.
    - Pastallar (CuttingBatches) ro'yxati va ularning umumiy statistikasi bir zumda ko'rsatiladi.
    - Pastal ochilganda uning razmerlari va qutilari bazadan yuklanadi (Lazy loading).
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
        'cutting_batches__items__boxes'
    )

    items_data = []
    for item in order_items:
        operations_count = item.article.article_operations.count() if item.article else 0
        batches_data = []

        for batch in item.cutting_batches.all().order_by('-batch_number'):
            b_items = list(batch.items.all())
            total_cut_qty = sum(bi.quantity for bi in b_items)
            total_real_qty = sum(bi.effective_quantity for bi in b_items)
            confirmed_items_count = sum(1 for bi in b_items if bi.status != CuttingBatchItem.Status.CUT_ENTERED)
            total_items_count = len(b_items)
            has_pending = confirmed_items_count < total_items_count
            active_boxes_count = sum(len([b for b in bi.boxes.all() if b.status != Box.Status.CANCELLED]) for bi in b_items)
            total_boxes_count = active_boxes_count
            has_boxes = total_boxes_count > 0
            needs_boxes_generation = any(len([b for b in bi.boxes.all() if b.status != Box.Status.CANCELLED]) == 0 for bi in b_items)

            batches_data.append({
                'batch': batch,
                'id': batch.id,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'cutter_name': batch.cutter_name,
                'pastal_code': batch.pastal_code,
                'partiya_number': batch.partiya_number,
                'fabric_weight_kg': batch.fabric_weight_kg,
                'fabric_batch_code': batch.fabric_batch_code,
                'notes': batch.notes,
                'created_at': batch.created_at,
                'total_cut_qty': total_cut_qty,
                'total_real_qty': total_real_qty,
                'total_items_count': total_items_count,
                'confirmed_items_count': confirmed_items_count,
                'has_pending': has_pending,
                'has_boxes': has_boxes,
                'needs_boxes_generation': needs_boxes_generation,
                'total_boxes_count': total_boxes_count,
            })

        items_data.append({
            'item': item,
            'operations_count': operations_count,
            'batches': batches_data,
        })

    return render(request, 'meto/order_detail.html', {
        'order': order,
        'items_data': items_data,
        'open_batch_id': open_batch_id,
    })


@meto_required
def meto_batch_items_view(request, batch_id: int):
    """
    Pastal (CuttingBatch) ichidagi razmerlar va qutilar ma'lumotlarini bazadan yuklab berish (Lazy Loading):
    """
    batch = get_object_or_404(
        CuttingBatch.objects.select_related(
            'order_item__order',
            'order_item__article'
        ).prefetch_related(
            'items__order_item_size',
            'items__boxes__tickets'
        ),
        id=batch_id
    )
    order = batch.order_item.order
    article = batch.order_item.article

    batch_items_data = []
    for b_it in batch.items.all().order_by('order_item_size__id'):
        boxes = list(b_it.boxes.exclude(status=Box.Status.CANCELLED))
        has_printed = any(b.is_printed for b in boxes)
        has_scanned = any(t.status == Ticket.Status.SCANNED for b in boxes for t in b.tickets.all())
        batch_items_data.append({
            'item': b_it,
            'id': b_it.id,
            'size_name': b_it.order_item_size.size_name,
            'quantity': b_it.quantity,
            'status': b_it.status,
            'status_display': b_it.get_status_display(),
            'is_confirmed': b_it.status != CuttingBatchItem.Status.CUT_ENTERED,
            'real_quantity': b_it.real_quantity or b_it.quantity,
            'effective_quantity': b_it.effective_quantity,
            'meto_number_start': b_it.meto_number_start or "1",
            'meto_number_end': b_it.meto_number_end or str(b_it.quantity),
            'meto_worker_name': b_it.meto_worker_name,
            'meto_notes': b_it.meto_notes,
            'meto_completed_at': b_it.meto_completed_at,
            'boxes_count': len(boxes),
            'boxes': boxes,
            'can_reset': b_it.status != CuttingBatchItem.Status.CUT_ENTERED and not has_scanned,
            'has_printed': has_printed,
        })

    return render(request, 'meto/partials/batch_items.html', {
        'batch': batch,
        'order': order,
        'article': article,
        'batch_items': batch_items_data,
    })


@meto_required
def meto_reset_item(request, item_id: int):
    """
    Qutilarni qayta taqsimlash (Re-split / Reset):
    - Eski qutilarni bazadan o'chirib yubormaydi, balki CANCELLED (Bekor qilingan) qiladi.
    - Agar eski qog'oz stiker skanerlansa, terminal "Ushbu stiker bekor qilingan (eskirgan)" deb aniq ko'rsatadi!
    - Yangi qutilar noldan to'g'ri taqsimlanadi.
    """
    if request.method != 'POST':
        return redirect('meto_dashboard')

    batch_item = get_object_or_404(
        CuttingBatchItem.objects.select_related(
            'batch__order_item__order',
            'order_item_size'
        ),
        id=item_id
    )
    order = batch_item.batch.order_item.order

    # Skanerlangan biletlar bor-yo'qligini tekshirish
    has_scanned = batch_item.boxes.filter(tickets__status=Ticket.Status.SCANNED).exists()

    if has_scanned:
        messages.error(
            request,
            f"'{batch_item.order_item_size.size_name}' razmeri bo'yicha operatsiyalar tikuvchilar tomonidan allaqachon skanerlangan! "
            f"Ushbu qutilarni o'zgartirib bo'lmaydi."
        )
        return redirect(f"/meto/orders/{order.id}/?open_batch={batch_item.batch_id}")

    with transaction.atomic():
        # Qutilarni o'chirmasdan, BEKOR QILINDI (CANCELLED) holatiga o'tkazish
        # Agar eski stikerlar chop etilgan bo'lsa, skanerda "Eskirgan/Bekor qilingan" deb aniq ko'rsatiladi
        active_boxes = batch_item.boxes.exclude(status=Box.Status.CANCELLED)
        cancelled_count = active_boxes.count()
        for b in active_boxes:
            b.status = Box.Status.CANCELLED
            b.save(update_fields=['status'])
            b.tickets.filter(status=Ticket.Status.PENDING).update(status=Ticket.Status.CANCELLED)

        batch_item.boxes_created_qty = 0
        batch_item.status = CuttingBatchItem.Status.CUT_ENTERED
        batch_item.save(update_fields=['boxes_created_qty', 'status'])

    messages.success(
        request,
        f"'{batch_item.order_item_size.size_name}' razmeri qutilari qaytadan taqsimlash uchun ochildi! "
        f"Oldingi {cancelled_count} ta quti bekor qilindi (agar eski stikerlar chop etilgan bo'lsa, "
        f"skanerda ular 'Bekor qilingan (Eskirgan)' deb ko'rsatiladi)."
    )
    return redirect(f"/meto/orders/{order.id}/?open_batch={batch_item.batch_id}")


@meto_required
def meto_confirm_item(request, item_id: int):
    """
    Meto Bo'limi Tasdiqlash va Ishni Tugatish (POST):
    - Foydalanuvchi talabi:
      "KEYIN ULAR KERAKLI RASMERLARNI DETALINI YIGIB ANIQ BOLGAN SONNI KIRITADI VA TUGATADI ISHNI STIKER CHIQARISHGA BERADI
       U TUGATGANDA AVTOMATIK STIKERLAR GENERATISYA BOLADI VA STIKER CHIQARADIGAN ODAM OZI CHIQARADI VA TIKUVGA BERADI"
    """
    if request.method != 'POST':
        return redirect('meto_dashboard')

    batch_item = get_object_or_404(
        CuttingBatchItem.objects.select_related(
            'batch__order_item__order',
            'batch__order_item__article',
            'order_item_size'
        ),
        id=item_id
    )
    order = batch_item.batch.order_item.order
    article = batch_item.batch.order_item.article
    size_name = batch_item.order_item_size.size_name

    real_qty_str = request.POST.get('real_quantity', '').strip()
    meto_start = request.POST.get('meto_number_start', '').strip()
    meto_end = request.POST.get('meto_number_end', '').strip()
    meto_worker = request.POST.get('meto_worker_name', '').strip()
    notes = request.POST.get('meto_notes', '').strip()
    box_count_str = request.POST.get('box_count', '').strip()
    split_mode = request.POST.get('split_mode', 'single')  # single, split_2, split_3, capacity_50, capacity_100

    try:
        real_qty = max(0, int(real_qty_str))
    except (ValueError, TypeError):
        real_qty = batch_item.quantity

    if real_qty == 0:
        messages.warning(request, f"'{size_name}' razmeri bo'yicha aniq son 0 bo'lishi mumkin emas!")
        return redirect(f"/meto/orders/{order.id}/?open_batch={batch_item.batch_id}")

    # Qutilarga bo'lish konfiguratsiyasi
    split_count = 1
    box_capacity = None
    if box_count_str:
        try:
            split_count = max(1, min(int(box_count_str), real_qty))
        except (ValueError, TypeError):
            split_count = 1
    elif split_mode == 'split_2':
        split_count = 2
    elif split_mode == 'split_3':
        split_count = 3
    elif split_mode == 'capacity_50':
        box_capacity = 50
    elif split_mode == 'capacity_100':
        box_capacity = 100

    # Meto raqamlari avtomatik to'ldirish
    if not meto_start:
        meto_start = "1"
    if not meto_end:
        meto_end = str(real_qty)

    worker_name = meto_worker or (request.user.get_full_name() or request.user.username)

    with transaction.atomic():
        batch_item.real_quantity = real_qty
        batch_item.meto_number_start = meto_start
        batch_item.meto_number_end = meto_end
        batch_item.meto_worker_name = worker_name
        batch_item.meto_notes = notes
        batch_item.meto_completed_at = timezone.now()
        batch_item.meto_completed_by = request.user
        batch_item.status = CuttingBatchItem.Status.METO_CONFIRMED
        batch_item.save()

        # AVTOMATIK STIKERLAR VA QUTILARNI GENERATSIYA QILISH
        pastal_number = request.POST.get('pastal_number', '').strip() or (batch_item.batch.pastal_code if batch_item.batch else '') or str(batch_item.batch.batch_number)
        boxes = auto_generate_boxes_for_batch_item(
            batch_item=batch_item,
            split_count=split_count,
            box_capacity=box_capacity,
            pastal_number=pastal_number
        )

    ops_count = ArticleOperation.objects.filter(article=article).count()
    if ops_count == 0:
        messages.warning(
            request,
            f"Razmer [{size_name}]: Meto tasdiqlandi (Aniq son: {real_qty} dona, Meto #{meto_start}-#{meto_end}). "
            f"Biroq ushbu artikulda hali operatsiyalar (narxlar) kiritilmagan, shuning uchun QR stikerlar hali hosil bo'lmadi! "
            f"Iltimos, avval modelga operatsiyalarni qo'shing."
        )
    else:
        messages.success(
            request,
            f"Razmer [{size_name}]: Meto yakunlandi! Aniq son: {real_qty} dona (Meto #{meto_start}-#{meto_end}). "
            f"{len(boxes)} ta quti va barcha QR stikerlar avtomatik generatsiya qilindi va Stiker bo'limiga uzatildi!"
        )

    return redirect(f"/meto/orders/{order.id}/?open_batch={batch_item.batch_id}")


@meto_required
def meto_generate_missing_boxes(request, batch_id: int):
    """
    Pastalning qutisi yaratilmagan barcha bandlari uchun qutilar va QR stikerlarni bir zumda generatsiya qilish:
    """
    batch = get_object_or_404(
        CuttingBatch.objects.select_related('order_item__order', 'order_item__article'),
        id=batch_id
    )
    order = batch.order_item.order
    created_boxes_total = 0

    with transaction.atomic():
        for b_it in batch.items.all():
            if b_it.boxes.count() == 0:
                if b_it.status == CuttingBatchItem.Status.CUT_ENTERED:
                    b_it.status = CuttingBatchItem.Status.METO_CONFIRMED
                    if not b_it.real_quantity:
                        b_it.real_quantity = b_it.quantity
                    b_it.save(update_fields=['status', 'real_quantity'])

                if b_it.remaining_to_box > 0:
                    boxes = auto_generate_boxes_for_batch_item(
                        batch_item=b_it,
                        split_count=1,
                        pastal_number=batch.pastal_code or str(batch.batch_number)
                    )
                    created_boxes_total += len(boxes)

    if created_boxes_total > 0:
        messages.success(
            request,
            f"Muvaffaqiyatli! '{batch.name}' (Pastal: {batch.pastal_code or '—'}) bo'yicha {created_boxes_total} ta quti va barcha QR stikerlar generatsiya qilindi va Stikerlar bo'limiga uzatildi!"
        )
    else:
        messages.info(request, "Ushbu pastalning barcha qutilari allaqachon mavjud.")

    return_to = request.GET.get('from')
    if return_to == 'stickers':
        return redirect('sticker_order_boxes', order_id=order.id)

    return redirect(f"/meto/orders/{order.id}/?open_batch={batch.id}")
