import json
from datetime import datetime, time
from decimal import Decimal
import re
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction, models
from django.utils import timezone
from accounts.models import Worker, User
from production.models import Ticket, Box, Order


def _get_request_param(request, *keys, default=''):
    """POST form-data yoki JSON request body dan parametrni xavfsiz o'qib olish."""
    for k in keys:
        if k in request.POST:
            val = request.POST.get(k)
            if val is not None and str(val).strip() != '':
                return str(val).strip()
    if request.body:
        try:
            body_json = json.loads(request.body.decode('utf-8'))
            if isinstance(body_json, dict):
                for k in keys:
                    if k in body_json:
                        val = body_json.get(k)
                        if val is not None and str(val).strip() != '':
                            return str(val).strip()
        except Exception:
            pass
    return default


def extract_worker_code(text: str) -> str:
    text = (text or '').strip()
    if text.startswith('WORKER:'):
        return text[len('WORKER:'):].strip()
    if text.startswith('USER:'):
        return text[len('USER:'):].strip()
    return text


def extract_box_code(text: str) -> str:
    text = (text or '').strip()
    text = re.sub(r'^(?:BOX[A-Z\.\s_]*:\s*)+', '', text, flags=re.IGNORECASE).strip()
    if text.startswith('TICKET:'):
        text = text[len('TICKET:'):].strip()
    ticket_match = re.search(r'-([A-Z0-9]{8})(?:-[A-Z0-9]+)?$', text)
    if ticket_match:
        return ticket_match.group(1)
    return text


def extract_ticket_code(text: str) -> str:
    text = (text or '').strip()
    # Har xil skaner va klaviatura drayveri buzilishlarini tozalash (masalan: TICK. T:, TICKET:, TICK:)
    text = re.sub(r'^(?:TICK[A-Z\.\s_]*:\s*)+', '', text, flags=re.IGNORECASE).strip()
    return text


def terminal_home_view(request):
    """
    Zebra DS22 Skaner uskunasi uchun ixtisoslashgan Master Skanerlash Terminali sahifasi.
    """
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(today, time.max), tz)
    
    # Bugungi umumiy terminal statistikasi (Index-friendly datetime range)
    today_scans = Ticket.objects.filter(status=Ticket.Status.SCANNED, scanned_at__range=(day_start, day_end))
    today_total_units = today_scans.aggregate(s=models.Sum('quantity'))['s'] or 0
    today_total_amount = today_scans.aggregate(s=models.Sum('total_amount'))['s'] or Decimal('0')
    today_active_workers = today_scans.values('worker').distinct().count()

    # Oxirgi 10 ta skanerlangan biletlar oqimi
    recent_scans = today_scans.select_related(
        'worker', 'article_operation__operation', 'box__order'
    ).order_by('-scanned_at')[:10]

    screens_list = list(range(1, 11))

    return render(request, 'terminal/index.html', {
        'screens_list': screens_list,
        'today_total_units': today_total_units,
        'today_total_amount': today_total_amount,
        'today_active_workers': today_active_workers,
        'recent_scans': recent_scans,
        'today_str': today.strftime("%d.%m.%Y"),
    })


