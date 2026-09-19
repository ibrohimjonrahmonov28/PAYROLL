from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max
from django.http import JsonResponse
from .models import Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box
from .services import allocate_ticket_quantities, create_boxes_for_order


def cutter_required(view_func):
    """Kesimchi (Bichuvchi) yoki Superadmin uchun ruxsat tekshiruvi"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        if not (getattr(request.user, 'is_cutter', lambda: False)() or request.user.is_superadmin()):
            messages.error(request, "Ushbu bo'limga faqat kesimchilar va superadminlar kira oladi!")
            return redirect('production:order_list')
        return view_func(request, *args, **kwargs)
    return wrapper


@cutter_required
def cutting_dashboard(request):
    """
    Kesim Bo'limi Boshqaruv Paneli:
    - Barcha faol buyurtmalar ro'yxati.
    - Qaysi zakazda qancha reja bor va aslida qancha kesilgan.
    """
    search_q = request.GET.get('q', '').strip()
    orders_qs = Order.objects.filter(
        status__in=[Order.Status.IN_PROGRESS, Order.Status.DRAFT]
    ).select_related('customer', 'article').prefetch_related(
        'items__article__model',
        'items__sizes__cutting_items',
        'items__cutting_batches'
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
    for ord_obj in orders_qs:
        total_planned = 0
        total_cut = 0
        batches_count = 0
        sizes_set = set()

        for item in ord_obj.items.all():
            total_planned += item.total_planned_quantity
            total_cut += item.total_cut_quantity
            batches_count += item.cutting_batches.count()
            for s in item.sizes.all():
                sizes_set.add(s.size_name)

        overall_pct = round((total_cut / total_planned) * 100, 1) if total_planned > 0 else 0.0

        orders_data.append({
            'order': ord_obj,
            'total_planned': total_planned,
            'total_cut': total_cut,
            'overall_cut_percentage': overall_pct,
            'batches_count': batches_count,
            'sizes_str': ", ".join(sorted(sizes_set)) if sizes_set else "—",
            'is_cut_complete': (total_planned > 0 and total_cut >= total_planned),
        })

    return render(request, 'cutting/dashboard.html', {
        'orders_data': orders_data,
        'search_q': search_q,
    })


@cutter_required
def cutting_order_detail(request, order_id: int):
    """
    Ushbu Zakaz Bo'yicha Kesim Oynasi:
    - Har bir artikul va uning razmerlari
    - Yangi Kesim Partiyasi kiritish (Kesim 1, Kesim 2...)
    - Oldingi kesimlar tarixi va ularni qutilarga bo'lib stiker chiqarish
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article').prefetch_related(
            'items__article__model',
            'items__sizes__cutting_items',
            'items__cutting_batches__items__order_item_size'
        ),
        id=order_id
    )

    items_data = []
    total_planned_order = 0
    total_cut_order = 0

    for item in order.items.all():
        sizes_data = []
        for s in item.sizes.all():
            sizes_data.append({
                'size': s,
                'planned': s.planned_quantity,
                'cut': s.total_cut_quantity,
                'percentage': s.cut_percentage,
                'remaining': s.remaining_to_cut_quantity,
                'excess': s.excess_cut_quantity,
                'boxes_created': s.boxes_created_qty,
                'remaining_to_box': s.remaining_to_box_qty,
            })
            total_planned_order += s.planned_quantity
            total_cut_order += s.total_cut_quantity

        # Keyingi kesim raqami
        max_b = item.cutting_batches.aggregate(m=Max('batch_number'))['m'] or 0
        next_batch_num = max_b + 1
        next_batch_name = f"Kesim {next_batch_num}"

        batches_data = []
        for batch in item.cutting_batches.all().order_by('-batch_number'):
            batch_items = []
            for b_it in batch.items.all():
                batch_items.append({
                    'item': b_it,
                    'size_name': b_it.order_item_size.size_name,
                    'quantity': b_it.quantity,
                    'boxes_created_qty': b_it.boxes_created_qty,
                    'remaining_to_box': b_it.remaining_to_box,
                })

            batches_data.append({
                'batch': batch,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'cutter_name': batch.cutter_name,
                'notes': batch.notes,
                'created_at': batch.created_at,
                'total_quantity': batch.total_quantity,
                'items': batch_items,
            })

        items_data.append({
            'item': item,
            'sizes': sizes_data,
            'next_batch_num': next_batch_num,
            'next_batch_name': next_batch_name,
            'batches': batches_data,
            'planned_qty': item.total_planned_quantity,
            'cut_qty': item.total_cut_quantity,
            'cut_percentage': item.overall_cut_percentage,
        })

    overall_order_pct = round((total_cut_order / total_planned_order) * 100, 1) if total_planned_order > 0 else 0.0

    return render(request, 'cutting/order_detail.html', {
        'order': order,
        'items_data': items_data,
        'total_planned_order': total_planned_order,
        'total_cut_order': total_cut_order,
        'overall_order_pct': overall_order_pct,
    })


