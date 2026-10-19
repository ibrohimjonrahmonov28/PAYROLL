import json
import re
from datetime import datetime
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Q

from accounts.models import User, Worker
from production.models import CuttingBatch, Box, Ticket, Order
from production.services import get_patok_code, get_patok_name, parse_patok_number


_CYRILLIC_TO_LATIN_MAP = str.maketrans(
    "ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮйцукенгшщзхъфывапролджэячсмитьбю",
    "QWERTYUIOP[]ASDFGHJKL;'ZXCVBNM,.qwertyuiop[]asdfghjkl;'zxcvbnm,."
)


def _normalize_scanner_code(raw_code: str) -> str:
    """
    Zebra skaneridan kelgan matnni normallashtirish:
    - Agar rus (kirill) klaviaturasida skanerlangan bo'lsa (masalan: ГЫУКЖ123456 -> USER:123456), lotinga o'girish
    - '+' belgilarini ':' yoki '-' ga to'g'irlash
    """
    text = (raw_code or '').strip()
    if not text:
        return ''
    if re.search(r'[А-Яа-яЁё]', text):
        text = text.translate(_CYRILLIC_TO_LATIN_MAP)
        # Kirillda ':' ko'pincha '^' yoki ';' yoki 'Ж' bo'lib tushadi
        text = re.sub(r'^(USER|WORKER|MASTER|PASTAL|BATCH|TICKET|CONTROL|BOX)[;^Жж+]', r'\1:', text, flags=re.IGNORECASE)
    if '+' in text:
        text = re.sub(r'^(USER|WORKER|MASTER|PASTAL|BATCH|TICKET|CONTROL|BOX)\+', r'\1:', text, flags=re.IGNORECASE)
        text = text.replace('+', '-')
    return text.strip()


def _get_batch_boxes_q(batch):
    """
    Pastalga tegishli qutilarni xavfsiz qidirish filtri:
    pastal_code bo'sh ('') bo'lganda boshqa pastallarning bo'sh qutilarini tortib ketmaslik uchun!
    """
    q = Q(cutting_batch_item__batch=batch)
    pastal_code = (batch.pastal_code or '').strip()
    order = batch.order_item.order if batch.order_item else None
    article = batch.order_item.article if batch.order_item else None
    if pastal_code and order:
        fallback_q = Q(pastal_number=pastal_code, order=order)
        if article:
            fallback_q &= Q(article=article)
        q |= fallback_q
    return q


def _get_batch_active_boxes(batch):
    """
    Pastalning faqat AKTIV (bekor qilinmagan / CANCELLED bo'lmagan) qutilarini qaytaradi.
    """
    return Box.objects.filter(_get_batch_boxes_q(batch)).exclude(
        status=Box.Status.CANCELLED
    ).distinct()


def _get_active_master(request):
    """
    Faol master foydalanuvchisini aniqlash:
    1. request.session['master_user_id']
    2. Agar master_tablet_logged_out bo'lsa -> None (Chiqish tugmasi bosilgan)
    3. Yoki request.user (agar tizimga kirgan bo'lsa)
    """
    if hasattr(request, 'session'):
        master_id = request.session.get('master_user_id')
        if master_id:
            user = User.objects.filter(id=master_id).first()
            if user:
                return user
        if request.session.get('master_tablet_logged_out'):
            return None

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
    default_branch = master.branch if (master and getattr(master, 'branch', None)) else User.Branch.HQ

    master_name = ''
    master_initial = 'M'
    master_branch_display = "Kosonsoy (Bosh bino)" if default_branch == User.Branch.HQ else "Uychi filiali"
    if master:
        master_name = master.get_full_name() or master.username or 'Master'
        master_initial = (master.first_name or master.username or 'M')[:1].upper()
        if getattr(master, 'branch', None):
            master_branch_display = "Kosonsoy (Bosh bino)" if master.branch == User.Branch.HQ else "Uychi filiali"

    context = {
        'master': master,
        'master_name': master_name,
        'master_initial': master_initial,
        'master_branch_display': master_branch_display,
        'default_branch': default_branch,
        'now_date': timezone.localdate(),
    }
    return render(request, 'production/master_tablet.html', context)