@csrf_exempt
@require_POST
def terminal_identify_worker_api(request):
    """
    Xodimni QR nishoni (USER:468626, WORKER:W-001), 6 xonali UID yoki ID orqali aniqlash.
    """
    raw_code = _get_request_param(request, 'code', 'scan_value', 'worker_code')
    if not raw_code:
        return JsonResponse({'status': 'ERROR', 'message': "Xodim kodi kiritilmadi!"}, status=400)

    clean_code = extract_worker_code(raw_code).strip()

    # 1. Aniq mosliklar (UID, worker_id, raqamli ID)
    exact = Worker.objects.filter(is_active=True).select_related('user').filter(
        models.Q(worker_id__iexact=clean_code) |
        models.Q(user__uid=clean_code)
    ).first()

    if not exact and clean_code.isdigit():
        exact = Worker.objects.filter(is_active=True).select_related('user').filter(
            models.Q(user__uid=clean_code) |
            models.Q(worker_id__iexact=f"W-{int(clean_code):03d}") |
            models.Q(worker_id__iexact=f"W-{clean_code}") |
            models.Q(id=int(clean_code))
        ).first()

    if not exact and clean_code.upper().startswith("W-"):
        num_part = clean_code[2:]
        if num_part.isdigit():
            exact = Worker.objects.filter(is_active=True).select_related('user').filter(
                models.Q(worker_id__iexact=f"W-{int(num_part):03d}") |
                models.Q(worker_id__iexact=clean_code.upper())
            ).first()

    if exact:
        worker = exact
    else:
        # Ism, familiya yoki login bo'yicha qidirish
        q = (
            models.Q(first_name__icontains=clean_code) |
            models.Q(last_name__icontains=clean_code) |
            models.Q(user__username__icontains=clean_code)
        )
        matches = list(Worker.objects.filter(q).filter(is_active=True).select_related('user')[:10])

        if matches:
            if len(matches) > 1:
                return JsonResponse({
                    'status': 'MULTIPLE_MATCHES',
                    'message': f"'{clean_code}' bo'yicha bir nechta xodim topildi. Keraklisini tanlang:",
                    'candidates': [
                        {
                            'id': w.id,
                            'worker_id': w.worker_id,
                            'full_name': w.full_name,
                            'uid': w.user.uid if w.user and w.user.uid else "",
                            'phone': w.phone_number or (w.user.phone_number if w.user else ""),
                        }
                        for w in matches
                    ]
                })
            worker = matches[0]
        else:
            # Agar Worker da bo'lmasa, lekin User larda mavjud bo'lsa (masalan SuperAdmin yoki ro'yxatdan o'tgan user)
            user_match = User.objects.filter(models.Q(uid=clean_code) | models.Q(username__iexact=clean_code)).first()
            if user_match:
                worker_code = f"W-{user_match.id:03d}"
                while Worker.objects.filter(worker_id=worker_code).exists():
                    worker_code = f"W-{user_match.id:03d}_{user_match.username}"
                exact, _ = Worker.objects.get_or_create(
                    user=user_match,
                    defaults={
                        'worker_id': worker_code,
                        'first_name': user_match.first_name or user_match.username,
                        'last_name': user_match.last_name or '',
                        'phone_number': user_match.phone_number or '',
                        'is_active': True
                    }
                )
                worker = exact
            else:
                return JsonResponse({
                    'status': 'NOT_FOUND',
                    'message': f"❌ Xodim topilmadi (Kiritildi: '{clean_code}'). QR nishonni qayta skanerlang."
                })

    # Sessiyaga yozish
    request.session['terminal_worker_id'] = worker.id
    request.session['terminal_pending_tickets'] = []
    request.session.modified = True

    # Bugungi ish ko'rsatkichlari (Index-friendly datetime range)
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(today, time.max), tz)
    today_tickets = Ticket.objects.filter(worker=worker, status=Ticket.Status.SCANNED, scanned_at__range=(day_start, day_end))
    today_units = today_tickets.aggregate(s=models.Sum('quantity'))['s'] or 0
    today_earned = today_tickets.aggregate(s=models.Sum('total_amount'))['s'] or Decimal('0')

    # Umumiy balans
    total_earned = Ticket.objects.filter(worker=worker, status=Ticket.Status.SCANNED).aggregate(s=models.Sum('total_amount'))['s'] or Decimal('0')
    from accounts.models import WorkerPayout
    total_paid = WorkerPayout.objects.filter(worker=worker).aggregate(s=models.Sum('amount'))['s'] or Decimal('0')
    current_balance = total_earned - total_paid

    return JsonResponse({
        'status': 'OK',
        'worker': {
            'id': worker.id,
            'worker_id': worker.worker_id,
            'full_name': worker.full_name,
            'uid': worker.user.uid if worker.user and worker.user.uid else "",
            'phone': worker.phone_number or (worker.user.phone_number if worker.user else ""),
            'today_units': today_units,
            'today_earned': float(today_earned),
            'today_earned_formatted': f"{int(today_earned):,}".replace(",", " "),
            'current_balance': float(current_balance),
            'current_balance_formatted': f"{int(current_balance):,}".replace(",", " "),
        }
    })


