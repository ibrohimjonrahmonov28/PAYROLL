import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max, Sum
from django.http import JsonResponse
from .models import Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box, Ticket


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
    Ushbu Zakaz Bo'yicha Kesim Oynasi (Super Tezkor / Lazy Loading asosida):
    - Har bir artikul uchun qisqa xulosa ko'rsatiladi (N+1 so'rovlarsiz).
    - To'liq razmerlar jadvali va partiyalar kartalari faqat "Batafsil" bosilganda yuklanadi.
    """
    search_q = request.GET.get('q', '').strip()
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article').prefetch_related(
            'items__article__model',
            'items__sizes',
            'items__cutting_batches__items__order_item_size'
        ),
        id=order_id
    )

    # Skanerlangan partiyalar ID lari (Reestr jadvali uchun)
    scanned_batch_ids = set(
        Ticket.objects.filter(
            box__order=order,
            box__cutting_batch_item__isnull=False,
            status=Ticket.Status.SCANNED
        ).values_list('box__cutting_batch_item__batch_id', flat=True)
    )
    scanned_pastal_codes = set(
        Ticket.objects.filter(
            box__order=order,
            status=Ticket.Status.SCANNED
        ).exclude(
            box__pastal_number__exact=''
        ).values_list('box__pastal_number', flat=True)
    )

    items_data = []
    all_batches_flat = []
    total_planned_order = 0
    total_cut_order = 0

    for item in order.items.all():
        item_sizes = list(item.sizes.all())
        item_batches = list(item.cutting_batches.all().order_by('-batch_number'))

        # In-memory calculation for article planned & cut (ZERO DB queries inside loop)
        item_planned = sum(s.planned_quantity for s in item_sizes) if item_sizes else item.quantity

        item_cut = 0
        search_words = [item.article.code, item.article.name]
        if item.article.model:
            search_words.append(item.article.model.name)

        for batch in item_batches:
            batch_qty = 0
            batch_real_qty = 0
            can_edit_this_batch = True
            batch_items_list = []
            items_json_list = []

            for b_it in batch.items.all():
                batch_qty += b_it.quantity
                if b_it.real_quantity is not None:
                    batch_real_qty += b_it.real_quantity
                if not b_it.can_edit_cut:
                    can_edit_this_batch = False
                batch_items_list.append({
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
                items_json_list.append({
                    'id': b_it.id,
                    'size_name': b_it.order_item_size.size_name,
                    'quantity': b_it.quantity
                })

            item_cut += batch_qty

            can_delete_this_batch = (
                batch.id not in scanned_batch_ids and
                (not batch.pastal_code or batch.pastal_code not in scanned_pastal_codes)
            )

            if batch.pastal_code:
                search_words.append(batch.pastal_code)
            if batch.partiya_number:
                search_words.append(batch.partiya_number)
            if batch.cutter_name:
                search_words.append(batch.cutter_name)
            if batch.fabric_batch_code:
                search_words.append(batch.fabric_batch_code)
            search_words.append(batch.name)

            all_batches_flat.append({
                'batch': batch,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'cutter_name': batch.cutter_name,
                'pastal_code': batch.pastal_code,
                'partiya_number': batch.partiya_number,
                'fabric_weight_kg': batch.fabric_weight_kg,
                'fabric_batch_code': batch.fabric_batch_code,
                'notes': batch.notes,
                'created_at': batch.created_at,
                'total_quantity': batch_qty,
                'total_real_quantity': batch_real_qty,
                'items': batch_items_list,
                'items_json': json.dumps(items_json_list),
                'can_edit': can_edit_this_batch,
                'can_delete': can_delete_this_batch,
                'is_all_meto_confirmed': batch.is_all_meto_confirmed,
                'item_id': item.id,
                'article_code': item.article.code,
                'article_name': item.article.name,
                'model_name': item.article.model.name if item.article.model else '',
            })

        total_planned_order += item_planned
        total_cut_order += item_cut

        max_b = max([b.batch_number for b in item_batches], default=0)
        next_batch_num = max_b + 1
        next_batch_name = f"Kesim {next_batch_num}"
        cut_percentage = round((item_cut / item_planned * 100), 1) if item_planned > 0 else 0.0

        # + Yangi Partiya modali uchun kerak bo'ladigan sodda razmerlar ro'yxati
        simple_sizes = [
            {'size': s, 'planned': s.planned_quantity}
            for s in item_sizes
        ]

        items_data.append({
            'item': item,
            'sizes': simple_sizes,
            'sizes_count': len(item_sizes),
            'batches_count': len(item_batches),
            'next_batch_num': next_batch_num,
            'next_batch_name': next_batch_name,
            'planned_qty': item_planned,
            'cut_qty': item_cut,
            'cut_percentage': cut_percentage,
            'search_keywords': " ".join(filter(None, search_words)).lower(),
        })

    overall_order_pct = round((total_cut_order / total_planned_order) * 100, 1) if total_planned_order > 0 else 0.0

    return render(request, 'cutting/order_detail.html', {
        'order': order,
        'items_data': items_data,
        'all_batches_flat': all_batches_flat,
        'total_planned_order': total_planned_order,
        'total_cut_order': total_cut_order,
        'overall_order_pct': overall_order_pct,
        'search_q': search_q,
    })


@cutter_required
def cutting_item_detail_view(request, order_id: int, order_item_id: int):
    """
    Artikul bo'yicha Razmerlar va Partiyalar tafsilotlari (AJAX Lazy Loading):
    Foydalanuvchi "Batafsil" tugmasini bosganda bazadan yagona agregatsiya so'rovi bilan tezkor yuklanadi.
    """
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(
        OrderItem.objects.select_related('article__model').prefetch_related(
            'sizes',
            'cutting_batches__items__order_item_size'
        ),
        id=order_item_id,
        order=order
    )

    sizes = list(item.sizes.all())
    batches = list(item.cutting_batches.all().order_by('-batch_number'))

    # 1 ta agregatsiya orqali ushbu artikulning barcha razmerlari bo'yicha qutilar soni
    boxes_agg = Box.objects.filter(
        order=order,
        article=item.article
    ).values('razmer').annotate(total=Sum('quantity'))
    boxes_by_size = {
        (b['razmer'] or '').strip().lower(): (b['total'] or 0)
        for b in boxes_agg if b['razmer']
    }

    # 1 ta agregatsiya orqali Meto tasdiqlagan sonlar
    real_agg = CuttingBatchItem.objects.filter(
        batch__order_item=item,
        real_quantity__isnull=False
    ).values('order_item_size_id').annotate(total=Sum('real_quantity'))
    real_by_size = {r['order_item_size_id']: (r['total'] or 0) for r in real_agg}

    # 1 ta agregatsiya orqali jami kesilgan sonlar
    cut_agg = CuttingBatchItem.objects.filter(
        batch__order_item=item
    ).values('order_item_size_id').annotate(total=Sum('quantity'))
    cut_by_size = {c['order_item_size_id']: (c['total'] or 0) for c in cut_agg}

    # Skanerlangan partiyalar ID lari (o'chirish taqiqlanadi)
    scanned_batch_ids = set(
        Ticket.objects.filter(
            box__order=order,
            box__cutting_batch_item__batch__order_item=item,
            status=Ticket.Status.SCANNED
        ).values_list('box__cutting_batch_item__batch_id', flat=True)
    )
    scanned_pastal_codes = set(
        Ticket.objects.filter(
            box__order=order,
            box__article=item.article,
            status=Ticket.Status.SCANNED
        ).exclude(
            box__pastal_number__exact=''
        ).values_list('box__pastal_number', flat=True)
    )

    sizes_data = []
    for s in sizes:
        cut = cut_by_size.get(s.id, 0)
        real = real_by_size.get(s.id, 0)
        boxes_created = boxes_by_size.get(s.size_name.strip().lower(), 0)
        pct = round((cut / s.planned_quantity * 100), 1) if s.planned_quantity > 0 else 0.0
        rem = max(0, s.planned_quantity - cut)
        excess = max(0, cut - s.planned_quantity)

        sizes_data.append({
            'size': s,
            'planned': s.planned_quantity,
            'cut': cut,
            'percentage': pct,
            'remaining': rem,
            'excess': excess,
            'boxes_created': boxes_created,
            'total_real': real,
        })

    batches_data = []
    for batch in batches:
        batch_items = []
        items_json_list = []
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
            items_json_list.append({
                'id': b_it.id,
                'size_name': b_it.order_item_size.size_name,
                'quantity': b_it.quantity
            })

        can_delete_this_batch = (
            batch.id not in scanned_batch_ids and
            (not batch.pastal_code or batch.pastal_code not in scanned_pastal_codes)
        )

        batches_data.append({
            'batch': batch,
            'name': batch.name,
            'batch_number': batch.batch_number,
            'cutter_name': batch.cutter_name,
            'pastal_code': batch.pastal_code,
            'partiya_number': batch.partiya_number,
            'fabric_weight_kg': batch.fabric_weight_kg,
            'fabric_batch_code': batch.fabric_batch_code,
            'notes': batch.notes,
            'created_at': batch.created_at,
            'total_quantity': batch.total_quantity,
            'total_real_quantity': batch.total_real_quantity,
            'items': batch_items,
            'items_json': json.dumps(items_json_list),
            'can_edit': can_edit_this_batch,
            'can_delete': can_delete_this_batch,
            'is_all_meto_confirmed': batch.is_all_meto_confirmed,
        })

    return render(request, 'cutting/partials/item_detail.html', {
        'order': order,
        'item': item,
        'sizes_data': sizes_data,
        'batches_data': batches_data,
    })


@cutter_required
def cutting_add_batch(request, order_id: int, order_item_id: int):
    """
    Yangi Kesim Partiyasi Kiritish (POST):
    - Tayyor mato omboridan mato partiyasi (kg va rulon/kod)
    - Pastal kodi (majburiy)
    - Partiya raqami (masalan: 9-26-190, 10-10-2026)
    - Bichuvchi ismi (avtomatik akkount egasi)
    - Kesilgan razmerlar soni
    - Saqlangach, partiya Meto bo'limiga o'tadi
    """
    if request.method != 'POST':
        return redirect('cutting_order_detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    order_item = get_object_or_404(OrderItem, id=order_item_id, order=order)

    pastal_code = request.POST.get('pastal_code', '').strip()
    if not pastal_code:
        messages.error(request, "Pastal kodi kiritilishi majburiy!")
        return redirect('cutting_order_detail', order_id=order_id)

    partiya_number = request.POST.get('partiya_number', '').strip()
    cutter_name = request.POST.get('cutter_name', '').strip() or request.user.get_full_name() or request.user.username
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
            pastal_code=pastal_code,
            partiya_number=partiya_number,
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
        f"'{order_item.article.code}' uchun '{batch.name}' (Pastal: {pastal_code}) muvaffaqiyatli kiritildi! Jami bichildi: {total_batch_qty} dona. Meto bo'limiga uzatildi."
    )
    return redirect('cutting_order_detail', order_id=order_id)


@cutter_required
def cutting_edit_batch(request, order_id: int, batch_id: int):
    """
    Kesim Partiyasini Tahrirlash:
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
        pastal_code = request.POST.get('pastal_code', '').strip()
        if not pastal_code:
            messages.error(request, "Pastal kodi kiritilishi majburiy!")
            return redirect('cutting_order_detail', order_id=order.id)

        partiya_number = request.POST.get('partiya_number', '').strip()
        cutter_name = request.POST.get('cutter_name', '').strip() or request.user.get_full_name() or request.user.username
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
            batch.pastal_code = pastal_code
            batch.partiya_number = partiya_number
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