@csrf_exempt
@require_POST
def api_identify_master(request):
    """
    Planshetda masterni QR-kod / Beydjik / UID orqali aniqlash va sessiyaga kiritish
    Kutiladi: POST { "code": "USER:123456" yoki "WORKER:W-001" yoki "123456" yoki username }
    """
    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    raw_code = _normalize_scanner_code(data.get('code') or '')
    if not raw_code:
        return JsonResponse({'success': False, 'message': "Beydjik kodi kiritilmadi"}, status=400)

    # USER:, WORKER:, MASTER: prefikslarini tozalash
    clean_code = raw_code
    for prefix in ('USER:', 'WORKER:', 'MASTER:'):
        if clean_code.upper().startswith(prefix):
            clean_code = clean_code[len(prefix):].strip()
            break

    user = User.objects.filter(
        Q(uid__iexact=clean_code) | Q(username__iexact=clean_code)
    ).first()

    # Agar Worker beydjigi skanerlangan bo'lsa va Worker User ga ulangan bo'lsa
    if not user:
        worker = Worker.objects.select_related('user').filter(
            Q(worker_id__iexact=clean_code) | Q(user__uid__iexact=clean_code)
        ).first()
        if worker and worker.user:
            user = worker.user

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
            'message': f"Ushbu xodim ({user.get_full_name() or user.username}) Master roliga ega emas ({user.get_role_display()})"
        }, status=403)

    request.session['master_user_id'] = user.id
    request.session.pop('master_tablet_logged_out', None)
    request.session.modified = True

    branch = user.branch or User.Branch.HQ

    return JsonResponse({
        'success': True,
        'master': {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'uid': user.uid,
            'role': user.get_role_display(),
            'branch': branch,
            'branch_display': "Kosonsoy (Bosh bino)" if branch == User.Branch.HQ else "Uychi filiali",
        }
    })


@csrf_exempt
@require_POST
def api_logout_master(request):
    """Planshet sessiyasidan masterni chiqarish"""
    request.session.pop('master_user_id', None)
    request.session['master_tablet_logged_out'] = True
    request.session.modified = True
    return JsonResponse({'success': True, 'message': "Sessiya yakunlandi"})


@require_GET
def api_get_patoks_summary(request):
    """
    Filial bo'yicha barcha patoklarning kutilayotgan va faol pastallari xulosasi
    Parametr: ?branch=HQ yoki UYCHI
    """
    master = _get_active_master(request)
    branch = request.GET.get('branch') or (master.branch if (master and getattr(master, 'branch', None)) else 'HQ')

    is_hq = (branch == 'HQ')
    screen_numbers = list(range(1, 14)) if is_hq else list(range(14, 41))

    # Kutilayotgan (DISPATCHED) pastallar (bekor qilingan zakazlarni chiqarib tashlaymiz)
    dispatched_batches = CuttingBatch.objects.filter(
        assigned_screen__in=screen_numbers,
        dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    ).values('assigned_screen').annotate(
        count=Count('id', distinct=True),
        total_units=Sum('items__quantity')
    )
    dispatched_map = {item['assigned_screen']: item for item in dispatched_batches}

    # Qabul qilingan faol (ACCEPTED) pastallar
    accepted_batches = CuttingBatch.objects.filter(
        assigned_screen__in=screen_numbers,
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    ).values('assigned_screen').annotate(
        count=Count('id', distinct=True)
    )
    accepted_map = {item['assigned_screen']: item['count'] for item in accepted_batches}

    patoks = []
    total_waiting_pastals = 0
    for sn in screen_numbers:
        p_code = get_patok_code(sn)
        p_name = get_patok_name(sn)
        d_info = dispatched_map.get(sn, {})
        waiting_count = d_info.get('count', 0) or 0
        waiting_units = d_info.get('total_units', 0) or 0
        active_count = accepted_map.get(sn, 0) or 0
        total_waiting_pastals += waiting_count

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
        'total_waiting_pastals': total_waiting_pastals,
        'patoks': patoks
    })