@csrf_exempt
@require_POST
def terminal_scan_ticket_api(request):
    """
    Bilet shtrix-kodini (Zebra DS22 skaneri orqali) qabul qilish,
    dublikatdan himoya qilish va sessiyaga qo'shish.
    """
    raw_code = _get_request_param(request, 'ticket_code', 'code', 'scan_value')
    if not raw_code:
        return JsonResponse({'status': 'ERROR', 'message': "Bilet kodi bo'sh!"}, status=400)

    worker_id = request.session.get('terminal_worker_id')
    if not worker_id:
        return JsonResponse({
            'status': 'NO_WORKER',
            'message': "⚠️ Avval xodim QR nishonini skanerlang!"
        })

    worker = Worker.objects.filter(id=worker_id, is_active=True).first()
    if not worker:
        request.session.pop('terminal_worker_id', None)
        return JsonResponse({'status': 'NO_WORKER', 'message': "Tanlangan xodim topilmadi!"})

    clean_code = extract_ticket_code(raw_code).strip()

    # 1. Biletni qidirish: Aniq moslik bo'yicha
    ticket = Ticket.objects.filter(
        models.Q(ticket_code=clean_code) | models.Q(ticket_code=clean_code.upper())
    ).select_related(
        'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
    ).first()

    if not ticket:
        ticket = Ticket.objects.filter(ticket_code__iexact=clean_code).select_related(
            'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
        ).first()

    # 2. Skaner orqali kelgan quti kodi va bilet xeshi bo'yicha qidirish
    # Masalan: "TICK. T:TK- -13-Q0S13EZI-3B6412" yoki "TK- -13-Q0S13EZI-3B6412" -> "Q0S13EZI-3B6412"
    if not ticket:
        suffix_match = re.search(r'([A-Z0-9]{6,12}-[A-Z0-9]{4,10})$', clean_code.upper())
        if suffix_match:
            suffix = suffix_match.group(1)
            ticket = Ticket.objects.filter(ticket_code__iendswith=suffix).select_related(
                'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
            ).first()

    # 3. Agar faqat oxirgi 6 xonali bilet xeshi bo'lsa (masalan: "-3B6412")
    if not ticket:
        hash_match = re.search(r'-([A-Z0-9]{6})$', clean_code.upper())
        if hash_match:
            ticket_hash = hash_match.group(1)
            possible = Ticket.objects.filter(ticket_code__iendswith=f"-{ticket_hash}").select_related(
                'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
            )
            if possible.count() == 1:
                ticket = possible.first()
            elif possible.count() > 1:
                num_match = re.search(r'-(\d+)-', clean_code)
                if num_match:
                    bx_num = int(num_match.group(1))
                    filtered = possible.filter(box__box_number=bx_num).first()
                    if filtered:
                        ticket = filtered

    # 4. Agar 8 xonali unikal Stiker kodi bo'yicha kiritilgan bo'lsa (masalan: K7B9P2X4, #K7B9P2X4, ST-K7B9P2X4)
    if not ticket:
        clean_stiker = re.sub(r'^(?:#|ST-|TK-|st-|tk-)+', '', clean_code).strip().upper()
        if len(clean_stiker) == 8:
            ticket = Ticket.objects.filter(stiker_code=clean_stiker).select_related(
                'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
            ).first()

    # 5. Agar raqamli ID bo'yicha kiritilgan bo'lsa (masalan: 1042, #1042, ST-1042)
    if not ticket:
        clean_id = re.sub(r'^(?:#|ST-|TK-|st-|tk-)+', '', clean_code).strip().replace(' ', '').replace(',', '')
        if clean_id.isdigit():
            ticket = Ticket.objects.filter(id=int(clean_id)).select_related(
                'box__order', 'box__article', 'article_operation__operation', 'worker', 'scanned_by'
            ).first()

    # 5. Agar foydalanuvchi adashib butun Quti QR kodini skanerlagan bo'lsa
    if not ticket:
        box_clean = extract_box_code(raw_code)
        box_match = Box.objects.filter(
            models.Q(box_code__iexact=box_clean) | models.Q(id=int(box_clean) if box_clean.isdigit() else -1)
        ).first()
        if box_match:
            return JsonResponse({
                'status': 'IS_BOX_CODE',
                'message': f"⚠️ Bu QUTI kodi (#{box_match.box_number} [{box_match.box_code}])!\n\n"
                           f"Ishbay haq hisoblanishi uchun quti stikeridagi kerakli OPERATSIYA BILETI QR kodini skanerlang."
            })

    if not ticket:
        return JsonResponse({
            'status': 'NOT_FOUND',
            'message': f"❌ Bilet bazada topilmadi:\n'{clean_code}'"
        })

    # 1. Baza bo'yicha dublikat tekshiruvi: bilet allaqachon qabul qilinganmi?
    if ticket.status == Ticket.Status.SCANNED:
        scanned_time = timezone.localtime(ticket.scanned_at).strftime("%d.%m.%Y %H:%M") if ticket.scanned_at else ""
        who = ticket.worker.full_name if ticket.worker else "Boshqa tikuvchi"
        master_name = ticket.scanned_by.get_full_name() or ticket.scanned_by.username if ticket.scanned_by else "Master"
        return JsonResponse({
            'status': 'ALREADY_SCANNED',
            'message': f"⚠️ DIQQAT: Ushbu bilet allaqachon qabul qilingan!\n\n"
                       f"Operatsiya: {ticket.article_operation.operation.name}\n"
                       f"Xodim: {who}\n"
                       f"Qabul qilgan: {master_name} ({scanned_time})"
        })

    # 2. Sessiya bo'yicha dublikat tekshiruvi: ushbu partiyada skanerlanganmi?
    pending_ids = request.session.get('terminal_pending_tickets', [])
    if ticket.id in pending_ids:
        return JsonResponse({
            'status': 'DUPLICATE_IN_SESSION',
            'message': f"⚠️ Ushbu bilet ({ticket.ticket_code}) joriy ro'yxatga allaqachon qo'shilgan!"
        })

    # Sessiyaga qo'shish
    pending_ids.append(ticket.id)
    request.session['terminal_pending_tickets'] = pending_ids
    request.session.modified = True

    # Barcha kiritilgan biletlarni hisoblash
    pending_tickets = list(Ticket.objects.filter(id__in=pending_ids).select_related(
        'box__order', 'box__article', 'article_operation__operation'
    ))
    # Saqlangan tartibda saralash
    ticket_map = {t.id: t for t in pending_tickets}
    sorted_tickets = [ticket_map[tid] for tid in pending_ids if tid in ticket_map]

    total_count = len(sorted_tickets)
    total_units = sum(t.quantity for t in sorted_tickets)
    total_amount = sum(t.total_amount for t in sorted_tickets)

    return JsonResponse({
        'status': 'OK',
        'scanned_ticket': {
            'id': ticket.id,
            'ticket_code': ticket.ticket_code,
            'operation_name': ticket.article_operation.operation.name,
            'order_number': ticket.box.order.order_number,
            'box_number': ticket.box.box_number,
            'model_name': ticket.box.article.name if ticket.box.article else (ticket.box.order.article.name if ticket.box.order.article else ""),
            'quantity': ticket.quantity,
            'price_per_unit': float(ticket.price_per_unit),
            'price_formatted': f"{int(ticket.price_per_unit):,}".replace(",", " "),
            'total_amount': float(ticket.total_amount),
            'total_amount_formatted': f"{int(ticket.total_amount):,}".replace(",", " "),
            'split_index': ticket.split_index,
            'total_splits': ticket.total_splits,
        },
        'summary': {
            'count': total_count,
            'units': total_units,
            'amount': float(total_amount),
            'amount_formatted': f"{int(total_amount):,}".replace(",", " "),
        }
    })


