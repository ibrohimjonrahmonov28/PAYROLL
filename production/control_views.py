import json
import re
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Sum, Q
from accounts.models import User
from production.models import Box, BoxQualityInspectionLog, Ticket, DefectReason


def _get_active_defect_reasons():
    """
    Faol nuqson sabablari shablonlarini qaytaradi.
    Agar baza bo'sh bo'lsa, standart shablonlarni avtomatik yaratadi.
    """
    if not DefectReason.objects.exists():
        default_templates = [
            {'code': 'BRK-001', 'name': "Chok qiyshiq yoki to'lqinsimon", 'category': DefectReason.Category.SEWING, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 1},
            {'code': 'BRK-002', 'name': "Chok qadami tashlab ketgan (propusk)", 'category': DefectReason.Category.SEWING, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 2},
            {'code': 'BRK-003', 'name': "Ip uzilgan yoki tortilib qolgan", 'category': DefectReason.Category.SEWING, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 3},
            {'code': 'BRK-004', 'name': "Detallar simmetriyasi buzilgan", 'category': DefectReason.Category.SEWING, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 4},
            {'code': 'BRK-005', 'name': "Cho'ntak yoki yoqa qiyshiq o'rnatilgan", 'category': DefectReason.Category.SEWING, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 5},
            {'code': 'BRK-006', 'name': "Mato ignadan yoki pichoqdan teshilgan", 'category': DefectReason.Category.FABRIC, 'defect_type': DefectReason.DefectType.NON_REPAIRABLE, 'order': 6},
            {'code': 'BRK-007', 'name': "Mato rangida dog' yoki yog' izlari", 'category': DefectReason.Category.IRONING, 'defect_type': DefectReason.DefectType.NON_REPAIRABLE, 'order': 7},
            {'code': 'BRK-008', 'name': "O'lcham / Razmer mos kelmaydi", 'category': DefectReason.Category.CUTTING, 'defect_type': DefectReason.DefectType.NON_REPAIRABLE, 'order': 8},
            {'code': 'BRK-009', 'name': "Furnitura / Tugma noto'g'ri qadalgan", 'category': DefectReason.Category.ACCESSORY, 'defect_type': DefectReason.DefectType.REPAIRABLE, 'order': 9},
            {'code': 'BRK-010', 'name': "Mato to'qilishi yoki rang tuslanishi", 'category': DefectReason.Category.FABRIC, 'defect_type': DefectReason.DefectType.NON_REPAIRABLE, 'order': 10},
            {'code': 'BRK-011', 'name': "Boshqa tashqi nuqsonlar (tikishga aloqador emas)", 'category': DefectReason.Category.OTHER, 'defect_type': DefectReason.DefectType.NON_REPAIRABLE, 'order': 11},
        ]
        for item in default_templates:
            if not DefectReason.objects.filter(code=item['code']).exists():
                DefectReason.objects.create(**item)

    reasons = DefectReason.objects.filter(is_active=True).order_by('order', 'id')
    return [
        {
            'id': r.id,
            'code': r.code,
            'name': r.name,
            'category': r.category,
            'category_display': r.get_category_display(),
            'defect_type': r.defect_type,
            'description': r.description or '',
        }
        for r in reasons
    ]