def _compute_batch_box_and_ticket_stats(batch):
    """
    Pastalning aktiv qutilari va aktiv stikerlari bo'yicha yopilganlik, ta'mir va tikuv foizini
    bitta optimallashtirilgan so'rovda hisoblaydi (CANCELLED quti va stikerlar hisobga olinmaydi).
    """
    valid_tickets_filter = ~Q(tickets__status=Ticket.Status.CANCELLED) & (
        Q(tickets__article_operation__is_active=True) | Q(tickets__status=Ticket.Status.SCANNED)
    )
    boxes = list(
        _get_batch_active_boxes(batch).annotate(
            valid_t_cnt=Count('tickets', filter=valid_tickets_filter, distinct=True),
            scanned_t_cnt=Count('tickets', filter=Q(tickets__status=Ticket.Status.SCANNED), distinct=True),
        )
    )
    total_boxes = len(boxes)
    closed_boxes = sum(1 for bx in boxes if bx.is_controlled and bx.controlled_repair_qty == 0)
    unclosed_boxes = total_boxes - closed_boxes
    repair_boxes = sum(1 for bx in boxes if bx.controlled_repair_qty > 0)
    repair_units = sum(bx.controlled_repair_qty for bx in boxes)
    is_pastal_closed = (total_boxes > 0 and closed_boxes == total_boxes)

    total_t = sum(bx.valid_t_cnt for bx in boxes)
    scanned_t = sum(bx.scanned_t_cnt for bx in boxes)
    progress_pct = int(round(scanned_t / total_t * 100)) if total_t > 0 else 0
    boxes_units = sum(bx.quantity for bx in boxes)

    return {
        'total_boxes': total_boxes,
        'closed_boxes': closed_boxes,
        'unclosed_boxes': unclosed_boxes,
        'repair_boxes': repair_boxes,
        'repair_units': repair_units,
        'is_pastal_closed': is_pastal_closed,
        'total_tickets': total_t,
        'scanned_tickets': scanned_t,
        'progress_pct': progress_pct,
        'boxes_units': boxes_units,
    }


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
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    ).select_related(
        'order_item__order__customer',
        'order_item__article__model'
    ).prefetch_related('items').order_by('-dispatched_at')

    waiting_list = []
    for b in waiting_qs:
        article = b.order_item.article if b.order_item else None
        order = b.order_item.order if b.order_item else None
        customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
        active_boxes_qs = _get_batch_active_boxes(b)
        total_boxes = active_boxes_qs.count()

        waiting_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'article_code': article.code if article else '',
            'total_units': b.total_real_quantity or b.total_quantity,
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
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    ).select_related(
        'order_item__order__customer',
        'order_item__article',
        'accepted_by'
    ).prefetch_related('items').order_by('-accepted_at')[:35]

    accepted_list = []
    for b in accepted_qs:
        article = b.order_item.article if b.order_item else None
        order = b.order_item.order if b.order_item else None
        customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')

        st = _compute_batch_box_and_ticket_stats(b)
        can_reject = (st['scanned_tickets'] == 0 and st['closed_boxes'] == 0)

        accepted_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'total_units': st['boxes_units'] or b.total_real_quantity or b.total_quantity,
            'actual_weight_kg': float(b.actual_weight_kg) if b.actual_weight_kg else 0.0,
            'boxes_count': st['total_boxes'],
            'closed_boxes': st['closed_boxes'],
            'unclosed_boxes': st['unclosed_boxes'],
            'repair_boxes': st['repair_boxes'],
            'repair_units': st['repair_units'],
            'is_pastal_closed': st['is_pastal_closed'],
            'scanned_tickets': st['scanned_tickets'],
            'total_tickets': st['total_tickets'],
            'progress_pct': st['progress_pct'],
            'can_reject': can_reject,
            'accepted_at': timezone.localtime(b.accepted_at).strftime("%d.%m.%Y %H:%M") if b.accepted_at else '—',
            'accepted_by_name': (b.accepted_by.get_full_name() or b.accepted_by.username) if b.accepted_by else 'Master',
        })

    # Yopilmagan pastallarni ro'yxat boshiga chiqaramiz (Master birinchi navbatda tugamagan ishlarni ko'rishi uchun)
    accepted_list.sort(key=lambda x: (1 if x['is_pastal_closed'] else 0))

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
    - Barcha AKTIV qutilar shu patokka rasman biriktiriladi
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

    sn = int(screen_number)
    p_code = get_patok_code(sn)

    with transaction.atomic():
        batch = CuttingBatch.objects.select_for_update().select_related(
            'order_item__order',
            'order_item__article'
        ).filter(id=batch_id).first()
        if not batch:
            return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

        if batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED and batch.assigned_screen and batch.assigned_screen != sn:
            other_patok = get_patok_code(batch.assigned_screen)
            return JsonResponse({
                'success': False,
                'message': f"Diqqat! Ushbu pastal allaqachon {other_patok}-Patok tomonidan qabul qilib bo'lingan."
            }, status=400)

        batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
        batch.assigned_screen = sn
        batch.accepted_at = timezone.now()
        batch.accepted_by = master
        batch.assigned_branch = 'HQ' if sn <= 13 else 'UYCHI'
        batch.save(update_fields=['dispatch_status', 'assigned_screen', 'accepted_at', 'accepted_by', 'assigned_branch'])

        # Faqat AKTIV qutilarni biriktirish
        _get_batch_active_boxes(batch).update(
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
    - Statusi 'Kutilmoqda' bo'lib qolmasligi uchun ODDIY (WAITING) holatiga qaytariladi.
    - assigned_screen va assigned_branch tozalanadi (patokdan chiqariladi).
    - Qutilardan ham assigned_screen va is_received_by_patok olib tashlanadi.
    - Yuk beradigan odam (bichuv/omborchi) qaytadan tekshirib, qayta jo'natishi (chiqarishi) mumkin bo'ladi.
    - Agar pastal adashib ACCEPTED qilingan bo'lsa, faqat hali tikuvda stiker urilmagan (0 scan) bo'lsagina qaytarishga ruxsat beriladi.
    """
    master = _get_active_master(request)
    if not master:
        return JsonResponse({'success': False, 'message': "Iltimos, avval planshetga master beydjigingizni skanerlang!"}, status=401)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    batch_id = data.get('batch_id')
    reason = (data.get('reason') or "Master tomonidan qaytarildi").strip()

    if not batch_id:
        return JsonResponse({'success': False, 'message': "batch_id berilmadi"}, status=400)

    with transaction.atomic():
        batch = CuttingBatch.objects.select_for_update().select_related(
            'order_item__order',
            'order_item__article'
        ).filter(id=batch_id).first()
        if not batch:
            return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

        active_boxes_qs = _get_batch_active_boxes(batch)

        # Agar pastal allaqachon qabul qilingan (ACCEPTED) bo'lsa, undan stiker urilganmi tekshiramiz
        if batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED:
            scanned_cnt = Ticket.objects.filter(
                box__in=active_boxes_qs,
                status=Ticket.Status.SCANNED
            ).count()
            controlled_cnt = active_boxes_qs.filter(is_controlled=True).count()
            if scanned_cnt > 0 or controlled_cnt > 0:
                return JsonResponse({
                    'success': False,
                    'message': f"Rad etib bo'lmaydi! Ushbu pastal bo'yicha tikuvda {scanned_cnt} ta stiker urilgan (yoki OTK dan o'tgan)."
                }, status=400)

        prev_screen = batch.assigned_screen
        prev_patok_code = get_patok_code(prev_screen) if prev_screen else "Patok"
        master_name = master.get_full_name() or master.username

        # Oddiy holatga qaytarish (Jo'natilmagan / Kutilmayotgan holat)
        batch.dispatch_status = CuttingBatch.DispatchStatus.WAITING
        batch.assigned_screen = None
        batch.assigned_branch = None
        batch.dispatched_at = None
        batch.dispatched_by = ''
        batch.accepted_at = None
        batch.accepted_by = None
        batch.rejected_at = timezone.now()
        batch.rejection_reason = f"[{master_name}] ({prev_patok_code}): {reason}"
        batch.save(update_fields=[
            'dispatch_status',
            'assigned_screen',
            'assigned_branch',
            'dispatched_at',
            'dispatched_by',
            'accepted_at',
            'accepted_by',
            'rejected_at',
            'rejection_reason'
        ])

        # Qutilarni ham patokdan tozalash
        active_boxes_qs.update(
            assigned_screen=None,
            is_received_by_patok=False
        )

    article_name = batch.order_item.article.name if batch.order_item and batch.order_item.article else "Mahsulot"

    return JsonResponse({
        'success': True,
        'message': f"Pastal #{batch.batch_number} ({article_name}) muvaffaqiyatli QAYTARILDI va oddiy holatga o'tkazildi.",
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

    batches_qs = CuttingBatch.objects.filter(
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
        accepted_at__year=selected_year,
        accepted_at__month=selected_month,
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
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
    ).prefetch_related('items').order_by('-accepted_at')

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

        st = _compute_batch_box_and_ticket_stats(b)
        is_pastal_closed = st['is_pastal_closed']

        if is_pastal_closed:
            total_closed_pastals += 1
        else:
            total_unclosed_pastals += 1

        units_val = st['boxes_units'] or b.total_real_quantity or b.total_quantity
        total_repair_boxes_count += st['repair_boxes']
        total_units_sum += units_val

        p_code = get_patok_code(b.assigned_screen) if b.assigned_screen else '—'

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
            'total_units': units_val,
            'total_boxes': st['total_boxes'],
            'closed_boxes': st['closed_boxes'],
            'unclosed_boxes': st['unclosed_boxes'],
            'repair_boxes': st['repair_boxes'],
            'repair_units': st['repair_units'],
            'is_pastal_closed': is_pastal_closed,
            'scanned_tickets': st['scanned_tickets'],
            'total_tickets': st['total_tickets'],
            'progress_pct': st['progress_pct'],
            'can_reject': (st['scanned_tickets'] == 0 and st['closed_boxes'] == 0),
            'accepted_at': timezone.localtime(b.accepted_at).strftime("%d.%m.%Y %H:%M") if b.accepted_at else '—',
            'accepted_by_name': (b.accepted_by.get_full_name() or b.accepted_by.username) if b.accepted_by else 'Master',
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
    Bitta pastalga tegishli barcha AKTIV qutilarning batafsil ro'yxati va holati.
    Master qaysi quti tugamaganligini, qaysi operatsiyalari urilmaganligini,
    ta'mirda yoki OTK kutayotganligini ko'rishi uchun.
    """
    batch = CuttingBatch.objects.select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'accepted_by'
    ).prefetch_related('items').filter(id=batch_id).first()

    if not batch:
        return JsonResponse({'success': False, 'message': "Pastal topilmadi"}, status=404)

    order = batch.order_item.order if batch.order_item else None
    article = batch.order_item.article if batch.order_item else None
    customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
    p_code = get_patok_code(batch.assigned_screen) if batch.assigned_screen else '—'

    boxes_qs = _get_batch_active_boxes(batch).prefetch_related(
        'tickets__article_operation__operation'
    ).order_by('box_number')

    boxes = list(boxes_qs)
    total_boxes = len(boxes)

    boxes_data = []
    closed_boxes_count = 0
    unclosed_boxes_count = 0
    repair_boxes_count = 0
    total_repair_units = 0
    total_scanned_tickets_all = 0
    total_boxes_units = 0

    for bx in boxes:
        total_boxes_units += bx.quantity
        is_closed = (bx.is_controlled and bx.controlled_repair_qty == 0)
        if is_closed:
            closed_boxes_count += 1
        else:
            unclosed_boxes_count += 1

        if bx.controlled_repair_qty > 0:
            repair_boxes_count += 1
            total_repair_units += bx.controlled_repair_qty

        # Qutining aktiv biletlari (xotirada tezkor filtrlash — N+1 so'rovlarsiz)
        all_bx_tickets = [
            t for t in bx.tickets.all()
            if t.status != Ticket.Status.CANCELLED and (
                t.status == Ticket.Status.SCANNED or
                (t.article_operation and t.article_operation.is_active)
            )
        ]
        total_tickets_cnt = len(all_bx_tickets)
        scanned_tickets_cnt = sum(1 for t in all_bx_tickets if t.status == Ticket.Status.SCANNED)
        total_scanned_tickets_all += scanned_tickets_cnt

        # Hali urilmagan operatsiyalar nomlari (Master qaysi operatsiya qolganini ko'rishi uchun)
        pending_ops = []
        has_dazmol = False
        for t in all_bx_tickets:
            op_name = (
                t.article_operation.operation.name
                if (t.article_operation and t.article_operation.operation)
                else ''
            )
            op_upper = op_name.upper()
            if t.status == Ticket.Status.SCANNED:
                if 'DAZMOL' in op_upper and 'TASMA' not in op_upper and "CHO'NTAK" not in op_upper:
                    has_dazmol = True
            else:
                if op_name and op_name not in pending_ops:
                    pending_ops.append(op_name)

        if is_closed:
            status_mode = 'CLOSED'
            status_text = f"Yopilgan • OTK 1-sort: {bx.controlled_first_sort_qty}" + (f", 2-sort: {bx.controlled_second_sort_qty}" if bx.controlled_second_sort_qty else "") + (f", Brak: {bx.controlled_defect_qty}" if bx.controlled_defect_qty else "")
        elif bx.controlled_repair_qty > 0:
            status_mode = 'REPAIR'
            status_text = f"Ta'mirda ({bx.controlled_repair_qty} dona nuqsonli • 1-sort: {bx.controlled_first_sort_qty})"
        elif has_dazmol or (total_tickets_cnt > 0 and scanned_tickets_cnt == total_tickets_cnt):
            status_mode = 'WAITING_OTK'
            status_text = "Dazmoldan chiqqan • OTK nazoratini kutmoqda"
        elif scanned_tickets_cnt > 0:
            status_mode = 'IN_SEWING'
            rem_cnt = total_tickets_cnt - scanned_tickets_cnt
            status_text = f"Tikuv jarayonida ({rem_cnt} ta operatsiya qoldi)"
        else:
            status_mode = 'PENDING'
            status_text = "Tikuvga kirmagan (0 stiker urilgan)"

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
            'pending_operations': pending_ops[:5],
            'pending_operations_more': max(0, len(pending_ops) - 5),
            'status_mode': status_mode,
            'status_text': status_text,
        })

    is_pastal_closed = (total_boxes > 0 and closed_boxes_count == total_boxes)
    can_reject = (
        batch.dispatch_status in (CuttingBatch.DispatchStatus.DISPATCHED, CuttingBatch.DispatchStatus.ACCEPTED)
        and total_scanned_tickets_all == 0
        and closed_boxes_count == 0
    )

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
            'dispatch_status': batch.dispatch_status,
            'assigned_screen': batch.assigned_screen,
            'patok_code': p_code,
            'total_units': total_boxes_units or batch.total_real_quantity or batch.total_quantity,
            'total_boxes': total_boxes,
            'closed_boxes': closed_boxes_count,
            'unclosed_boxes': unclosed_boxes_count,
            'repair_boxes': repair_boxes_count,
            'repair_units': total_repair_units,
            'is_pastal_closed': is_pastal_closed,
            'can_reject': can_reject,
            'accepted_at': timezone.localtime(batch.accepted_at).strftime("%d.%m.%Y %H:%M") if batch.accepted_at else '—',
            'accepted_by_name': (batch.accepted_by.get_full_name() or batch.accepted_by.username) if batch.accepted_by else 'Master',
        },
        'boxes': boxes_data,
    })


