from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max
from django.http import JsonResponse
from .models import Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box


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
        status=Order.Status.IN_PROGRESS
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
    - Yangi Kesim Partiyasi kiritish (Mato kg, Kesim 1, Kesim 2...)
    - Oldingi kesimlar tarixi va Meto holati
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
                'total_real': s.total_real_quantity,
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
            can_edit_this_batch = True
            for b_it in batch.items.all():
                if not b_it.can_edit_cut:
                    can_edit_this_batch = False
                batch_items.append({
                    'item': b_it,
                    'size_name': b_it.order_item_size.size_name,
                    'quantity': b_it.quantity,
                    'status': b_it.status,
                    'status_display': b_it.get_status_display(),
                    'real_quantity': b_it.real_quantity,
                    'meto_number_start': b_it.meto_number_start,
                    'meto_number_end': b_it.meto_number_end,
                    'meto_worker_name': b_it.meto_worker_name,
                    'can_edit': b_it.can_edit_cut,
                })

            batches_data.append({
                'batch': batch,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'cutter_name': batch.cutter_name,
                'fabric_weight_kg': batch.fabric_weight_kg,
                'fabric_batch_code': batch.fabric_batch_code,
                'notes': batch.notes,
                'created_at': batch.created_at,
                'total_quantity': batch.total_quantity,
                'total_real_quantity': batch.total_real_quantity,
                'items': batch_items,
                'can_edit': can_edit_this_batch,
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
    - Tayyor mato omboridan mato partiyasi (kg va rulon/kod)
    - Kesilgan razmerlar soni
    - "KESIM SONNI KIRITADI HOLOS" -> Saqlangach, partiya Meto bo'limiga o'tadi
    """
    if request.method != 'POST':
        return redirect('cutting_order_detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    order_item = get_object_or_404(OrderItem, id=order_item_id, order=order)

    cutter_name = request.POST.get('cutter_name', '').strip()
    fabric_weight_str = request.POST.get('fabric_weight_kg', '').strip()
    fabric_batch_code = request.POST.get('fabric_batch_code', '').strip()
    notes = request.POST.get('notes', '').strip()

    fabric_weight = None
    if fabric_weight_str:
        try:
            fabric_weight = Decimal(fabric_weight_str.replace(',', '.'))
        except Exception:
            fabric_weight = None

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
            fabric_weight_kg=fabric_weight,
            fabric_batch_code=fabric_batch_code,
            notes=notes,
            created_by=request.user
        )

        for size_obj, qty in items_to_create:
            CuttingBatchItem.objects.create(
                batch=batch,
                order_item_size=size_obj,
                quantity=qty,
                status=CuttingBatchItem.Status.CUT_ENTERED
            )

    messages.success(
        request,
        f"'{order_item.article.code}' uchun '{batch.name}' muvaffaqiyatli kiritildi! Jami bichildi: {total_batch_qty} dona. Meto bo'limiga uzatildi."
    )
    return redirect('cutting_order_detail', order_id=order_id)


@cutter_required
def cutting_edit_batch(request, order_id: int, batch_id: int):
    """
    Kesim Partiyasini Tahrirlash:
    - Foydalanuvchi talabi:
      "SONNI OZGARTISH FUNKSIYASI BOR FAQAT BUNI HAM VAQTI BILAN HANDLE QILISH KERAK BOLADI"
    - Meto tasdiqlamaguncha kesimchi o'zgartira oladi. Meto tasdiqlagan bo'lsa, bloklanadi!
    """
    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(CuttingBatch, id=batch_id, order_item__order=order)

    # Bloklash tekshiruvi
    for item in batch.items.all():
        if not item.can_edit_cut:
            messages.error(
                request,
                f"'{batch.name}' partiyasi Meto bo'limi tomonidan qabul qilingan yoki tasdiqlangan! Sonni o'zgartirish taqiqlanadi."
            )
            return redirect('cutting_order_detail', order_id=order.id)

    if request.method == 'POST':
        cutter_name = request.POST.get('cutter_name', '').strip()
        notes = request.POST.get('notes', '').strip()
        fabric_weight_str = request.POST.get('fabric_weight_kg', '').strip()
        fabric_batch_code = request.POST.get('fabric_batch_code', '').strip()

        fabric_weight = None
        if fabric_weight_str:
            try:
                fabric_weight = Decimal(fabric_weight_str.replace(',', '.'))
            except Exception:
                pass

        total_new_qty = 0
        with transaction.atomic():
            batch.cutter_name = cutter_name
            batch.notes = notes
            if fabric_weight is not None:
                batch.fabric_weight_kg = fabric_weight
            if fabric_batch_code:
                batch.fabric_batch_code = fabric_batch_code
            batch.save()

            for item in batch.items.all():
                qty_str = request.POST.get(f'size_qty_{item.id}', str(item.quantity)).strip()
                try:
                    qty = max(0, int(qty_str))
                except (ValueError, TypeError):
                    qty = item.quantity

                item.quantity = qty
                item.save(update_fields=['quantity'])
                total_new_qty += qty

        messages.success(request, f"'{batch.name}' partiyasi sonlari muvaffaqiyatli yangilandi! Jami: {total_new_qty} dona.")
        return redirect('cutting_order_detail', order_id=order.id)

    return redirect('cutting_order_detail', order_id=order.id)
