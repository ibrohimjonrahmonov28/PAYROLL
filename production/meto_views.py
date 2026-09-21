from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Max, Sum, Count
from django.utils import timezone
from .models import Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box, ArticleOperation
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
        status__in=[Order.Status.IN_PROGRESS, Order.Status.DRAFT]
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
    - Tanlangan buyurtmaning barcha artikullari va kesim partiyalari
    - Har bir razmer bandi uchun detallarni sanab meto raqamlari oralig'i (#1 - #198)
    - Aniq haqiqiy sonni (real_quantity) kiritish va TUGATISH
    - Tugatilgach, tizim avtomatik tarzda stikerlar va qutilarni yaratadi
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article').prefetch_related(
            'items__article__model',
            'items__article__article_operations',
            'items__sizes',
            'items__cutting_batches__items__order_item_size',
            'items__cutting_batches__items__boxes'
        ),
        id=order_id
    )

    items_data = []

    for item in order.items.all():
        operations_count = ArticleOperation.objects.filter(article=item.article).count()
        batches_data = []

        for batch in item.cutting_batches.all().order_by('-batch_number'):
            batch_items = []
            for b_it in batch.items.all():
                batch_items.append({
                    'item': b_it,
                    'size_name': b_it.order_item_size.size_name,
                    'quantity': b_it.quantity,
                    'status': b_it.status,
                    'status_display': b_it.get_status_display(),
                    'is_confirmed': b_it.status != CuttingBatchItem.Status.CUT_ENTERED,
                    'real_quantity': b_it.real_quantity,
                    'effective_quantity': b_it.effective_quantity,
                    'meto_number_start': b_it.meto_number_start,
                    'meto_number_end': b_it.meto_number_end,
                    'meto_worker_name': b_it.meto_worker_name,
                    'meto_notes': b_it.meto_notes,
                    'meto_completed_at': b_it.meto_completed_at,
                    'boxes_count': b_it.boxes.count(),
                    'boxes': b_it.boxes.all(),
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
                'items': batch_items,
                'has_pending': any(not bi['is_confirmed'] for bi in batch_items),
            })

        items_data.append({
            'item': item,
            'operations_count': operations_count,
            'batches': batches_data,
        })

    return render(request, 'meto/order_detail.html', {
        'order': order,
        'items_data': items_data,
    })


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
        return redirect('meto_order_detail', order_id=order.id)

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
        pastal_number = request.POST.get('pastal_number', '').strip() or str(batch_item.batch.batch_number)
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

    return redirect('meto_order_detail', order_id=order.id)