@cutter_required
def cutting_delete_batch(request, order_id: int, batch_id: int):
    """
    Kesim Partiyasini (Pastalni) O'chirish (POST):
    - Xato artikulga kiritilgan pastalni butunlay o'chirish.
    - Agar ushbu partiya stikerlari tikuvchilar tomonidan allaqachon skanerlangan bo'lsa (SCANNED), o'chirish taqiqlanadi!
    - Agar Meto tasdiqlagan bo'lsa-yu, stikerlar chiqarilgan yoki chiqarilmagan bo'lsa, lekin hali skanerlanmagan bo'lsa:
      Meto bo'limidagi barcha hisoblar bekor bo'ladi va ushbu pastalning barcha QR stikerlari va qutilari bazadan butunlay o'chiriladi.
    """
    if request.method != 'POST':
        messages.error(request, "Noto'g'ri so'rov usuli!")
        return redirect('cutting_order_detail', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    batch = get_object_or_404(CuttingBatch, id=batch_id, order_item__order=order)

    # Ushbu partiyaga tegishli barcha qutilarni aniqlash:
    # 1) cutting_batch_item orqali
    # 2) pastal_code va order_item.article orqali
    boxes_filter = Q(cutting_batch_item__batch=batch)
    if batch.pastal_code:
        boxes_filter |= Q(order=order, article=batch.order_item.article, pastal_number=batch.pastal_code)
    
    related_boxes = Box.objects.filter(boxes_filter).distinct()

    # 1. Tikuvchilar tomonidan skanerlangan stikerlar bor-yo'qligini tekshirish
    scanned_tickets_count = Ticket.objects.filter(
        box__in=related_boxes,
        status=Ticket.Status.SCANNED
    ).count()

    if scanned_tickets_count > 0:
        messages.error(
            request,
            f"'{batch.name}' (Pastal: {batch.pastal_code or '—'}) bo'yicha {scanned_tickets_count} ta operatsiya "
            f"tikuvchilar tomonidan allaqachon skanerlangan va ish haqi hisoblangan! Ushbu partiyani o'chirib bo'lmaydi."
        )
        return redirect('cutting_order_detail', order_id=order_id)

    # 2. Chop etilgan qutilar bor-yo'qligini tekshirish
    printed_boxes_count = related_boxes.filter(is_printed=True).count()
    if printed_boxes_count > 0 and not (request.user.is_superuser or (hasattr(request.user, 'is_superadmin') and request.user.is_superadmin())):
        messages.error(
            request,
            f"'{batch.name}' (Pastal: {batch.pastal_code or '—'}) bo'yicha {printed_boxes_count} ta qutining stikerlari allaqachon chop etilgan! "
            f"Chevarlar qo'lidagi qog'oz stikerlar bekor bo'lib qolmasligi uchun bu partiyani o'chirish taqiqlanadi. "
            f"Zarur bo'lsa, Superadminga murojaat qiling."
        )
        return redirect('cutting_order_detail', order_id=order_id)

    batch_name = batch.name
    pastal_code = batch.pastal_code or "—"
    article_code = batch.order_item.article.code

    with transaction.atomic():
        deleted_boxes_count = related_boxes.count()
        total_tickets_deleted = Ticket.objects.filter(box__in=related_boxes).count()

        # 1. Bog'liq barcha qutilar va ularning QR stikerlarini butunlay o'chirish (CASCADE)
        if deleted_boxes_count > 0:
            related_boxes.delete()

        # 2. Batch va unga tegishli barcha Meto/Kesim bandlarini o'chirish (CASCADE)
        batch.delete()

    msg = (
        f"'{article_code}' artikulidan '{batch_name}' (Pastal: {pastal_code}) muvaffaqiyatli o'chirildi! "
        f"Meto bo'limidagi unga oid barcha hisoblar bekor qilindi."
    )
    if deleted_boxes_count > 0:
        msg += f" Unga tegishli {deleted_boxes_count} ta quti va {total_tickets_deleted} ta QR stiker bazadan butunlay o'chirildi (chop etilgan stikerlar endi skanerda 'Topilmadi' deb bekor bo'ldi)."

    messages.success(request, msg)
    return redirect('cutting_order_detail', order_id=order_id)


