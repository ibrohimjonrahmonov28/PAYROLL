import json
import re
from datetime import datetime, time
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.utils import timezone
from django.db.models import Sum, Q
from accounts.models import User, Worker
from production.models import Box, BoxQualityInspectionLog, Ticket, DefectReason, ControlSetting
from production.terminal_views import extract_worker_code


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
    """
    Planshet / Patok foydalanuvchisi avtorizatsiyasi.
    Planshetlar oldindan patok akkaunti bilan kirib qo'yiladi.
    """
    return user.is_authenticated


def _get_active_operator(request):
    """
    Sessiyadagi faol operator (ishchi).
    Yangi kun boshlangan bo'lsa (sana o'zgargan bo'lsa), sessiya avtomatik tozalanadi!
    """
    today_str = str(timezone.localdate())
    op = request.session.get('control_operator')
    if not op:
        return None
    if op.get('login_date') != today_str:
        request.session.pop('control_operator', None)
        request.session.modified = True
        return None
    return op


def _get_scoped_inspection_logs_qs(request, operator=None, target_date=None):
    """
    Sifat nazorati jurnallarini (BoxQualityInspectionLog) faqat joriy foydalanuvchi
    yoki faol operator bo'yicha filterlaydi:
    - Agar faol operator (operator dict) bo'lsa: shu operatorning bugungi ishlari.
    - Agar faol operator bo'lmasa: faqat request.user ning o'zi bevosita qilgan ishlari
      (boshqa xodim birkasi bilan qilinmagan) hisoblanadi.
    - Yangi kun boshlanishida yoki birorta ish tekshirilmaganda bo'sh QuerySet qaytadi (0 ga tushadi).
    """
    if target_date is None:
        target_date = timezone.localdate()

    current_tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(target_date, time.min), current_tz)
    day_end = timezone.make_aware(datetime.combine(target_date, time.max), current_tz)

    base_qs = BoxQualityInspectionLog.objects.filter(
        created_at__gte=day_start,
        created_at__lte=day_end
    )

    if operator:
        op_user_id = operator.get('user_id')
        op_worker_id = operator.get('worker_id')
        op_name = operator.get('full_name')
        cond = Q()
        if op_user_id:
            cond |= Q(inspector_id=op_user_id)
        if op_worker_id:
            cond |= Q(notes__icontains=str(op_worker_id))
        if op_name:
            cond |= Q(notes__icontains=f"Nazoratchi: {op_name}")
        return base_qs.filter(cond)
    else:
        # Operator sessiyasi bo'lmasa:
        # Faqat joriy tizimga kirgan foydalanuvchi (request.user) ning to'g'ridan-to'g'ri ishlari
        # (boshqa xodimlar birkasi bilan qilinmagan)
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            return base_qs.filter(inspector=request.user).exclude(notes__icontains="[Nazoratchi:")
        return base_qs.none()


def _get_today_stats(request, operator=None):
    """
    Bugun tekshirilgan ishlar (donalar) soni:
    - 1-sort: yashilda (first_sort_qty)
    - Ta'mir: qizilda (repair_qty)
    - 2-sort: sariqda (second_sort_qty)
    Faqat joriy foydalanuvchi yoki faol operatorning bugungi ishlari hisoblanadi.
    Yangi kunda yoki ish boshlanmaganda 0 qaytaradi.
    """
    logs_qs = _get_scoped_inspection_logs_qs(request, operator)

    first_sort = logs_qs.aggregate(s=Sum('first_sort_qty'))['s'] or 0
    repair = logs_qs.aggregate(s=Sum('repair_qty'))['s'] or 0
    second_sort = logs_qs.aggregate(s=Sum('second_sort_qty'))['s'] or 0
    legacy_defects = logs_qs.filter(
        action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
        second_sort_qty=0
    ).aggregate(d=Sum('defect_qty'))['d'] or 0
    second_sort += legacy_defects

    return {
        'first_sort': int(first_sort),
        'repair': int(repair),
        'second_sort': int(second_sort),
    }


