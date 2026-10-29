import functools
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.urls import reverse

from accounts.models import User
from production.models import CuttingBatch, CuttingBatchItem, Box, Ticket, Waybill
from production.services import get_patok_code, get_patok_name

# Uychi patoklari: U1..U27 (ekran 14..40) — Telegram bot va master planshet bilan bir xil
UYCHI_FIRST_SCREEN = 14
UYCHI_LAST_SCREEN = 40


def meto_hub_access_required(view_func):
    """
    Meto Markazi (Hub) uchun ruxsat tekshiruvi:
    Superadmin, Admin, Menejer, Metochi, Stikerchi va Filial Admini kira oladi.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('root_login')}?next={request.path}")
        u = request.user
        allowed_roles = [
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.MANAGER,
            User.Role.METO,
            User.Role.BRANCH_ADMIN,
            User.Role.STICKER,
        ]
        if u.is_superuser or u.role in allowed_roles:
            return view_func(request, *args, **kwargs)
        messages.error(request, "Ushbu bo'limga kirish huquqingiz yo'q!")
        return redirect('root_login')
    return wrapper


def _get_cart_data(request):
    """Sessiondagi savatcha (Uychi yuk xati korzinkasi) ma'lumotlarini to'plash"""
    cart_ids = request.session.get('waybill_cart', [])
    if not cart_ids:
        return {
            'batches': [],
            'count': 0,
            'total_qty': 0,
            'total_weight': Decimal('0.00'),
            'total_boxes': 0,
        }

    batches = list(CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).filter(id__in=cart_ids))

    total_qty = sum(b.total_cut_qty for b in batches)
    total_weight = sum((b.actual_weight_kg or b.fabric_weight_kg or Decimal('0.00')) for b in batches)
    total_boxes = sum(b.boxes.exclude(status=Box.Status.CANCELLED).count() for b in batches)

    return {
        'batches': batches,
        'count': len(batches),
        'total_qty': total_qty,
        'total_weight': total_weight,
        'total_boxes': total_boxes,
    }


@meto_hub_access_required
def meto_hub_dashboard_view(request):
    """
    Asosiy Meto Markazi (Dashboard):
    1. Meto kutilmoqda (Kesilgan, bo'linmaganlar)
    2. Stiker kutilmoqda (Meto bo'lgan, stiker chiqmaganlar)
    3. Meto Skladi (Hamma narsasi tayyor erkin ombor qoldig'i)
    4. Yuk Xatlari (Filiallararo yo'ldagi va qabul qilingan yuklar)
    """
    active_tab = request.GET.get('tab', 'sklad')
    q = request.GET.get('q', '').strip()

    base_batches = CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'created_by',
        'waybill'
    ).prefetch_related(
        'items__order_item_size',
        'items__boxes'
    ).order_by('-id')

    if q:
        base_batches = base_batches.filter(
            Q(pastal_code__icontains=q) |
            Q(name__icontains=q) |
            Q(order_item__order__order_number__icontains=q) |
            Q(order_item__article__name__icontains=q) |
            Q(order_item__article__code__icontains=q)
        )

    # 1-bo'lim: Meto kutilmoqda (Kesilgan, kamida bitta razmeri hali CUT_ENTERED bo'lgan)
    kutilmoqda_qs = base_batches.filter(
        items__status=CuttingBatchItem.Status.CUT_ENTERED
    ).distinct()

    # 2-bo'lim: Stiker kutilmoqda (Meto tasdiqlangan, lekin hali chop etilmagan qutilari bor)
    stikerlar_qs = base_batches.exclude(
        items__status=CuttingBatchItem.Status.CUT_ENTERED
    ).filter(
        items__boxes__is_printed=False
    ).exclude(
        items__boxes__status=Box.Status.CANCELLED
    ).distinct()

    # 3-bo'lim: Meto Skladi (Barcha razmerlari tasdiqlangan, barcha qutilari chop etilgan,
    # hali birorta Koson patogiga berilmagan, hali birorta Yuk Xatiga olinmagan, erkin ombor qoldig'i!)
    sklad_qs = base_batches.exclude(
        items__status=CuttingBatchItem.Status.CUT_ENTERED
    ).exclude(
        items__boxes__is_printed=False
    ).filter(
        assigned_screen__isnull=True,
        waybill__isnull=True,
        dispatch_status=CuttingBatch.DispatchStatus.WAITING
    ).distinct()

    # 4-bo'lim: Yuk Xatlari
    waybills_qs = Waybill.objects.select_related(
        'created_by', 'received_by'
    ).prefetch_related('batches').order_by('-id')

    # Hisob-kitoblar (KPI)
    kutilmoqda_count = kutilmoqda_qs.count()
    stikerlar_count = stikerlar_qs.count()

    sklad_count = sklad_qs.count()
    sklad_batches_list = list(sklad_qs)
    sklad_total_qty = sum(b.total_cut_qty for b in sklad_batches_list)
    sklad_total_weight = sum((b.actual_weight_kg or b.fabric_weight_kg or Decimal('0.00')) for b in sklad_batches_list)

    waybills_count = waybills_qs.count()
    in_transit_count = waybills_qs.filter(status=Waybill.Status.IN_TRANSIT).count()

    # Koson patoklari (K1 - K13)
    koson_patoks = [
        {'number': i, 'code': f'K{i}', 'name': f'K{i}-Patok'}
        for i in range(1, 14)
    ]

    cart_data = _get_cart_data(request)
    cart_batch_ids = [b.id for b in cart_data['batches']]

    # Tab bo'yicha ma'lumot uzatish
    tab_items = []
    if active_tab == 'kutilmoqda':
        tab_items = list(kutilmoqda_qs[:100])
    elif active_tab == 'stikerlar':
        tab_items = list(stikerlar_qs[:100])
    elif active_tab == 'sklad':
        tab_items = sklad_batches_list[:100]
    elif active_tab == 'yukxatlari':
        tab_items = list(waybills_qs[:100])

    return render(request, 'meto_hub/dashboard.html', {
        'active_tab': active_tab,
        'q': q,
        'tab_items': tab_items,
        'kutilmoqda_count': kutilmoqda_count,
        'stikerlar_count': stikerlar_count,
        'sklad_count': sklad_count,
        'sklad_total_qty': sklad_total_qty,
        'sklad_total_weight': sklad_total_weight,
        'waybills_count': waybills_count,
        'in_transit_count': in_transit_count,
        'cart': cart_data,
        'cart_batch_ids': cart_batch_ids,
        'koson_patoks': koson_patoks,
    })


@meto_hub_access_required
def meto_hub_cart_add_view(request, batch_id: int):
    """Pastalni Uychi Yuk Xati savatchasiga (korzinkasiga) qo'shish"""
    if request.method != 'POST':
        return redirect('meto_hub_dashboard')

    batch = get_object_or_404(CuttingBatch, id=batch_id)

    # Faqat erkin Meto Skladida turgan pastalni qo'shish mumkin
    if batch.assigned_screen is not None or batch.waybill_id is not None or batch.dispatch_status != CuttingBatch.DispatchStatus.WAITING:
        messages.error(request, f"Pastal {batch.pastal_code or batch.name} allaqachon patokka yoki boshqa yuk xatiga biriktirilgan!")
        return redirect('meto_hub_dashboard')

    cart_ids = request.session.get('waybill_cart', [])
    if batch_id not in cart_ids:
        cart_ids.append(batch_id)
        request.session['waybill_cart'] = cart_ids
        request.session.modified = True
        messages.success(request, f"🛒 Pastal {batch.pastal_code or batch.name} Uychi yuk savatchasiga qo'shildi!")

    return redirect(request.META.get('HTTP_REFERER') or 'meto_hub_dashboard')


@meto_hub_access_required
def meto_hub_cart_remove_view(request, batch_id: int):
    """Pastalni savatchadan chiqarib tashlash"""
    if request.method != 'POST':
        return redirect('meto_hub_dashboard')

    cart_ids = request.session.get('waybill_cart', [])
    if batch_id in cart_ids:
        cart_ids.remove(batch_id)
        request.session['waybill_cart'] = cart_ids
        request.session.modified = True
        messages.info(request, "Pastal savatchadan chiqarildi.")

    return redirect(request.META.get('HTTP_REFERER') or 'meto_hub_dashboard')


@meto_hub_access_required
def meto_hub_cart_clear_view(request):
    """Savatchani butunlay tozalash"""
    if request.method == 'POST':
        request.session['waybill_cart'] = []
        request.session.modified = True
        messages.info(request, "Savatcha tozalandi.")
    return redirect('meto_hub_dashboard')


@meto_hub_access_required
def meto_hub_dispatch_koson_view(request, batch_id: int):
    """
    Pastalni to'g'ridan-to'g'ri Koson patogiga (1 - 13) berish:
    Shu zahoti Meto Skladdan chiqadi va o'sha patok balansiga o'tadi.
    """
    if request.method != 'POST':
        return redirect('meto_hub_dashboard')

    batch = get_object_or_404(CuttingBatch, id=batch_id)
    screen_number_raw = request.POST.get('screen_number')

    try:
        screen_num = int(screen_number_raw)
        if not (1 <= screen_num <= 13):
            raise ValueError()
    except (TypeError, ValueError):
        messages.error(request, "Koson patogi 1 dan 13 gacha bo'lishi kerak!")
        return redirect('meto_hub_dashboard')

    patok_code = get_patok_code(screen_num)

    with transaction.atomic():
        batch.assigned_screen = screen_num
        batch.assigned_branch = 'HQ'
        batch.dispatch_status = CuttingBatch.DispatchStatus.DISPATCHED
        batch.dispatched_at = timezone.now()
        batch.dispatched_by = request.user.get_full_name() or request.user.username
        batch.save(update_fields=[
            'assigned_screen', 'assigned_branch', 'dispatch_status',
            'dispatched_at', 'dispatched_by'
        ])

        # Tegishli barcha aktiv qutilarni ham shu patokka biriktirish
        Box.objects.filter(
            cutting_batch_item__batch=batch
        ).exclude(
            status=Box.Status.CANCELLED
        ).update(assigned_screen=screen_num)

        # Agar bu pastal savatchada bo'lsa, undan ham o'chirish
        cart_ids = request.session.get('waybill_cart', [])
        if batch.id in cart_ids:
            cart_ids.remove(batch.id)
            request.session['waybill_cart'] = cart_ids
            request.session.modified = True

    messages.success(
        request,
        f"✅ Pastal {batch.pastal_code or batch.name} muvaffaqiyatli {patok_code}-Patokka jo'natildi va Meto Skladdan chiqarildi!"
    )
    return redirect('meto_hub_dashboard')


@meto_hub_access_required
def meto_hub_waybill_create_view(request):
    """
    Savatchadagi pastallarni Uychi filialiga rasmiy Yuk Xati (Nakladnaya) qilib jo'natish
    """
    if request.method != 'POST':
        return redirect('meto_hub_dashboard')

    cart_ids = request.session.get('waybill_cart', [])
    if not cart_ids:
        messages.error(request, "Savatchada pastal mavjud emas! Avval Uychiga jo'natiladigan pastallarni savatchaga qo'shing.")
        return redirect('meto_hub_dashboard')

    vehicle_number = request.POST.get('vehicle_number', '').strip().upper()
    driver_name = request.POST.get('driver_name', '').strip()
    driver_phone = request.POST.get('driver_phone', '').strip()
    notes = request.POST.get('notes', '').strip()
    destination_branch = request.POST.get('destination_branch', 'UYCHI')

    with transaction.atomic():
        waybill = Waybill.objects.create(
            source_branch='HQ',
            destination_branch=destination_branch,
            vehicle_number=vehicle_number,
            driver_name=driver_name,
            driver_phone=driver_phone,
            notes=notes,
            status=Waybill.Status.IN_TRANSIT,
            created_by=request.user,
            dispatched_at=timezone.now()
        )

        batches = CuttingBatch.objects.filter(id__in=cart_ids)
        for b in batches:
            b.waybill = waybill
            b.assigned_branch = destination_branch
            b.dispatch_status = CuttingBatch.DispatchStatus.DISPATCHED
            b.dispatched_at = timezone.now()
            b.dispatched_by = request.user.get_full_name() or request.user.username
            b.save(update_fields=[
                'waybill', 'assigned_branch', 'dispatch_status',
                'dispatched_at', 'dispatched_by'
            ])

        # Savatchani tozalash
        request.session['waybill_cart'] = []
        request.session.modified = True

    messages.success(
        request,
        f"🚚 {waybill.waybill_number} raqamli Yuk Xati tuzildi! {waybill.total_batches_count} ta pastal Uychi filialiga yo'lga chiqdi."
    )
    return redirect('meto_hub_waybill_detail', waybill_id=waybill.id)


@meto_hub_access_required
def meto_hub_waybill_detail_view(request, waybill_id: int):
    """Yuk Xati tafsilotlarini ko'rish"""
    waybill = get_object_or_404(
        Waybill.objects.select_related('created_by', 'received_by', 'rejected_by'),
        id=waybill_id
    )
    batches = waybill.batches.select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).prefetch_related('items__order_item_size', 'items__boxes').order_by('id')

    return render(request, 'meto_hub/waybill_detail.html', {
        'waybill': waybill,
        'batches': batches,
    })


@meto_hub_access_required
def meto_hub_waybill_print_view(request, waybill_id: int):
    """Yuk Xatini A4 formatida rasmiy chop etish (Nakladnaya)"""
    waybill = get_object_or_404(
        Waybill.objects.select_related('created_by', 'received_by'),
        id=waybill_id
    )
    batches = waybill.batches.select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).prefetch_related('items__order_item_size', 'items__boxes').order_by('id')

    return render(request, 'meto_hub/waybill_print_a4.html', {
        'waybill': waybill,
        'batches': batches,
    })


@meto_hub_access_required
def meto_hub_waybill_accept_view(request, waybill_id: int):
    """
    Uychi filiali mas'ul xodimi tomonidan Yuk Xatini qabul qilish:
    Pastallar avtomatik Uychi filiali ombor balansiga o'tadi!
    """
    if request.method != 'POST':
        return redirect('meto_hub_waybill_detail', waybill_id=waybill_id)

    waybill = get_object_or_404(Waybill, id=waybill_id)

    if waybill.status != Waybill.Status.IN_TRANSIT:
        messages.error(request, "Ushbu yuk xati yo'lda emas yoki allaqachon ko'rib chiqilgan!")
        return redirect('meto_hub_waybill_detail', waybill_id=waybill_id)

    with transaction.atomic():
        waybill.status = Waybill.Status.RECEIVED
        waybill.received_by = request.user
        waybill.received_at = timezone.now()
        waybill.save(update_fields=['status', 'received_by', 'received_at'])

        for b in waybill.batches.all():
            b.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
            b.accepted_at = timezone.now()
            b.accepted_by = request.user
            b.save(update_fields=['dispatch_status', 'accepted_at', 'accepted_by'])

    messages.success(
        request,
        f"✅ {waybill.waybill_number} raqamli Yuk Xati qabul qilindi! Barcha {waybill.total_batches_count} ta pastal Uychi omboriga kiritildi."
    )
    return redirect('meto_hub_uychi')


@meto_hub_access_required
def meto_hub_waybill_reject_view(request, waybill_id: int):
    """Yuk Xatini rad etish / qaytarish"""
    if request.method != 'POST':
        return redirect('meto_hub_waybill_detail', waybill_id=waybill_id)

    waybill = get_object_or_404(Waybill, id=waybill_id)
    reason = request.POST.get('rejection_reason', '').strip()

    if not reason:
        messages.error(request, "Iltimos, qaytarish sababini yozing!")
        return redirect('meto_hub_waybill_detail', waybill_id=waybill_id)

    with transaction.atomic():
        waybill.status = Waybill.Status.REJECTED
        waybill.rejected_by = request.user
        waybill.rejected_at = timezone.now()
        waybill.rejection_reason = reason
        waybill.save(update_fields=['status', 'rejected_by', 'rejected_at', 'rejection_reason'])

        for b in waybill.batches.all():
            b.dispatch_status = CuttingBatch.DispatchStatus.REJECTED
            b.rejected_at = timezone.now()
            b.rejection_reason = reason
            b.save(update_fields=['dispatch_status', 'rejected_at', 'rejection_reason'])

    messages.warning(request, f"{waybill.waybill_number} raqamli Yuk Xati rad etildi va Kosonga qaytarildi.")
    return redirect('meto_hub_dashboard')


@meto_hub_access_required
def meto_hub_uychi_view(request):
    """
    Uychi filiali alohida ombor va yuk qabul qilish paneli:
    1. Kutilayotgan Yuk Xatlari (In Transit)
    2. Uychi Ombori (Qabul qilingan, lekin hali Uychi patogiga berilmagan pastallar)
    3. Tarix (Qabul qilingan yuk xatlari)
    """
    incoming_waybills = Waybill.objects.filter(
        destination_branch='UYCHI',
        status=Waybill.Status.IN_TRANSIT
    ).prefetch_related('batches').order_by('-id')

    # Uychi erkin ombor qoldig'i (qabul qilingan, lekin hali hech qaysi Uychi patogiga berilmagan)
    uychi_sklad_batches = CuttingBatch.objects.filter(
        assigned_branch='UYCHI',
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
        assigned_screen__isnull=True
    ).select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'waybill'
    ).order_by('-id')

    history_waybills = Waybill.objects.filter(
        destination_branch='UYCHI',
        status=Waybill.Status.RECEIVED
    ).order_by('-id')[:30]

    # Uychi patoklari (U1 dan U27 gacha -> Screen 14 dan 40 gacha)
    uychi_patoks = [
        {'number': sn, 'code': f'U{sn - 13}', 'name': f'U{sn - 13}-Patok'}
        for sn in range(UYCHI_FIRST_SCREEN, UYCHI_LAST_SCREEN + 1)
    ]

    return render(request, 'meto_hub/uychi_dashboard.html', {
        'incoming_waybills': incoming_waybills,
        'uychi_sklad_batches': uychi_sklad_batches,
        'history_waybills': history_waybills,
        'uychi_patoks': uychi_patoks,
    })


@meto_hub_access_required
def meto_hub_dispatch_uychi_view(request, batch_id: int):
    """
    Uychi omboridagi pastalni Uychi patogiga (U1 - U27) berish:
    Shu zahoti Uychi omboridan chiqib, tikuvga kiradi.
    """
    if request.method != 'POST':
        return redirect('meto_hub_uychi')

    batch = get_object_or_404(CuttingBatch, id=batch_id)
    screen_number_raw = request.POST.get('screen_number')

    try:
        screen_num = int(screen_number_raw)
        if not (UYCHI_FIRST_SCREEN <= screen_num <= UYCHI_LAST_SCREEN):
            raise ValueError()
    except (TypeError, ValueError):
        messages.error(request, "Uychi patogi U1 dan U27 gacha (ekran 14-40) bo'lishi kerak!")
        return redirect('meto_hub_uychi')

    patok_code = get_patok_code(screen_num)

    with transaction.atomic():
        batch.assigned_screen = screen_num
        batch.save(update_fields=['assigned_screen'])

        Box.objects.filter(
            cutting_batch_item__batch=batch
        ).exclude(
            status=Box.Status.CANCELLED
        ).update(assigned_screen=screen_num)

    messages.success(
        request,
        f"✅ Pastal {batch.pastal_code or batch.name} {patok_code}-Patokka topshirildi va Uychi omboridan chiqarildi!"
    )
    return redirect('meto_hub_uychi')