@csrf_exempt
@require_POST
def terminal_remove_ticket_api(request):
    """
    Sessiyadan adashib kiritilgan biletni o'chirish.
    """
    ticket_id_raw = _get_request_param(request, 'ticket_id', 'id')
    if not ticket_id_raw:
        return JsonResponse({'status': 'ERROR', 'message': "Bilet ID ko'rsatilmadi"}, status=400)

    try:
        ticket_id = int(ticket_id_raw)
    except ValueError:
        return JsonResponse({'status': 'ERROR', 'message': "Noto'g'ri ID"}, status=400)

    pending_ids = request.session.get('terminal_pending_tickets', [])
    if ticket_id in pending_ids:
        pending_ids.remove(ticket_id)
        request.session['terminal_pending_tickets'] = pending_ids
        request.session.modified = True

    pending_tickets = list(Ticket.objects.filter(id__in=pending_ids))
    total_count = len(pending_tickets)
    total_units = sum(t.quantity for t in pending_tickets)
    total_amount = sum(t.total_amount for t in pending_tickets)

    return JsonResponse({
        'status': 'OK',
        'summary': {
            'count': total_count,
            'units': total_units,
            'amount': float(total_amount),
            'amount_formatted': f"{int(total_amount):,}".replace(",", " "),
        }
    })


