import json
from datetime import datetime
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Sum, Count, Q

from accounts.models import User
from production.models import CuttingBatch, Box, Ticket
from production.services import get_patok_code, get_patok_name, parse_patok_number


def _get_active_master(request):
    """
    Faol master foydalanuvchisini aniqlash:
    1. request.session['master_user_id']
    2. Yoki request.user (agar tizimga kirgan bo'lsa)
    """
    if hasattr(request, 'session'):
        master_id = request.session.get('master_user_id')
        if master_id:
            user = User.objects.filter(id=master_id).first()
            if user:
                return user

    if hasattr(request, 'user') and request.user.is_authenticated:
        return request.user

    return None


def master_tablet_view(request):
    """
    Masterlar uchun Katta Planshet boshqaruv paneli (Tablet Interface):
    - Har bir filialda (Kosonsoy / Uychi) turgan planshetlar uchun
    - Master o'z birkasini (QR kod) skanerlab tizimga kiradi
    - O'z filialining patoklari ro'yxatini ko'radi
    - Har bir patokka kelgan pastallarni [QABUL QILISH] yoki [QAYTARISH] qiladi
    """
    master = _get_active_master(request)
    default_branch = master.branch if master else User.Branch.HQ

    context = {
        'master': master,
        'default_branch': default_branch,
        'now_date': timezone.localdate(),
    }
    return render(request, 'production/master_tablet.html', context)


@csrf_exempt
@require_POST
def api_identify_master(request):
    """
    Planshetda masterni QR-kod / Beydjik / UID orqali aniqlash va sessiyaga kiritish
    Kutiladi: POST { "code": "USER:123456" yoki "123456" yoki username }
    """
    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    raw_code = (data.get('code') or '').strip()
    if not raw_code:
        return JsonResponse({'success': False, 'message': "Beydjik kodi kiritilmadi"}, status=400)

    # USER: prefiksini tozalash
    clean_code = raw_code
    if clean_code.upper().startswith('USER:'):
        clean_code = clean_code[5:].strip()

    user = User.objects.filter(Q(uid=clean_code) | Q(username=clean_code)).first()
    if not user:
        return JsonResponse({'success': False, 'message': f"Xodim topilmadi (Kod: {clean_code})"}, status=404)

    # Master roli yoki boshqaruvchilar
    allowed_roles = [
        User.Role.MASTER,
        User.Role.SUPER_ADMIN,
        User.Role.ADMIN,
        User.Role.BRANCH_ADMIN,
        User.Role.MANAGER,
        User.Role.CONTROL,
    ]
    if user.role not in allowed_roles:
        return JsonResponse({
            'success': False,
            'message': f"Ushbu xodim ({user.get_full_name()}) Master roliga ega emas ({user.get_role_display()})"
        }, status=403)

    request.session['master_user_id'] = user.id
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'master': {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'uid': user.uid,
            'role': user.get_role_display(),
            'branch': user.branch,
            'branch_display': "Kosonsoy (Bosh bino)" if user.branch == User.Branch.HQ else "Uychi filiali",
        }
    })


@csrf_exempt
@require_POST
def api_logout_master(request):
    """Planshet sessiyasidan masterni chiqarish"""
    request.session.pop('master_user_id', None)
    request.session.modified = True
    return JsonResponse({'success': True, 'message': "Sessiya yakunlandi"})


@require_GET
def api_get_patoks_summary(request):
    """
    Filial bo'yicha barcha patoklarning kutilayotgan va faol pastallari xulosasi
    Parametr: ?branch=HQ yoki UYCHI
    """
    master = _get_active_master(request)
    branch = request.GET.get('branch') or (master.branch if master else 'HQ')

    is_hq = (branch == 'HQ')
    screen_numbers = list(range(1, 14)) if is_hq else list(range(14, 41))

    # Kutilayotgan (DISPATCHED) pastallar
    dispatched_batches = CuttingBatch.objects.filter(
        assigned_screen__in=screen_numbers,
        dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED
    ).values('assigned_screen').annotate(
        count=Count('id', distinct=True),
        total_units=Sum('items__quantity')
    )
    dispatched_map = {item['assigned_screen']: item for item in dispatched_batches}

    # Qabul qilingan faol (ACCEPTED) pastallar
    accepted_batches = CuttingBatch.objects.filter(
        assigned_screen__in=screen_numbers,
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
    ).values('assigned_screen').annotate(
        count=Count('id', distinct=True)
    )
    accepted_map = {item['assigned_screen']: item['count'] for item in accepted_batches}

    patoks = []
    for sn in screen_numbers:
        p_code = get_patok_code(sn)
        p_name = get_patok_name(sn)
        d_info = dispatched_map.get(sn, {})
        waiting_count = d_info.get('count', 0)
        waiting_units = d_info.get('total_units', 0)
        active_count = accepted_map.get(sn, 0)

        patoks.append({
            'screen_number': sn,
            'patok_code': p_code,
            'patok_name': p_name,
            'waiting_count': waiting_count,
            'waiting_units': waiting_units,
            'active_count': active_count,
            'has_incoming': (waiting_count > 0),
        })

    return JsonResponse({
        'success': True,
        'branch': branch,
        'branch_display': "Kosonsoy (Bosh bino)" if is_hq else "Uychi filiali",
        'patoks': patoks
    })