@cutter_required
def cutting_add_batch(request, order_id: int, order_item_id: int):
    """
    Yangi Kesim Partiyasi Kiritish (POST):
    - Masalan Kesim 1 yoki Kesim 2
    - Tanlangan artikulning har bir razmeridan kesilgan sonlar yoziladi
    """
    if request.method != 'POST':
        return redirect('cutting_order_detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    order_item = get_object_or_404(OrderItem, id=order_item_id, order=order)

    cutter_name = request.POST.get('cutter_name', '').strip()
    notes = request.POST.get('notes', '').strip()

    # Avtomatik keyingi kesim raqami
    max_b = order_item.cutting_batches.aggregate(m=Max('batch_number'))['m'] or 0
    next_batch_num = max_b + 1
    batch_name = f"Kesim {next_batch_num}"

    total_batch_qty = 0
    items_to_create = []

    for size in order_item.sizes.all():
        val_str = request.POST.get(f'size_qty_{size.id}', '0').strip()
        try:
            qty = max(0, int(val_str))
        except (ValueError, TypeError):
            qty = 0

        if qty > 0:
            items_to_create.append((size, qty))
            total_batch_qty += qty

    if total_batch_qty == 0:
        messages.warning(request, "Hech qanday razmer bo'yicha kesim soni kiritilmadi!")
        return redirect('cutting_order_detail', order_id=order_id)

    with transaction.atomic():
        batch = CuttingBatch.objects.create(
            order_item=order_item,
            batch_number=next_batch_num,
            name=batch_name,
            cutter_name=cutter_name,
            notes=notes,
            created_by=request.user
        )

        for size_obj, qty in items_to_create:
            CuttingBatchItem.objects.create(
                batch=batch,
                order_item_size=size_obj,
                quantity=qty
            )

    messages.success(
        request,
        f"'{order_item.article.code}' uchun '{batch.name}' muvaffaqiyatli kiritildi! Jami kesildi: {total_batch_qty} dona."
    )
    return redirect('cutting_order_detail', order_id=order_id)


@cutter_required
def cutting_split_and_create_boxes(request, batch_id: int):
    """
    Kesim Partiyasidan Qutilarga Bo'lish va QR Stikerlar Generatsiya Qilish (POST):
    - Foydalanuvchi talabi:
      "agar kesim soni kop bolsa uni 2 ga 3 ga bolib olsak boladi bu orqali ham zakazni ham modelni ham stikerni ham umumiy jarayonni ham nazorat qila olamiz"
    """
    if request.method != 'POST':
        return redirect('cutting_dashboard')

    batch = get_object_or_404(
        CuttingBatch.objects.select_related('order_item__order', 'order_item__article'),
        id=batch_id
    )
    order = batch.order_item.order
    article = batch.order_item.article

    batch_item_id = request.POST.get('batch_item_id')
    split_mode = request.POST.get('split_mode', 'equal_splits')  # equal_splits, box_capacity, single_box
    splits_count_str = request.POST.get('splits_count', '2').strip()
    box_capacity_str = request.POST.get('box_capacity', '100').strip()

    batch_item = get_object_or_404(CuttingBatchItem, id=batch_item_id, batch=batch)
    size_name = batch_item.order_item_size.size_name
    available_qty = batch_item.remaining_to_box

    if available_qty <= 0:
        messages.warning(request, f"'{size_name}' razmeridagi barcha kesimlar uchun allaqachon qutilar yaratilgan!")
        return redirect('cutting_order_detail', order_id=order.id)

    box_sizes = []

    if split_mode == 'single_box':
        box_sizes = [available_qty]
    elif split_mode == 'box_capacity':
        try:
            capacity = max(1, int(box_capacity_str))
        except (ValueError, TypeError):
            capacity = 100
        # Masalan available_qty = 310, capacity = 100 -> [100, 100, 100, 10]
        full_boxes = available_qty // capacity
        rem = available_qty % capacity
        box_sizes = [capacity] * full_boxes
        if rem > 0:
            box_sizes.append(rem)
    else:
        # Default: equal_splits (2 ga, 3 ga, N ga bo'lish)
        try:
            n_splits = max(1, int(splits_count_str))
        except (ValueError, TypeError):
            n_splits = 2
        # SRS butun sonli taqsimoti
        box_sizes = allocate_ticket_quantities(available_qty, n_splits)

    if not box_sizes:
        box_sizes = [available_qty]

    created_boxes = []
    with transaction.atomic():
        created_boxes = create_boxes_for_order(
            order=order,
            box_sizes=box_sizes,
            article=article,
            razmer=size_name
        )
        # Partiya bandidagi quti qilingan sonni yangilash
        total_created = sum(box_sizes)
        batch_item.boxes_created_qty += total_created
        batch_item.save(update_fields=['boxes_created_qty'])

    messages.success(
        request,
        f"Razmer [{size_name}]: Jami {total_created} dona kesim {len(created_boxes)} ta qutiga bo'lindi va QR stikerlari tayyorlandi!"
    )
    return redirect('cutting_order_detail', order_id=order.id)