@csrf_exempt
@require_POST
def terminal_finalize_api(request):
    """
    Barcha biletlarni tasdiqlash, tanlangan Sex Ekraniga (1-10) uzatish va bazaga yozish.
    """
    worker_id = request.session.get('terminal_worker_id')
    pending_ids = request.session.get('terminal_pending_tickets', [])

    if not worker_id:
        return JsonResponse({'status': 'ERROR', 'message': "Xodim tanlanmagan!"}, status=400)

    if not pending_ids:
        return JsonResponse({'status': 'ERROR', 'message': "Hech qanday bilet skanerlanmagan!"}, status=400)

    try:
        screen_raw = _get_request_param(request, 'screen_number', 'screen', default='1')
        screen_number = int(screen_raw)
        if screen_number < 1 or screen_number > 10:
            screen_number = 1
    except (ValueError, TypeError):
        screen_number = 1

    worker = Worker.objects.filter(id=worker_id, is_active=True).first()
    if not worker:
        return JsonResponse({'status': 'ERROR', 'message': "Xodim topilmadi!"}, status=400)

    master_user = request.user if request.user.is_authenticated else None

    now = timezone.now()

    with transaction.atomic():
        # Qayta tekshirish: pending biletlar orasida boshqa kimdir yopib yuborgani yo'qmi?
        valid_tickets = list(Ticket.objects.select_for_update().filter(
            id__in=pending_ids,
            status=Ticket.Status.PENDING
        ))

        if len(valid_tickets) != len(pending_ids):
            # Qaysidir bilet boshqa joyda yopilgan
            valid_ids = {t.id for t in valid_tickets}
            invalid_ids = [tid for tid in pending_ids if tid not in valid_ids]
            return JsonResponse({
                'status': 'CONFLICT',
                'message': f"Ayrim biletlar boshqa master tomonidan qabul qilingan (ID: {invalid_ids}). Iltimos tekshiring."
            }, status=409)

        total_units = sum(t.quantity for t in valid_tickets)
        total_amount = sum(t.total_amount for t in valid_tickets)

        # Hammasini bir vaqtda SCANNED holatiga o'tkazish
        Ticket.objects.filter(id__in=pending_ids).update(
            status=Ticket.Status.SCANNED,
            worker=worker,
            screen_number=screen_number,
            scanned_by=master_user,
            scanned_at=now
        )

        # Tegishli qutilar holatini avtomatik yangilash (IN_PROGRESS yoki COMPLETED)
        affected_box_ids = list(Ticket.objects.filter(id__in=pending_ids).values_list('box_id', flat=True).distinct())
        for b_obj in Box.objects.filter(id__in=affected_box_ids):
            b_obj.update_status_from_tickets()

        # Sessiyani tozalash
        request.session.pop('terminal_worker_id', None)
        request.session.pop('terminal_pending_tickets', None)
        request.session.modified = True

    return JsonResponse({
        'status': 'OK',
        'summary': {
            'worker_name': worker.full_name,
            'worker_id': worker.worker_id,
            'screen_number': screen_number,
            'tickets_count': len(valid_tickets),
            'total_units': total_units,
            'total_amount': float(total_amount),
            'total_amount_formatted': f"{int(total_amount):,}".replace(",", " "),
        }
    })


@require_GET
def terminal_box_lookup_api(request):
    """
    Quti ma'lumotlarini tezkor tekshirish (BOX:XXXX, kod yoki raqam bo'yicha).
    """
    identifier = request.GET.get('identifier', '').strip()
    if not identifier:
        return JsonResponse({'status': 'ERROR', 'message': "Quti kodi ko'rsatilmadi"}, status=400)

    clean_id = extract_box_code(identifier).strip().upper()
    q = models.Q(box_code__iexact=clean_id)
    if clean_id.isdigit():
        q |= models.Q(id=int(clean_id)) | models.Q(box_number=int(clean_id))
    else:
        combo_match = re.search(r'^(.*?)[-#\s]+(\d+)$', clean_id)
        if combo_match:
            ord_num = combo_match.group(1).strip()
            bx_num = int(combo_match.group(2))
            q |= models.Q(order__order_number__iexact=ord_num, box_number=bx_num)

    box = Box.objects.select_related('order', 'article').prefetch_related(
        'tickets__article_operation__operation',
        'tickets__worker'
    ).filter(q).first()

    if not box:
        return JsonResponse({'status': 'NOT_FOUND', 'message': f"Quti topilmadi: '{clean_id}'"})

    tickets = box.tickets.all().order_by('article_operation__sequence', 'split_index')
    total_tickets = tickets.count()
    scanned_tickets = tickets.filter(status=Ticket.Status.SCANNED).count()

    tickets_data = []
    for t in tickets:
        tickets_data.append({
            'id': t.id,
            'ticket_code': t.ticket_code,
            'operation_name': t.article_operation.operation.name,
            'quantity': t.quantity,
            'price_per_unit': float(t.price_per_unit),
            'price_formatted': f"{int(t.price_per_unit):,}".replace(",", " "),
            'total_amount': float(t.total_amount),
            'total_amount_formatted': f"{int(t.total_amount):,}".replace(",", " "),
            'status': t.status,
            'worker_name': t.worker.full_name if t.worker else "—",
            'scanned_at': timezone.localtime(t.scanned_at).strftime("%d.%m %H:%M") if t.scanned_at else "—",
            'screen_number': t.screen_number or "—",
        })

    model_name = box.article.name if box.article else (box.order.article.name if box.order.article else "—")
    model_code = box.article.code if box.article else (box.order.article.code if box.order.article else "—")

    return JsonResponse({
        'status': 'OK',
        'box': {
            'id': box.id,
            'box_code': box.box_code,
            'box_number': box.box_number,
            'order_number': box.order.order_number,
            'client_name': box.order.client_name or "—",
            'model_name': model_name,
            'model_code': model_code,
            'quantity': box.quantity,
            'total_tickets': total_tickets,
            'scanned_tickets': scanned_tickets,
            'progress_percent': int((scanned_tickets / total_tickets * 100)) if total_tickets > 0 else 0,
            'tickets': tickets_data,
        }
    })