@csrf_exempt
@require_POST
def api_lookup_pastal_by_code(request):
    """
    Planshetda pasport QR kodi (PASTAL:12, BATCH:12) yoki quti/stiker skanerlanganda
    tegishli pastalni topib, uning holati va qaysi patokda ekanligini qaytaradi.
    """
    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    raw_code = _normalize_scanner_code(data.get('code') or '')
    if not raw_code:
        return JsonResponse({'success': False, 'message': "Kod kiritilmadi"}, status=400)

    from production.passport_bot_service import resolve_code_to_entity
    batch, box, ticket, res_type, clean_code = resolve_code_to_entity(raw_code)

    if not batch and box:
        if box.cutting_batch_item and box.cutting_batch_item.batch:
            batch = box.cutting_batch_item.batch
        elif box.pastal_number and box.order:
            batch = CuttingBatch.objects.filter(
                order_item__order=box.order,
                pastal_code=box.pastal_number
            ).first()

    if not batch:
        return JsonResponse({
            'success': False,
            'message': f"Ushbu kod bo'yicha pastal topilmadi: {raw_code}"
        }, status=404)

    sn = batch.assigned_screen
    p_code = get_patok_code(sn) if sn else '—'
    p_name = get_patok_name(sn) if sn else '—'
    branch = batch.assigned_branch or (('HQ' if sn <= 13 else 'UYCHI') if sn else 'HQ')

    return JsonResponse({
        'success': True,
        'batch_id': batch.id,
        'batch_number': batch.batch_number,
        'pastal_code': batch.pastal_code or str(batch.batch_number),
        'dispatch_status': batch.dispatch_status,
        'assigned_screen': sn,
        'assigned_branch': branch,
        'patok_code': p_code,
        'patok_name': p_name,
    })

