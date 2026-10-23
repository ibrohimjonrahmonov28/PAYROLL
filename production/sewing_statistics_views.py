from collections import defaultdict
from datetime import datetime, date, timedelta
from decimal import Decimal
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Prefetch, Sum, Count
from django.http import JsonResponse, HttpResponse
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.utils import timezone

from accounts.models import User
from production.models import (
    Order, OrderItem, Box, Ticket, ArticleOperation, BoxQualityInspectionLog,
    DailyPatokProgress, CuttingBatch, Operation
)
from production.services import get_patok_name, get_patok_code, get_patok_login, parse_patok_number
from screens.views import get_screen_data


def manager_or_superadmin_required(view_func):
    """Superadmin, Menejerlar va Plan roli uchun ruxsat tekshiruvi"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        is_super = request.user.is_superadmin() or request.user.is_superuser
        is_mgr = getattr(request.user, 'is_manager', lambda: False)() or request.user.role in [
            User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.BRANCH_ADMIN, User.Role.MANAGER
        ]
        is_plan = (getattr(request.user, 'role', None) == User.Role.PLAN)
        if not (is_super or is_mgr or is_plan):
            messages.error(request, "Ushbu sahifaga faqat Superadmin, Menejer va Plan hisoblari kira oladi!")
            return redirect('root_login')
        return view_func(request, *args, **kwargs)
    return wrapper


def is_main_dazmol_operation(name, code=None):
    """
    Haqiqiy yakuniy kiyim dazmoli ekanini aniqlaydi:
    - TASMA DAZMOL va CHO'NTAK DAZMOL kabi oraliq operatsiyalar bundan mustasno.
    """
    if not name and not code:
        return False
    name_u = (name or "").upper()
    code_u = (code or "").upper()
    has_dazmol = ('DAZMOL' in name_u) or ('DAZMOL' in code_u)
    if not has_dazmol:
        return False
    # Oraliq dazmol amallari (tasma, cho'ntak) kiyimning yakuniy dazmoli emas
    is_intermediate = any(excl in name_u or excl in code_u for excl in ('TASMA', "CHO'NTAK", "CHONTAK"))
    return not is_intermediate


def get_dazmol_operation_ids_for_article(article):
    """
    Model uchun dazmol yoki oxirgi tikuv operatsiyasi ID larini qaytaradi:
    - Agar operatsiya nomida yoki kodida 'DAZMOL' bo'lsa (tasma, cho'ntak oraliq dazmollaridan tashqari), o'shalarni oladi.
    - Agar modelda dazmol bo'lmasa, eng oxirgi ketma-ketlikdagi (sequence) operatsiyani oladi.
    """
    ops = list(article.article_operations.select_related('operation').order_by('sequence', 'id'))
    if not ops:
        return set()

    dazmol_ops = [
        ao for ao in ops
        if is_main_dazmol_operation(ao.operation.name, ao.operation.code)
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
    tickets = [
        t for t in box.tickets.all() 
        if t.status != Ticket.Status.CANCELLED and (not t.article_operation or t.article_operation.is_active)
    ]
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

    has_finished_all_ops = (total_tickets > 0 and scanned_count == total_tickets)
    has_passed_terminal = (has_passed_dazmol or has_finished_all_ops)

    # Foydalanuvchi qoidasi: Dazmoldan (yoki oxirgi operatsiyadan) o'tgan, lekin hali OTK yopmagan va ta'mirda bo'lmagan
    # barcha qutilar OTK stoli oldida kutmoqda hisoblanadi (Dazmoldan o'tgan = OTK Kutmoqda + 1-sort + 2-sort + Ta'mir)
    is_waiting_control = has_passed_terminal and (not box.is_controlled) and (not is_in_repair)

    # Mas'uliyatni ajratish (Kim jazolanadi / javobgar):
    # 1. 100% barcha stikeri urilgan - OTK nazoratchisi zudlik bilan tekshirishi shart (OTKchi javobgar)
    is_waiting_control_ready = is_waiting_control and has_finished_all_ops
    # 2. Oraliq tikuv stikeri urilmay dazmolga o'tib ketgan - Tikuvchi / Patok masteri javobgar
    is_waiting_control_missing_ops = is_waiting_control and not is_waiting_control_ready

    # Moslik uchun unreached_control stikeri chala bo'lgan qutilarni ko'rsatadi
    unreached_control = is_waiting_control_missing_ops

    # Dazmolgacha bo'lgan tikim jarayonida (tikimga kirgan, lekin hali dazmoldan o'tmagan, ta'mirda bo'lmagan va yopilmagan)
    is_in_sewing_pre_dazmol = has_entered_sewing and (not has_passed_terminal) and (not is_in_repair) and (not box.is_controlled)

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
        'has_passed_dazmol': has_passed_terminal,
        'is_in_sewing_pre_dazmol': is_in_sewing_pre_dazmol,
        'is_in_repair': is_in_repair,
        'repair_cycles': repair_cycles,
        'is_re_repair': is_re_repair,
        'is_waiting_control': is_waiting_control,
        'is_waiting_control_ready': is_waiting_control_ready,
        'is_waiting_control_missing_ops': is_waiting_control_missing_ops,
        'unreached_control': unreached_control,
        'actual_controlled_qty': actual_controlled_qty,
        'control_variance': control_variance,
        'last_scanned': last_scanned,
        'next_pending': next_pending,
    }


def get_model_pastals_analysis(boxes):
    """
    Model qutilarini tahlil qiladi (Foydalanuvchining yangi qoidasi - qutilar kesimida):
    - Har bir quti tekshiriladi (Bekor qilingan / atmen qutilar qat'iy hisobga olinmaydi)
    - Agar qutidagi kamida 1 dona stiker skanerlangan bo'lsa -> Tikimga Kirgan
    - Agar qutidan birorta ham stiker skanerlanmagan bo'lsa (0 ta skaner) -> Tikim Kutmoqda
    - Stikeri skanerlanishi bilan ushbu quti 'Tikim Kutmoqda'dan chiqib avtomatik patokka (WIP) o'tadi
    - Natijada qutilar pastallar kesimida qulay guruhlanib, qaysi pastaldan qaysi qutilar kutayotgani ko'rsatiladi
    """
    waiting_boxes = []
    entered_boxes = []
    waiting_pastals_map = {}
    waiting_by_size = {}
    waiting_boxes_by_size = {}

    for b in boxes:
        # Atmen bo'lgan qutilar umuman yo'q narsa - qat'iy o'tkazib yuboriladi
        if getattr(b, 'status', None) == Box.Status.CANCELLED:
            continue

        scanned_in_box = sum(1 for t in b.tickets.all() if t.status == Ticket.Status.SCANNED)
        rz = (b.razmer or '').strip().upper()
        pcode = (b.pastal_code or b.pastal_number or 'NOMA\'LUM').strip()

        if scanned_in_box == 0:
            # Tikim Kutmoqda (0 stiker skanerlangan)
            waiting_boxes.append(b)
            if rz:
                waiting_by_size[rz] = waiting_by_size.get(rz, 0) + b.quantity
                waiting_boxes_by_size[rz] = waiting_boxes_by_size.get(rz, 0) + 1

            if pcode not in waiting_pastals_map:
                batch_num = None
                batch_created = None
                if b.cutting_batch_item and b.cutting_batch_item.batch:
                    batch_num = b.cutting_batch_item.batch.batch_number
                    batch_created = b.cutting_batch_item.batch.created_at
                waiting_pastals_map[pcode] = {
                    'pastal_code': pcode,
                    'boxes_count': 0,
                    'total_quantity': 0,
                    'sizes_qty': {},
                    'article_codes': set(),
                    'batch_number': batch_num,
                    'created_at': batch_created,
                    'box_numbers': [],
                    'boxes': [],
                }
            p = waiting_pastals_map[pcode]
            p['boxes_count'] += 1
            p['total_quantity'] += b.quantity
            p['boxes'].append(b)
            p['box_numbers'].append(b.box_number)
            if b.article:
                p['article_codes'].add(b.article.code)
            if rz:
                p['sizes_qty'][rz] = p['sizes_qty'].get(rz, 0) + b.quantity
        else:
            # Tikimga Kirgan
            entered_boxes.append(b)

    waiting_pastals = []
    for pcode, p in sorted(waiting_pastals_map.items(), key=lambda x: (x[1]['batch_number'] or 999, x[0])):
        p['sizes_display'] = ', '.join(f"{sz}: {qty}" for sz, qty in sorted(p['sizes_qty'].items()))
        p['article_display'] = ', '.join(sorted(p['article_codes'])) if p['article_codes'] else ''
        box_nums = sorted(p['box_numbers'])
        if len(box_nums) > 1:
            p['boxes_range_display'] = f"№{box_nums[0]} – №{box_nums[-1]}"
        elif box_nums:
            p['boxes_range_display'] = f"№{box_nums[0]}"
        else:
            p['boxes_range_display'] = "—"
        waiting_pastals.append(p)

    waiting_sewing_qty = sum(b.quantity for b in waiting_boxes)
    waiting_sewing_boxes = len(waiting_boxes)
    waiting_pastals_count = len(waiting_pastals)

    return {
        'waiting_pastals': waiting_pastals,
        'waiting_pastals_count': waiting_pastals_count,
        'waiting_sewing_qty': waiting_sewing_qty,
        'waiting_sewing_boxes': waiting_sewing_boxes,
        'waiting_boxes': waiting_boxes,
        'entered_boxes_count': len(entered_boxes),
        'entered_sewing_qty': sum(b.quantity for b in entered_boxes),
        'waiting_by_size': waiting_by_size,
        'waiting_boxes_by_size': waiting_boxes_by_size,
    }


def calculate_pipeline_balance(boxes, planned_qty=0, cut_qty=0, dazmol_ao_ids=None, pastals_info=None):
    """
    Rahbariyat savollariga to'liq javob beruvchi Ishlab chiqarish va Nazorat Balansi:
    1. Kesim nechta kesdi (cut_qty)
    2. Nechtasi tikim kutmoqda (waiting_sewing_qty va waiting_pastals_count)
    3. Nechtasi tikimda (entered_sewing_qty va in_sewing_pre_dazmol_qty)
    4. Nechtasi dazmoldan o'tdi (dazmol_qty)
    4. Dazmoldan o'tgan-u, lekin kontrolgacha yetib bormagan (unreached_control_qty)
    5. Controlda nechta quti va nechta ish kutyapti (waiting_control_qty, waiting_control_boxes)
    6. Controldan nechta quti o'tib yopildi va qanday holatda (controlled_boxes_count, 1-sort, 2-sort)
    7. Nechta quti va ish ta'mir jarayonida (in_repair_boxes, in_repair_qty)
    8. Necha marta ta'mirga borgan (1-marta va 2+ marta qayta ta'mir)
    9. Kontrol bergan aniq son va Farq/Balans (+ / - / 0)
    10. 10% Brak ogohlantirish indikatori (is_brak_warning, brak_rate_pct)
    """
    active_boxes = [b for b in boxes if getattr(b, 'status', None) != Box.Status.CANCELLED]
    total_boxed_qty = sum(b.quantity for b in active_boxes)
    boxes_count = len(active_boxes)

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
    waiting_control_ready_qty = 0
    waiting_control_ready_boxes = 0
    waiting_control_missing_ops_qty = 0
    waiting_control_missing_ops_boxes = 0

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

    for b in active_boxes:
        b_eval = evaluate_box_progress(b, dazmol_ao_ids)

        if b_eval['has_entered_sewing']:
            entered_sewing_qty += b.quantity
            entered_sewing_boxes += 1

            if b_eval['is_in_sewing_pre_dazmol']:
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
            if b_eval['is_waiting_control_ready']:
                waiting_control_ready_qty += b.quantity
                waiting_control_ready_boxes += 1
            else:
                waiting_control_missing_ops_qty += b.quantity
                waiting_control_missing_ops_boxes += 1

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

    # Real Patokdagi ish (Haqiqiy Tikuvdagi WIP):
    # Dazmolgacha bo'lgan tikuvdagi ishlar + OTKdan ta'mirga qaytgan ishlar
    real_wip_sewing_qty = in_sewing_pre_dazmol_qty + in_repair_qty
    real_wip_sewing_boxes = in_sewing_pre_dazmol_boxes + in_repair_boxes

    # Pastallar tahlili: Tikim Kutmoqda (0 stiker) vs Tikimga Kirgan
    if pastals_info is None:
        pastals_info = get_model_pastals_analysis(active_boxes)
    waiting_sewing_qty = pastals_info['waiting_sewing_qty']
    waiting_sewing_boxes = pastals_info['waiting_sewing_boxes']
    waiting_pastals_count = pastals_info['waiting_pastals_count']

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
        'waiting_sewing_qty': waiting_sewing_qty,
        'waiting_sewing_boxes': waiting_sewing_boxes,
        'waiting_pastals_count': waiting_pastals_count,
        'waiting_pastals': pastals_info['waiting_pastals'],
        'waiting_by_size': pastals_info['waiting_by_size'],
        'waiting_boxes_by_size': pastals_info['waiting_boxes_by_size'],
        'entered_sewing_qty': entered_sewing_qty,
        'entered_sewing_boxes': entered_sewing_boxes,
        'sewing_pct': sewing_pct,
        'in_sewing_pre_dazmol_qty': in_sewing_pre_dazmol_qty,
        'in_sewing_pre_dazmol_boxes': in_sewing_pre_dazmol_boxes,
        'real_wip_sewing_qty': real_wip_sewing_qty,
        'real_wip_sewing_boxes': real_wip_sewing_boxes,
        'dazmol_qty': dazmol_qty,
        'dazmol_boxes': dazmol_boxes,
        'dazmol_pct': dazmol_pct,
        'unreached_control_qty': unreached_control_qty,
        'unreached_control_boxes': unreached_control_boxes,
        'waiting_control_qty': waiting_control_qty,
        'waiting_control_boxes': waiting_control_boxes,
        'waiting_control_ready_qty': waiting_control_ready_qty,
        'waiting_control_ready_boxes': waiting_control_ready_boxes,
        'waiting_control_missing_ops_qty': waiting_control_missing_ops_qty,
        'waiting_control_missing_ops_boxes': waiting_control_missing_ops_boxes,
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
    ).exclude(status=Box.Status.CANCELLED).select_related(
        'order', 'article__model'
    ).prefetch_related('tickets', 'quality_inspection_logs')
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
                'active_repair_boxes': 0,
                'active_repair_units': 0,
                'repair_boxes': [],
            }
        ps = patok_stats[p_code]
        ps['active_repair_boxes'] = ps.get('active_repair_boxes', 0) + 1
        ps['active_repair_units'] = ps.get('active_repair_units', 0) + b.controlled_repair_qty

        logs = list(b.quality_inspection_logs.all())
        repair_logs = [l for l in logs if (l.repair_qty or 0) > 0]
        repair_cycles = len(repair_logs)
        latest_repair_log = repair_logs[0] if repair_logs else (logs[0] if logs else None)
        notes = (latest_repair_log.notes or "").strip() if latest_repair_log else ""

        model_name = ""
        if b.article and b.article.model:
            model_name = b.article.model.name
        elif b.article:
            model_name = b.article.name
        elif b.target_article and b.target_article.model:
            model_name = b.target_article.model.name
        elif b.target_article:
            model_name = b.target_article.name
        else:
            model_name = "Model"

        box_info = {
            'id': b.id,
            'box_number': b.box_number,
            'box_code': b.display_code,
            'order_number': b.order.order_number if b.order else "",
            'model_name': model_name,
            'article_code': b.article.code if b.article else (b.target_article.code if b.target_article else ""),
            'razmer': b.razmer or "—",
            'pastal_code': b.pastal_code or "—",
            'quantity': b.quantity,
            'repair_qty': b.controlled_repair_qty,
            'repair_cycles': repair_cycles,
            'is_re_repair': (repair_cycles >= 2),
            'notes': notes,
        }
        ps.setdefault('repair_boxes', []).append(box_info)

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
            'patok_login': get_patok_login(data['screen_number']),
            'boxes_count': boxes_count,
            'total_units': total_eval,
            'first_sort_qty': data['first_sort_qty'],
            'second_sort_qty': data['second_sort_qty'],
            'defect_qty': data['defect_qty'],
            'repair_qty': data['repair_qty'],
            'active_repair_boxes': data.get('active_repair_boxes', 0),
            'active_repair_units': data.get('active_repair_units', 0),
            'repair_boxes': data.get('repair_boxes', []),
            'brak_rate_pct': brak_rate_pct,
            'is_warning': is_warning,
        })

    report_list.sort(key=lambda x: (x['is_warning'], x['brak_rate_pct'], x['total_units']), reverse=True)

    return {
        'report_list': report_list,
        'has_any_warning': has_any_warning,
        'time_filter': time_filter,
    }


def natural_size_sort_key(s):
    s_clean = str(s).strip().upper()
    try:
        return (0, float(s_clean), s_clean)
    except ValueError:
        order_map = {
            'XXS': 1, '2XS': 1, 'XS': 2, 'S': 3, 'M': 4, 'L': 5,
            'XL': 6, '2XL': 7, 'XXL': 7, '3XL': 8, 'XXXL': 8,
            '4XL': 9, '5XL': 10, '6XL': 11, 'STANDART': 50, 'UNIVERSAL': 51
        }
        return (1, order_map.get(s_clean, 99), s_clean)


@manager_or_superadmin_required
def sewing_statistics_daily_view(request):
    """
    KUNLIK TIKIM STATISTIKASI (DESKTOP EXECUTIVE DASHBOARD):
    - Har bir patok (K1..K13, U1..U27)
    - Zakaz(lar) va Modellar yaxlitlangan ko'rinishi
    - Tikayotgan pastallar (shu kunniki) va pastal statusi (hamma qutilar yopilganda pastal yopildi)
    - Kunlik dazmol (shu kunniki)
    - Shu pastallardan bugun kontroldan o'tgani (1-sort / 2-sort)
    - Kontrolda kutayotgan quti va donalar
    - Ta'mir / Brak ko'rsatkichlari
    - BATAFSIL BO'LIMI:
      * Pastal bo'yicha to'liq pipeline balansi (tikimda, dazmoldan o'tdi, kontrolda kutmoqda, o'tdi, yopildi)
      * RAZMERLAR KESIMIDA TA'MIR VA MATO BALANSI JADVALI:
        (Tikimga kirdi, dazmoldan o'tdi, kontrolda kutmoqda, 1-sort, 2-sort, ta'mir, brak, qayta tikish / mato ehtiyoji)
      * Bajarilgan operatsiyalar ro'yxati (ism-familiyasiz)
    """
    from datetime import datetime, time
    date_str = request.GET.get('date', '').strip()
    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = None
    if not selected_date:
        selected_date = timezone.localdate()

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    is_today = (selected_date == today)

    # Foydalanuvchi talabi: Bugun real-time, kecha va undan oldingi kunlar tungi 12:00 (23:59:59) dagi holat
    day_start = timezone.make_aware(datetime.combine(selected_date, time.min), tz)
    if is_today:
        day_end = timezone.now()  # Real-time
    else:
        day_end = timezone.make_aware(datetime.combine(selected_date, time(23, 59, 59, 999999)), tz)

    patok_filter = request.GET.get('patok', '').strip().upper()
    is_uychi_admin = (
        request.user.is_authenticated and
        getattr(request.user, 'role', None) == User.Role.BRANCH_ADMIN and
        getattr(request.user, 'branch', None) == 'UYCHI'
    )
    search_q = request.GET.get('q', '').strip()
    show_all_patoks = request.GET.get('all', '0') == '1'

    # 1. Shu kunda skanerlangan barcha biletlar (Index-friendly datetime range)
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        screen_number__isnull=False
    ).select_related(
        'box__order__customer',
        'box__article__model',
        'box__cutting_batch_item__batch',
        'article_operation__operation',
        'article_operation__article'
    ).order_by('screen_number', 'article_operation__sequence', 'id')

    tickets_list = list(tickets_qs)

    # Screen / Patok bo'yicha biletlarni guruhlash
    tickets_by_screen = {}
    today_box_ids = set()
    for t in tickets_list:
        tickets_by_screen.setdefault(t.screen_number, []).append(t)
        today_box_ids.add(t.box_id)

    # Shu kuni ishlangan qutilarni keshlaymiz
    today_boxes_dict = {
        b.id: b for b in Box.objects.filter(id__in=today_box_ids).select_related(
            'order__customer', 'article__model', 'cutting_batch_item__batch'
        )
    }

    # 1. Patokka rasman biriktirilgan (ACCEPTED) partiyalar
    assigned_batches_qs = CuttingBatch.objects.filter(
        assigned_screen__isnull=False,
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
    ).select_related('order_item__order', 'order_item__article__model')

    screen_assigned_pastals_map = {}
    all_assigned_pastal_codes = set()
    for batch in assigned_batches_qs:
        pcode = (batch.pastal_code or f"K{batch.batch_number}").strip()
        if pcode and batch.assigned_screen:
            screen_assigned_pastals_map.setdefault(batch.assigned_screen, set()).add(pcode)
            all_assigned_pastal_codes.add(pcode)

    # Shuningdek, Box.assigned_screen bo'yicha biriktirilgan pastallar
    for b_row in Box.objects.filter(assigned_screen__isnull=False).exclude(status=Box.Status.CANCELLED).values('assigned_screen', 'pastal_number', 'cutting_batch_item__batch__pastal_code'):
        sn_b = b_row['assigned_screen']
        pcode_b = (b_row['pastal_number'] or b_row['cutting_batch_item__batch__pastal_code'] or '').strip()
        if pcode_b and sn_b:
            screen_assigned_pastals_map.setdefault(sn_b, set()).add(pcode_b)
            all_assigned_pastal_codes.add(pcode_b)

    # 2. Bugun butun fabrika bo'yicha har bir pastaldan qancha DAZMOL chiqqani (qayerdan chiqsa ham!)
    today_dazmol_by_pastal = {}
    today_dazmol_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end)
    ).filter(
        Q(article_operation__operation__name__icontains='DAZMOL') |
        Q(article_operation__operation__code__icontains='DAZMOL')
    ).exclude(
        Q(article_operation__operation__name__icontains='TASMA') |
        Q(article_operation__operation__code__icontains='TASMA') |
        Q(article_operation__operation__name__icontains="CHO'NTAK") |
        Q(article_operation__operation__code__icontains="CHO'NTAK") |
        Q(article_operation__operation__name__icontains='CHONTAK') |
        Q(article_operation__operation__code__icontains='CHONTAK')
    ).values('box__pastal_number', 'box__cutting_batch_item__batch__pastal_code').annotate(s=Sum('quantity'))

    for row in today_dazmol_tickets:
        code = (row['box__pastal_number'] or row['box__cutting_batch_item__batch__pastal_code'] or '').strip()
        if code:
            today_dazmol_by_pastal[code] = today_dazmol_by_pastal.get(code, 0) + int(row['s'] or 0)

    # 3. Shu kuni tekshirilgan barcha Sifat Nazorati (OTK) loglari
    inspection_logs = list(
        BoxQualityInspectionLog.objects.filter(
            created_at__range=(day_start, day_end)
        ).select_related(
            'box__order__customer',
            'box__article__model',
            'box__cutting_batch_item__batch'
        )
    )
    otk_box_ids = {l.box_id for l in inspection_logs if l.box_id}
    otk_box_screens_map = {}
    if otk_box_ids:
        otk_ticket_screens = Ticket.objects.filter(
            box_id__in=otk_box_ids,
            screen_number__isnull=False
        ).values('box_id', 'screen_number').annotate(c=Count('id')).order_by('box_id', '-c')
        for row in otk_ticket_screens:
            bid = row['box_id']
            if bid not in otk_box_screens_map:
                otk_box_screens_map[bid] = row['screen_number']

    boxes_for_otk = {b.id: b for b in Box.objects.filter(id__in=otk_box_ids).select_related(
        'order__customer', 'article__model', 'cutting_batch_item__batch'
    )}

    logs_by_box = defaultdict(list)
    patok_otk_summary = defaultdict(lambda: {
        'controlled_qty': 0,
        'total_inspected_qty': 0,
        'first_sort_qty': 0,
        'second_sort_qty': 0,
        'repair_qty': 0,
        'defect_qty': 0,
        'boxes': [],
        'boxes_set': set(),
    })

    otk_pastal_codes = set()
    for l in inspection_logs:
        if l.box_id:
            logs_by_box[l.box_id].append(l)

        b = boxes_for_otk.get(l.box_id) or (today_boxes_dict.get(l.box_id))
        if b:
            pcode = (b.pastal_code or '').strip()
            if pcode:
                otk_pastal_codes.add(pcode)

        b_sn = otk_box_screens_map.get(l.box_id) if l.box_id else None
        if not b_sn and b and b.assigned_screen:
            b_sn = b.assigned_screen
        if not b_sn and b and b.cutting_batch_item and b.cutting_batch_item.batch and b.cutting_batch_item.batch.assigned_screen:
            b_sn = b.cutting_batch_item.batch.assigned_screen
        if not b_sn and l.inspector:
            b_sn = parse_patok_number(l.inspector.username)
        if not b_sn and l.notes and '[Terminal:' in l.notes:
            import re
            m = re.search(r'\[Terminal:\s*([^\]]+)\]', l.notes)
            if m:
                b_sn = parse_patok_number(m.group(1))

        if b_sn:
            pos = patok_otk_summary[b_sn]
            eff_2nd = l.second_sort_qty or (l.defect_qty if l.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)
            eff_def = l.defect_qty if l.action_type != BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0

            pos['first_sort_qty'] += l.first_sort_qty
            pos['second_sort_qty'] += eff_2nd
            pos['controlled_qty'] += (l.first_sort_qty + eff_2nd)
            pos['repair_qty'] += l.repair_qty
            pos['defect_qty'] += eff_def
            pos['total_inspected_qty'] += (l.inspected_qty or (l.first_sort_qty + eff_2nd + l.repair_qty))
            if l.box_id:
                pos['boxes_set'].add(l.box_id)
            if b and b not in pos['boxes']:
                pos['boxes'].append(b)

    # 4. Bugungi, OTK o'tgan va biriktirilgan barcha tegishli pastallar qutilarini olish
    active_pastal_codes = {b.pastal_code.strip() for b in today_boxes_dict.values() if b.pastal_code and b.pastal_code.strip()}
    all_relevant_pastal_codes = set(active_pastal_codes) | set(all_assigned_pastal_codes) | otk_pastal_codes

    all_pastal_boxes = list(
        Box.objects.filter(
            Q(pastal_number__in=all_relevant_pastal_codes) |
            Q(cutting_batch_item__batch__pastal_code__in=all_relevant_pastal_codes)
        ).exclude(status=Box.Status.CANCELLED).distinct().select_related(
            'order__customer', 'article__model', 'cutting_batch_item__batch'
        )
    )
    boxes_by_pastal = {}
    all_pbox_ids = []
    for b in all_pastal_boxes:
        pcode = (b.pastal_code or "").strip()
        if pcode:
            boxes_by_pastal.setdefault(pcode, []).append(b)
            all_pbox_ids.append(b.id)

    # Dazmoldan o'tgan qutilar ID to'plami (faqat yakuniy dazmol)
    dazmol_box_ids_set = set(
        Ticket.objects.filter(
            box_id__in=all_pbox_ids,
            status=Ticket.Status.SCANNED
        ).filter(
            Q(article_operation__operation__name__icontains='DAZMOL') |
            Q(article_operation__operation__code__icontains='DAZMOL')
        ).exclude(
            Q(article_operation__operation__name__icontains='TASMA') |
            Q(article_operation__operation__code__icontains='TASMA') |
            Q(article_operation__operation__name__icontains="CHO'NTAK") |
            Q(article_operation__operation__code__icontains="CHO'NTAK") |
            Q(article_operation__operation__name__icontains='CHONTAK') |
            Q(article_operation__operation__code__icontains='CHONTAK')
        ).values_list('box_id', flat=True).distinct()
    )

    active_screens = sorted(
        set(tickets_by_screen.keys()) |
        {sn for sn, pset in screen_assigned_pastals_map.items() if pset} |
        set(patok_otk_summary.keys())
    )
    # Screens moduli uchun oldindan yuklash (Preloading - 40 ta ekranni 1 tagacha so'rovga tushiradi)
    all_today_scans = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        worker__isnull=False
    ).values('worker_id', 'screen_number', 'scanned_at').order_by('scanned_at')

    worker_latest_screen = {}
    active_screens_scanned = set()
    for scan in all_today_scans:
        worker_latest_screen[scan['worker_id']] = scan['screen_number']
        if scan['screen_number']:
            active_screens_scanned.add(scan['screen_number'])

    dazmol_op_ids_cached = list(
        Operation.objects.filter(name__icontains='DAZMOL')
        .exclude(name__icontains='TASMA')
        .exclude(name__icontains="CHO'NTAK")
        .exclude(name__icontains="CHONTAK")
        .values_list('id', flat=True)
    )

    screens_preloaded = {
        'worker_latest_screen': worker_latest_screen,
        'dazmol_op_ids': dazmol_op_ids_cached,
        'active_screens': active_screens_scanned,
    }

    screens_to_process = list(range(1, 41)) if show_all_patoks else active_screens
    patoks_data = []

    # KPI hisoblagichlari
    total_active_patoks = len(active_screens)
    total_daily_dazmol_qty = 0
    total_daily_controlled_qty = 0
    total_daily_first_sort = 0
    total_daily_second_sort = 0
    total_daily_waiting_qty = 0
    total_daily_repair_qty = 0
    total_daily_scanned_ops_qty = 0
    all_worked_orders = set()
    all_worked_pastals = set()

    for sn in screens_to_process:
        if is_uychi_admin and sn <= 13:
            continue

        p_code = get_patok_code(sn)
        p_name = get_patok_name(sn)

        if patok_filter and (patok_filter != p_code and patok_filter not in p_name.upper()):
            continue

        t_list = tickets_by_screen.get(sn, [])

        # Ushbu patokning bugungi qutilari
        sn_box_ids = {t.box_id for t in t_list}
        sn_today_boxes = [today_boxes_dict[bid] for bid in sn_box_ids if bid in today_boxes_dict]

        # Ushbu patokda haqiqatda tikilgan (Dazmol bo'lmagan) biletlar qutilari
        # Bu dazmolchilar boshqa patok biletlarini skaner qilganda patokka soxta pastallar qo'shilib ketishini oldini oladi
        sn_sewing_boxes = [
            today_boxes_dict[t.box_id] for t in t_list
            if t.box_id in today_boxes_dict and not (
                t.article_operation and t.article_operation.operation and
                is_main_dazmol_operation(t.article_operation.operation.name, t.article_operation.operation.code)
            )
        ]
        # Agar patokda faqat dazmol skanerlangan bo'lsa (tikuv bo'lmasa), sn_today_boxes dan foydalanamiz
        active_sewing_boxes = sn_sewing_boxes if sn_sewing_boxes else sn_today_boxes

        # Ushbu patokka rasman biriktirilgan pastallar (Asosiy)
        assigned_pastal_codes = screen_assigned_pastals_map.get(sn, set())

        # Bugun ushbu patokda skanerlangan pastallar (Haqiqiy tikuv biletlari bo'yicha)
        sn_scanned_pastal_codes = set()
        for b in active_sewing_boxes:
            pcode = (b.pastal_code or "").strip()
            if pcode:
                sn_scanned_pastal_codes.add(pcode)
        for b in patok_otk_summary.get(sn, {}).get('boxes', []):
            pcode = (b.pastal_code or "").strip()
            if pcode:
                sn_scanned_pastal_codes.add(pcode)

        # Asosiy pastallarni filtrlash: agar butunlay yopilgan bo'lsa va bugun hech qanday harakat bo'lmagan bo'lsa, chiqarmaymiz
        filtered_assigned_codes = set()
        for pcode in assigned_pastal_codes:
            p_boxes = boxes_by_pastal.get(pcode, [])
            total_p_boxes = len(p_boxes)
            closed_p_boxes = len([b for b in p_boxes if b.is_controlled])
            repair_qty = sum(b.controlled_repair_qty for b in p_boxes)
            is_pastal_closed = (total_p_boxes > 0 and closed_p_boxes == total_p_boxes and repair_qty == 0)
            has_activity_today = (
                (pcode in sn_scanned_pastal_codes) or
                (today_dazmol_by_pastal.get(pcode, 0) > 0) or
                any(b.id in logs_by_box for b in p_boxes)
            )
            if not is_pastal_closed or has_activity_today or total_p_boxes == 0:
                filtered_assigned_codes.add(pcode)

        sn_primary_pastal_codes = sorted(list(filtered_assigned_codes))
        sn_extra_pastal_codes = sorted([c for c in sn_scanned_pastal_codes if c not in filtered_assigned_codes])
        sn_pastal_codes = sn_primary_pastal_codes + sn_extra_pastal_codes

        # Zakazlar va Modellarni yaxlitlash (bugun skanerlangan + biriktirilgan qutilar)
        orders_dict = {}
        models_dict = {}
        sn_norms = []

        all_sn_candidate_boxes = list(active_sewing_boxes)
        for b in patok_otk_summary.get(sn, {}).get('boxes', []):
            if b not in all_sn_candidate_boxes:
                all_sn_candidate_boxes.append(b)
        for pcode in sn_primary_pastal_codes:
            for b in boxes_by_pastal.get(pcode, []):
                if b not in all_sn_candidate_boxes:
                    all_sn_candidate_boxes.append(b)

        for b in all_sn_candidate_boxes:
            if b.order:
                all_worked_orders.add(b.order.order_number)
                orders_dict[b.order_id] = {
                    'order_id': b.order_id,
                    'order_number': b.order.order_number,
                    'customer_name': (b.order.customer.name if b.order.customer else (b.order.client_name or "")) or "",
                }
            art = b.target_article or b.article
            if art:
                m_name = (art.name or (art.model.name if art.model else "—"))
                art_norm = art.daily_norm or (art.model.daily_norm if art.model else 0) or 1200
                if art_norm > 0 and art_norm not in sn_norms:
                    sn_norms.append(art_norm)

                models_dict[art.id] = {
                    'article_id': art.id,
                    'article_code': art.code or "",
                    'model_name': m_name,
                    'order_number': b.order.order_number if b.order else "—",
                    'daily_norm': art_norm,
                }
            pcode = (b.pastal_code or "").strip()
            if pcode:
                all_worked_pastals.add(pcode)

        # Patok kunlik normasi (modellar normasi asosida)
        patok_daily_norm = int(sum(sn_norms) / len(sn_norms)) if sn_norms else 1200

        # Kunlik dazmolni aniqlash
        sn_dazmol_qty = 0
        sn_dazmol_box_ids = set()
        operations_map = {}

        for t in t_list:
            ao = t.article_operation
            op_name = ao.operation.name if ao and ao.operation else ""
            op_code = ao.operation.code if ao and ao.operation else ""
            is_dazmol = is_main_dazmol_operation(op_name, op_code)

            if is_dazmol:
                sn_dazmol_qty += t.quantity
                sn_dazmol_box_ids.add(t.box_id)

            if ao and ao.operation:
                op_id = ao.id
                if op_id not in operations_map:
                    box = today_boxes_dict.get(t.box_id)
                    operations_map[op_id] = {
                        'operation_id': op_id,
                        'sequence': ao.sequence,
                        'operation_name': ao.operation.name,
                        'operation_code': ao.operation.code or "",
                        'order_number': box.order.order_number if box and box.order else "—",
                        'model_name': (box.target_article.name if box and box.target_article else (box.article.name if box and box.article else "—")),
                        'is_dazmol': is_dazmol,
                        'total_quantity': 0,
                        'tickets_count': 0,
                    }
                operations_map[op_id]['total_quantity'] += t.quantity
                operations_map[op_id]['tickets_count'] += 1

        # Kunlik OTK, Kutmoqda, Ta'mir hisoblash
        pos = patok_otk_summary.get(sn, {
            'controlled_qty': 0,
            'total_inspected_qty': 0,
            'first_sort_qty': 0,
            'second_sort_qty': 0,
            'repair_qty': 0,
            'defect_qty': 0,
            'boxes_set': set(),
        })
        sn_first_sort_qty = pos['first_sort_qty']
        sn_second_sort_qty = pos['second_sort_qty']
        sn_repair_qty = pos['repair_qty']
        sn_defect_qty = pos['defect_qty']
        # Jami OTK ko'rib chiqqan ishlar soni (1-sort + 2-sort + ta'mir):
        # Foydalanuvchi talabi: /otk/ dagi A'lo + 2-sort + Ta'mir ga va planshetdagi hisobga 100% teng bo'ladi
        sn_total_inspected = pos.get('total_inspected_qty', 0) or (sn_first_sort_qty + sn_second_sort_qty + sn_repair_qty)
        sn_controlled_qty = sn_total_inspected
        sn_boxes_count = len(pos.get('boxes_set', set()))
        sn_waiting_qty = 0

        for bid in sn_box_ids:
            box = today_boxes_dict.get(bid)
            if not box:
                continue
            if not box.is_controlled and box.controlled_repair_qty == 0:
                sn_waiting_qty += box.quantity

        patok_scanned_sum = sum(t.quantity for t in t_list)

        # Normaga qarshi bajarilgan dona va foiz hisoblash
        completed_for_norm = sn_dazmol_qty if sn_dazmol_qty > 0 else (sn_total_inspected if sn_total_inspected > 0 else int(patok_scanned_sum / max(1, len(operations_map))))
        if patok_daily_norm > 0:
            norm_percentage = round((Decimal(completed_for_norm) / Decimal(patok_daily_norm)) * Decimal('100.0'), 1)
        else:
            norm_percentage = Decimal('0.0')

        is_norm_completed = (completed_for_norm >= patok_daily_norm)
        norm_diff = completed_for_norm - patok_daily_norm

        # Screens moduli orqali hisoblangan aniqlik, davomat, foizlar, patok quvvati va tikuvchilar
        screen_data = get_screen_data(sn, selected_date, preloaded=screens_preloaded)
        sn_aniqlik = screen_data.get('aniqlik', 0)
        sn_davomat = screen_data.get('davomat', 0)
        sn_davomat_foiz = screen_data.get('foiz', 0.0)
        sn_avg_kpi = screen_data.get('avg_screen_kpi', 0.0)
        sn_patok_kpi = screen_data.get('patok_kpi', 0.0)
        sn_patok_quvvati = screen_data.get('patok_quvvati', 0.0)
        sn_quvvat_color = screen_data.get('quvvat_color', 'emerald')
        sn_quvvat_color_class = screen_data.get('quvvat_color_class', '')
        sn_quvvat_badge_bg = screen_data.get('quvvat_badge_bg', '')
        sn_workers = screen_data.get('workers', [])

        # DailyPatokProgress ga saqlash / muhrlash (Bugun real-time, kecha va undan oldingi kunlar muzlatilgan snapshot)
        if len(t_list) > 0 or sn_total_inspected > 0 or sn_dazmol_qty > 0 or len(sn_primary_pastal_codes) > 0:
            DailyPatokProgress.objects.update_or_create(
                screen_number=sn,
                date=selected_date,
                defaults={
                    'patok_code': p_code,
                    'daily_norm': patok_daily_norm,
                    'completed_units': completed_for_norm,
                    'completion_percentage': norm_percentage,
                    'dazmol_qty': sn_dazmol_qty,
                    'scanned_ops_qty': patok_scanned_sum,
                    'tickets_count': len(t_list),
                    'controlled_qty': sn_total_inspected,
                    'waiting_qty': sn_waiting_qty,
                    'repair_qty': sn_repair_qty,
                    'is_completed': is_norm_completed,
                    'aniqlik': sn_aniqlik,
                    'davomat': sn_davomat,
                    'davomat_foiz': sn_davomat_foiz,
                    'avg_kpi': sn_avg_kpi,
                    'patok_kpi': sn_patok_kpi,
                }
            )

        # PASTALLAR VA RAZMERLAR KESIMIDA CHUQUR HISOB-KITOB (4-BOSQICHLI QUVUR BALANSI)
        pastals_detail_list = []
        all_patok_pastals_closed = (len(sn_pastal_codes) > 0)

        for pcode in sn_pastal_codes:
            is_primary_pastal = (pcode in filtered_assigned_codes)
            today_factory_dazmol = today_dazmol_by_pastal.get(pcode, 0)

            p_boxes = boxes_by_pastal.get(pcode, [])
            total_p_boxes = len(p_boxes)
            p_total_qty = sum(b.quantity for b in p_boxes)

            # 1. Hali tikimda (Pre-dazmol)
            in_sewing_boxes = [b for b in p_boxes if not (b.id in dazmol_box_ids_set or b.is_controlled)]
            in_sewing_boxes_count = len(in_sewing_boxes)
            in_sewing_qty = sum(b.quantity for b in in_sewing_boxes)

            # 2. Dazmoldan o'tgan (Total passed Dazmol)
            dazmol_boxes = [b for b in p_boxes if (b.id in dazmol_box_ids_set or b.is_controlled)]
            dazmol_boxes_count = len(dazmol_boxes)
            dazmol_qty = sum(b.quantity for b in dazmol_boxes)

            # 3. Oraliq: Dazmoldan o'tgan, lekin hali kontroldan o'tmagan (WIP: Waiting OTK)
            waiting_otk_boxes = [b for b in p_boxes if (b.id in dazmol_box_ids_set or b.is_controlled) and (not b.is_controlled) and (b.controlled_repair_qty == 0)]
            waiting_otk_boxes_count = len(waiting_otk_boxes)
            waiting_otk_qty = sum(b.quantity for b in waiting_otk_boxes)

            # 4. Ta'mirda (In repair)
            repair_boxes = [b for b in p_boxes if b.controlled_repair_qty > 0]
            repair_boxes_count = len(repair_boxes)
            repair_qty = sum(b.controlled_repair_qty for b in p_boxes)

            # 5. Kontroldan o'tgan (OTK closed)
            closed_p_boxes = len([b for b in p_boxes if b.is_controlled])
            controlled_p_boxes = [b for b in p_boxes if b.is_controlled]
            controlled_qty = sum(b.quantity for b in controlled_p_boxes)

            p_first_sort = sum(b.controlled_first_sort_qty for b in p_boxes)
            p_second_sort = sum(b.controlled_second_sort_qty for b in p_boxes)
            p_defect = sum(b.controlled_defect_qty for b in p_boxes)

            # Qolgan ish
            remaining_boxes = max(0, total_p_boxes - closed_p_boxes)
            remaining_qty = max(0, p_total_qty - controlled_qty)

            # Foizlar
            completion_pct = round((controlled_qty / p_total_qty * 100), 1) if p_total_qty > 0 else 0.0
            box_completion_pct = round((closed_p_boxes / total_p_boxes * 100), 1) if total_p_boxes > 0 else 0.0
            dazmol_pct = round((dazmol_qty / p_total_qty * 100), 1) if p_total_qty > 0 else 0.0

            # Yopilish holati: barcha qutilar dazmoldan va kontroldan to'liq o'tgan hamda ta'mir 0
            is_pastal_closed = (total_p_boxes > 0 and closed_p_boxes == total_p_boxes and repair_qty == 0)
            if not is_pastal_closed:
                all_patok_pastals_closed = False

            first_b = p_boxes[0] if p_boxes else None
            p_order_num = first_b.order.order_number if first_b and first_b.order else "—"
            p_model_name = (first_b.target_article.name if first_b and first_b.target_article else (first_b.article.name if first_b and first_b.article else "—"))

            # Bugungi kunlik dazmol va OTK shu pastal uchun (faqat yakuniy dazmol)
            p_today_dazmol = sum(
                t.quantity for t in t_list
                if t.box and (t.box.pastal_code or "").strip() == pcode
                and t.article_operation and t.article_operation.operation
                and is_main_dazmol_operation(t.article_operation.operation.name, t.article_operation.operation.code)
            )
            p_today_dazmol_boxes = len({
                t.box_id for t in t_list
                if t.box and (t.box.pastal_code or "").strip() == pcode
                and t.article_operation and t.article_operation.operation
                and is_main_dazmol_operation(t.article_operation.operation.name, t.article_operation.operation.code)
            })

            # Bugungi kunlik OTK shu pastal uchun
            p_today_logs = [l for bid in [b.id for b in p_boxes] for l in logs_by_box.get(bid, [])]
            if p_today_logs:
                p_today_first_sort = sum(l.first_sort_qty for l in p_today_logs)
                p_today_second_sort = sum(
                    l.second_sort_qty or (l.defect_qty if l.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)
                    for l in p_today_logs
                )
                p_today_repair = sum(l.repair_qty for l in p_today_logs)
                p_today_defect = sum(
                    l.defect_qty if l.action_type != BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0
                    for l in p_today_logs
                )
                p_today_controlled = p_today_first_sort + p_today_second_sort
            else:
                today_controlled_boxes = [b for b in p_boxes if b.is_controlled and b.controlled_at and timezone.localdate(b.controlled_at) == selected_date]
                p_today_first_sort = sum(b.controlled_first_sort_qty for b in today_controlled_boxes)
                p_today_second_sort = sum(b.controlled_second_sort_qty for b in today_controlled_boxes)
                p_today_repair = sum(b.controlled_repair_qty for b in today_controlled_boxes)
                p_today_defect = sum(b.controlled_defect_qty for b in today_controlled_boxes)
                p_today_controlled = p_today_first_sort + p_today_second_sort

            # RAZMERLAR KESIMIDA GURUHLASH (Mato buyurtmasi uchun!)
            sizes_dict = {}
            for b in p_boxes:
                sz = b.razmer or "Standart"
                if sz not in sizes_dict:
                    sizes_dict[sz] = {
                        'size': sz,
                        'boxes_count': 0,
                        'closed_boxes': 0,
                        'total_qty': 0,
                        'in_sewing_boxes': 0,
                        'in_sewing_qty': 0,
                        'dazmol_boxes': 0,
                        'dazmol_qty': 0,
                        'waiting_boxes': 0,
                        'waiting_qty': 0,
                        'repair_boxes': 0,
                        'repair_qty': 0,
                        'controlled_qty': 0,
                        'first_sort': 0,
                        'second_sort': 0,
                        'defect': 0,
                    }
                sd = sizes_dict[sz]
                sd['boxes_count'] += 1
                sd['total_qty'] += b.quantity

                has_dazmol = (b.id in dazmol_box_ids_set or b.is_controlled)
                if not has_dazmol:
                    sd['in_sewing_boxes'] += 1
                    sd['in_sewing_qty'] += b.quantity
                else:
                    sd['dazmol_boxes'] += 1
                    sd['dazmol_qty'] += b.quantity

                if has_dazmol and not b.is_controlled and b.controlled_repair_qty == 0:
                    sd['waiting_boxes'] += 1
                    sd['waiting_qty'] += b.quantity

                if b.controlled_repair_qty > 0:
                    sd['repair_boxes'] += 1
                    sd['repair_qty'] += b.controlled_repair_qty

                if b.is_controlled:
                    sd['closed_boxes'] += 1
                    sd['controlled_qty'] += b.quantity

                sd['first_sort'] += b.controlled_first_sort_qty
                sd['second_sort'] += b.controlled_second_sort_qty
                sd['defect'] += b.controlled_defect_qty

            sizes_breakdown = []
            for sz in sorted(sizes_dict.keys(), key=natural_size_sort_key):
                sd = sizes_dict[sz]
                deficit = sd['repair_qty'] + sd['defect'] + sd['second_sort']
                is_sz_closed = (sd['closed_boxes'] == sd['boxes_count'] and sd['boxes_count'] > 0 and sd['repair_qty'] == 0)
                sz_comp_pct = round((sd['controlled_qty'] / sd['total_qty'] * 100), 1) if sd['total_qty'] > 0 else 0.0
                sizes_breakdown.append({
                    'size': sd['size'],
                    'boxes_count': sd['boxes_count'],
                    'closed_boxes': sd['closed_boxes'],
                    'remaining_boxes': max(0, sd['boxes_count'] - sd['closed_boxes']),
                    'is_closed': is_sz_closed,
                    'completion_pct': sz_comp_pct,
                    'total_qty': sd['total_qty'],
                    'in_sewing_boxes': sd['in_sewing_boxes'],
                    'in_sewing_qty': sd['in_sewing_qty'],
                    'dazmol_boxes': sd['dazmol_boxes'],
                    'dazmol_qty': sd['dazmol_qty'],
                    'waiting_boxes': sd['waiting_boxes'],
                    'waiting_qty': sd['waiting_qty'],
                    'repair_boxes': sd['repair_boxes'],
                    'repair': sd['repair_qty'],
                    'controlled_qty': sd['controlled_qty'],
                    'first_sort': sd['first_sort'],
                    'second_sort': sd['second_sort'],
                    'defect': sd['defect'],
                    'deficit_qty': deficit,
                })

            pastals_detail_list.append({
                'is_primary': is_primary_pastal,
                'pastal_code': pcode,
                'order_number': p_order_num,
                'model_name': p_model_name,
                'total_boxes': total_p_boxes,
                'closed_boxes': closed_p_boxes,
                'remaining_boxes': remaining_boxes,
                'remaining_qty': remaining_qty,
                'is_closed': is_pastal_closed,
                'completion_pct': completion_pct,
                'box_completion_pct': box_completion_pct,
                'dazmol_pct': dazmol_pct,
                'total_qty': p_total_qty,
                'in_sewing_boxes': in_sewing_boxes_count,
                'in_sewing_qty': in_sewing_qty,
                'dazmol_boxes': dazmol_boxes_count,
                'dazmol_qty': dazmol_qty,
                'today_factory_dazmol_qty': today_factory_dazmol,
                'today_dazmol_qty': p_today_dazmol,
                'today_dazmol_boxes': p_today_dazmol_boxes,
                'waiting_otk_boxes': waiting_otk_boxes_count,
                'waiting_otk_qty': waiting_otk_qty,
                'waiting_qty': waiting_otk_qty,
                'repair_boxes': repair_boxes_count,
                'repair_qty': repair_qty,
                'controlled_boxes': closed_p_boxes,
                'controlled_qty': controlled_qty,
                'first_sort_qty': p_first_sort,
                'second_sort_qty': p_second_sort,
                'defect_qty': p_defect,
                'deficit_qty': (repair_qty + p_defect + p_second_sort),
                'today_controlled_qty': p_today_controlled,
                'today_first_sort_qty': p_today_first_sort,
                'today_second_sort_qty': p_today_second_sort,
                'today_repair_qty': p_today_repair,
                'today_defect_qty': p_today_defect,
                'sizes': sizes_breakdown,
            })

        # Qidiruv filtri
        if search_q:
            q_u = search_q.upper()
            matches_search = (
                (q_u in p_name.upper()) or (q_u in p_code.upper())
                or any(q_u in o['order_number'].upper() for o in orders_dict.values())
                or any(q_u in m['model_name'].upper() for m in models_dict.values())
                or any(q_u in p['pastal_code'].upper() for p in pastals_detail_list)
            )
            if not matches_search:
                continue

        primary_pastals = [p for p in pastals_detail_list if p['is_primary']]
        extra_pastals = [p for p in pastals_detail_list if not p['is_primary']]

        # Dual Dazmol nazorati:
        # 1. Asosiy pastallar bo'yicha bugun butun fabrikada chiqqan dazmol (450/A1, 560/A2)
        pastal_dazmol_items = [
            {
                'code': p['pastal_code'],
                'qty': p['today_factory_dazmol_qty'],
            }
            for p in primary_pastals
        ]
        if not pastal_dazmol_items and extra_pastals:
            pastal_dazmol_items = [
                {
                    'code': p['pastal_code'],
                    'qty': p['today_factory_dazmol_qty'],
                }
                for p in extra_pastals if p['today_factory_dazmol_qty'] > 0
            ]

        # Foydalanuvchi talabi: Asosiy pastallar bo'yicha qilingan dazmollarning umumiy yig'indisi (qo'lda qo'shib o'tirmaslik uchun)
        primary_dazmol_total = sum(item['qty'] for item in pastal_dazmol_items)

        # Patok faolligi: stikerlar bo'lsa yoki biriktirilgan asosiy pastali bo'lsa yoki OTK harakati bo'lsa
        is_active = (len(t_list) > 0) or (len(primary_pastals) > 0) or (sn_controlled_qty > 0 or sn_repair_qty > 0)

        total_daily_dazmol_qty += sn_dazmol_qty
        total_daily_controlled_qty += sn_controlled_qty
        total_daily_first_sort += sn_first_sort_qty
        total_daily_second_sort += sn_second_sort_qty
        total_daily_waiting_qty += sn_waiting_qty
        total_daily_repair_qty += sn_repair_qty
        total_daily_scanned_ops_qty += patok_scanned_sum

        sorted_ops = sorted(
            operations_map.values(),
            key=lambda o: (o['order_number'], o['sequence'], o['operation_name'])
        )

        patok_total_qty = sum(p['total_qty'] for p in pastals_detail_list)
        patok_controlled_qty = sum(p['controlled_qty'] for p in pastals_detail_list)
        patok_summary = {
            'total_boxes': sum(p['total_boxes'] for p in pastals_detail_list),
            'total_qty': patok_total_qty,
            'in_sewing_boxes': sum(p['in_sewing_boxes'] for p in pastals_detail_list),
            'in_sewing_qty': sum(p['in_sewing_qty'] for p in pastals_detail_list),
            'dazmol_boxes': sum(p['dazmol_boxes'] for p in pastals_detail_list),
            'dazmol_qty': sum(p['dazmol_qty'] for p in pastals_detail_list),
            'waiting_otk_boxes': sum(p['waiting_otk_boxes'] for p in pastals_detail_list),
            'waiting_otk_qty': sum(p['waiting_otk_qty'] for p in pastals_detail_list),
            'repair_boxes': sum(p['repair_boxes'] for p in pastals_detail_list),
            'repair_qty': sum(p['repair_qty'] for p in pastals_detail_list),
            'controlled_boxes': sum(p['closed_boxes'] for p in pastals_detail_list),
            'controlled_qty': patok_controlled_qty,
            'remaining_boxes': sum(p['remaining_boxes'] for p in pastals_detail_list),
            'remaining_qty': sum(p['remaining_qty'] for p in pastals_detail_list),
            'first_sort_qty': sum(p['first_sort_qty'] for p in pastals_detail_list),
            'second_sort_qty': sum(p['second_sort_qty'] for p in pastals_detail_list),
            'defect_qty': sum(p['defect_qty'] for p in pastals_detail_list),
            'deficit_qty': sum(p['deficit_qty'] for p in pastals_detail_list),
            'closed_pastals_count': len([p for p in pastals_detail_list if p['is_closed']]),
            'total_pastals_count': len(pastals_detail_list),
            'completion_pct': round((patok_controlled_qty / patok_total_qty * 100), 1) if patok_total_qty > 0 else 0.0,
        }

        patoks_data.append({
            'screen_number': sn,
            'patok_code': p_code,
            'patok_name': p_name,
            'is_active': is_active,
            'orders': list(orders_dict.values()),
            'models': list(models_dict.values()),
            'primary_pastals': primary_pastals,
            'extra_pastals': extra_pastals,
            'pastals': pastals_detail_list,
            'pastals_count': len(pastals_detail_list),
            'pastals_summary': patok_summary,
            'all_pastals_closed': all_patok_pastals_closed if is_active else False,
            'daily_norm': patok_daily_norm,
            'completed_norm_units': completed_for_norm,
            'norm_percentage': norm_percentage,
            'is_norm_completed': is_norm_completed,
            'norm_diff': norm_diff,
            'aniqlik': sn_aniqlik,
            'davomat': sn_davomat,
            'davomat_foiz': sn_davomat_foiz,
            'avg_kpi': sn_avg_kpi,
            'patok_kpi': sn_patok_kpi,
            'patok_quvvati': sn_patok_quvvati,
            'quvvat_color': sn_quvvat_color,
            'quvvat_color_class': sn_quvvat_color_class,
            'quvvat_badge_bg': sn_quvvat_badge_bg,
            'workers': sn_workers,
            'workers_count': len(sn_workers),
            'pastal_dazmol_items': pastal_dazmol_items,
            'primary_dazmol_total': primary_dazmol_total,
            'patok_dazmol_qty': sn_dazmol_qty,
            'dazmol_qty': sn_dazmol_qty,
            'daily_dazmol_qty': sn_dazmol_qty,
            'dazmol_boxes_count': len(sn_dazmol_box_ids),
            'daily_dazmol_boxes_count': len(sn_dazmol_box_ids),
            'total_inspected_qty': sn_total_inspected,
            'daily_total_inspected_qty': sn_total_inspected,
            'boxes_count': sn_boxes_count,
            'daily_boxes_count': sn_boxes_count,
            'daily_controlled_boxes_count': sn_boxes_count,
            'controlled_qty': sn_total_inspected,
            'daily_controlled_qty': sn_total_inspected,
            'first_sort_qty': sn_first_sort_qty,
            'daily_first_sort_qty': sn_first_sort_qty,
            'second_sort_qty': sn_second_sort_qty,
            'daily_second_sort_qty': sn_second_sort_qty,
            'waiting_qty': sn_waiting_qty,
            'daily_waiting_qty': sn_waiting_qty,
            'repair_qty': sn_repair_qty,
            'daily_repair_qty': sn_repair_qty,
            'defect_qty': sn_defect_qty,
            'daily_defect_qty': sn_defect_qty,
            'models_count': len(models_dict),
            'operations': sorted_ops,
            'operations_count': len(sorted_ops),
            'scanned_ops_qty': patok_scanned_sum,
            'tickets_count': len(t_list),
        })

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    active_patok_items = [p for p in patoks_data if p['is_active']]
    total_planned_norm = sum(p['daily_norm'] for p in active_patok_items)
    total_completed_norm = sum(p['completed_norm_units'] for p in active_patok_items)
    avg_norm_pct = round((sum(p['norm_percentage'] for p in active_patok_items) / len(active_patok_items)), 1) if active_patok_items else 0.0
    total_workers = sum(p.get('davomat', 0) for p in active_patok_items)
    avg_kpi_overall = round((sum(p.get('avg_kpi', 0) for p in active_patok_items) / len(active_patok_items)), 1) if active_patok_items else 0.0
    avg_patok_kpi_overall = round((sum(p.get('patok_kpi', 0) for p in active_patok_items) / len(active_patok_items)), 1) if active_patok_items else 0.0
    avg_patok_quvvati = round((sum(p.get('patok_quvvati', 0.0) for p in active_patok_items) / len(active_patok_items)), 1) if active_patok_items else 0.0
    total_primary_dazmol_qty = sum(p.get('primary_dazmol_total', 0) for p in patoks_data)
    total_daily_inspected_qty = total_daily_first_sort + total_daily_second_sort + total_daily_repair_qty

    context = {
        'selected_date': selected_date,
        'selected_date_str': selected_date.isoformat(),
        'prev_date_str': prev_date.isoformat(),
        'next_date_str': next_date.isoformat(),
        'is_today': is_today,
        'patok_filter': patok_filter,
        'is_uychi_admin': is_uychi_admin,
        'search_q': search_q,
        'show_all_patoks': show_all_patoks,
        'patoks_data': patoks_data,
        'kpis': {
            'active_patoks': len(active_patok_items),
            'total_workers': total_workers,
            'avg_kpi_overall': avg_kpi_overall,
            'avg_patok_kpi_overall': avg_patok_kpi_overall,
            'avg_patok_quvvati': avg_patok_quvvati,
            'total_orders': len(all_worked_orders),
            'total_pastals': len(all_worked_pastals),
            'total_planned_norm': total_planned_norm,
            'total_completed_norm': total_completed_norm,
            'avg_norm_pct': avg_norm_pct,
            'dazmol_qty': total_daily_dazmol_qty,
            'primary_dazmol_qty': total_primary_dazmol_qty,
            'total_inspected_qty': total_daily_inspected_qty,
            'controlled_qty': total_daily_inspected_qty,
            'first_sort_qty': total_daily_first_sort,
            'second_sort_qty': total_daily_second_sort,
            'waiting_qty': total_daily_waiting_qty,
            'repair_qty': total_daily_repair_qty,
            'scanned_ops_qty': total_daily_scanned_ops_qty,
        }
    }
    return render(request, 'production/sewing_statistics_daily.html', context)


@manager_or_superadmin_required
def api_sewing_statistics_daily_operations(request):
    """
    Patok bo'yicha kunlik bajarilgan operatsiyalar ro'yxatini qaytarish (ism-familiyasiz)
    """
    from datetime import datetime
    from production.services import parse_patok_number
    date_str = request.GET.get('date', '').strip()
    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = None
    if not selected_date:
        selected_date = timezone.localdate()

    screen_param = request.GET.get('screen_number') or request.GET.get('patok')
    sn = parse_patok_number(screen_param)
    if not sn:
        return JsonResponse({'status': 'ERROR', 'message': "Patok raqami noto'g'ri"}, status=400)

    tickets = Ticket.objects.filter(
        screen_number=sn,
        status=Ticket.Status.SCANNED,
        scanned_at__date=selected_date
    ).select_related(
        'box__order',
        'box__article',
        'article_operation__operation',
        'article_operation__article'
    ).order_by('article_operation__sequence', 'id')

    operations_map = {}
    for t in tickets:
        ao = t.article_operation
        if not ao or not ao.operation:
            continue
        box = t.box
        order = box.order if box else None
        article = ao.article or (box.article if box else None)
        op_id = ao.id
        if op_id not in operations_map:
            op_name = ao.operation.name
            op_code = ao.operation.code or ""
            is_dazmol = is_main_dazmol_operation(op_name, op_code)
            operations_map[op_id] = {
                'operation_id': op_id,
                'sequence': ao.sequence,
                'operation_name': op_name,
                'operation_code': op_code,
                'order_number': order.order_number if order else "—",
                'model_name': article.name if article else "—",
                'is_dazmol': is_dazmol,
                'total_quantity': 0,
                'tickets_count': 0,
            }
        operations_map[op_id]['total_quantity'] += t.quantity
        operations_map[op_id]['tickets_count'] += 1

    ops_list = sorted(
        operations_map.values(),
        key=lambda x: (x['order_number'], x['sequence'], x['operation_name'])
    )

    return JsonResponse({
        'status': 'OK',
        'screen_number': sn,
        'patok_code': get_patok_code(sn),
        'patok_name': get_patok_name(sn),
        'date': selected_date.isoformat(),
        'operations': ops_list,
        'total_units': sum(o['total_quantity'] for o in ops_list),
        'operations_count': len(ops_list),
    })


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
        'total_unprinted_boxes': 0,
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
        unprinted_boxes_count = 0

        for b in boxes:
            if not b.is_printed and not b.is_controlled:
                unprinted_boxes_count += 1
                overall_kpis['total_unprinted_boxes'] += 1

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
            'unprinted_boxes_count': unprinted_boxes_count,
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
    - Kunlik nazorat (Tanlangan sana bo'yicha: prior + today = total)
    - Kesim, Tikim kutayotgan pastallar, Real WIP, Dazmol, OTK kutayotgan (tayyor vs chala) va 1-sort
    - Har bir modelning o'z statistikasi va "Statistikani ko'rish" tugmasi orqali razmerlar bo'limiga kirish
    """
    from datetime import datetime, time, timedelta

    # Sana filtri
    date_str = request.GET.get('date', '').strip()
    selected_date = None
    if date_str:
        for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y'):
            try:
                selected_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                pass
    if not selected_date:
        selected_date = timezone.localdate()

    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(selected_date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(selected_date, time.max), tz)

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    is_today = (selected_date == timezone.localdate())

    order = get_object_or_404(
        Order.objects.select_related('customer').prefetch_related(
            'items__article__model',
            'items__article__article_operations__operation',
            'items__sizes__cutting_items__batch',
            Prefetch(
                'boxes',
                queryset=Box.objects.exclude(status=Box.Status.CANCELLED).select_related(
                    'article', 'controlled_by', 'cutting_batch_item__batch'
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
    pastals_info = get_model_pastals_analysis(boxes)

    models_data = []

    order_totals = {
        'planned': 0,
        'cut_prior': 0,
        'cut_today': 0,
        'cut_total': 0,
        'cut': 0,
        'cut_pct': 0.0,
        'boxed': 0,
        'boxes_count': len(boxes),
        'waiting_sewing_qty': pastals_info['waiting_sewing_qty'],
        'waiting_sewing_boxes': pastals_info['waiting_sewing_boxes'],
        'waiting_pastals_count': pastals_info['waiting_pastals_count'],
        'waiting_pastals': pastals_info['waiting_pastals'],
        'sewing_prior': 0,
        'sewing_today': 0,
        'sewing_total': 0,
        'entered_sewing': 0,
        'entered_sewing_boxes': 0,
        'in_sewing_pre_dazmol': 0,
        'in_sewing_pre_dazmol_boxes': 0,
        'real_wip_sewing': 0,
        'real_wip_sewing_boxes': 0,
        'dazmol_prior': 0,
        'dazmol_today': 0,
        'dazmol_total': 0,
        'dazmol': 0,
        'dazmol_boxes': 0,
        'dazmol_pct': 0.0,
        'unreached_control': 0,
        'unreached_control_boxes': 0,
        'waiting_control': 0,
        'waiting_control_boxes': 0,
        'waiting_control_ready': 0,
        'waiting_control_ready_boxes': 0,
        'waiting_control_missing_ops': 0,
        'waiting_control_missing_ops_boxes': 0,
        'unprinted_boxes_count': sum(1 for b in boxes if not b.is_printed and not b.is_controlled),
        'in_repair': 0,
        'in_repair_boxes': 0,
        're_repair': 0,
        're_repair_boxes': 0,
        'first_sort_prior': 0,
        'first_sort_today': 0,
        'first_sort_total': 0,
        'first_sort': 0,
        'second_sort_prior': 0,
        'second_sort_today': 0,
        'second_sort_total': 0,
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

        # 1. Kesim delta
        cutting_items = []
        for s in item.sizes.all():
            cutting_items.extend(list(s.cutting_items.select_related('batch')))
        if cutting_items:
            m_cut_prior = sum(ci.quantity for ci in cutting_items if ci.batch and ci.batch.created_at and ci.batch.created_at < day_start)
            m_cut_today = sum(ci.quantity for ci in cutting_items if ci.batch and ci.batch.created_at and day_start <= ci.batch.created_at <= day_end)
        else:
            m_cut_prior = sum(b.quantity for b in art_boxes if b.created_at and b.created_at < day_start)
            m_cut_today = sum(b.quantity for b in art_boxes if b.created_at and day_start <= b.created_at <= day_end)
        m_cut_total = m_cut_prior + m_cut_today

        # 2. Tikim delta
        m_sewing_prior = 0
        m_sewing_today = 0
        for b in art_boxes:
            scanned_ts = [t.scanned_at for t in b.tickets.all() if t.status == Ticket.Status.SCANNED and t.scanned_at]
            if scanned_ts:
                min_ts = min(scanned_ts)
                if min_ts < day_start:
                    m_sewing_prior += b.quantity
                elif day_start <= min_ts <= day_end:
                    m_sewing_today += b.quantity
        m_sewing_total = m_sewing_prior + m_sewing_today

        # 3. Dazmol delta
        m_dazmol_prior = 0
        m_dazmol_today = 0
        for b in art_boxes:
            dz_tickets = [t for t in b.tickets.all() if t.article_operation_id in dazmol_ao_ids and t.status == Ticket.Status.SCANNED and t.scanned_at]
            if dz_tickets:
                min_dz = min(t.scanned_at for t in dz_tickets)
                if min_dz < day_start:
                    m_dazmol_prior += b.quantity
                elif day_start <= min_dz <= day_end:
                    m_dazmol_today += b.quantity
        m_dazmol_total = m_dazmol_prior + m_dazmol_today

        # 4. OTK delta
        m_first_sort_prior = 0
        m_first_sort_today = 0
        m_second_sort_prior = 0
        m_second_sort_today = 0
        for b in art_boxes:
            if b.is_controlled and b.controlled_at:
                if b.controlled_at < day_start:
                    m_first_sort_prior += (b.controlled_first_sort_qty or 0)
                    m_second_sort_prior += (b.controlled_second_sort_qty or 0)
                elif day_start <= b.controlled_at <= day_end:
                    m_first_sort_today += (b.controlled_first_sort_qty or 0)
                    m_second_sort_today += (b.controlled_second_sort_qty or 0)
        m_first_sort_total = m_first_sort_prior + m_first_sort_today
        m_second_sort_total = m_second_sort_prior + m_second_sort_today

        # Ushbu model uchun kutayotgan pastallar
        art_pastals_info = get_model_pastals_analysis(art_boxes)
        m_waiting_sewing_qty = art_pastals_info['waiting_sewing_qty']
        m_waiting_sewing_boxes = art_pastals_info['waiting_sewing_boxes']

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
            'cut_qty': m_cut_total,
            'cut_prior': m_cut_prior,
            'cut_today': m_cut_today,
            'cut_total': m_cut_total,
            'cut_pct': round((m_cut_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'total_boxed_qty': total_boxed_qty,
            'boxes_count': len(art_boxes),
            'waiting_sewing_qty': m_waiting_sewing_qty,
            'waiting_sewing_boxes': m_waiting_sewing_boxes,
            'entered_sewing_qty': m_sewing_total,
            'sewing_prior': m_sewing_prior,
            'sewing_today': m_sewing_today,
            'sewing_total': m_sewing_total,
            'entered_sewing_boxes': model_balance['entered_sewing_boxes'],
            'sewing_pct': round((m_sewing_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'in_sewing_pre_dazmol_qty': model_balance['in_sewing_pre_dazmol_qty'],
            'in_sewing_pre_dazmol_boxes': model_balance['in_sewing_pre_dazmol_boxes'],
            'real_wip_sewing_qty': model_balance['real_wip_sewing_qty'],
            'real_wip_sewing_boxes': model_balance['real_wip_sewing_boxes'],
            'dazmol_qty': m_dazmol_total,
            'dazmol_prior': m_dazmol_prior,
            'dazmol_today': m_dazmol_today,
            'dazmol_total': m_dazmol_total,
            'dazmol_boxes': model_balance['dazmol_boxes'],
            'dazmol_pct': round((m_dazmol_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'unreached_control_qty': model_balance['unreached_control_qty'],
            'unreached_control_boxes': model_balance['unreached_control_boxes'],
            'waiting_control_qty': model_balance['waiting_control_qty'],
            'waiting_control_boxes': model_balance['waiting_control_boxes'],
            'waiting_control_ready_qty': model_balance['waiting_control_ready_qty'],
            'waiting_control_ready_boxes': model_balance['waiting_control_ready_boxes'],
            'waiting_control_missing_ops_qty': model_balance['waiting_control_missing_ops_qty'],
            'waiting_control_missing_ops_boxes': model_balance['waiting_control_missing_ops_boxes'],
            'in_repair_qty': model_balance['in_repair_qty'],
            'in_repair_boxes': model_balance['in_repair_boxes'],
            're_repair_qty': model_balance['re_repair_qty'],
            're_repair_boxes': model_balance['re_repair_boxes'],
            'first_sort_qty': m_first_sort_total,
            'first_sort_prior': m_first_sort_prior,
            'first_sort_today': m_first_sort_today,
            'first_sort_total': m_first_sort_total,
            'second_sort_qty': m_second_sort_total,
            'second_sort_prior': m_second_sort_prior,
            'second_sort_today': m_second_sort_today,
            'second_sort_total': m_second_sort_total,
            'repair_qty': model_balance['in_repair_qty'],
            'controlled_boxes_count': model_balance['controlled_boxes_count'],
            'control_variance': model_balance['net_variance'],
            'brak_rate_pct': model_balance['brak_rate_pct'],
            'is_brak_warning': model_balance['is_brak_warning'],
            'balance': model_balance,
        })

        order_totals['planned'] += planned_qty
        order_totals['cut_prior'] += m_cut_prior
        order_totals['cut_today'] += m_cut_today
        order_totals['cut_total'] += m_cut_total
        order_totals['cut'] += m_cut_total
        order_totals['boxed'] += total_boxed_qty

        order_totals['sewing_prior'] += m_sewing_prior
        order_totals['sewing_today'] += m_sewing_today
        order_totals['sewing_total'] += m_sewing_total
        order_totals['entered_sewing'] += m_sewing_total
        order_totals['entered_sewing_boxes'] += model_balance['entered_sewing_boxes']
        order_totals['in_sewing_pre_dazmol'] += model_balance['in_sewing_pre_dazmol_qty']
        order_totals['in_sewing_pre_dazmol_boxes'] += model_balance['in_sewing_pre_dazmol_boxes']
        order_totals['real_wip_sewing'] += model_balance['real_wip_sewing_qty']
        order_totals['real_wip_sewing_boxes'] += model_balance['real_wip_sewing_boxes']

        order_totals['dazmol_prior'] += m_dazmol_prior
        order_totals['dazmol_today'] += m_dazmol_today
        order_totals['dazmol_total'] += m_dazmol_total
        order_totals['dazmol'] += m_dazmol_total
        order_totals['dazmol_boxes'] += model_balance['dazmol_boxes']

        order_totals['unreached_control'] += model_balance['unreached_control_qty']
        order_totals['unreached_control_boxes'] += model_balance['unreached_control_boxes']
        order_totals['waiting_control'] += model_balance['waiting_control_qty']
        order_totals['waiting_control_boxes'] += model_balance['waiting_control_boxes']
        order_totals['waiting_control_ready'] += model_balance['waiting_control_ready_qty']
        order_totals['waiting_control_ready_boxes'] += model_balance['waiting_control_ready_boxes']
        order_totals['waiting_control_missing_ops'] += model_balance['waiting_control_missing_ops_qty']
        order_totals['waiting_control_missing_ops_boxes'] += model_balance['waiting_control_missing_ops_boxes']

        order_totals['in_repair'] += model_balance['in_repair_qty']
        order_totals['in_repair_boxes'] += model_balance['in_repair_boxes']
        order_totals['re_repair'] += model_balance['re_repair_qty']
        order_totals['re_repair_boxes'] += model_balance['re_repair_boxes']

        order_totals['first_sort_prior'] += m_first_sort_prior
        order_totals['first_sort_today'] += m_first_sort_today
        order_totals['first_sort_total'] += m_first_sort_total
        order_totals['first_sort'] += m_first_sort_total
        order_totals['second_sort_prior'] += m_second_sort_prior
        order_totals['second_sort_today'] += m_second_sort_today
        order_totals['second_sort_total'] += m_second_sort_total
        order_totals['second_sort'] += m_second_sort_total

        order_totals['controlled_boxes_count'] += model_balance['controlled_boxes_count']
        order_totals['control_variance'] += model_balance['net_variance']

    # Foizlar va ogohlantirishlar
    order_totals['cut_pct'] = round((order_totals['cut_total'] / order_totals['planned'] * 100), 1) if order_totals['planned'] > 0 else 0.0
    order_totals['dazmol_pct'] = round((order_totals['dazmol_total'] / order_totals['planned'] * 100), 1) if order_totals['planned'] > 0 else 0.0

    total_closed_qty = order_totals['first_sort_total'] + order_totals['second_sort_total']
    if total_closed_qty > 0:
        order_totals['brak_rate_pct'] = round((order_totals['second_sort_total'] / total_closed_qty * 100), 1)
        order_totals['is_brak_warning'] = bool(order_totals['brak_rate_pct'] > 10.0)

    # Butun zakaz balansi
    order_balance = calculate_pipeline_balance(boxes, order_totals['planned'], order_totals['cut_total'])

    return render(request, 'production/sewing_statistics_order_models.html', {
        'order': order,
        'models_data': models_data,
        'order_totals': order_totals,
        'order_balance': order_balance,
        'selected_date': selected_date,
        'selected_date_str': selected_date.strftime('%Y-%m-%d'),
        'selected_date_display': selected_date.strftime('%d.%m.%Y'),
        'prev_date_str': prev_date.strftime('%Y-%m-%d'),
        'next_date_str': next_date.strftime('%Y-%m-%d'),
        'today_str': timezone.localdate().strftime('%Y-%m-%d'),
        'is_today': is_today,
    })


@manager_or_superadmin_required
def sewing_statistics_model_detail_view(request, order_id: int, order_item_id: int):
    """
    3-BOSQICH: Tanlangan Modelning Razmerlar Bo'yicha To'liq Tikim Statistikasi
    - Kunlik nazorat (Tanlangan sana bo'yicha):
      * Oldingi kunga qadar bo'lgan natija (prior)
      * Tanlangan kunda qo'shilgan natija (daily / delta)
      * Tanlangan kun oxiridagi umumiy natija (total = prior + daily)
    - Kesim, Tikimga kirgan, Dazmoldan o'tgan, OTK 1-sort ko'rsatkichlari bo'yicha
    """
    from datetime import datetime, time, timedelta

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

    # Sana filtri
    date_str = request.GET.get('date', '').strip()
    selected_date = None
    if date_str:
        for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y'):
            try:
                selected_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                pass
    if not selected_date:
        selected_date = timezone.localdate()

    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(selected_date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(selected_date, time.max), tz)

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    is_today = (selected_date == timezone.localdate())

    # Qutilar, biletlar va sifat jurnallarini prefetch qilish
    boxes = list(
        order.boxes.filter(article=article).exclude(
            status=Box.Status.CANCELLED
        ).select_related('controlled_by', 'cutting_batch_item__batch').prefetch_related(
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

    # Pastallar tahlili: Tikim Kutmoqda (0 stiker) vs Tikimga Kirgan
    pastals_info = get_model_pastals_analysis(boxes)

    sizes_breakdown = []
    model_totals = {
        'planned': 0,
        'cut_prior': 0,
        'cut_today': 0,
        'cut_total': 0,
        'cut': 0,
        'boxed': 0,
        'boxes_count': len(boxes),
        'waiting_sewing_qty': pastals_info['waiting_sewing_qty'],
        'waiting_sewing_boxes': pastals_info['waiting_sewing_boxes'],
        'waiting_pastals_count': pastals_info['waiting_pastals_count'],
        'waiting_pastals': pastals_info['waiting_pastals'],
        'sewing_prior': 0,
        'sewing_today': 0,
        'sewing_total': 0,
        'entered_sewing': 0,
        'entered_boxes': 0,
        'in_sewing_pre_dazmol': 0,
        'in_sewing_pre_dazmol_boxes': 0,
        'real_wip_sewing': 0,
        'real_wip_sewing_boxes': 0,
        'dazmol_prior': 0,
        'dazmol_today': 0,
        'dazmol_total': 0,
        'dazmol': 0,
        'dazmol_boxes': 0,
        'unreached_control': 0,
        'unreached_control_boxes': 0,
        'waiting_control': 0,
        'waiting_control_boxes': 0,
        'waiting_control_ready': 0,
        'waiting_control_ready_boxes': 0,
        'waiting_control_missing_ops': 0,
        'waiting_control_missing_ops_boxes': 0,
        'in_repair': 0,
        'in_repair_boxes': 0,
        're_repair': 0,
        're_repair_boxes': 0,
        'first_sort_prior': 0,
        'first_sort_today': 0,
        'first_sort_total': 0,
        'first_sort': 0,
        'second_sort_prior': 0,
        'second_sort_today': 0,
        'second_sort_total': 0,
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

        # Ushbu razmer qutilari
        size_boxes = [b for b in boxes if (b.razmer or '').strip().upper() == size_name]
        total_boxed_qty = sum(b.quantity for b in size_boxes)
        size_balance = calculate_pipeline_balance(size_boxes, planned_qty, size_obj.total_cut_quantity if size_obj else 0, dazmol_ao_ids)

        # 1. Kesim delta
        cutting_items = list(size_obj.cutting_items.select_related('batch')) if size_obj else []
        if cutting_items:
            s_cut_prior = sum(ci.quantity for ci in cutting_items if ci.batch and ci.batch.created_at and ci.batch.created_at < day_start)
            s_cut_today = sum(ci.quantity for ci in cutting_items if ci.batch and ci.batch.created_at and day_start <= ci.batch.created_at <= day_end)
        else:
            s_cut_prior = sum(b.quantity for b in size_boxes if b.created_at and b.created_at < day_start)
            s_cut_today = sum(b.quantity for b in size_boxes if b.created_at and day_start <= b.created_at <= day_end)
        s_cut_total = s_cut_prior + s_cut_today

        # 2. Tikim delta
        s_sewing_prior = 0
        s_sewing_today = 0
        for b in size_boxes:
            scanned_ts = [t.scanned_at for t in b.tickets.all() if t.status == Ticket.Status.SCANNED and t.scanned_at]
            if scanned_ts:
                min_ts = min(scanned_ts)
                if min_ts < day_start:
                    s_sewing_prior += b.quantity
                elif day_start <= min_ts <= day_end:
                    s_sewing_today += b.quantity
        s_sewing_total = s_sewing_prior + s_sewing_today

        # 3. Dazmol delta
        s_dazmol_prior = 0
        s_dazmol_today = 0
        for b in size_boxes:
            dz_tickets = [t for t in b.tickets.all() if t.article_operation_id in dazmol_ao_ids and t.status == Ticket.Status.SCANNED and t.scanned_at]
            if dz_tickets:
                min_dz = min(t.scanned_at for t in dz_tickets)
                if min_dz < day_start:
                    s_dazmol_prior += b.quantity
                elif day_start <= min_dz <= day_end:
                    s_dazmol_today += b.quantity
        s_dazmol_total = s_dazmol_prior + s_dazmol_today

        # 4. OTK delta
        s_first_sort_prior = 0
        s_first_sort_today = 0
        s_second_sort_prior = 0
        s_second_sort_today = 0
        for b in size_boxes:
            if b.is_controlled and b.controlled_at:
                if b.controlled_at < day_start:
                    s_first_sort_prior += (b.controlled_first_sort_qty or 0)
                    s_second_sort_prior += (b.controlled_second_sort_qty or 0)
                elif day_start <= b.controlled_at <= day_end:
                    s_first_sort_today += (b.controlled_first_sort_qty or 0)
                    s_second_sort_today += (b.controlled_second_sort_qty or 0)
        s_first_sort_total = s_first_sort_prior + s_first_sort_today
        s_second_sort_total = s_second_sort_prior + s_second_sort_today

        s_waiting_sewing_qty = pastals_info['waiting_by_size'].get(size_name, 0)
        s_waiting_sewing_boxes = pastals_info['waiting_boxes_by_size'].get(size_name, 0)

        sizes_breakdown.append({
            'size_name': size_name,
            'planned_qty': planned_qty,
            'cut_qty': s_cut_total,
            'cut_prior': s_cut_prior,
            'cut_today': s_cut_today,
            'cut_total': s_cut_total,
            'cut_pct': round((s_cut_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'total_boxed_qty': total_boxed_qty,
            'boxes_count': len(size_boxes),
            'waiting_sewing_qty': s_waiting_sewing_qty,
            'waiting_sewing_boxes': s_waiting_sewing_boxes,
            'entered_sewing_qty': s_sewing_total,
            'sewing_prior': s_sewing_prior,
            'sewing_today': s_sewing_today,
            'sewing_total': s_sewing_total,
            'entered_sewing_boxes': size_balance['entered_sewing_boxes'],
            'sewing_pct': round((s_sewing_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'in_sewing_pre_dazmol_qty': size_balance['in_sewing_pre_dazmol_qty'],
            'in_sewing_pre_dazmol_boxes': size_balance['in_sewing_pre_dazmol_boxes'],
            'real_wip_sewing_qty': size_balance['real_wip_sewing_qty'],
            'real_wip_sewing_boxes': size_balance['real_wip_sewing_boxes'],
            'dazmol_qty': s_dazmol_total,
            'dazmol_prior': s_dazmol_prior,
            'dazmol_today': s_dazmol_today,
            'dazmol_total': s_dazmol_total,
            'dazmol_boxes': size_balance['dazmol_boxes'],
            'dazmol_pct': round((s_dazmol_total / planned_qty * 100), 1) if planned_qty > 0 else 0.0,
            'unreached_control_qty': size_balance['unreached_control_qty'],
            'unreached_control_boxes': size_balance['unreached_control_boxes'],
            'waiting_control_qty': size_balance['waiting_control_qty'],
            'waiting_control_boxes': size_balance['waiting_control_boxes'],
            'waiting_control_ready_qty': size_balance['waiting_control_ready_qty'],
            'waiting_control_ready_boxes': size_balance['waiting_control_ready_boxes'],
            'waiting_control_missing_ops_qty': size_balance['waiting_control_missing_ops_qty'],
            'waiting_control_missing_ops_boxes': size_balance['waiting_control_missing_ops_boxes'],
            'in_repair_qty': size_balance['in_repair_qty'],
            'in_repair_boxes': size_balance['in_repair_boxes'],
            're_repair_qty': size_balance['re_repair_qty'],
            're_repair_boxes': size_balance['re_repair_boxes'],
            'first_sort_qty': s_first_sort_total,
            'first_sort_prior': s_first_sort_prior,
            'first_sort_today': s_first_sort_today,
            'first_sort_total': s_first_sort_total,
            'second_sort_qty': s_second_sort_total,
            'second_sort_prior': s_second_sort_prior,
            'second_sort_today': s_second_sort_today,
            'second_sort_total': s_second_sort_total,
            'repair_qty': size_balance['in_repair_qty'],
            'controlled_boxes': size_balance['controlled_boxes_count'],
            'control_variance': size_balance['net_variance'],
            'brak_rate_pct': size_balance['brak_rate_pct'],
            'is_brak_warning': size_balance['is_brak_warning'],
            'balance': size_balance,
        })

        model_totals['planned'] += planned_qty
        model_totals['cut_prior'] += s_cut_prior
        model_totals['cut_today'] += s_cut_today
        model_totals['cut_total'] += s_cut_total
        model_totals['cut'] += s_cut_total
        model_totals['boxed'] += total_boxed_qty

        model_totals['sewing_prior'] += s_sewing_prior
        model_totals['sewing_today'] += s_sewing_today
        model_totals['sewing_total'] += s_sewing_total
        model_totals['entered_sewing'] += s_sewing_total
        model_totals['entered_boxes'] += size_balance['entered_sewing_boxes']
        model_totals['in_sewing_pre_dazmol'] += size_balance['in_sewing_pre_dazmol_qty']
        model_totals['in_sewing_pre_dazmol_boxes'] += size_balance['in_sewing_pre_dazmol_boxes']
        model_totals['real_wip_sewing'] += size_balance['real_wip_sewing_qty']
        model_totals['real_wip_sewing_boxes'] += size_balance['real_wip_sewing_boxes']

        model_totals['dazmol_prior'] += s_dazmol_prior
        model_totals['dazmol_today'] += s_dazmol_today
        model_totals['dazmol_total'] += s_dazmol_total
        model_totals['dazmol'] += s_dazmol_total
        model_totals['dazmol_boxes'] += size_balance['dazmol_boxes']

        model_totals['unreached_control'] += size_balance['unreached_control_qty']
        model_totals['unreached_control_boxes'] += size_balance['unreached_control_boxes']
        model_totals['waiting_control'] += size_balance['waiting_control_qty']
        model_totals['waiting_control_boxes'] += size_balance['waiting_control_boxes']
        model_totals['waiting_control_ready'] += size_balance['waiting_control_ready_qty']
        model_totals['waiting_control_ready_boxes'] += size_balance['waiting_control_ready_boxes']
        model_totals['waiting_control_missing_ops'] += size_balance['waiting_control_missing_ops_qty']
        model_totals['waiting_control_missing_ops_boxes'] += size_balance['waiting_control_missing_ops_boxes']
        model_totals['in_repair'] += size_balance['in_repair_qty']
        model_totals['in_repair_boxes'] += size_balance['in_repair_boxes']
        model_totals['re_repair'] += size_balance['re_repair_qty']
        model_totals['re_repair_boxes'] += size_balance['re_repair_boxes']

        model_totals['first_sort_prior'] += s_first_sort_prior
        model_totals['first_sort_today'] += s_first_sort_today
        model_totals['first_sort_total'] += s_first_sort_total
        model_totals['first_sort'] += s_first_sort_total

        model_totals['second_sort_prior'] += s_second_sort_prior
        model_totals['second_sort_today'] += s_second_sort_today
        model_totals['second_sort_total'] += s_second_sort_total
        model_totals['second_sort'] += s_second_sort_total

        model_totals['repair'] += size_balance['in_repair_qty']
        model_totals['controlled_boxes'] += size_balance['controlled_boxes_count']
        model_totals['control_variance'] += size_balance['net_variance']

    # To'liq model balansi
    model_balance = calculate_pipeline_balance(boxes, model_totals['planned'], model_totals['cut_total'], dazmol_ao_ids, pastals_info=pastals_info)
    model_totals['cut_pct'] = round((model_totals['cut_total'] / model_totals['planned'] * 100), 1) if model_totals['planned'] > 0 else 0.0
    model_totals['sewing_pct'] = round((model_totals['sewing_total'] / model_totals['planned'] * 100), 1) if model_totals['planned'] > 0 else 0.0
    model_totals['dazmol_pct'] = round((model_totals['dazmol_total'] / model_totals['planned'] * 100), 1) if model_totals['planned'] > 0 else 0.0

    total_closed = model_totals['first_sort_total'] + model_totals['second_sort_total']
    brak_pct = round((model_totals['second_sort_total'] / total_closed * 100), 1) if total_closed > 0 else 0.0
    model_totals['brak_rate_pct'] = brak_pct
    model_totals['is_brak_warning'] = (brak_pct >= 10.0 and total_closed >= 10)

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
        'selected_date': selected_date,
        'selected_date_str': selected_date.strftime('%Y-%m-%d'),
        'selected_date_display': selected_date.strftime('%d.%m.%Y'),
        'prev_date_str': prev_date.strftime('%Y-%m-%d'),
        'next_date_str': next_date.strftime('%Y-%m-%d'),
        'today_str': timezone.localdate().strftime('%Y-%m-%d'),
        'is_today': is_today,
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
        'in_sewing_pre_dazmol_boxes': 0,
        'in_sewing_pre_dazmol_units': 0,
        'real_wip_sewing_boxes': 0,
        'real_wip_sewing_units': 0,
        'passed_dazmol_boxes': 0,
        'passed_dazmol_units': 0,
        'unreached_control_boxes': 0,
        'unreached_control_units': 0,
        'waiting_control_boxes': 0,
        'waiting_control_units': 0,
        'waiting_control_ready_boxes': 0,
        'waiting_control_ready_units': 0,
        'waiting_control_missing_ops_boxes': 0,
        'waiting_control_missing_ops_units': 0,
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

            if b_eval['is_in_sewing_pre_dazmol']:
                summary['in_sewing_pre_dazmol_boxes'] += 1
                summary['in_sewing_pre_dazmol_units'] += b.quantity

        if has_dazmol:
            summary['passed_dazmol_boxes'] += 1
            summary['passed_dazmol_units'] += b.quantity

        if unreached_ctrl:
            summary['unreached_control_boxes'] += 1
            summary['unreached_control_units'] += b.quantity

        if is_waiting_ctrl:
            summary['waiting_control_boxes'] += 1
            summary['waiting_control_units'] += b.quantity
            if b_eval['is_waiting_control_ready']:
                summary['waiting_control_ready_boxes'] += 1
                summary['waiting_control_ready_units'] += b.quantity
            else:
                summary['waiting_control_missing_ops_boxes'] += 1
                summary['waiting_control_missing_ops_units'] += b.quantity

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
                'stiker_id': t.stiker_id,
                'stiker_code': t.stiker_code,
                'ticket_code': t.ticket_code,
                'qr_code_url': t.qr_code_data_uri,
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
            'is_in_sewing_pre_dazmol': b_eval['is_in_sewing_pre_dazmol'],
            'unreached_control': unreached_ctrl,
            'is_waiting_control': is_waiting_ctrl,
            'is_waiting_control_ready': b_eval['is_waiting_control_ready'],
            'is_waiting_control_missing_ops': b_eval['is_waiting_control_missing_ops'],
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

    summary['real_wip_sewing_boxes'] = summary['in_sewing_pre_dazmol_boxes'] + summary['in_repair_boxes']
    summary['real_wip_sewing_units'] = summary['in_sewing_pre_dazmol_units'] + summary['in_repair_units']

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


@manager_or_superadmin_required
@require_POST
def sewing_statistics_close_order_unprinted_boxes_view(request, order_id: int):
    """
    Bitta buyurtma bo'yicha stiker chop etilmagan (yoki avvalgi) qutilarni OTK sifat nazoratidan 1-sort qilib yopish.
    """
    if getattr(request.user, 'role', None) == User.Role.PLAN:
        messages.error(request, "Plan hisobi faqat ma'lumotlarni ko'rish huquqiga ega!")
        return redirect('production:sewing_statistics_order_models', order_id=order_id)

    order = get_object_or_404(Order, id=order_id)
    boxes = order.boxes.exclude(status=Box.Status.CANCELLED).filter(is_controlled=False, is_printed=False)

    # Agar stiker chiqarilmagan bo'lmasa, lekin barcha tikilgan va kutayotgan qutilar bo'lsa:
    close_all_waiting = request.POST.get('close_all_waiting') == '1'
    if close_all_waiting or not boxes.exists():
        boxes = order.boxes.exclude(status=Box.Status.CANCELLED).filter(is_controlled=False)

    from .services import close_boxes_as_controlled
    result = close_boxes_as_controlled(
        boxes,
        user=request.user,
        mark_printed=True,
        note=f"Zakaz #{order.order_number}: Avvalgi (stiker chiqarilmagan / controlsiz) partiya yopildi"
    )

    if result['closed_count'] > 0:
        messages.success(
            request,
            f"✅ Muvaffaqiyatli! Zakaz #{order.order_number} bo'yicha {result['closed_count']} ta quti "
            f"({result['closed_units']} dona) OTK sifat nazoratidan 1-sort qilib to'liq yopildi!"
        )
    else:
        messages.info(request, "Ushbu buyurtmada yopilishi kerak bo'lgan ochiq qutilar topilmadi.")

    return redirect('production:sewing_statistics_order_models', order_id=order.id)


@manager_or_superadmin_required
@require_POST
def sewing_statistics_close_all_unprinted_boxes_view(request):
    """
    Barcha buyurtmalar bo'yicha stiker chop etilmagan (is_printed=False) va OTK o'tmagan qutilarni to'liq yopish.
    """
    if getattr(request.user, 'role', None) == User.Role.PLAN:
        messages.error(request, "Plan hisobi faqat ma'lumotlarni ko'rish huquqiga ega!")
        return redirect('production:sewing_statistics_orders')

    boxes = Box.objects.exclude(status=Box.Status.CANCELLED).filter(is_controlled=False, is_printed=False)

    from .services import close_boxes_as_controlled
    result = close_boxes_as_controlled(
        boxes,
        user=request.user,
        mark_printed=True,
        note="Tizimdagi barcha avvalgi stiker chiqarilmagan partiyalar avtomatik yopildi"
    )

    if result['closed_count'] > 0:
        messages.success(
            request,
            f"✅ Muvaffaqiyatli! Jami {result['closed_count']} ta stiker chiqarilmagan quti "
            f"({result['closed_units']} dona) OTK sifat nazoratidan 1-sort qilib to'liq yopildi!"
        )
    else:
        messages.info(request, "Tizimda stiker chop etilmagan ochiq qutilar mavjud emas.")

    return redirect('production:sewing_statistics_orders')


@manager_or_superadmin_required
@require_POST
def api_send_telegram_daily_report_view(request):
    """
    Kunlik rasm-hisobotini Telegram guruhlarga yuborish (Web dashboarddan test qilish yoki yuborish):
    - date: YYYY-MM-DD
    - branch: ALL | HQ | UYCHI
    """
    from datetime import datetime
    from .daily_image_reports import send_daily_1800_reports, send_single_branch_report

    date_str = request.POST.get('date', '').strip()
    branch = request.POST.get('branch', 'ALL').strip().upper()

    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'status': 'ERROR', 'message': f"Xato sana formati: {date_str}"}, status=400)
    else:
        target_date = timezone.localdate()

    if branch == 'ALL':
        results = send_daily_1800_reports(target_date)
        hq_res = results.get('HQ', {})
        uychi_res = results.get('UYCHI', {})
        success = bool(hq_res.get('success') or uychi_res.get('success'))

        messages_list = []
        if hq_res.get('success'):
            messages_list.append(f"✅ Kosonsoy filiali: {hq_res.get('chat_id')} guruhiga rasm yuborildi.")
        else:
            messages_list.append(f"❌ Kosonsoy filiali: {hq_res.get('message')}")

        if uychi_res.get('success'):
            messages_list.append(f"✅ Uychi filiali: {uychi_res.get('chat_id')} guruhiga rasm yuborildi.")
        else:
            messages_list.append(f"❌ Uychi filiali: {uychi_res.get('message')}")

        return JsonResponse({
            'status': 'OK' if success else 'ERROR',
            'message': '<br>'.join(messages_list),
            'target_date': str(target_date),
            'results': results,
        })
    else:
        res = send_single_branch_report(branch, target_date=target_date)
        return JsonResponse({
            'status': 'OK' if res.get('success') else 'ERROR',
            'message': res.get('message', ''),
            'target_date': str(target_date),
            'result': res,
        })


@manager_or_superadmin_required
def sewing_statistics_otk_view(request):
    """
    OTK SIFAT NAZORATI: ODAMLAR VA PATOKLAR KESIMIDA KUNLIK MONITORING VA REYTING
    - Nazoratchilar kesimida (kim qancha 1-sort, 2-sort, ta'mir ko'rdi, qaysi patoklarda ishladi)
    - Patoklar kesimida (har bir patokdan qancha ish tekshirildi, kimlar tekshirdi)
    - Filial filtri: Barchasi, Bosh bino (HQ), Uychi filiali
    - Sana filtri: Bugun real-time, kecha va istalgan sana
    """
    from datetime import datetime, time, timedelta
    from collections import defaultdict
    from production.services import get_patok_code, get_patok_name

    date_str = request.GET.get('date', '').strip()
    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = None
    if not selected_date:
        selected_date = timezone.localdate()

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    is_today = (selected_date == today)

    day_start = timezone.make_aware(datetime.combine(selected_date, time.min), tz)
    if is_today:
        day_end = timezone.now()
    else:
        day_end = timezone.make_aware(datetime.combine(selected_date, time(23, 59, 59, 999999)), tz)

    is_uychi_admin = (
        request.user.is_authenticated and
        getattr(request.user, 'role', None) == User.Role.BRANCH_ADMIN and
        getattr(request.user, 'branch', None) == 'UYCHI'
    )

    branch_filter = request.GET.get('branch', '').strip().upper()
    if is_uychi_admin:
        branch_filter = 'UYCHI'
    elif not branch_filter:
        branch_filter = 'ALL'

    view_tab = request.GET.get('tab', 'inspectors').strip().lower()
    if view_tab not in ('inspectors', 'patoks'):
        view_tab = 'inspectors'

    search_q = request.GET.get('q', '').strip()

    # Query all inspection logs for the selected date range
    logs_qs = BoxQualityInspectionLog.objects.filter(
        created_at__range=(day_start, day_end)
    ).select_related(
        'inspector',
        'box__order__customer',
        'box__article__model',
        'box__cutting_batch_item__batch'
    ).order_by('-created_at')

    logs = list(logs_qs)

    # Pre-fetch screen numbers for all boxes in 1 index-scan query
    box_ids = {l.box_id for l in logs if l.box_id}
    box_screens_map = {}
    if box_ids:
        ticket_screens = Ticket.objects.filter(
            box_id__in=box_ids,
            screen_number__isnull=False
        ).values('box_id', 'screen_number').annotate(c=Count('id')).order_by('box_id', '-c')
        for row in ticket_screens:
            bid = row['box_id']
            if bid not in box_screens_map:
                box_screens_map[bid] = row['screen_number']

    inspectors_map = {}
    patoks_map = {}
    all_unique_boxes = set()

    for l in logs:
        insp = l.inspector
        if not insp:
            continue

        b = l.box
        b_screen = box_screens_map.get(b.id) if b else None
        if not b_screen and b and b.assigned_screen:
            b_screen = b.assigned_screen
        if not b_screen and b and b.cutting_batch_item and b.cutting_batch_item.batch and b.cutting_batch_item.batch.assigned_screen:
            b_screen = b.cutting_batch_item.batch.assigned_screen
        if not b_screen and l.inspector:
            b_screen = parse_patok_number(l.inspector.username)
        if not b_screen and l.notes and '[Terminal:' in l.notes:
            import re
            m = re.search(r'\[Terminal:\s*([^\]]+)\]', l.notes)
            if m:
                b_screen = parse_patok_number(m.group(1))

        p_code = get_patok_code(b_screen) if b_screen else "Noma'lum"
        p_name = get_patok_name(b_screen) if b_screen else "Noma'lum Patok"
        p_branch = 'HQ' if (b_screen and b_screen <= 13) else ('UYCHI' if b_screen else (insp.branch or 'HQ'))

        if branch_filter != 'ALL' and p_branch != branch_filter:
            continue

        insp_branch = insp.branch or 'HQ'
        effective_second_sort = l.second_sort_qty or (l.defect_qty if l.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)

        if b:
            all_unique_boxes.add(b.id)

        # 1. Update inspector
        if insp.id not in inspectors_map:
            inspectors_map[insp.id] = {
                'id': insp.id,
                'name': insp.get_full_name() or insp.username,
                'username': insp.username,
                'uid': insp.uid or '',
                'branch': insp_branch,
                'total_inspected': 0,
                'first_sort': 0,
                'second_sort': 0,
                'repair': 0,
                'boxes_set': set(),
                'patoks': defaultdict(lambda: {'patok_code': '', 'patok_name': '', 'screen_number': None, 'total': 0, 'first_sort': 0, 'second_sort': 0, 'repair': 0, 'boxes_count': 0}),
                'recent_logs': [],
            }
        im = inspectors_map[insp.id]
        im['total_inspected'] += l.inspected_qty
        im['first_sort'] += l.first_sort_qty
        im['second_sort'] += effective_second_sort
        im['repair'] += l.repair_qty
        if b:
            im['boxes_set'].add(b.id)

        p_item = im['patoks'][p_code]
        p_item['patok_code'] = p_code
        p_item['patok_name'] = p_name
        p_item['screen_number'] = b_screen
        p_item['total'] += l.inspected_qty
        p_item['first_sort'] += l.first_sort_qty
        p_item['second_sort'] += effective_second_sort
        p_item['repair'] += l.repair_qty
        if b:
            p_item['boxes_count'] += 1

        if len(im['recent_logs']) < 15:
            im['recent_logs'].append({
                'id': l.id,
                'time': timezone.localtime(l.created_at).strftime("%H:%M:%S"),
                'box_code': b.box_code if b else "—",
                'box_number': b.box_number if b else "—",
                'order_number': b.order.order_number if b and b.order else "—",
                'model_name': b.article.name if b and b.article else "—",
                'patok_code': p_code,
                'inspected_qty': l.inspected_qty,
                'first_sort': l.first_sort_qty,
                'second_sort': effective_second_sort,
                'repair': l.repair_qty,
                'action_type': l.get_action_type_display(),
            })

        # 2. Update patok
        if p_code not in patoks_map:
            patoks_map[p_code] = {
                'patok_code': p_code,
                'patok_name': p_name,
                'screen_number': b_screen,
                'branch': p_branch,
                'total_inspected': 0,
                'first_sort': 0,
                'second_sort': 0,
                'repair': 0,
                'boxes_set': set(),
                'inspectors': defaultdict(lambda: {'id': 0, 'name': '', 'uid': '', 'branch': '', 'total': 0, 'first_sort': 0, 'second_sort': 0, 'repair': 0, 'boxes_count': 0}),
                'recent_logs': [],
            }
        pm = patoks_map[p_code]
        pm['total_inspected'] += l.inspected_qty
        pm['first_sort'] += l.first_sort_qty
        pm['second_sort'] += effective_second_sort
        pm['repair'] += l.repair_qty
        if b:
            pm['boxes_set'].add(b.id)

        insp_item = pm['inspectors'][insp.id]
        insp_item['id'] = insp.id
        insp_item['name'] = insp.get_full_name() or insp.username
        insp_item['uid'] = insp.uid or ''
        insp_item['branch'] = insp_branch
        insp_item['total'] += l.inspected_qty
        insp_item['first_sort'] += l.first_sort_qty
        insp_item['second_sort'] += effective_second_sort
        insp_item['repair'] += l.repair_qty
        if b:
            insp_item['boxes_count'] += 1

        if len(pm['recent_logs']) < 15:
            pm['recent_logs'].append({
                'id': l.id,
                'time': timezone.localtime(l.created_at).strftime("%H:%M:%S"),
                'inspector_name': insp.get_full_name() or insp.username,
                'box_code': b.box_code if b else "—",
                'box_number': b.box_number if b else "—",
                'order_number': b.order.order_number if b and b.order else "—",
                'model_name': b.article.name if b and b.article else "—",
                'inspected_qty': l.inspected_qty,
                'first_sort': l.first_sort_qty,
                'second_sort': effective_second_sort,
                'repair': l.repair_qty,
                'action_type': l.get_action_type_display(),
            })

    # Format inspectors list
    inspectors_list = []
    for uid, im in inspectors_map.items():
        if search_q:
            sq = search_q.upper()
            if sq not in im['name'].upper() and sq not in im['uid'] and sq not in im['username'].upper():
                continue

        tot = im['total_inspected']
        fs = im['first_sort']
        rep = im['repair']
        ss = im['second_sort']

        patok_items = sorted(im['patoks'].values(), key=lambda x: x['total'], reverse=True)

        inspectors_list.append({
            'id': im['id'],
            'name': im['name'],
            'username': im['username'],
            'uid': im['uid'],
            'branch': im['branch'],
            'total_inspected': tot,
            'boxes_count': len(im['boxes_set']),
            'first_sort': fs,
            'second_sort': ss,
            'repair': rep,
            'first_sort_pct': round((fs / tot * 100), 1) if tot > 0 else 0.0,
            'repair_pct': round((rep / tot * 100), 1) if tot > 0 else 0.0,
            'second_sort_pct': round((ss / tot * 100), 1) if tot > 0 else 0.0,
            'patoks': patok_items,
            'patoks_count': len(patok_items),
            'recent_logs': im['recent_logs'],
        })

    inspectors_list.sort(key=lambda x: x['total_inspected'], reverse=True)

    # Format patoks list
    patoks_list = []
    for pcode, pm in patoks_map.items():
        if branch_filter != 'ALL' and pm['branch'] != branch_filter:
            continue
        if search_q:
            sq = search_q.upper()
            if sq not in pm['patok_code'].upper() and sq not in pm['patok_name'].upper():
                continue

        tot = pm['total_inspected']
        fs = pm['first_sort']
        rep = pm['repair']
        ss = pm['second_sort']

        insp_items = sorted(pm['inspectors'].values(), key=lambda x: x['total'], reverse=True)

        patoks_list.append({
            'patok_code': pm['patok_code'],
            'patok_name': pm['patok_name'],
            'screen_number': pm['screen_number'],
            'branch': pm['branch'],
            'total_inspected': tot,
            'boxes_count': len(pm['boxes_set']),
            'first_sort': fs,
            'second_sort': ss,
            'repair': rep,
            'first_sort_pct': round((fs / tot * 100), 1) if tot > 0 else 0.0,
            'repair_pct': round((rep / tot * 100), 1) if tot > 0 else 0.0,
            'second_sort_pct': round((ss / tot * 100), 1) if tot > 0 else 0.0,
            'inspectors': insp_items,
            'inspectors_count': len(insp_items),
            'recent_logs': pm['recent_logs'],
        })

    # Sort patoks: by screen_number (1..40) if available, or total inspected
    patoks_list.sort(key=lambda x: (x['screen_number'] or 999, -x['total_inspected']))

    # Grand Totals and KPIs
    total_inspected_sum = sum(x['total_inspected'] for x in inspectors_list)
    total_first_sort_sum = sum(x['first_sort'] for x in inspectors_list)
    total_second_sort_sum = sum(x['second_sort'] for x in inspectors_list)
    total_repair_sum = sum(x['repair'] for x in inspectors_list)

    kpis = {
        'total_inspected_qty': total_inspected_sum,
        'total_first_sort_qty': total_first_sort_sum,
        'total_second_sort_qty': total_second_sort_sum,
        'total_repair_qty': total_repair_sum,
        'total_boxes_count': len(all_unique_boxes),
        'active_inspectors': len(inspectors_list),
        'active_patoks': len(patoks_list),
        'first_sort_pct': round((total_first_sort_sum / total_inspected_sum * 100), 1) if total_inspected_sum > 0 else 0.0,
        'repair_pct': round((total_repair_sum / total_inspected_sum * 100), 1) if total_inspected_sum > 0 else 0.0,
        'second_sort_pct': round((total_second_sort_sum / total_inspected_sum * 100), 1) if total_inspected_sum > 0 else 0.0,
    }

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)

    context = {
        'selected_date': selected_date,
        'selected_date_str': selected_date.isoformat(),
        'prev_date_str': prev_date.isoformat(),
        'next_date_str': next_date.isoformat(),
        'is_today': is_today,
        'is_uychi_admin': is_uychi_admin,
        'branch_filter': branch_filter,
        'view_tab': view_tab,
        'search_q': search_q,
        'inspectors_list': inspectors_list,
        'patoks_list': patoks_list,
        'kpis': kpis,
    }

    return render(request, 'production/sewing_statistics_otk.html', context)




@manager_or_superadmin_required
def sewing_statistics_daily_report_view(request):
    """
    Kunlik Ishlab Chiqarish Hisoboti (18:00 Monitoringi):
    - Kosonsoy (K1..K13) va Uychi (U1..U21 / U40 gacha) patoklari
    - 5 ta ustun:
      1. Паток №
      2. Маҳсулот & Дазмол (Дазмол / Нархлар шаблони)
      3. Давомот
      4. Операционка (Аниқлик)
      5. Паток қуввати (%)
    - Telegramga jo'natish (Test) tugmalari (Koson: -4425170756, Uychi: -3903019133)
    - Rasmni ko'rish / yuklab olish
    - Oylik Excel hisobotini yuklab olish
    """
    from production.daily_image_reports import get_branch_daily_report_data

    date_str = request.GET.get('date', '').strip()
    active_tab = request.GET.get('tab', 'koson').lower()
    if active_tab not in ['koson', 'uychi']:
        active_tab = 'koson'

    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    is_today = (selected_date == timezone.localdate())

    koson_data = get_branch_daily_report_data('HQ', selected_date)
    uychi_data = get_branch_daily_report_data('UYCHI', selected_date)

    current_data = koson_data if active_tab == 'koson' else uychi_data

    context = {
        'selected_date': selected_date,
        'selected_date_str': selected_date.isoformat(),
        'prev_date_str': prev_date.isoformat(),
        'next_date_str': next_date.isoformat(),
        'is_today': is_today,
        'active_tab': active_tab,
        'koson_data': koson_data,
        'uychi_data': uychi_data,
        'current_data': current_data,
        'current_year': selected_date.year,
        'current_month': selected_date.month,
    }
    return render(request, 'production/sewing_statistics_daily_report.html', context)


@manager_or_superadmin_required
@require_POST
def api_send_branch_daily_report(request):
    """
    Telegramga filial hisobot rasmini jo'natish (Test yoki qo'lda jo'natish):
    - branch: 'HQ' (Kosonsoy) yoki 'UYCHI' yoki 'ALL'
    """
    from production.daily_image_reports import send_single_branch_report, send_daily_1800_reports

    branch = request.POST.get('branch', 'HQ').upper()
    date_str = request.POST.get('date', '').strip()
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    if branch == 'ALL':
        res = send_daily_1800_reports(target_date)
        success = res.get('success', False)
        msg = f"Ikkala filial hisoboti jo'natildi. Koson: {res['HQ'].get('message')}, Uychi: {res['UYCHI'].get('message')}"
    else:
        res = send_single_branch_report(branch, target_date)
        success = res.get('success', False)
        msg = res.get('message', '')

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
        return JsonResponse({'success': success, 'message': msg})

    if success:
        messages.success(request, f"✅ {msg}")
    else:
        messages.error(request, f"⚠️ {msg}")

    tab_name = 'koson' if branch in ['HQ', 'KOSON'] else 'uychi'
    return redirect(f"/sewing-statistics/daily-report/?date={target_date.isoformat()}&tab={tab_name}")


@manager_or_superadmin_required
def sewing_statistics_daily_report_image_preview(request):
    """
    Hisobot rasmini brauzerda jonli ko'rish (Preview) yoki yuklab olish (Download):
    - branch: 'HQ' yoki 'UYCHI'
    - date: YYYY-MM-DD
    """
    from django.http import HttpResponse
    from production.daily_image_reports import get_branch_daily_report_data, generate_branch_report_image

    branch = request.GET.get('branch', 'HQ').upper()
    date_str = request.GET.get('date', '').strip()
    download = request.GET.get('download') == '1'

    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    b_code = 'HQ' if branch in ['HQ', 'KOSON'] else 'UYCHI'
    data = get_branch_daily_report_data(b_code, target_date)
    img_buf = generate_branch_report_image(data)

    resp = HttpResponse(img_buf.getvalue(), content_type='image/png')
    filename = f"Hisobot_{b_code}_{target_date.isoformat()}.png"
    disposition = 'attachment' if download else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    return resp


@manager_or_superadmin_required
def export_monthly_daily_report_excel(request):
    """
    Tanlangan oy bo'yicha barcha kunlik ishlab chiqarish ko'rsatkichlarini Excel (.xlsx) qilib berish.
    """
    from django.http import HttpResponse
    from production.excel_reports import generate_monthly_patok_production_excel

    try:
        year = int(request.GET.get('year', timezone.localdate().year))
        month = int(request.GET.get('month', timezone.localdate().month))
        if month < 1 or month > 12:
            month = timezone.localdate().month
    except (ValueError, TypeError):
        year = timezone.localdate().year
        month = timezone.localdate().month

    excel_buf = generate_monthly_patok_production_excel(year, month)
    filename = f"TerryJar_Oylik_Tikim_Hisoboti_{year}_{month:02d}.xlsx"

    resp = HttpResponse(
        excel_buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
