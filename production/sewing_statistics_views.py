from datetime import timedelta
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Prefetch
from django.http import JsonResponse, HttpResponse
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from django.utils import timezone

from accounts.models import User
from production.models import Order, OrderItem, Box, Ticket, ArticleOperation, BoxQualityInspectionLog
from production.services import get_patok_name, get_patok_code


def manager_or_superadmin_required(view_func):
    """Faqat Superadmin va Menejerlar uchun ruxsat tekshiruvi"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        is_super = request.user.is_superadmin() or request.user.is_superuser
        is_mgr = getattr(request.user, 'is_manager', lambda: False)() or request.user.role in [
            User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.BRANCH_ADMIN, User.Role.MANAGER
        ]
        if not (is_super or is_mgr):
            messages.error(request, "Ushbu sahifaga faqat Superadmin va Menejerlar kira oladi!")
            return redirect('root_login')
        return view_func(request, *args, **kwargs)
    return wrapper


def get_dazmol_operation_ids_for_article(article):
    """
    Model uchun dazmol yoki oxirgi tikuv operatsiyasi ID larini qaytaradi:
    - Agar operatsiya nomida yoki kodida 'DAZMOL' bo'lsa, o'shalarni oladi.
    - Agar modelda dazmol bo'lmasa, eng oxirgi ketma-ketlikdagi (sequence) operatsiyani oladi.
    """
    ops = list(article.article_operations.select_related('operation').order_by('sequence', 'id'))
    if not ops:
        return set()

    dazmol_ops = [
        ao for ao in ops
        if 'DAZMOL' in ao.operation.name.upper() or (ao.operation.code and 'DAZMOL' in ao.operation.code.upper())
    ]
    if dazmol_ops:
        return {ao.id for ao in dazmol_ops}

    # Dazmol bo'lmasa, eng oxirgi operatsiya
    return {ops[-1].id}


def evaluate_box_progress(box, dazmol_ao_ids=None):
    """
    Bitta qutining tikim jarayonidagi to'liq statusini hisoblaydi:
    - total_tickets: jami bekor qilinmagan operatsiyalar
    - scanned_count: tikuvchilar bajargan operatsiyalar
    - progress_pct: bajarilish foizi
    - has_entered_sewing: kamida 1 ta stikeri urilganmi
    - has_passed_dazmol: dazmoldan o'tganmi
    - is_in_repair: hozir ta'mirda turibdimi (controlled_repair_qty > 0)
    - repair_cycles: necha marta ta'mirga borgan
    - is_re_repair: qayta ta'mirmi (repair_cycles >= 2)
    - is_waiting_control: tikuv 100% tugagan, lekin sifat nazorati (OTK) o'tmagan va ta'mirda emas
    - unreached_control: dazmoldan o'tgan, lekin hali kontrolda kutmayapti, kontroldan o'tmagan va ta'mirda emas
    - actual_controlled_qty: kontrol bergan aniq son (1-sort + 2-sort + defect + repair)
    - control_variance: actual_controlled_qty - nominal quantity (ortiqcha / kam)
    - last_scanned: oxirgi skanerlangan operatsiya (qayergacha borgan)
    - next_pending: navbatdagi kutilayotgan operatsiya
    """
    tickets = [t for t in box.tickets.all() if t.status != Ticket.Status.CANCELLED]
    total_tickets = len(tickets)
    scanned_tickets = [t for t in tickets if t.status == Ticket.Status.SCANNED]
    scanned_count = len(scanned_tickets)

    has_entered_sewing = (scanned_count > 0)
    progress_pct = int((scanned_count / total_tickets * 100)) if total_tickets > 0 else 0

    has_passed_dazmol = False
    if dazmol_ao_ids:
        has_passed_dazmol = any(
            t.article_operation_id in dazmol_ao_ids and t.status == Ticket.Status.SCANNED
            for t in tickets
        )

    # Ta'mir tekshiruvi va necha marta ta'mirga ketgani
    logs = list(box.quality_inspection_logs.all()) if hasattr(box, 'quality_inspection_logs') else []
    repair_cycles = len([l for l in logs if (l.repair_qty or 0) > 0])
    is_in_repair = (box.controlled_repair_qty > 0)
    is_re_repair = (repair_cycles >= 2)

    # Foydalanuvchi qoidasi: Tikuv va dazmol operatsiyalari to'liq tugagan (100%), lekin hali OTK o'tmagan va ta'mirda emas
    is_waiting_control = (not box.is_controlled) and (not is_in_repair) and (total_tickets > 0) and (scanned_count == total_tickets)

    # Dazmoldan o'tgan-u, lekin kontrolgacha yetib bormagan / oraliqda turgan ishlar
    unreached_control = has_passed_dazmol and (not box.is_controlled) and (not is_waiting_control) and (not is_in_repair)

    # Kontrol bergan aniq son va farq (variance)
    actual_controlled_qty = (box.controlled_first_sort_qty or 0) + (box.controlled_second_sort_qty or 0) + (box.controlled_defect_qty or 0) + (box.controlled_repair_qty or 0)

    nominal_qty = box.quantity
    control_variance = 0
    if box.is_controlled or actual_controlled_qty > 0:
        control_variance = actual_controlled_qty - nominal_qty

    # Qayergacha borgan (oxirgi skanerlangan operatsiya)
    last_scanned = None
    if scanned_tickets:
        scanned_tickets_sorted = sorted(
            scanned_tickets,
            key=lambda t: (
                t.article_operation.sequence if t.article_operation else 0,
                t.scanned_at.timestamp() if t.scanned_at else 0
            ),
            reverse=True
        )
        last_scanned = scanned_tickets_sorted[0]

    # Navbatdagi kutilayotgan operatsiya
    next_pending = None
    pending_tickets = [t for t in tickets if t.status == Ticket.Status.PENDING]
    if pending_tickets:
        pending_tickets_sorted = sorted(
            pending_tickets,
            key=lambda t: (t.article_operation.sequence if t.article_operation else 0, t.id)
        )
        next_pending = pending_tickets_sorted[0]

    return {
        'total_tickets': total_tickets,
        'scanned_count': scanned_count,
        'progress_pct': progress_pct,
        'has_entered_sewing': has_entered_sewing,
        'has_passed_dazmol': has_passed_dazmol,
        'is_in_repair': is_in_repair,
        'repair_cycles': repair_cycles,
        'is_re_repair': is_re_repair,
        'is_waiting_control': is_waiting_control,
        'unreached_control': unreached_control,
        'actual_controlled_qty': actual_controlled_qty,
        'control_variance': control_variance,
        'last_scanned': last_scanned,
        'next_pending': next_pending,
    }


def calculate_pipeline_balance(boxes, planned_qty=0, cut_qty=0, dazmol_ao_ids=None):
    """
    Rahbariyat savollariga to'liq javob beruvchi Ishlab chiqarish va Nazorat Balansi:
    1. Kesim nechta kesdi (cut_qty)
    2. Nechtasi tikimda (entered_sewing_qty va in_sewing_pre_dazmol_qty)
    3. Nechtasi dazmoldan o'tdi (dazmol_qty)
    4. Dazmoldan o'tgan-u, lekin kontrolgacha yetib bormagan (unreached_control_qty)
    5. Controlda nechta quti va nechta ish kutyapti (waiting_control_qty, waiting_control_boxes)
    6. Controldan nechta quti o'tib yopildi va qanday holatda (controlled_boxes_count, 1-sort, 2-sort)
    7. Nechta quti va ish ta'mir jarayonida (in_repair_boxes, in_repair_qty)
    8. Necha marta ta'mirga borgan (1-marta va 2+ marta qayta ta'mir)
    9. Kontrol bergan aniq son va Farq/Balans (+ / - / 0)
    10. 10% Brak ogohlantirish indikatori (is_brak_warning, brak_rate_pct)
    """
    total_boxed_qty = sum(b.quantity for b in boxes)
    boxes_count = len(boxes)

    entered_sewing_qty = 0
    entered_sewing_boxes = 0
    in_sewing_pre_dazmol_qty = 0
    in_sewing_pre_dazmol_boxes = 0

    dazmol_qty = 0
    dazmol_boxes = 0

    unreached_control_qty = 0
    unreached_control_boxes = 0

    waiting_control_qty = 0
    waiting_control_boxes = 0

    in_repair_qty = 0
    in_repair_boxes = 0
    re_repair_qty = 0
    re_repair_boxes = 0
    first_time_repair_boxes = 0

    controlled_boxes_count = 0
    first_sort_qty = 0
    second_sort_qty = 0
    defect_qty = 0

    total_closed_qty = 0
    nominal_closed_qty = 0
    net_variance = 0

    for b in boxes:
        b_eval = evaluate_box_progress(b, dazmol_ao_ids)

        if b_eval['has_entered_sewing']:
            entered_sewing_qty += b.quantity
            entered_sewing_boxes += 1

            if not b_eval['has_passed_dazmol']:
                in_sewing_pre_dazmol_qty += b.quantity
                in_sewing_pre_dazmol_boxes += 1

        if b_eval['has_passed_dazmol']:
            dazmol_qty += b.quantity
            dazmol_boxes += 1

        if b_eval['unreached_control']:
            unreached_control_qty += b.quantity
            unreached_control_boxes += 1

        if b_eval['is_waiting_control']:
            waiting_control_qty += b.quantity
            waiting_control_boxes += 1

        if b_eval['is_in_repair']:
            in_repair_boxes += 1
            in_repair_qty += b.controlled_repair_qty or b.quantity
            if b_eval['is_re_repair']:
                re_repair_boxes += 1
                re_repair_qty += b.controlled_repair_qty or b.quantity
            else:
                first_time_repair_boxes += 1

        if b.is_controlled:
            controlled_boxes_count += 1
            first_sort_qty += b.controlled_first_sort_qty
            second_sort_qty += b.controlled_second_sort_qty
            defect_qty += b.controlled_defect_qty

            box_closed_actual = b.controlled_first_sort_qty + b.controlled_second_sort_qty + b.controlled_defect_qty
            total_closed_qty += box_closed_actual
            nominal_closed_qty += b.quantity
            net_variance += (box_closed_actual - b.quantity)

    # Brak foizi (yopilgan ishlar ichida 2-sort + defect nisbati)
    effective_defects = second_sort_qty + defect_qty
    brak_rate_pct = round((effective_defects / total_closed_qty * 100), 1) if total_closed_qty > 0 else 0.0
    is_brak_warning = (brak_rate_pct >= 10.0) and (total_closed_qty > 0)

    # Foizlar
    cut_pct = round((cut_qty / planned_qty * 100), 1) if planned_qty > 0 else 0.0
    sewing_pct = round((entered_sewing_qty / planned_qty * 100), 1) if planned_qty > 0 else 0.0
    dazmol_pct = round((dazmol_qty / planned_qty * 100), 1) if planned_qty > 0 else 0.0

    return {
        'planned_qty': planned_qty,
        'cut_qty': cut_qty,
        'cut_pct': cut_pct,
        'boxed_qty': total_boxed_qty,
        'boxes_count': boxes_count,
        'entered_sewing_qty': entered_sewing_qty,
        'entered_sewing_boxes': entered_sewing_boxes,
        'sewing_pct': sewing_pct,
        'in_sewing_pre_dazmol_qty': in_sewing_pre_dazmol_qty,
        'in_sewing_pre_dazmol_boxes': in_sewing_pre_dazmol_boxes,
        'dazmol_qty': dazmol_qty,
        'dazmol_boxes': dazmol_boxes,
        'dazmol_pct': dazmol_pct,
        'unreached_control_qty': unreached_control_qty,
        'unreached_control_boxes': unreached_control_boxes,
        'waiting_control_qty': waiting_control_qty,
        'waiting_control_boxes': waiting_control_boxes,
        'in_repair_qty': in_repair_qty,
        'in_repair_boxes': in_repair_boxes,
        'first_time_repair_boxes': first_time_repair_boxes,
        're_repair_qty': re_repair_qty,
        're_repair_boxes': re_repair_boxes,
        'controlled_boxes_count': controlled_boxes_count,
        'first_sort_qty': first_sort_qty,
        'second_sort_qty': second_sort_qty,
        'defect_qty': defect_qty,
        'total_closed_qty': total_closed_qty,
        'nominal_closed_qty': nominal_closed_qty,
        'net_variance': net_variance,
        'brak_rate_pct': brak_rate_pct,
        'is_brak_warning': is_brak_warning,
    }


def get_patoks_scrap_and_warning_report(time_filter='week', order_id=None):
    """
    Patoklar / Masterlar kesimida Haftalik va Oylik Sifat, 2-Sort (Brak) va Ta'mir Hisoboti:
    - Har bir patok bo'yicha jami kontroldan o'tgan dona, 1-sort, 2-sort, ta'mir soni
    - 2-sort (brak) foizi
    - Agar brak foizi >= 10% bo'lsa: 🚨 OGOHLANTIRISH banneri va alohida ajratib ko'rsatish
    """
    now = timezone.now()
    if time_filter == 'week':
        # Oxirgi 7 kun
        start_date = now - timedelta(days=7)
    elif time_filter == 'month':
        # Oxirgi 30 kun
        start_date = now - timedelta(days=30)
    else:
        start_date = None

    logs_qs = BoxQualityInspectionLog.objects.select_related(
        'box', 'box__order', 'box__article', 'inspector'
    ).prefetch_related(
        'box__tickets'
    )

    if start_date:
        logs_qs = logs_qs.filter(created_at__gte=start_date)

    if order_id:
        logs_qs = logs_qs.filter(box__order_id=order_id)

    patok_stats = {}

    def get_box_patok_info(box):
        scanned_tickets = [t for t in box.tickets.all() if t.status == Ticket.Status.SCANNED and t.screen_number]
        if not scanned_tickets:
            any_tickets = [t for t in box.tickets.all() if t.screen_number]
            if any_tickets:
                sn = any_tickets[-1].screen_number
                return sn, get_patok_code(sn), get_patok_name(sn)
            return None, "OTHER", "Noma'lum Patok"
        sn = scanned_tickets[-1].screen_number
        return sn, get_patok_code(sn), get_patok_name(sn)

    for log in logs_qs:
        box = log.box
        sn, p_code, p_name = get_box_patok_info(box)

        if p_code not in patok_stats:
            patok_stats[p_code] = {
                'screen_number': sn,
                'patok_code': p_code,
                'patok_name': p_name,
                'inspected_boxes_set': set(),
                'inspected_qty': 0,
                'first_sort_qty': 0,
                'second_sort_qty': 0,
                'repair_qty': 0,
                'defect_qty': 0,
                'closed_qty': 0,
            }

        ps = patok_stats[p_code]
        ps['inspected_boxes_set'].add(box.id)
        ps['first_sort_qty'] += log.first_sort_qty
        sec = log.second_sort_qty or (log.defect_qty if log.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)
        ps['second_sort_qty'] += sec
        ps['repair_qty'] += log.repair_qty
        ps['defect_qty'] += (log.defect_qty if log.action_type != BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)
        ps['inspected_qty'] += log.inspected_qty
        ps['closed_qty'] += (log.first_sort_qty + sec)

    # Hozirgi kunda faol ta'mirda turgan qutilarni ham patoklar bo'yicha bog'laymiz
    active_repairs_qs = Box.objects.filter(
        controlled_repair_qty__gt=0
    ).exclude(status=Box.Status.CANCELLED).prefetch_related('tickets')
    if order_id:
        active_repairs_qs = active_repairs_qs.filter(order_id=order_id)

    for b in active_repairs_qs:
        sn, p_code, p_name = get_box_patok_info(b)
        if p_code not in patok_stats:
            patok_stats[p_code] = {
                'screen_number': sn,
                'patok_code': p_code,
                'patok_name': p_name,
                'inspected_boxes_set': set(),
                'inspected_qty': 0,
                'first_sort_qty': 0,
                'second_sort_qty': 0,
                'repair_qty': 0,
                'defect_qty': 0,
                'closed_qty': 0,
            }
        ps = patok_stats[p_code]
        ps['active_repair_boxes'] = ps.get('active_repair_boxes', 0) + 1
        ps['active_repair_units'] = ps.get('active_repair_units', 0) + b.controlled_repair_qty

    report_list = []
    has_any_warning = False

    for p_code, data in patok_stats.items():
        boxes_count = len(data['inspected_boxes_set'])
        total_eval = data['first_sort_qty'] + data['second_sort_qty'] + data['defect_qty']
        brak_count = data['second_sort_qty'] + data['defect_qty']
        brak_rate_pct = round((brak_count / total_eval * 100), 1) if total_eval > 0 else 0.0

        is_warning = (brak_rate_pct >= 10.0) and (total_eval >= 10)
        if is_warning:
            has_any_warning = True

        report_list.append({
            'screen_number': data['screen_number'],
            'patok_code': data['patok_code'],
            'patok_name': data['patok_name'],
            'boxes_count': boxes_count,
            'total_units': total_eval,
            'first_sort_qty': data['first_sort_qty'],
            'second_sort_qty': data['second_sort_qty'],
            'defect_qty': data['defect_qty'],
            'repair_qty': data['repair_qty'],
            'active_repair_boxes': data.get('active_repair_boxes', 0),
            'active_repair_units': data.get('active_repair_units', 0),
            'brak_rate_pct': brak_rate_pct,
            'is_warning': is_warning,
        })

    report_list.sort(key=lambda x: (x['is_warning'], x['brak_rate_pct'], x['total_units']), reverse=True)

    return {
        'report_list': report_list,
        'has_any_warning': has_any_warning,
        'time_filter': time_filter,
    }


@manager_or_superadmin_required
def sewing_statistics_orders_view(request):
    """
    1-BOSQICH: Zakazlar Ro'yxati (Tikim Jarayoni Statistikasi Bosh Sahifasi)
    - Har bir zakaz bo'yicha umumiy reja, tikimga kirgan dona, dazmol, kontrolda kutayotgan, 1-sort, 2-sort
    """
    status_filter = request.GET.get('status', 'IN_PROGRESS').strip()
    search_q = request.GET.get('q', '').strip()

    orders_qs = Order.objects.select_related('customer').prefetch_related(
        'items__article__model',
        'items__article__article_operations__operation',
        'items__sizes',
        Prefetch(
            'boxes',
            queryset=Box.objects.exclude(status=Box.Status.CANCELLED).select_related(
                'article'
            ).prefetch_related(
                Prefetch(
                    'tickets',
                    queryset=Ticket.objects.exclude(status=Ticket.Status.CANCELLED).select_related(
                        'article_operation__operation'
                    )
                ),
                Prefetch(
                    'quality_inspection_logs',
                    queryset=BoxQualityInspectionLog.objects.order_by('created_at')
                )
            )
        )
    ).order_by('-created_at')

    if status_filter == 'IN_PROGRESS':
        orders_qs = orders_qs.filter(status=Order.Status.IN_PROGRESS)
    elif status_filter == 'COMPLETED':
        orders_qs = orders_qs.filter(status=Order.Status.COMPLETED)
    elif status_filter == 'ARCHIVED':
        orders_qs = orders_qs.filter(status=Order.Status.ARCHIVED)

    if search_q:
        orders_qs = orders_qs.filter(
            Q(order_number__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(customer__name__icontains=search_q) |
            Q(items__article__name__icontains=search_q) |
            Q(items__article__code__icontains=search_q)
        ).distinct()

    orders_list = []
    overall_kpis = {
        'total_orders': 0,
        'total_planned': 0,
        'total_boxed': 0,
        'total_boxes': 0,
        'total_entered_sewing': 0,
        'total_entered_sewing_boxes': 0,
        'total_dazmol': 0,
        'total_dazmol_boxes': 0,
        'total_unreached_control': 0,
        'total_unreached_control_boxes': 0,
        'total_waiting_control': 0,
        'total_waiting_control_boxes': 0,
        'total_in_repair': 0,
        'total_in_repair_boxes': 0,
        'total_first_sort': 0,
        'total_second_sort': 0,
        'total_controlled_boxes': 0,
        'total_variance': 0,
        'has_brak_warning': False,
    }

    for ord_obj in orders_qs:
        total_planned = sum(item.total_planned_quantity for item in ord_obj.items.all()) or ord_obj.total_quantity or 0
        boxes = list(ord_obj.boxes.all())
        total_boxed = sum(b.quantity for b in boxes)

        # Har bir artikul uchun dazmol operatsiyalari keshini tayyorlash
        article_dazmol_map = {}
        for it in ord_obj.items.all():
            article_dazmol_map[it.article_id] = get_dazmol_operation_ids_for_article(it.article)

        entered_sewing_qty = 0
        entered_sewing_boxes = 0
        dazmol_qty = 0
        dazmol_boxes = 0
        unreached_control_qty = 0
        unreached_control_boxes = 0
        waiting_control_qty = 0
        waiting_control_boxes = 0
        in_repair_qty = 0
        in_repair_boxes = 0
        first_sort_qty = 0
        second_sort_qty = 0
        controlled_boxes = 0

        for b in boxes:
            dazmol_ids = article_dazmol_map.get(b.article_id, set())
            b_eval = evaluate_box_progress(b, dazmol_ids)

            if b_eval['has_entered_sewing']:
                entered_sewing_qty += b.quantity
                entered_sewing_boxes += 1

            if b_eval['has_passed_dazmol']:
                dazmol_qty += b.quantity
                dazmol_boxes += 1

            if b_eval['unreached_control']:
                unreached_control_qty += b.quantity
                unreached_control_boxes += 1

            if b_eval['is_waiting_control']:
                waiting_control_qty += b.quantity
                waiting_control_boxes += 1

            if b_eval['is_in_repair']:
                in_repair_boxes += 1
                in_repair_qty += b.controlled_repair_qty or b.quantity

            if b.is_controlled:
                controlled_boxes += 1
                first_sort_qty += b.controlled_first_sort_qty
                second_sort_qty += b.controlled_second_sort_qty

        entered_pct = round((entered_sewing_qty / total_planned * 100), 1) if total_planned > 0 else 0.0
        dazmol_pct = round((dazmol_qty / total_planned * 100), 1) if total_planned > 0 else 0.0

        # Model / Order brak foizi
        total_eval = first_sort_qty + second_sort_qty
        ord_brak_pct = round((second_sort_qty / total_eval * 100), 1) if total_eval > 0 else 0.0
        is_warning = (ord_brak_pct >= 10.0) and (total_eval >= 10)
        if is_warning:
            overall_kpis['has_brak_warning'] = True

        orders_list.append({
            'order': ord_obj,
            'models_count': ord_obj.items.count(),
            'total_planned': total_planned,
            'total_boxed': total_boxed,
            'boxes_count': len(boxes),
            'entered_sewing_qty': entered_sewing_qty,
            'entered_sewing_boxes': entered_sewing_boxes,
            'entered_pct': entered_pct,
            'dazmol_qty': dazmol_qty,
            'dazmol_boxes': dazmol_boxes,
            'dazmol_pct': dazmol_pct,
            'unreached_control_qty': unreached_control_qty,
            'unreached_control_boxes': unreached_control_boxes,
            'waiting_control_qty': waiting_control_qty,
            'waiting_control_boxes': waiting_control_boxes,
            'in_repair_qty': in_repair_qty,
            'in_repair_boxes': in_repair_boxes,
            'controlled_boxes': controlled_boxes,
            'first_sort_qty': first_sort_qty,
            'second_sort_qty': second_sort_qty,
            'brak_rate_pct': ord_brak_pct,
            'is_brak_warning': is_warning,
        })

        overall_kpis['total_orders'] += 1
        overall_kpis['total_planned'] += total_planned
        overall_kpis['total_boxed'] += total_boxed
        overall_kpis['total_boxes'] += len(boxes)
        overall_kpis['total_entered_sewing'] += entered_sewing_qty
        overall_kpis['total_entered_sewing_boxes'] += entered_sewing_boxes
        overall_kpis['total_dazmol'] += dazmol_qty
        overall_kpis['total_dazmol_boxes'] += dazmol_boxes
        overall_kpis['total_unreached_control'] += unreached_control_qty
        overall_kpis['total_unreached_control_boxes'] += unreached_control_boxes
        overall_kpis['total_waiting_control'] += waiting_control_qty
        overall_kpis['total_waiting_control_boxes'] += waiting_control_boxes
        overall_kpis['total_in_repair'] += in_repair_qty
        overall_kpis['total_in_repair_boxes'] += in_repair_boxes
        overall_kpis['total_first_sort'] += first_sort_qty
        overall_kpis['total_second_sort'] += second_sort_qty
        overall_kpis['total_controlled_boxes'] += controlled_boxes

    paginator = Paginator(orders_list, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'production/sewing_statistics_orders.html', {
        'page_obj': page_obj,
        'orders_data': page_obj.object_list,
        'status_filter': status_filter,
        'search_q': search_q,
        'overall_kpis': overall_kpis,
    })


@manager_or_superadmin_required
def sewing_statistics_order_models_view(request, order_id: int):
    """
    2-BOSQICH: Bitta Zakaz Ichidagi Barcha Modellar (Artikullar) Ko'rinishi
    - Model rasmi, nomi, artikul kodi
    - Har bir modelning umumiy reja, kesilgan, tikimga kirgan, dazmol, kontrolda kutayotgan va sifat natijasi
    - "Statistikani ko'rish" tugmasi orqali razmerlar bo'limiga kirish
    """
    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__article__model',
            'items__article__article_operations__operation',
            'items__sizes',
            Prefetch(
                'boxes',
                queryset=Box.objects.exclude(status=Box.Status.CANCELLED).select_related(
                    'article'
                ).prefetch_related(
                    Prefetch(
                        'tickets',
                        queryset=Ticket.objects.exclude(status=Ticket.Status.CANCELLED).select_related(
                            'article_operation__operation'
                        )
                    ),
                    Prefetch(
                        'quality_inspection_logs',
                        queryset=BoxQualityInspectionLog.objects.order_by('created_at')
                    )
                )
            )
        ),
        id=order_id
    )

    boxes = list(order.boxes.all())
    models_data = []

    order_totals = {
        'planned': 0,
        'cut': 0,
        'boxed': 0,
        'boxes_count': len(boxes),
        'entered_sewing': 0,
        'entered_sewing_boxes': 0,
        'dazmol': 0,
        'dazmol_boxes': 0,
        'unreached_control': 0,
        'unreached_control_boxes': 0,
        'waiting_control': 0,
        'waiting_control_boxes': 0,
        'in_repair': 0,
        'in_repair_boxes': 0,
        're_repair': 0,
        're_repair_boxes': 0,
        'first_sort': 0,
        'second_sort': 0,
        'controlled_boxes_count': 0,
        'control_variance': 0,
        'brak_rate_pct': 0.0,
        'is_brak_warning': False,
    }

    for item in order.items.all():
        art = item.article
        dazmol_ao_ids = get_dazmol_operation_ids_for_article(art)

        # Ushbu artikulga tegishli qutilar
        art_boxes = [b for b in boxes if b.article_id == art.id]
        total_boxed_qty = sum(b.quantity for b in art_boxes)

        planned_qty = item.total_planned_quantity
        cut_qty = item.total_cut_quantity
        model_balance = calculate_pipeline_balance(art_boxes, planned_qty, cut_qty, dazmol_ao_ids)

        article_image_url = None
        if art.image:
            try:
                article_image_url = art.image.url
            except Exception:
                article_image_url = None

        models_data.append({
            'item': item,
            'article': art,
            'article_image_url': article_image_url,
            'model_name': art.model.name if art.model else art.name,
            'planned_qty': planned_qty,
            'cut_qty': cut_qty,
            'cut_pct': model_balance['cut_pct'],
            'total_boxed_qty': total_boxed_qty,
            'boxes_count': len(art_boxes),
            'entered_sewing_qty': model_balance['entered_sewing_qty'],
            'entered_sewing_boxes': model_balance['entered_sewing_boxes'],
            'sewing_pct': model_balance['sewing_pct'],
            'dazmol_qty': model_balance['dazmol_qty'],
            'dazmol_boxes': model_balance['dazmol_boxes'],
            'dazmol_pct': model_balance['dazmol_pct'],
            'unreached_control_qty': model_balance['unreached_control_qty'],
            'unreached_control_boxes': model_balance['unreached_control_boxes'],
            'waiting_control_qty': model_balance['waiting_control_qty'],
            'waiting_control_boxes': model_balance['waiting_control_boxes'],
            'in_repair_qty': model_balance['in_repair_qty'],
            'in_repair_boxes': model_balance['in_repair_boxes'],
            're_repair_qty': model_balance['re_repair_qty'],
            're_repair_boxes': model_balance['re_repair_boxes'],
            'first_sort_qty': model_balance['first_sort_qty'],
            'second_sort_qty': model_balance['second_sort_qty'],
            'repair_qty': model_balance['in_repair_qty'],
            'controlled_boxes_count': model_balance['controlled_boxes_count'],
            'control_variance': model_balance['net_variance'],
            'brak_rate_pct': model_balance['brak_rate_pct'],
            'is_brak_warning': model_balance['is_brak_warning'],
            'balance': model_balance,
        })

        order_totals['planned'] += planned_qty
        order_totals['cut'] += cut_qty
        order_totals['boxed'] += total_boxed_qty
        order_totals['entered_sewing'] += model_balance['entered_sewing_qty']
        order_totals['entered_sewing_boxes'] += model_balance['entered_sewing_boxes']
        order_totals['dazmol'] += model_balance['dazmol_qty']
        order_totals['dazmol_boxes'] += model_balance['dazmol_boxes']
        order_totals['unreached_control'] += model_balance['unreached_control_qty']
        order_totals['unreached_control_boxes'] += model_balance['unreached_control_boxes']
        order_totals['waiting_control'] += model_balance['waiting_control_qty']
        order_totals['waiting_control_boxes'] += model_balance['waiting_control_boxes']
        order_totals['in_repair'] += model_balance['in_repair_qty']
        order_totals['in_repair_boxes'] += model_balance['in_repair_boxes']
        order_totals['re_repair'] += model_balance['re_repair_qty']
        order_totals['re_repair_boxes'] += model_balance['re_repair_boxes']
        order_totals['first_sort'] += model_balance['first_sort_qty']
        order_totals['second_sort'] += model_balance['second_sort_qty']
        order_totals['controlled_boxes_count'] += model_balance['controlled_boxes_count']
        order_totals['control_variance'] += model_balance['net_variance']

    # Butun zakaz balansi
    order_balance = calculate_pipeline_balance(boxes, order_totals['planned'], order_totals['cut'])
    order_totals['brak_rate_pct'] = order_balance['brak_rate_pct']
    order_totals['is_brak_warning'] = order_balance['is_brak_warning']

    return render(request, 'production/sewing_statistics_order_models.html', {
        'order': order,
        'models_data': models_data,
        'order_totals': order_totals,
        'order_balance': order_balance,
    })


@manager_or_superadmin_required
def sewing_statistics_model_detail_view(request, order_id: int, order_item_id: int):
    """
    3-BOSQICH: Tanlangan Modelning Razmerlar Bo'yicha To'liq Tikim Statistikasi
    - Har bir razmer bo'yicha:
      * Reja, Kesilgan, Qutilardagi jami dona va qutilar soni
      * Tikimga kirgan dona va qutilar soni (kamida 1 stikeri urilgan)
      * Dazmoldan o'tgan dona va qutilar soni
      * Kontrolgacha yetib bormagan ishlar soni
      * Kontrolda kutib turgan dona va qutilar soni (100% tikilgan, OTK kutilmoqda)
      * Kontroldan o'tgan: 1-sort, 2-sort, Ta'mirda
      * Aniq nazorat soni va Farq (+ / - / Balans)
    - Razmer qatori bosilganda pastdan real-time bazadan qutilar yuklanadi (AJAX)
    """
    order = get_object_or_404(Order.objects.select_related('customer'), id=order_id)
    item = get_object_or_404(
        OrderItem.objects.select_related('article', 'article__model').prefetch_related(
            'sizes__cutting_items',
            'article__article_operations__operation'
        ),
        id=order_item_id,
        order=order
    )
    article = item.article
    dazmol_ao_ids = get_dazmol_operation_ids_for_article(article)

    # Qutilar, biletlar va sifat jurnallarini prefetch qilish
    boxes = list(
        order.boxes.filter(article=article).exclude(
            status=Box.Status.CANCELLED
        ).select_related('controlled_by').prefetch_related(
            Prefetch(
                'tickets',
                queryset=Ticket.objects.exclude(status=Ticket.Status.CANCELLED).select_related(
                    'article_operation__operation', 'worker__user', 'scanned_by'
                ).order_by('article_operation__sequence', 'id')
            ),
            Prefetch(
                'quality_inspection_logs',
                queryset=BoxQualityInspectionLog.objects.order_by('created_at')
            )
        ).order_by('box_number')
    )

    # Razmerlar ma'lumotlarini tayyorlash
    item_sizes = list(item.sizes.all())
    sizes_map = {s.size_name.strip().upper(): s for s in item_sizes}

    # Barcha unikal razmer nomlari (OrderItemSize + Box.razmer)
    all_size_names_set = set(sizes_map.keys())
    for b in boxes:
        if b.razmer:
            all_size_names_set.add(b.razmer.strip().upper())

    # Tartib bo'yicha saralash
    sorted_size_names = sorted(
        all_size_names_set,
        key=lambda name: (sizes_map[name].id if name in sizes_map else 999999, name)
    )

    sizes_breakdown = []
    model_totals = {
        'planned': 0,
        'cut': 0,
        'boxed': 0,
        'boxes_count': len(boxes),
        'entered_sewing': 0,
        'entered_boxes': 0,
        'dazmol': 0,
        'dazmol_boxes': 0,
        'unreached_control': 0,
        'unreached_control_boxes': 0,
        'waiting_control': 0,
        'waiting_control_boxes': 0,
        'in_repair': 0,
        'in_repair_boxes': 0,
        're_repair': 0,
        're_repair_boxes': 0,
        'first_sort': 0,
        'second_sort': 0,
        'repair': 0,
        'controlled_boxes': 0,
        'control_variance': 0,
        'brak_rate_pct': 0.0,
        'is_brak_warning': False,
    }

    for size_name in sorted_size_names:
        size_obj = sizes_map.get(size_name)
        planned_qty = size_obj.planned_quantity if size_obj else 0
        cut_qty = size_obj.total_cut_quantity if size_obj else 0

        # Ushbu razmer qutilari
        size_boxes = [b for b in boxes if (b.razmer or '').strip().upper() == size_name]
        total_boxed_qty = sum(b.quantity for b in size_boxes)
        size_balance = calculate_pipeline_balance(size_boxes, planned_qty, cut_qty, dazmol_ao_ids)

        sizes_breakdown.append({
            'size_name': size_name,
            'planned_qty': planned_qty,
            'cut_qty': cut_qty,
            'cut_pct': size_balance['cut_pct'],
            'total_boxed_qty': total_boxed_qty,
            'boxes_count': len(size_boxes),
            'entered_sewing_qty': size_balance['entered_sewing_qty'],
            'entered_sewing_boxes': size_balance['entered_sewing_boxes'],
            'sewing_pct': size_balance['sewing_pct'],
            'dazmol_qty': size_balance['dazmol_qty'],
            'dazmol_boxes': size_balance['dazmol_boxes'],
            'dazmol_pct': size_balance['dazmol_pct'],
            'unreached_control_qty': size_balance['unreached_control_qty'],
            'unreached_control_boxes': size_balance['unreached_control_boxes'],
            'waiting_control_qty': size_balance['waiting_control_qty'],
            'waiting_control_boxes': size_balance['waiting_control_boxes'],
            'in_repair_qty': size_balance['in_repair_qty'],
            'in_repair_boxes': size_balance['in_repair_boxes'],
            're_repair_qty': size_balance['re_repair_qty'],
            're_repair_boxes': size_balance['re_repair_boxes'],
            'first_sort_qty': size_balance['first_sort_qty'],
            'second_sort_qty': size_balance['second_sort_qty'],
            'repair_qty': size_balance['in_repair_qty'],
            'controlled_boxes': size_balance['controlled_boxes_count'],
            'control_variance': size_balance['net_variance'],
            'brak_rate_pct': size_balance['brak_rate_pct'],
            'is_brak_warning': size_balance['is_brak_warning'],
            'balance': size_balance,
        })

        model_totals['planned'] += planned_qty
        model_totals['cut'] += cut_qty
        model_totals['boxed'] += total_boxed_qty
        model_totals['entered_sewing'] += size_balance['entered_sewing_qty']
        model_totals['entered_boxes'] += size_balance['entered_sewing_boxes']
        model_totals['dazmol'] += size_balance['dazmol_qty']
        model_totals['dazmol_boxes'] += size_balance['dazmol_boxes']
        model_totals['unreached_control'] += size_balance['unreached_control_qty']
        model_totals['unreached_control_boxes'] += size_balance['unreached_control_boxes']
        model_totals['waiting_control'] += size_balance['waiting_control_qty']
        model_totals['waiting_control_boxes'] += size_balance['waiting_control_boxes']
        model_totals['in_repair'] += size_balance['in_repair_qty']
        model_totals['in_repair_boxes'] += size_balance['in_repair_boxes']
        model_totals['re_repair'] += size_balance['re_repair_qty']
        model_totals['re_repair_boxes'] += size_balance['re_repair_boxes']
        model_totals['first_sort'] += size_balance['first_sort_qty']
        model_totals['second_sort'] += size_balance['second_sort_qty']
        model_totals['repair'] += size_balance['in_repair_qty']
        model_totals['controlled_boxes'] += size_balance['controlled_boxes_count']
        model_totals['control_variance'] += size_balance['net_variance']

    # To'liq model balansi
    model_balance = calculate_pipeline_balance(boxes, model_totals['planned'], model_totals['cut'], dazmol_ao_ids)
    model_totals['cut_pct'] = model_balance['cut_pct']
    model_totals['sewing_pct'] = model_balance['sewing_pct']
    model_totals['dazmol_pct'] = model_balance['dazmol_pct']
    model_totals['brak_rate_pct'] = model_balance['brak_rate_pct']
    model_totals['is_brak_warning'] = model_balance['is_brak_warning']

    article_image_url = None
    if article.image:
        try:
            article_image_url = article.image.url
        except Exception:
            article_image_url = None

    return render(request, 'production/sewing_statistics_model_detail.html', {
        'order': order,
        'item': item,
        'article': article,
        'article_image_url': article_image_url,
        'sizes_breakdown': sizes_breakdown,
        'model_totals': model_totals,
        'model_balance': model_balance,
    })


@manager_or_superadmin_required
def api_sewing_statistics_size_boxes(request):
    """
    4-BOSQICH (AJAX / Real-Time Loader): Tanlangan razmer bo'yicha qutilar reestri
    - Qaysi qutilar tikimga kirgan, nechtasi kirmagan
    - Har bir qutining operatsiyalari, qayergacha borgani (oxirgi skanerlangan operatsiya, tikuvchi, vaqti)
    - Dazmoldan o'tganmi
    - Kontrolgacha yetib bormaganmi
    - Kontrolda kutib turibdimi yoki kontroldan o'tganmi (1-sort, 2-sort)
    - Ta'mirda turibdimi va necha marta ta'mirga borgan
    - Kontrol bergan aniq son va Farq
    """
    order_item_id = request.GET.get('order_item_id')
    size_name = request.GET.get('size_name', '').strip()
    req_format = request.GET.get('format', 'html').strip().lower()

    if not order_item_id or not size_name:
        return JsonResponse({'status': 'ERROR', 'message': "order_item_id va size_name majburiy!"}, status=400)

    order_item = get_object_or_404(
        OrderItem.objects.select_related('order', 'article').prefetch_related(
            'article__article_operations__operation'
        ),
        id=order_item_id
    )
    order = order_item.order
    article = order_item.article
    dazmol_ao_ids = get_dazmol_operation_ids_for_article(article)

    # Ushbu razmerga tegishli barcha qutilarni yuklash
    boxes = list(
        order.boxes.filter(
            article=article,
            razmer__iexact=size_name
        ).exclude(
            status=Box.Status.CANCELLED
        ).select_related('controlled_by').prefetch_related(
            Prefetch(
                'tickets',
                queryset=Ticket.objects.exclude(status=Ticket.Status.CANCELLED).select_related(
                    'article_operation__operation', 'worker__user', 'scanned_by'
                ).order_by('article_operation__sequence', 'id')
            ),
            Prefetch(
                'quality_inspection_logs',
                queryset=BoxQualityInspectionLog.objects.order_by('created_at')
            )
        ).order_by('box_number')
    )

    boxes_data = []
    current_tz = timezone.get_current_timezone()

    summary = {
        'total_boxes': len(boxes),
        'total_units': sum(b.quantity for b in boxes),
        'entered_sewing_boxes': 0,
        'entered_sewing_units': 0,
        'passed_dazmol_boxes': 0,
        'passed_dazmol_units': 0,
        'unreached_control_boxes': 0,
        'unreached_control_units': 0,
        'waiting_control_boxes': 0,
        'waiting_control_units': 0,
        'in_repair_boxes': 0,
        'in_repair_units': 0,
        're_repair_boxes': 0,
        're_repair_units': 0,
        'controlled_boxes': 0,
        'first_sort_units': 0,
        'second_sort_units': 0,
        'repair_units': 0,
        'control_variance': 0,
    }

    for b in boxes:
        b_eval = evaluate_box_progress(b, dazmol_ao_ids)
        has_entered = b_eval['has_entered_sewing']
        has_dazmol = b_eval['has_passed_dazmol']
        is_waiting_ctrl = b_eval['is_waiting_control']
        unreached_ctrl = b_eval['unreached_control']
        is_in_rep = b_eval['is_in_repair']
        is_re_rep = b_eval['is_re_repair']

        if has_entered:
            summary['entered_sewing_boxes'] += 1
            summary['entered_sewing_units'] += b.quantity

        if has_dazmol:
            summary['passed_dazmol_boxes'] += 1
            summary['passed_dazmol_units'] += b.quantity

        if unreached_ctrl:
            summary['unreached_control_boxes'] += 1
            summary['unreached_control_units'] += b.quantity

        if is_waiting_ctrl:
            summary['waiting_control_boxes'] += 1
            summary['waiting_control_units'] += b.quantity

        if is_in_rep:
            summary['in_repair_boxes'] += 1
            summary['in_repair_units'] += b.controlled_repair_qty or b.quantity
            if is_re_rep:
                summary['re_repair_boxes'] += 1
                summary['re_repair_units'] += b.controlled_repair_qty or b.quantity

        if b.is_controlled:
            summary['controlled_boxes'] += 1
            summary['first_sort_units'] += b.controlled_first_sort_qty
            summary['second_sort_units'] += b.controlled_second_sort_qty
            summary['repair_units'] += b.controlled_repair_qty
            summary['control_variance'] += b_eval['control_variance']

        # Oxirgi skanerlangan operatsiya ma'lumotlari
        last_op_info = None
        if b_eval['last_scanned']:
            ls = b_eval['last_scanned']
            worker_display = "—"
            if ls.worker:
                w_uid = getattr(ls.worker, 'worker_id', '') or (ls.worker.user.uid if getattr(ls.worker, 'user', None) else '')
                w_name = getattr(ls.worker, 'full_name', '') or ls.worker.first_name or f"Ishchi #{ls.worker.id}"
                worker_display = f"{w_name} ({w_uid})" if w_uid else w_name

            patok_str = get_patok_name(ls.screen_number) if ls.screen_number else "—"

            last_op_info = {
                'operation_name': ls.article_operation.operation.name if ls.article_operation else "—",
                'worker_name': worker_display,
                'scanned_at': ls.scanned_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M") if ls.scanned_at else "—",
                'patok': patok_str,
            }

        # Navbatdagi kutilayotgan operatsiya
        next_op_info = None
        if b_eval['next_pending']:
            np = b_eval['next_pending']
            next_op_info = {
                'operation_name': np.article_operation.operation.name if np.article_operation else "—",
                'sequence': np.article_operation.sequence if np.article_operation else 1,
            }

        # Qutining barcha operatsiyalari ro'yxati (Tafsilotlar jadvali uchun)
        tickets_list = []
        for t in b.tickets.all():
            if t.status == Ticket.Status.CANCELLED:
                continue
            is_scanned = (t.status == Ticket.Status.SCANNED)
            w_disp = "—"
            if is_scanned and t.worker:
                w_uid = getattr(t.worker, 'worker_id', '') or (t.worker.user.uid if getattr(t.worker, 'user', None) else '')
                w_name = getattr(t.worker, 'full_name', '') or t.worker.first_name or f"Ishchi #{t.worker.id}"
                w_disp = f"{w_name} ({w_uid})" if w_uid else w_name

            tickets_list.append({
                'id': t.id,
                'sequence': t.article_operation.sequence if t.article_operation else 1,
                'operation_name': t.article_operation.operation.name if t.article_operation else "—",
                'price_per_unit': t.article_operation.price_per_unit if t.article_operation else 0,
                'is_scanned': is_scanned,
                'status': t.status,
                'status_display': t.get_status_display(),
                'worker_name': w_disp,
                'scanned_at': t.scanned_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M") if t.scanned_at else "—",
                'patok': get_patok_name(t.screen_number) if t.screen_number else "—",
                'is_dazmol': bool(t.article_operation_id in dazmol_ao_ids),
            })

        boxes_data.append({
            'box': b,
            'box_code': b.display_code,
            'box_number': b.box_number,
            'quantity': b.quantity,
            'pastal_code': b.pastal_code or "—",
            'meto_range': b.meto_range or "—",
            'has_entered_sewing': has_entered,
            'total_tickets': b_eval['total_tickets'],
            'scanned_count': b_eval['scanned_count'],
            'progress_pct': b_eval['progress_pct'],
            'has_passed_dazmol': has_dazmol,
            'unreached_control': unreached_ctrl,
            'is_waiting_control': is_waiting_ctrl,
            'is_in_repair': is_in_rep,
            'repair_cycles': b_eval['repair_cycles'],
            'is_re_repair': is_re_rep,
            'actual_controlled_qty': b_eval['actual_controlled_qty'],
            'control_variance': b_eval['control_variance'],
            'is_controlled': b.is_controlled,
            'first_sort_qty': b.controlled_first_sort_qty,
            'second_sort_qty': b.controlled_second_sort_qty,
            'repair_qty': b.controlled_repair_qty,
            'defect_qty': b.controlled_defect_qty,
            'controlled_by_name': (b.controlled_by.get_full_name() or b.controlled_by.username) if b.controlled_by else "—",
            'controlled_at': b.controlled_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M") if b.controlled_at else "—",
            'last_op': last_op_info,
            'next_op': next_op_info,
            'tickets': tickets_list,
        })

    if req_format == 'json':
        return JsonResponse({
            'status': 'OK',
            'size_name': size_name,
            'summary': summary,
            'boxes': [
                {
                    'id': bx['box'].id,
                    'box_code': bx['box_code'],
                    'box_number': bx['box_number'],
                    'quantity': bx['quantity'],
                    'progress_pct': bx['progress_pct'],
                    'has_entered_sewing': bx['has_entered_sewing'],
                    'has_passed_dazmol': bx['has_passed_dazmol'],
                    'unreached_control': bx['unreached_control'],
                    'is_waiting_control': bx['is_waiting_control'],
                    'is_in_repair': bx['is_in_repair'],
                    'repair_cycles': bx['repair_cycles'],
                    'is_controlled': bx['is_controlled'],
                    'first_sort_qty': bx['first_sort_qty'],
                    'second_sort_qty': bx['second_sort_qty'],
                    'control_variance': bx['control_variance'],
                }
                for bx in boxes_data
            ]
        })

    # Standart format: HTML partial
    html = render_to_string('production/partials/size_boxes_pipeline.html', {
        'order': order,
        'item': order_item,
        'article': article,
        'size_name': size_name,
        'summary': summary,
        'boxes_data': boxes_data,
    }, request=request)

    return HttpResponse(html)


@manager_or_superadmin_required
def sewing_statistics_repairs_view(request):
    """
    5-BOSQICH / ALOHIDA URL: Ta'mir Jarayoni va Sifat Monitoringi
    - URL: /production/sewing-statistics/repairs/
    - Hozir ta'mirda turgan qutilar reestri
    - Qayta ta'mirga (2+ marta) tushgan qutilar
    - Necha marta ta'mirga ketgani (1-marta, 2-marta, ...)
    - Masterlar / Patoklar haftalik sifat & 10% brak ogohlantirish reytingi
    - Qidiruv va status/zakaz/patok bo'yicha filtrlar
    """
    status_tab = request.GET.get('status', 'active').strip()  # 'active', 're_repair', 'resolved', 'all'
    time_filter = request.GET.get('time_filter', 'week').strip()  # 'week', 'month', 'all'
    order_id_filter = request.GET.get('order_id', '').strip()
    patok_filter = request.GET.get('patok', '').strip()
    search_q = request.GET.get('q', '').strip()

    boxes_qs = Box.objects.exclude(status=Box.Status.CANCELLED).select_related(
        'order', 'order__customer', 'article', 'article__model', 'controlled_by'
    ).prefetch_related(
        Prefetch(
            'tickets',
            queryset=Ticket.objects.exclude(status=Ticket.Status.CANCELLED).select_related(
                'article_operation__operation', 'worker__user'
            ).order_by('article_operation__sequence', 'id')
        ),
        Prefetch(
            'quality_inspection_logs',
            queryset=BoxQualityInspectionLog.objects.select_related('inspector').order_by('-created_at')
        )
    )

    if order_id_filter.isdigit():
        boxes_qs = boxes_qs.filter(order_id=int(order_id_filter))

    if search_q:
        boxes_qs = boxes_qs.filter(
            Q(box_code__icontains=search_q) |
            Q(order__order_number__icontains=search_q) |
            Q(article__name__icontains=search_q) |
            Q(article__code__icontains=search_q) |
            Q(pastal_number__icontains=search_q) |
            Q(razmer__icontains=search_q)
        )

    # Barcha ta'mirga aloqador qutilar
    all_repair_candidates = list(
        boxes_qs.filter(
            Q(controlled_repair_qty__gt=0) |
            Q(quality_inspection_logs__repair_qty__gt=0)
        ).distinct().order_by('-controlled_at', '-id')
    )

    current_tz = timezone.get_current_timezone()

    def get_box_patok_name(box):
        scanned_tickets = [t for t in box.tickets.all() if t.status == Ticket.Status.SCANNED and t.screen_number]
        if scanned_tickets:
            return get_patok_name(scanned_tickets[-1].screen_number)
        any_tickets = [t for t in box.tickets.all() if t.screen_number]
        if any_tickets:
            return get_patok_name(any_tickets[-1].screen_number)
        return "Noma'lum"

    processed_boxes = []
    active_repair_boxes_count = 0
    active_repair_units_count = 0
    first_time_repair_count = 0
    re_repair_count = 0
    resolved_count = 0

    for b in all_repair_candidates:
        logs = list(b.quality_inspection_logs.all())
        repair_logs = [l for l in logs if (l.repair_qty or 0) > 0]
        repair_cycles = len(repair_logs)
        is_in_repair = (b.controlled_repair_qty > 0)
        is_re_repair = (repair_cycles >= 2)
        is_resolved = (not is_in_repair) and (repair_cycles > 0) and b.is_controlled
        patok_str = get_box_patok_name(b)

        if is_in_repair:
            active_repair_boxes_count += 1
            active_repair_units_count += b.controlled_repair_qty
            if is_re_repair:
                re_repair_count += 1
            else:
                first_time_repair_count += 1
        elif is_resolved:
            resolved_count += 1

        latest_repair_log = repair_logs[0] if repair_logs else (logs[0] if logs else None)
        repair_notes = latest_repair_log.notes if latest_repair_log else ""

        box_item = {
            'box': b,
            'box_code': b.display_code,
            'box_number': b.box_number,
            'order': b.order,
            'article': b.article or b.target_article,
            'razmer': b.razmer or "—",
            'pastal_code': b.pastal_code or "—",
            'meto_range': b.meto_range or "—",
            'quantity': b.quantity,
            'repair_qty': b.controlled_repair_qty,
            'repair_cycles': repair_cycles,
            'is_in_repair': is_in_repair,
            'is_re_repair': is_re_repair,
            'is_resolved': is_resolved,
            'patok': patok_str,
            'inspector_name': (b.controlled_by.get_full_name() or b.controlled_by.username) if b.controlled_by else (latest_repair_log.inspector.get_full_name() if latest_repair_log and latest_repair_log.inspector else "—"),
            'controlled_at': b.controlled_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M") if b.controlled_at else (latest_repair_log.created_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M") if latest_repair_log else "—"),
            'notes': repair_notes,
            'logs_count': len(logs),
        }

        # Patok filter
        if patok_filter and patok_filter.upper() not in patok_str.upper():
            continue

        # Status filter
        if status_tab == 'active':
            if is_in_repair:
                processed_boxes.append(box_item)
        elif status_tab == 're_repair':
            if is_in_repair and is_re_repair:
                processed_boxes.append(box_item)
        elif status_tab == 'resolved':
            if is_resolved:
                processed_boxes.append(box_item)
        else:  # 'all'
            processed_boxes.append(box_item)

    # Patoklar bo'yicha sifat & 10% ogohlantirish hisoboti
    oid = int(order_id_filter) if order_id_filter.isdigit() else None
    patoks_report = get_patoks_scrap_and_warning_report(time_filter=time_filter, order_id=oid)

    # Paginator
    paginator = Paginator(processed_boxes, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    active_orders = Order.objects.filter(status=Order.Status.IN_PROGRESS).order_by('-created_at')[:30]

    return render(request, 'production/sewing_statistics_repairs.html', {
        'page_obj': page_obj,
        'repair_boxes': page_obj.object_list,
        'status_tab': status_tab,
        'time_filter': time_filter,
        'order_id_filter': order_id_filter,
        'patok_filter': patok_filter,
        'search_q': search_q,
        'active_repair_boxes_count': active_repair_boxes_count,
        'active_repair_units_count': active_repair_units_count,
        'first_time_repair_count': first_time_repair_count,
        're_repair_count': re_repair_count,
        'resolved_count': resolved_count,
        'patoks_report': patoks_report['report_list'],
        'has_any_warning': patoks_report['has_any_warning'],
        'active_orders': active_orders,
    })