@login_required
@ensure_csrf_cookie
def control_home_view(request):
    """
    Sifat Nazorati (OTK / Control) planshet interfeysi.
    Har bir planshet oldindan tegishli patok akkaunti bilan kiritilgan bo'ladi.
    Yangi kun boshlanishi bilan katta 'ISHNI BOSHLASH' tugmasi chiqadi.
    """
    if not _is_control_authorized(request.user):
        return redirect('root_login')

    today = timezone.localdate()
    active_operator = _get_active_operator(request)
    stats = _get_today_stats(request, active_operator)

    today_logs_qs = _get_scoped_inspection_logs_qs(request, active_operator, today)
    today_boxes_count = today_logs_qs.values('box_id').distinct().count()
    today_inspections = today_logs_qs.select_related(
        'box', 'box__order', 'box__article', 'inspector'
    ).order_by('-created_at')[:25]

    return render(request, 'production/control_home.html', {
        'inspector': request.user,
        'active_operator': active_operator,
        'active_operator_json': json.dumps(active_operator) if active_operator else "null",
        'today_boxes_count': today_boxes_count,
        'today_first_sort': stats['first_sort'],
        'today_repair': stats['repair'],
        'today_second_sort': stats['second_sort'],
        'today_inspections': today_inspections,
        'today_date': str(today),
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def control_identify_operator_api(request):
    """
    Xodim birkasini (QR / shtrix-kod) skaner qilish orqali bazadan topish va tasdiqlash API.
    Kiritilgan kod: USER:468626, WORKER:W-001, W-001, 468626, yoki ism/familiya.
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
        return JsonResponse({'status': 'ERROR', 'message': "Iltimos, xodim birkasini skanerlang yoki ID sini kiriting!"}, status=400)

    clean = extract_worker_code(raw_code).strip()

    # 1. Aniq mosliklar: worker_id yoki user.uid
    worker = Worker.objects.filter(is_active=True).select_related('user').filter(
        Q(worker_id__iexact=clean) | Q(user__uid=clean)
    ).first()

    # 2. Son orqali qidirish (UID, worker_id W-00X, ID)
    if not worker and clean.isdigit():
        num = int(clean)
        worker = Worker.objects.filter(is_active=True).select_related('user').filter(
            Q(user__uid=clean) |
            Q(worker_id__iexact=f"W-{num:03d}") |
            Q(worker_id__iexact=f"W-{clean}") |
            Q(id=num)
        ).first()

    # 3. W- prefiksi bo'lsa
    if not worker and clean.upper().startswith("W-"):
        num_part = clean[2:]
        if num_part.isdigit():
            worker = Worker.objects.filter(is_active=True).select_related('user').filter(
                Q(worker_id__iexact=f"W-{int(num_part):03d}") |
                Q(worker_id__iexact=clean.upper())
            ).first()

    # 4. User ob'ekti (agar Worker profili bo'lmasa ham)
    user_match = None
    if not worker:
        user_match = User.objects.filter(
            Q(uid=clean) | Q(username__iexact=clean)
        ).filter(is_active=True).first()
        if user_match and hasattr(user_match, 'worker_profile') and user_match.worker_profile.is_active:
            worker = user_match.worker_profile
            user_match = None

    # 5. Ism / familiya bo'yicha qidiruv
    if not worker and not user_match:
        q = Q(first_name__icontains=clean) | Q(last_name__icontains=clean) | Q(user__username__icontains=clean)
        matches = list(Worker.objects.filter(q, is_active=True).select_related('user')[:5])
        if len(matches) == 1:
            worker = matches[0]
        elif len(matches) > 1:
            return JsonResponse({
                'status': 'MULTIPLE_MATCHES',
                'message': f"«{clean}» bo'yicha bir nechta xodim topildi. Keraklisini tanlang:",
                'candidates': [
                    {
                        'id': w.id,
                        'worker_id': w.worker_id,
                        'full_name': w.full_name,
                        'uid': w.user.uid if w.user and w.user.uid else "",
                    }
                    for w in matches
                ]
            })

    if not worker and not user_match:
        return JsonResponse({
            'status': 'NOT_FOUND',
            'message': f"«{raw_code}» kodi bo'yicha xodim topilmadi! Birkani to'g'ri skaner qiling."
        }, status=404)

    if worker:
        w_uid = worker.user.uid if (worker.user and worker.user.uid) else worker.worker_id
        w_user_id = worker.user.id if worker.user else None
        operator_data = {
            'id': worker.id,
            'worker_id': worker.worker_id,
            'full_name': worker.full_name,
            'uid': w_uid,
            'user_id': w_user_id,
            'phone': worker.phone_number or (worker.user.phone_number if worker.user else ""),
            'login_date': str(timezone.localdate()),
            'type': 'WORKER',
        }
    else:
        operator_data = {
            'id': user_match.id,
            'worker_id': user_match.uid or f"U-{user_match.id}",
            'full_name': user_match.get_full_name() or user_match.username,
            'uid': user_match.uid or str(user_match.id),
            'user_id': user_match.id,
            'phone': user_match.phone_number or "",
            'login_date': str(timezone.localdate()),
            'type': 'USER',
        }

    # Sessiyada faol operator sifatida saqlash
    request.session['control_operator'] = operator_data
    request.session.modified = True

    stats = _get_today_stats(request, operator_data)

    return JsonResponse({
        'status': 'OK',
        'message': f"Xush kelibsiz, {operator_data['full_name']}! Ishni boshlashingiz mumkin.",
        'operator': operator_data,
        'today_stats': stats,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def control_logout_operator_api(request):
    """
    Faol xodim smenasini yakunlash (accountdan chiqish).
    Planshetning o'zidan (patok loginidan) chiqilmaydi — faqat xodim sessiyasi bo'shaydi,
    shunda boshqa xodim kelib o'z birkasini skaner qilishi mumkin.
    """
    request.session.pop('control_operator', None)
    request.session.modified = True

    # Faqat explicit redirect so'ralsa yoki brauzer to'g'ridan-to'g'ri GET qilganda:
    if request.method == 'GET' and (
        request.GET.get('redirect') == '1' or
        'text/html' in request.headers.get('accept', '')
    ):
        return redirect('production:control_home')

    stats = _get_today_stats(request, None)
    return JsonResponse({
        'status': 'OK',
        'message': "Xodim smenasi yakunlandi. Keyingi xodim birkasini skaner qilishi mumkin.",
        'today_stats': stats
    })


@require_http_methods(["GET", "POST"])
def control_box_lookup_api(request):
    """
    Skanerlangan QR/shtrix-kod bo'yicha qutini qidirib topish API.
    Faqat Quti ID kodi (masalan: A10-394) yoki CONTROL:A10-394 qabul qilinadi.
    Eski #456 kabi quti raqamlari rad etiladi!
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

    # Prefikslarni tozalash (CONTROL:, BOX:, QUTI:)
    cleaned = raw_code.upper()
    for prefix in ['CONTROL:', 'BOX:', 'QUTI:', 'QUTI ']:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # User talabi: Quti raqami (#456) qabul qilinmaydi! Faqat Quti ID kodi (masalan: A10-394 yoki unikal Box ID)!
    if raw_code.strip().startswith('#') or cleaned.startswith('#'):
        return JsonResponse({
            'status': 'INVALID_FORMAT',
            'message': "Quti raqami (#456) qabul qilinmaydi! Iltimos, faqat Quti ID kodi (masalan: A10-394) yoki CONTROL stikerini skanerlang."
        }, status=400)

    # Bazadan qidirish: Faqat box_code (A10-394 kabi) yoki unikal Box ID bo'yicha qidirish
    box = Box.objects.filter(box_code__iexact=cleaned).select_related('order', 'article').first()
    if not box and cleaned.isdigit():
        box = Box.objects.filter(id=int(cleaned)).select_related('order', 'article').first()

    # Agar quti topilmagan bo'lsa, lekin kiritilgan kod biletga tegishli bo'lsa:
    if not box:
        if Ticket.objects.filter(Q(stiker_code__iexact=cleaned) | Q(ticket_code__iexact=cleaned)).exists():
            return JsonResponse({
                'status': 'PERMISSION_DENIED',
                'message': "Sizda bunday huquq yo'q! Bu tikuvchining operatsiya stikeri. Sifat nazorati (OTK) uchun faqat qutidagi CONTROL stikerini yoki Quti ID sini skanerlang!"
            }, status=403)

    if not box:
        return JsonResponse({
            'status': 'NOT_FOUND',
            'message': f"«{raw_code}» kodi bo'yicha quti topilmadi! Faqat Quti ID kodi (masalan: A10-394) yoki CONTROL stikerini skanerlang."
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
    tickets_qs = box.tickets.all().select_related(
        'article_operation__operation', 'worker'
    ).order_by('article_operation__sequence', 'id')

    tickets_data = []
    missing_operations = []
    for t in tickets_qs:
        ao = t.article_operation
        op_name = ao.operation.name if ao and ao.operation else f"Operatsiya #{t.id}"
        op_seq = ao.sequence if ao else 1

        is_disabled_op = bool(ao and not ao.is_active)
        is_cancelled = bool(t.status == Ticket.Status.CANCELLED or is_disabled_op)
        is_scanned = bool(t.status == Ticket.Status.SCANNED and t.worker_id is not None)
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
        elif not is_cancelled:
            # Faqat faol va bekor qilinmagan operatsiyalar yetishmayapti deb hisoblanadi
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
            'is_cancelled': is_cancelled,
            'is_disabled_op': is_disabled_op,
            'worker': worker_info,
        })

    active_tickets = [t for t in tickets_data if not t['is_cancelled']]
    total_tickets = len(active_tickets)
    missing_count = len(missing_operations)
    scanned_count = sum(1 for t in active_tickets if t['is_scanned'])
    all_tickets_scanned = (missing_count == 0 and total_tickets > 0)
    require_all_ops = ControlSetting.is_all_operations_required()

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
            'require_all_operations_scanned': require_all_ops,
            'defect_reasons': _get_active_defect_reasons(),
        },
        'defect_reasons': _get_active_defect_reasons(),
    })