def _is_control_authorized(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role in [User.Role.CONTROL, User.Role.SUPER_ADMIN, User.Role.ADMIN]


@login_required
def control_home_view(request):
    """
    Sifat Nazorati (OTK / Control) planshet interfeysi.
    Faqat CONTROL, SUPER_ADMIN va ADMIN foydalanuvchilariga ruxsat beriladi.
    """
    if not _is_control_authorized(request.user):
        return redirect('root_login')

    today = timezone.localdate()
    today_logs_qs = BoxQualityInspectionLog.objects.filter(created_at__date=today)

    today_inspections = today_logs_qs.select_related(
        'box', 'box__order', 'box__article', 'inspector'
    ).order_by('-created_at')[:25]

    # Faqat unikal qutilar soni (bir quti 2-3 marta ta'mirga kirsa ham 1 ta quti hisoblanadi!)
    today_boxes_count = today_logs_qs.values('box_id').distinct().count()

    # Bugun 1-sort bo'lgan barcha donalar
    today_first_sort = today_logs_qs.aggregate(s=Sum('first_sort_qty'))['s'] or 0

    # Bugun 2-sort bo'lgan barcha donalar (birlamchi 2-sort + ta'mirdan chiqqan nuqsonli/2-sort ishlar)
    today_second_sort = (today_logs_qs.aggregate(s=Sum('second_sort_qty'))['s'] or 0)
    legacy_defects = today_logs_qs.filter(
        action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
        second_sort_qty=0
    ).aggregate(d=Sum('defect_qty'))['d'] or 0
    today_second_sort += legacy_defects

    return render(request, 'production/control_home.html', {
        'inspector': request.user,
        'today_inspections': today_inspections,
        'today_count': today_boxes_count,
        'today_boxes_count': today_boxes_count,
        'today_first_sort': today_first_sort,
        'today_second_sort': today_second_sort,
        'today_defects': today_second_sort,
        'today_date': today,
    })


@require_http_methods(["GET", "POST"])
def control_box_lookup_api(request):
    """
    Skanerlangan QR/shtrix-kod bo'yicha qutini qidirib topish API.
    Kiritilgan kod CONTROL:BOX_CODE, BOX:BOX_CODE, yoki shunchaki BOX_CODE bo'lishi mumkin.
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            raw_code = data.get('code', '')
        except Exception:
            raw_code = request.POST.get('code', '')
    else:
        raw_code = request.GET.get('code', '')

    raw_code = str(raw_code).strip()
    if not raw_code:
        return JsonResponse({'status': 'ERROR', 'message': "Iltimos, quti kodini kiriting yoki skaner qiling!"}, status=400)

    # Skaner klaviatura xatosini to'g'rilash (+ ni - ga o'girish, masalan CONTROL+A9+1 yoki A9+1)
    if '+' in raw_code:
        raw_code = re.sub(r'^(CONTROL|BOX|TICKET|TK)\+', r'\1:', raw_code, flags=re.IGNORECASE)
        raw_code = raw_code.replace('+', '-')

    # 0. Tikuvchi bilet kodi prefikslari (TICKET:, TK-) bo'lsa darhol bloklash
    raw_upper = raw_code.upper()
    if raw_upper.startswith('TICKET:') or raw_upper.startswith('TK-'):
        return JsonResponse({
            'status': 'PERMISSION_DENIED',
            'message': "Sizda bunday huquq yo'q! Bu tikuvchining operatsiya stikeri. Sifat nazorati (OTK) uchun faqat qutidagi CONTROL stikerini yoki Quti ID sini skanerlang!"
        }, status=403)

    # Prefikslarni tozalash (CONTROL:, BOX:, QUTI #, QUTI#, QUTI:, #)
    cleaned = raw_code.upper()
    for prefix in ['CONTROL:', 'BOX:', 'QUTI #', 'QUTI#', 'QUTI:', '#']:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Bazadan qidirish:
    # 1. Aniq box_code moslik
    box = None
    if len(cleaned) == 8 and cleaned.isalnum():
        box = Box.objects.filter(box_code=cleaned).select_related('order', 'article').first()

    # 2. Agar son bo'lsa ID yoki box_number
    if not box and cleaned.isdigit():
        num = int(cleaned)
        box = Box.objects.filter(id=num).select_related('order', 'article').first()
        if not box:
            box = Box.objects.filter(box_number=num).select_related('order', 'article').first()

    # 3. Katta-kichik harf farqsiz box_code (masalan: a9-1, A9-1)
    if not box:
        box = Box.objects.filter(box_code__iexact=cleaned).select_related('order', 'article').first()

    # 4. Agar quti topilmagan bo'lsa, lekin kiritilgan kod biletga (operatsiyaga) tegishli bo'lsa:
    # Kontrolchi operatsiya stikerini ura olmaydi — faqat CONTROL stiker yoki quti ID!
    if not box:
        if Ticket.objects.filter(Q(stiker_code__iexact=cleaned) | Q(ticket_code__iexact=cleaned)).exists():
            return JsonResponse({
                'status': 'PERMISSION_DENIED',
                'message': "Sizda bunday huquq yo'q! Bu tikuvchining operatsiya stikeri. Sifat nazorati (OTK) uchun faqat qutidagi CONTROL stikerini yoki Quti ID sini skanerlang!"
            }, status=403)

    if not box:
        return JsonResponse({
            'status': 'NOT_FOUND',
            'message': f"«{raw_code}» kodi bo'yicha quti topilmadi! Qaytadan tekshirib ko'ring."
        }, status=404)

    art = box.target_article
    image_url = None
    if art and art.image:
        try:
            image_url = art.image.url
        except Exception:
            image_url = None

    is_repair_mode = (box.controlled_repair_qty > 0)
    is_completed = (box.is_controlled and box.controlled_repair_qty == 0)
    if is_repair_mode:
        current_mode = 'REPAIR_RETURN'
    elif is_completed:
        current_mode = 'COMPLETED'
    else:
        current_mode = 'INITIAL'

    # Qutiga tegishli barcha biletlar va ularning tikuvchilari
    tickets_qs = box.tickets.exclude(status=Ticket.Status.CANCELLED).select_related(
        'article_operation__operation', 'worker'
    ).order_by('article_operation__sequence', 'id')

    tickets_data = []
    missing_operations = []
    for t in tickets_qs:
        ao = t.article_operation
        op_name = ao.operation.name if ao and ao.operation else f"Operatsiya #{t.id}"
        op_seq = ao.sequence if ao else 1

        is_scanned = (t.status == Ticket.Status.SCANNED and t.worker_id is not None)
        worker_info = None
        if is_scanned and t.worker:
            w_uid = getattr(t.worker, 'worker_id', None) or (getattr(t.worker.user, 'uid', None) if getattr(t.worker, 'user', None) else None) or f"W-{t.worker.id}"
            w_name = getattr(t.worker, 'full_name', None) or f"{getattr(t.worker, 'first_name', '')} {getattr(t.worker, 'last_name', '')}".strip() or f"Ishchi #{t.worker.id}"
            worker_info = {
                'id': t.worker.id,
                'uid': w_uid,
                'full_name': w_name,
                'screen_number': t.screen_number,
                'scanned_at': timezone.localtime(t.scanned_at).strftime("%d.%m %H:%M") if t.scanned_at else None,
            }
        else:
            missing_operations.append({
                'ticket_id': t.id,
                'ticket_code': t.ticket_code,
                'stiker_code': t.stiker_code or "",
                'operation_name': op_name,
                'sequence': op_seq,
            })

        tickets_data.append({
            'id': t.id,
            'ticket_code': t.ticket_code,
            'stiker_code': t.stiker_code or "",
            'sequence': op_seq,
            'operation_name': op_name,
            'is_scanned': is_scanned,
            'worker': worker_info,
        })

    total_tickets = len(tickets_data)
    missing_count = len(missing_operations)
    scanned_count = total_tickets - missing_count
    all_tickets_scanned = (missing_count == 0 and total_tickets > 0)

    return JsonResponse({
        'status': 'OK',
        'box': {
            'id': box.id,
            'box_code': box.box_code,
            'box_number': box.box_number,
            'order_number': box.order.order_number if box.order else "—",
            'order_client': box.order.client_name if (box.order and box.order.client_name) else (box.order.customer.name if (box.order and box.order.customer) else ""),
            'article_code': art.code if art else "N/A",
            'article_name': art.name if art else "Model",
            'image_url': image_url,
            'razmer': box.razmer or "—",
            'pastal_code': box.pastal_code or "—",
            'quantity': box.quantity,
            'is_controlled': box.is_controlled,
            'controlled_first_sort_qty': box.controlled_first_sort_qty,
            'controlled_second_sort_qty': box.controlled_second_sort_qty,
            'controlled_repair_qty': box.controlled_repair_qty,
            'controlled_defect_qty': box.controlled_defect_qty,
            'mode': current_mode,
            'tickets': tickets_data,
            'missing_operations': missing_operations,
            'total_tickets_count': total_tickets,
            'scanned_tickets_count': scanned_count,
            'missing_tickets_count': missing_count,
            'all_tickets_scanned': all_tickets_scanned,
            'defect_reasons': _get_active_defect_reasons(),
        },
        'defect_reasons': _get_active_defect_reasons(),
    })


@require_http_methods(["POST"])
def control_submit_inspection_api(request):
    """
    Sifat nazorati natijasini saqlash va tasdiqlash API.
    Birlamchi tekshiruv (INITIAL) yoki Ta'mirdan qaytish (REPAIR_RETURN).
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    box_id = data.get('box_id')
    mode = data.get('mode', 'INITIAL')

    box = get_object_or_404(Box, id=box_id)

    if mode == 'INITIAL':
        # Barcha operatsiyalar egasi borligini qat'iy tekshirish
        unscanned_tickets = box.tickets.exclude(
            status=Ticket.Status.CANCELLED
        ).filter(
            Q(status=Ticket.Status.PENDING) | Q(worker__isnull=True)
        ).select_related('article_operation__operation')

        if unscanned_tickets.exists():
            missing_names = [
                t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else f"Operatsiya #{t.id}"
                for t in unscanned_tickets
            ]
            unique_missing = list(dict.fromkeys(missing_names))
            missing_str = ", ".join(f"«{m}»" for m in unique_missing)
            return JsonResponse({
                'status': 'UNSCANNED_OPERATIONS',
                'message': (
                    f"Qabul qilib bo'lmaydi! Ushbu qutining quyidagi operatsiyasi(lari) hali skaner qilinmagan (egasi yo'q): "
                    f"{missing_str}. "
                    f"Chunki agar ushbu operatsiyadan brak chiqsa, hech qaysi tikuvchini ayblab bo'lmaydi! "
                    f"Avval barcha stikerlar skanerlanishi shart."
                ),
                'missing_operations': unique_missing,
            }, status=400)

        # Birlamchi tekshiruv
        try:
            total_qty = int(data.get('total_qty', box.quantity))
            second_sort = int(data.get('second_sort_qty', 0))
            repair_qty = int(data.get('repair_qty', 0))
        except (ValueError, TypeError):
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar to'g'ri butun son formatida bo'lishi kerak!"}, status=400)

        if total_qty < 1:
            return JsonResponse({'status': 'ERROR', 'message': "Qutidagi umumiy son kamida 1 dona bo'lishi shart!"}, status=400)
        if second_sort < 0 or repair_qty < 0:
            return JsonResponse({'status': 'ERROR', 'message': "Sort yoki ta'mir soni manfiy bo'lishi mumkin emas!"}, status=400)
        if second_sort + repair_qty > total_qty:
            return JsonResponse({
                'status': 'ERROR', 
                'message': f"2-sort ({second_sort}) va Ta'mir ({repair_qty}) yig'indisi jami sondan ({total_qty}) oshib ketishi mumkin emas!"
            }, status=400)

        first_sort = total_qty - second_sort - repair_qty
        is_closed = (repair_qty == 0)

        # Qutini yangilash
        box.quantity = total_qty
        box.controlled_first_sort_qty = first_sort
        box.controlled_second_sort_qty = second_sort
        box.controlled_repair_qty = repair_qty
        box.controlled_defect_qty = 0
        box.is_controlled = is_closed
        box.status = Box.Status.COMPLETED if is_closed else Box.Status.IN_PROGRESS
        box.controlled_at = timezone.now()
        box.controlled_by = request.user
        box.save(update_fields=[
            'quantity', 'controlled_first_sort_qty', 'controlled_second_sort_qty',
            'controlled_repair_qty', 'controlled_defect_qty', 'is_controlled',
            'status', 'controlled_at', 'controlled_by'
        ])

        # Jurnalga yozish
        notes_lines = []
        user_notes = data.get('notes', '')
        if user_notes and str(user_notes).strip():
            notes_lines.append(str(user_notes).strip())

        defect_details = data.get('defect_details', [])
        if defect_details and isinstance(defect_details, list):
            notes_lines.append("--- 2-SORT SABABLARI ---")
            for idx, d in enumerate(defect_details, 1):
                item_idx = d.get('item_number', idx)
                ops = ", ".join(d.get('operation_names', [])) if d.get('operation_names') else "Tikishga aloqador emas"
                reasons = ", ".join(d.get('reason_names', [])) if d.get('reason_names') else "Ko'rsatilmagan"
                workers = ", ".join(d.get('workers', [])) if d.get('workers') else ""
                worker_part = f" (Tikuvchi: {workers})" if workers else ""
                custom_note = f" [{d.get('notes')}]" if d.get('notes') else ""
                notes_lines.append(f"• #{item_idx} dona: Sabab: [{reasons}] | Operatsiya: [{ops}{worker_part}]{custom_note}")

        repair_details = data.get('repair_details', [])
        if repair_details and isinstance(repair_details, list):
            notes_lines.append("--- TA'MIR OPERATSIYALARI ---")
            for idx, r in enumerate(repair_details, 1):
                item_idx = r.get('item_number', idx)
                ops = ", ".join(r.get('operation_names', [])) if r.get('operation_names') else "Noma'lum operatsiya"
                workers = ", ".join(r.get('workers', [])) if r.get('workers') else ""
                worker_part = f" (Tikuvchi: {workers})" if workers else ""
                custom_note = f" [{r.get('notes')}]" if r.get('notes') else ""
                notes_lines.append(f"• #{item_idx} dona: Operatsiya: [{ops}{worker_part}]{custom_note}")

        final_notes = "\n".join(notes_lines)

        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=request.user,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=total_qty,
            first_sort_qty=first_sort,
            second_sort_qty=second_sort,
            repair_qty=repair_qty,
            defect_qty=0,
            notes=final_notes
        )

        if is_closed:
            msg = f"Quti #{box.box_number} yopildi: {first_sort} ta 1-sort, {second_sort} ta 2-sort. Mahsulot qabul qilindi va Upakovkaga topshiriladi!"
        else:
            msg = f"Quti #{box.box_number} ta'mirga yuborildi: {repair_qty} ta ta'mirga ketdi. Quti qutisi bilan ta'mir bo'limiga qaytariladi."

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': repair_qty,
            'box': {
                'box_code': box.box_code,
                'box_number': box.box_number,
                'first_sort': box.controlled_first_sort_qty,
                'second_sort': box.controlled_second_sort_qty,
                'repair': box.controlled_repair_qty,
                'defect': box.controlled_defect_qty,
                'is_repair_active': (box.controlled_repair_qty > 0),
                'is_closed': is_closed,
            }
        })

    elif mode == 'REPAIR_RETURN':
        # Ta'mirdan qaytib kelgan
        repair_in_hand = box.controlled_repair_qty
        if repair_in_hand <= 0:
            return JsonResponse({'status': 'ERROR', 'message': "Ushbu qutida ta'mirga ketgan ishlar mavjud emas!"}, status=400)

        try:
            defect_qty = int(data.get('defect_qty', data.get('second_sort_qty', 0)))
            re_repair_qty = int(data.get('re_repair_qty', data.get('repair_qty', 0)))
        except (ValueError, TypeError):
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar to'g'ri formatda kiritilishi shart!"}, status=400)

        if defect_qty < 0 or re_repair_qty < 0:
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar manfiy bo'lishi mumkin emas!"}, status=400)
        if defect_qty + re_repair_qty > repair_in_hand:
            return JsonResponse({
                'status': 'ERROR', 
                'message': f"2-Sort ({defect_qty}) va Qayta ta'mir ({re_repair_qty}) yig'indisi ta'mirdagi sondan ({repair_in_hand}) ko'p bo'lishi mumkin emas!"
            }, status=400)

        fixed_qty = repair_in_hand - defect_qty - re_repair_qty
        is_closed = (re_repair_qty == 0)

        # Qutini yangilash (Brak bilan 2-sort bitta narsa - ikkalasi ham 2-sort)
        box.controlled_first_sort_qty += fixed_qty
        box.controlled_second_sort_qty += defect_qty
        box.controlled_defect_qty += defect_qty
        box.controlled_repair_qty = re_repair_qty
        box.is_controlled = is_closed
        box.status = Box.Status.COMPLETED if is_closed else Box.Status.IN_PROGRESS
        box.controlled_at = timezone.now()
        box.controlled_by = request.user
        box.save(update_fields=[
            'controlled_first_sort_qty', 'controlled_second_sort_qty',
            'controlled_defect_qty', 'controlled_repair_qty',
            'is_controlled', 'status', 'controlled_at', 'controlled_by'
        ])

        # Jurnalga yozish
        notes_lines = []
        user_notes = data.get('notes', '')
        if user_notes and str(user_notes).strip():
            notes_lines.append(str(user_notes).strip())

        defect_details = data.get('defect_details', [])
        if defect_details and isinstance(defect_details, list):
            notes_lines.append("--- TA'MIRDAN 2-SORTGA O'TGANLAR ---")
            for idx, d in enumerate(defect_details, 1):
                item_idx = d.get('item_number', idx)
                ops = ", ".join(d.get('operation_names', [])) if d.get('operation_names') else "Tikishga aloqador emas"
                reasons = ", ".join(d.get('reason_names', [])) if d.get('reason_names') else "Ko'rsatilmagan"
                workers = ", ".join(d.get('workers', [])) if d.get('workers') else ""
                worker_part = f" (Tikuvchi: {workers})" if workers else ""
                custom_note = f" [{d.get('notes')}]" if d.get('notes') else ""
                notes_lines.append(f"• #{item_idx} dona: Sabab: [{reasons}] | Operatsiya: [{ops}{worker_part}]{custom_note}")

        repair_details = data.get('repair_details', [])
        if repair_details and isinstance(repair_details, list):
            notes_lines.append("--- QAYTA TA'MIRGA YUBORILGANLAR ---")
            for idx, r in enumerate(repair_details, 1):
                item_idx = r.get('item_number', idx)
                ops = ", ".join(r.get('operation_names', [])) if r.get('operation_names') else "Noma'lum operatsiya"
                workers = ", ".join(r.get('workers', [])) if r.get('workers') else ""
                worker_part = f" (Tikuvchi: {workers})" if workers else ""
                custom_note = f" [{r.get('notes')}]" if r.get('notes') else ""
                notes_lines.append(f"• #{item_idx} dona: Operatsiya: [{ops}{worker_part}]{custom_note}")

        final_notes = "\n".join(notes_lines)

        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=request.user,
            action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
            inspected_qty=repair_in_hand,
            first_sort_qty=fixed_qty,
            second_sort_qty=defect_qty,
            repair_qty=re_repair_qty,
            defect_qty=defect_qty,
            notes=final_notes
        )

        if is_closed:
            msg = f"Quti #{box.box_number} ta'miri yakunlandi va quti to'liq yopildi: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta 2-sort. Mahsulot Upakovkaga topshiriladi!"
        else:
            msg = f"Quti #{box.box_number}: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta 2-sort, {re_repair_qty} ta qayta ta'mirda qoldi."

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': re_repair_qty,
            'box': {
                'box_code': box.box_code,
                'box_number': box.box_number,
                'first_sort': box.controlled_first_sort_qty,
                'second_sort': box.controlled_second_sort_qty,
                'repair': box.controlled_repair_qty,
                'defect': box.controlled_second_sort_qty,
                'is_repair_active': (box.controlled_repair_qty > 0),
                'is_closed': is_closed,
            }
        })

    else:
        return JsonResponse({'status': 'ERROR', 'message': "Noma'lum tekshiruv rejimi!"}, status=400)