@require_GET
def api_get_patok_detail(request, screen_number):
    """
    Bitta patok ichidagi:
    1. Kutilayotgan pastallar (DISPATCHED) — Qabul qilish yoki Qaytarish uchun
    2. Faol qabul qilingan pastallar (ACCEPTED) — Ish jarayonida
    """
    screen_number = int(screen_number)
    p_code = get_patok_code(screen_number)
    p_name = get_patok_name(screen_number)

    # 1. Kutilayotgan pastallar
    waiting_qs = CuttingBatch.objects.filter(
        assigned_screen=screen_number,
        dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED
    ).select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).order_by('-dispatched_at')

    waiting_list = []
    for b in waiting_qs:
        article = b.order_item.article if b.order_item else None
        order = b.order_item.order if b.order_item else None
        customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
        total_boxes = b.boxes_count if hasattr(b, 'boxes_count') else Box.objects.filter(cutting_batch_item__batch=b).count()

        waiting_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'article_code': article.code if article else '',
            'total_units': b.total_quantity,
            'actual_weight_kg': float(b.actual_weight_kg) if b.actual_weight_kg else 0.0,
            'boxes_count': total_boxes,
            'dispatched_at': timezone.localtime(b.dispatched_at).strftime("%d.%m.%Y %H:%M") if b.dispatched_at else '—',
            'dispatched_by': b.dispatched_by or 'Bichuv',
            'notes': b.notes or '',
        })

    # 2. Qabul qilingan faol pastallar
    accepted_qs = CuttingBatch.objects.filter(
        assigned_screen=screen_number,
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
    ).select_related(
        'order_item__order',
        'order_item__article',
        'accepted_by'
    ).order_by('-accepted_at')[:15]

    accepted_list = []
    for b in accepted_qs:
        article = b.order_item.article if b.order_item else None
        order = b.order_item.order if b.order_item else None
        
        boxes_qs = Box.objects.filter(
            Q(cutting_batch_item__batch=b) |
            (Q(pastal_number=b.pastal_code) & Q(order=order))
        ).distinct()
        boxes = list(boxes_qs)
        total_boxes = len(boxes)
        closed_boxes = sum(1 for bx in boxes if bx.is_controlled and bx.controlled_repair_qty == 0)
        unclosed_boxes = total_boxes - closed_boxes
        repair_boxes = sum(1 for bx in boxes if bx.controlled_repair_qty > 0)
        repair_units = sum(bx.controlled_repair_qty for bx in boxes)
        is_pastal_closed = (total_boxes > 0 and closed_boxes == total_boxes)
        
        # Skanerlangan biletlar foizi
        all_tickets = Ticket.objects.filter(box__in=boxes)
        total_t = all_tickets.count()
        scanned_t = all_tickets.filter(status=Ticket.Status.SCANNED).count()
        progress_pct = int((scanned_t / total_t * 100)) if total_t > 0 else 0

        accepted_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'article_name': article.name if article else 'Model',
            'total_units': b.total_quantity,
            'actual_weight_kg': float(b.actual_weight_kg) if b.actual_weight_kg else 0.0,
            'boxes_count': total_boxes,
            'closed_boxes': closed_boxes,
            'unclosed_boxes': unclosed_boxes,
            'repair_boxes': repair_boxes,
            'repair_units': repair_units,
            'is_pastal_closed': is_pastal_closed,
            'progress_pct': progress_pct,
            'accepted_at': timezone.localtime(b.accepted_at).strftime("%d.%m.%Y %H:%M") if b.accepted_at else '—',
            'accepted_by_name': b.accepted_by.get_full_name() if b.accepted_by else 'Master',
        })

    return JsonResponse({
        'success': True,
        'screen_number': screen_number,
        'patok_code': p_code,
        'patok_name': p_name,
        'waiting_pastals': waiting_list,
        'accepted_pastals': accepted_list,
    })