@require_GET
def terminal_worker_balance_api(request):
    """
    Xodimning bugungi va umumiy hisobini tekshirish API.
    """
    code = request.GET.get('code', '').strip()
    if not code:
        return JsonResponse({'status': 'ERROR', 'message': "Xodim kodi ko'rsatilmadi"}, status=400)

    clean_code = extract_worker_code(code).strip()
    q = models.Q(worker_id__iexact=clean_code) | models.Q(user__uid=clean_code)
    if clean_code.isdigit():
        q |= models.Q(id=int(clean_code)) | models.Q(user__uid=clean_code) | models.Q(worker_id__iexact=f"W-{int(clean_code):03d}")

    worker = Worker.objects.filter(q, is_active=True).select_related('user').first()
    if not worker:
        return JsonResponse({'status': 'NOT_FOUND', 'message': f"Xodim topilmadi: '{clean_code}'"})

    today = timezone.localdate()
    today_tickets = Ticket.objects.filter(worker=worker, status=Ticket.Status.SCANNED, scanned_at__date=today)
    today_units = today_tickets.aggregate(s=models.Sum('quantity'))['s'] or 0
    today_earned = today_tickets.aggregate(s=models.Sum('total_amount'))['s'] or Decimal('0')

    from accounts.models import WorkerPayout
    total_earned = Ticket.objects.filter(worker=worker, status=Ticket.Status.SCANNED).aggregate(s=models.Sum('total_amount'))['s'] or Decimal('0')
    total_paid = WorkerPayout.objects.filter(worker=worker).aggregate(s=models.Sum('amount'))['s'] or Decimal('0')
    balance = total_earned - total_paid

    recent = today_tickets.select_related('article_operation__operation').order_by('-scanned_at')[:5]
    recent_list = []
    for r in recent:
        recent_list.append({
            'operation_name': r.article_operation.operation.name,
            'quantity': r.quantity,
            'amount_formatted': f"{int(r.total_amount):,}".replace(",", " "),
            'time': timezone.localtime(r.scanned_at).strftime("%H:%M") if r.scanned_at else ""
        })

    return JsonResponse({
        'status': 'OK',
        'worker': {
            'id': worker.id,
            'worker_id': worker.worker_id,
            'full_name': worker.full_name,
            'uid': worker.user.uid if worker.user and worker.user.uid else "",
            'today_units': today_units,
            'today_earned': float(today_earned),
            'today_earned_formatted': f"{int(today_earned):,}".replace(",", " "),
            'total_earned_formatted': f"{int(total_earned):,}".replace(",", " "),
            'total_paid_formatted': f"{int(total_paid):,}".replace(",", " "),
            'balance': float(balance),
            'balance_formatted': f"{int(balance):,}".replace(",", " "),
            'recent_scans': recent_list
        }
    })


@csrf_exempt
@require_POST
def terminal_reset_session_api(request):
    """
    Joriy terminal sessiyasini bekor qilish va boshlang'ich holatga qaytarish.
    """
    request.session.pop('terminal_worker_id', None)
    request.session.pop('terminal_pending_tickets', None)
    request.session.modified = True
    return JsonResponse({'status': 'OK', 'message': "Sessiya tozalandi"})