@require_http_methods(["GET"])
def control_recent_inspections_api(request):
    """
    Bugungi oxirgi tekshirilgan qutilar jurnali va umumiy statistika API.
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)

    today = timezone.localdate()
    today_logs_qs = BoxQualityInspectionLog.objects.filter(created_at__date=today)

    logs = today_logs_qs.select_related(
        'box', 'box__order', 'box__article', 'inspector'
    ).order_by('-created_at')[:25]

    # Faqat unikal qutilar soni (bir quti 2-3 marta ta'mirga kirsa ham 1 ta quti hisoblanadi!)
    today_boxes_count = today_logs_qs.values('box_id').distinct().count()

    # Bugun 1-sort bo'lgan barcha donalar
    today_first_sort = today_logs_qs.aggregate(s=Sum('first_sort_qty'))['s'] or 0

    # Bugun 2-sort bo'lgan barcha donalar (birlamchi 2-sort + ta'mir natijasidagi 2-sort/brak)
    today_second_sort = (today_logs_qs.aggregate(s=Sum('second_sort_qty'))['s'] or 0)
    legacy_defects = today_logs_qs.filter(
        action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
        second_sort_qty=0
    ).aggregate(d=Sum('defect_qty'))['d'] or 0
    today_second_sort += legacy_defects

    data = []
    for l in logs:
        effective_second_sort = l.second_sort_qty or (l.defect_qty if l.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN else 0)
        data.append({
            'id': l.id,
            'box_number': l.box.box_number,
            'box_code': l.box.box_code,
            'order_number': l.box.order.order_number if l.box.order else "—",
            'article_code': l.box.target_article.code if l.box.target_article else "—",
            'action_type': l.action_type,
            'action_type_display': l.get_action_type_display(),
            'first_sort': l.first_sort_qty,
            'second_sort': effective_second_sort,
            'repair': l.repair_qty,
            'defect': effective_second_sort,
            'inspector': l.inspector.get_full_name() or l.inspector.username if l.inspector else "—",
            'time': timezone.localtime(l.created_at).strftime("%H:%M:%S"),
        })

    return JsonResponse({
        'status': 'OK',
        'count': today_boxes_count,
        'today_boxes_count': today_boxes_count,
        'today_first_sort': today_first_sort,
        'today_second_sort': today_second_sort,
        'today_defects': today_second_sort,
        'logs': data
    })


def control_download_apk_view(request):
    """
    Sifat Nazorati (OTK / Control) plansheti uchun APK dasturini yuklab berish.
    """
    import os
    from django.conf import settings
    from django.http import FileResponse, Http404

    current_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(current_dir, 'terryjar_control.apk'),
        os.path.join(settings.BASE_DIR, 'production', 'terryjar_control.apk'),
        os.path.join(settings.BASE_DIR, 'static', 'apk', 'terryjar_control.apk'),
        os.path.join(settings.BASE_DIR, 'staticfiles', 'apk', 'terryjar_control.apk'),
        os.path.join(settings.BASE_DIR, 'android_control', 'terryjar_control.apk'),
        os.path.join(settings.BASE_DIR, 'media', 'terryjar_control.apk'),
    ]

    for p in candidate_paths:
        if os.path.isfile(p):
            file_handle = open(p, 'rb')
            response = FileResponse(
                file_handle,
                as_attachment=True,
                filename='terryjar_control.apk',
                content_type='application/vnd.android.package-archive'
            )
            response['Content-Length'] = os.path.getsize(p)
            return response

    raise Http404("APK fayli topilmadi.")