@csrf_exempt
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
        # Barcha faol operatsiyalar egasi borligini qat'iy tekshirish (faqat agar sozlamada yoqilgan bo'lsa)
        if ControlSetting.is_all_operations_required():
            unscanned_tickets = box.tickets.exclude(
                status=Ticket.Status.CANCELLED
            ).exclude(
                article_operation__is_active=False
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

        active_operator = _get_active_operator(request)
        inspector_user = request.user
        if active_operator and active_operator.get('user_id'):
            u = User.objects.filter(id=active_operator['user_id']).first()
            if u:
                inspector_user = u

        if active_operator:
            operator_tag = f"\n[Nazoratchi: {active_operator.get('full_name')} ({active_operator.get('worker_id')})]"
            final_notes += operator_tag

        box.controlled_by = inspector_user
        box.save(update_fields=['controlled_by'])

        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=inspector_user,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=total_qty,
            first_sort_qty=first_sort,
            second_sort_qty=second_sort,
            repair_qty=repair_qty,
            defect_qty=0,
            notes=final_notes
        )

        if is_closed:
            msg = f"Quti {box.box_code} yopildi: {first_sort} ta 1-sort, {second_sort} ta 2-sort. Mahsulot qabul qilindi!"
        else:
            msg = f"Quti {box.box_code} ta'mirga yuborildi: {repair_qty} ta ta'mirga ketdi."

        updated_stats = _get_today_stats(request, active_operator)

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': repair_qty,
            'today_stats': updated_stats,
            'today_first_sort': updated_stats['first_sort'],
            'today_repair': updated_stats['repair'],
            'today_second_sort': updated_stats['second_sort'],
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

        active_operator = _get_active_operator(request)
        inspector_user = request.user
        if active_operator and active_operator.get('user_id'):
            u = User.objects.filter(id=active_operator['user_id']).first()
            if u:
                inspector_user = u

        # Qutini yangilash (Brak bilan 2-sort bitta narsa - ikkalasi ham 2-sort)
        box.controlled_first_sort_qty += fixed_qty
        box.controlled_second_sort_qty += defect_qty
        box.controlled_repair_qty = re_repair_qty
        box.is_controlled = is_closed
        box.status = Box.Status.COMPLETED if is_closed else Box.Status.IN_PROGRESS
        box.controlled_at = timezone.now()
        box.controlled_by = inspector_user
        box.save(update_fields=[
            'controlled_first_sort_qty', 'controlled_second_sort_qty',
            'controlled_repair_qty',
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

        if active_operator:
            operator_tag = f"\n[Nazoratchi: {active_operator.get('full_name')} ({active_operator.get('worker_id')})]"
            notes_lines.append(operator_tag)

        final_notes = "\n".join(notes_lines)

        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=inspector_user,
            action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
            inspected_qty=repair_in_hand,
            first_sort_qty=fixed_qty,
            second_sort_qty=defect_qty,
            repair_qty=re_repair_qty,
            defect_qty=0,
            notes=final_notes
        )

        if is_closed:
            msg = f"Quti {box.box_code} ta'miri yakunlandi va quti to'liq yopildi: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta 2-sort."
        else:
            msg = f"Quti {box.box_code}: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta 2-sort, {re_repair_qty} ta qayta ta'mirda qoldi."

        updated_stats = _get_today_stats(request, active_operator)

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': re_repair_qty,
            'today_stats': updated_stats,
            'today_first_sort': updated_stats['first_sort'],
            'today_repair': updated_stats['repair'],
            'today_second_sort': updated_stats['second_sort'],
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
    Faqat joriy foydalanuvchi yoki faol operatorning ishlari qaytariladi.
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)

    today = timezone.localdate()
    active_operator = _get_active_operator(request)
    stats = _get_today_stats(request, active_operator)

    today_logs_qs = _get_scoped_inspection_logs_qs(request, active_operator, today)
    logs = today_logs_qs.select_related(
        'box', 'box__order', 'box__article', 'inspector'
    ).order_by('-created_at')[:25]

    today_boxes_count = today_logs_qs.values('box_id').distinct().count()

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
        'server_date': str(today),
        'count': today_boxes_count,
        'today_boxes_count': today_boxes_count,
        'today_first_sort': stats['first_sort'],
        'today_repair': stats['repair'],
        'today_second_sort': stats['second_sort'],
        'today_defects': stats['second_sort'],
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