@csrf_exempt
@require_POST
def api_accept_pastal(request):
    """
    Master pastalni qabul qilish tugmasini bosganda:
    - dispatch_status = ACCEPTED
    - accepted_by = master
    - Barcha qutilar shu patokka rasman biriktiriladi
    """
    master = _get_active_master(request)
    if not master:
        return JsonResponse({'success': False, 'message': "Iltimos, avval planshetga master beydjigingizni skanerlang!"}, status=401)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    batch_id = data.get('batch_id')
    screen_number = data.get('screen_number')

    if not batch_id or not screen_number:
        return JsonResponse({'success': False, 'message': "batch_id yoki screen_number berilmadi"}, status=400)

    batch = CuttingBatch.objects.select_related('order_item__article').filter(id=batch_id).first()
    if not batch:
        return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

    sn = int(screen_number)
    p_code = get_patok_code(sn)

    batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
    batch.assigned_screen = sn
    batch.accepted_at = timezone.now()
    batch.accepted_by = master
    batch.assigned_branch = master.branch or ('HQ' if sn <= 13 else 'UYCHI')
    batch.save(update_fields=['dispatch_status', 'assigned_screen', 'accepted_at', 'accepted_by', 'assigned_branch'])

    # Qutilarni biriktirish
    Box.objects.filter(
        Q(cutting_batch_item__batch=batch) |
        (Q(pastal_number=batch.pastal_code) & Q(order=batch.order_item.order))
    ).update(
        assigned_screen=sn,
        is_received_by_patok=True
    )

    article_name = batch.order_item.article.name if batch.order_item and batch.order_item.article else "Mahsulot"

    return JsonResponse({
        'success': True,
        'message': f"Pastal #{batch.batch_number} ({article_name}) {p_code}-Patok tomonidan muvaffaqiyatli QABUL QILINDI!",
        'batch_id': batch.id,
        'screen_number': sn,
        'patok_code': p_code,
    })


@csrf_exempt
@require_POST
def api_reject_pastal(request):
    """
    Master pastalni rad etib qaytarganda:
    - dispatch_status = REJECTED
    - rejection_reason = sababi
    """
    master = _get_active_master(request)
    if not master:
        return JsonResponse({'success': False, 'message': "Iltimos, avval planshetga master beydjigingizni skanerlang!"}, status=401)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    batch_id = data.get('batch_id')
    reason = (data.get('reason') or 'Master tomonidan qabul qilinmadi').strip()

    if not batch_id:
        return JsonResponse({'success': False, 'message': "batch_id berilmadi"}, status=400)

    batch = CuttingBatch.objects.filter(id=batch_id).first()
    if not batch:
        return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

    batch.dispatch_status = CuttingBatch.DispatchStatus.REJECTED
    batch.rejected_at = timezone.now()
    batch.rejection_reason = f"[{master.get_full_name() or master.username}]: {reason}"
    batch.save(update_fields=['dispatch_status', 'rejected_at', 'rejection_reason'])

    return JsonResponse({
        'success': True,
        'message': f"Pastal #{batch.batch_number} muvaffaqiyatli QAYTARILDI.",
        'batch_id': batch.id,
    })


@require_GET
def api_get_master_monthly_work(request):
    """
    Tizimga kirgan Master foydalanuvchining tanlangan oy bo'yicha qilgan ishlari (qabul qilingan pastallari) hisoboti.
    Pastal kodi, buyurtma, qutilar soni, yopilgan / yopilmagan holati (barcha qutilari OTK dan o'tgan va ta'miri qolmagan).
    Parametrlar:
    - year (standart: joriy yil)
    - month (standart: joriy oy)
    - scope: 'MY' (faqat shu master qabul qilganlar) yoki 'BRANCH' (filialdagi barcha qabul qilinganlar)
    """
    master = _get_active_master(request)
    if not master:
        return JsonResponse({'success': False, 'message': "Iltimos, avval planshetga beydjigingizni skanerlang!"}, status=401)

    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get('year', today.year))
        selected_month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        selected_year = today.year
        selected_month = today.month

    scope = request.GET.get('scope', 'MY').upper()

    # Queryset
    batches_qs = CuttingBatch.objects.filter(
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
        accepted_at__year=selected_year,
        accepted_at__month=selected_month,
    )

    if scope == 'MY':
        batches_qs = batches_qs.filter(accepted_by=master)
    elif scope == 'BRANCH':
        branch = request.GET.get('branch') or master.branch or 'HQ'
        batches_qs = batches_qs.filter(assigned_branch=branch)

    batches_qs = batches_qs.select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'accepted_by'
    ).order_by('-accepted_at')

    pastals_list = []
    total_accepted_batches = 0
    total_units_sum = 0
    total_closed_pastals = 0
    total_unclosed_pastals = 0
    total_repair_boxes_count = 0

    for b in batches_qs:
        total_accepted_batches += 1
        article = b.order_item.article if b.order_item else None
        order = b.order_item.order if b.order_item else None
        customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')

        # Ushbu pastalning barcha qutilari
        boxes_qs = Box.objects.filter(
            Q(cutting_batch_item__batch=b) |
            (Q(pastal_number=b.pastal_code) & Q(order=order))
        ).distinct()

        boxes = list(boxes_qs)
        total_boxes = len(boxes)
        
        # Quti yopilgan hisoblanadi: agar is_controlled==True VA controlled_repair_qty == 0
        closed_boxes = sum(1 for bx in boxes if bx.is_controlled and bx.controlled_repair_qty == 0)
        unclosed_boxes = total_boxes - closed_boxes
        repair_boxes = sum(1 for bx in boxes if bx.controlled_repair_qty > 0)
        repair_units = sum(bx.controlled_repair_qty for bx in boxes)
        
        # Pastal to'liq yopilgan: agar kamida 1 ta qutisi bo'lsa va BARCHA qutilari yopilgan bo'lsa
        is_pastal_closed = (total_boxes > 0 and closed_boxes == total_boxes)
        
        if is_pastal_closed:
            total_closed_pastals += 1
        else:
            total_unclosed_pastals += 1

        total_repair_boxes_count += repair_boxes
        total_units_sum += b.total_quantity

        p_code = get_patok_code(b.assigned_screen) if b.assigned_screen else '—'

        # Skanerlangan biletlar foizi
        all_tickets = Ticket.objects.filter(box__in=boxes)
        total_t = all_tickets.count()
        scanned_t = all_tickets.filter(status=Ticket.Status.SCANNED).count()
        progress_pct = int((scanned_t / total_t * 100)) if total_t > 0 else 0

        pastals_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'article_code': article.code if article else '',
            'assigned_screen': b.assigned_screen,
            'patok_code': p_code,
            'total_units': b.total_quantity,
            'total_boxes': total_boxes,
            'closed_boxes': closed_boxes,
            'unclosed_boxes': unclosed_boxes,
            'repair_boxes': repair_boxes,
            'repair_units': repair_units,
            'is_pastal_closed': is_pastal_closed,
            'progress_pct': progress_pct,
            'accepted_at': timezone.localtime(b.accepted_at).strftime("%d.%m.%Y %H:%M") if b.accepted_at else '—',
            'accepted_by_name': b.accepted_by.get_full_name() if b.accepted_by else 'Master',
        })

    month_names = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
    }

    return JsonResponse({
        'success': True,
        'year': selected_year,
        'month': selected_month,
        'month_name': month_names.get(selected_month, str(selected_month)),
        'scope': scope,
        'master_name': master.get_full_name() or master.username,
        'stats': {
            'total_pastals': total_accepted_batches,
            'total_units': total_units_sum,
            'closed_pastals': total_closed_pastals,
            'unclosed_pastals': total_unclosed_pastals,
            'repair_boxes': total_repair_boxes_count,
        },
        'pastals': pastals_list,
    })


@require_GET
def api_get_pastal_boxes_detail(request, batch_id):
    """
    Bitta pastalga tegishli barcha qutilarning batafsil ro'yxati va holati.
    Master qaysi quti tugamaganligini, ta'mirda yoki OTK kutayotganligini ko'rishi uchun.
    """
    batch = CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'accepted_by'
    ).filter(id=batch_id).first()

    if not batch:
        return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

    order = batch.order_item.order if batch.order_item else None
    article = batch.order_item.article if batch.order_item else None
    customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
    p_code = get_patok_code(batch.assigned_screen) if batch.assigned_screen else '—'

    boxes_qs = Box.objects.filter(
        Q(cutting_batch_item__batch=batch) |
        (Q(pastal_number=batch.pastal_code) & Q(order=order))
    ).distinct().order_by('box_number')

    boxes = list(boxes_qs)
    total_boxes = len(boxes)

    boxes_data = []
    closed_boxes_count = 0
    unclosed_boxes_count = 0
    repair_boxes_count = 0
    total_repair_units = 0

    for bx in boxes:
        is_closed = (bx.is_controlled and bx.controlled_repair_qty == 0)
        if is_closed:
            closed_boxes_count += 1
        else:
            unclosed_boxes_count += 1

        if bx.controlled_repair_qty > 0:
            repair_boxes_count += 1
            total_repair_units += bx.controlled_repair_qty

        # Stikerlar holati
        scanned_tickets_cnt = bx.tickets.filter(status=Ticket.Status.SCANNED).count()
        total_tickets_cnt = bx.tickets.count()

        # Dazmol stikeri urilganmi
        has_dazmol = bx.tickets.filter(
            status=Ticket.Status.SCANNED,
            article_operation__operation__name__icontains='DAZMOL'
        ).exclude(
            article_operation__operation__name__icontains='TASMA'
        ).exclude(
            article_operation__operation__name__icontains="CHO'NTAK"
        ).exists()

        if is_closed:
            status_mode = 'CLOSED'
            status_text = f"Yopilgan • OTK 1-sort: {bx.controlled_first_sort_qty}" + (f", 2-sort: {bx.controlled_second_sort_qty}" if bx.controlled_second_sort_qty else "")
        elif bx.controlled_repair_qty > 0:
            status_mode = 'REPAIR'
            status_text = f"Ta'mirda ({bx.controlled_repair_qty} dona nuqsonli)"
        elif has_dazmol or (total_tickets_cnt > 0 and scanned_tickets_cnt == total_tickets_cnt):
            status_mode = 'WAITING_OTK'
            status_text = "Dazmoldan chiqqan • OTK kutmoqda"
        elif scanned_tickets_cnt > 0:
            status_mode = 'IN_SEWING'
            status_text = f"Tikuv jarayonida ({scanned_tickets_cnt}/{total_tickets_cnt} stiker)"
        else:
            status_mode = 'PENDING'
            status_text = "Tikuvga kirmagan (0 stiker)"

        boxes_data.append({
            'box_id': bx.id,
            'box_number': bx.box_number,
            'box_code': bx.box_code,
            'display_code': bx.display_code,
            'razmer': bx.razmer or "—",
            'quantity': bx.quantity,
            'is_controlled': bx.is_controlled,
            'first_sort': bx.controlled_first_sort_qty,
            'second_sort': bx.controlled_second_sort_qty,
            'repair_qty': bx.controlled_repair_qty,
            'defect_qty': bx.controlled_defect_qty,
            'is_closed': is_closed,
            'scanned_tickets_count': scanned_tickets_cnt,
            'total_tickets_count': total_tickets_cnt,
            'status_mode': status_mode,
            'status_text': status_text,
        })

    is_pastal_closed = (total_boxes > 0 and closed_boxes_count == total_boxes)

    return JsonResponse({
        'success': True,
        'pastal': {
            'batch_id': batch.id,
            'batch_number': batch.batch_number,
            'pastal_code': batch.pastal_code or str(batch.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'article_code': article.code if article else '',
            'patok_code': p_code,
            'total_units': batch.total_quantity,
            'total_boxes': total_boxes,
            'closed_boxes': closed_boxes_count,
            'unclosed_boxes': unclosed_boxes_count,
            'repair_boxes': repair_boxes_count,
            'repair_units': total_repair_units,
            'is_pastal_closed': is_pastal_closed,
            'accepted_at': timezone.localtime(batch.accepted_at).strftime("%d.%m.%Y %H:%M") if batch.accepted_at else '—',
            'accepted_by_name': batch.accepted_by.get_full_name() if batch.accepted_by else 'Master',
        },
        'boxes': boxes_data,
    })
