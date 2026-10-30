import json
import re
import calendar
from datetime import datetime, date, time
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncDate

from accounts.models import User, Worker, MasterPatokHistory, MasterAbsentDay
from production.models import CuttingBatch, Box, Ticket, Order, Article, ArticleOperation
from production.services import get_patok_code, get_patok_name, parse_patok_number
from production.daily_dazmol import is_main_dazmol_operation


def _normalize_scanner_code(raw_code: str) -> str:
    """
    Zebra skaneridan kelgan matnni normallashtirish:
    - '+' belgilarini ':' yoki '-' ga to'g'irlash
    """
    text = (raw_code or '').strip()
    if not text:
        return ''
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

    master_assigned_screen = master.assigned_screen_number if master else None
    master_patok_code = get_patok_code(master_assigned_screen) if master_assigned_screen else None
    is_strictly_master = (master.role == User.Role.MASTER) if master else False

    context = {
        'master': master,
        'master_name': master_name,
        'master_initial': master_initial,
        'master_branch_display': master_branch_display,
        'master_assigned_screen': master_assigned_screen,
        'master_patok_code': master_patok_code,
        'is_strictly_master': is_strictly_master,
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
        User.Role.TECHNOLOGIST,
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
    sn = user.assigned_screen_number
    p_code = get_patok_code(sn) if sn else None

    return JsonResponse({
        'success': True,
        'master': {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'uid': user.uid,
            'role': user.get_role_display(),
            'branch': branch,
            'branch_display': "Kosonsoy (Bosh bino)" if branch == User.Branch.HQ else "Uychi filiali",
            'assigned_screen': sn,
            'patok_code': p_code,
            'is_strictly_master': (user.role == User.Role.MASTER),
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

    # Agar foydalanuvchi oddiy MASTER bo'lsa va unga patok biriktirilgan bo'lsa:
    # U FAQAT o'zining patog'ini ko'radi, boshqa patoklar unga berilmaydi!
    if master and master.role == User.Role.MASTER and master.assigned_screen_number:
        branch = master.branch or User.Branch.HQ
        is_hq = (branch == User.Branch.HQ)
        screen_numbers = [master.assigned_screen_number]
    else:
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
    master = _get_active_master(request)

    # Agar MASTER roli bo'lsa va unga boshqa patok berilgan bo'lsa, begona patokni ochishni bloklaymiz
    if master and master.role == User.Role.MASTER and master.assigned_screen_number:
        if screen_number != master.assigned_screen_number:
            p_code_allowed = get_patok_code(master.assigned_screen_number)
            return JsonResponse({
                'success': False,
                'message': f"Siz faqat o'zingizga biriktirilgan {p_code_allowed}-Patokni ko'ra olasiz!"
            }, status=403)

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

        article_image_url = ""
        if article and getattr(article, 'image', None):
            try:
                article_image_url = article.image.url
            except Exception:
                article_image_url = ""

        waiting_list.append({
            'batch_id': b.id,
            'batch_number': b.batch_number,
            'pastal_code': b.pastal_code or str(b.batch_number),
            'order_number': order.order_number if order else '—',
            'customer_name': customer_name,
            'article_name': article.name if article else 'Model',
            'article_code': article.code if article else '',
            'article_image_url': article_image_url,
            'cutter_name': b.cutter_name or '—',
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

    # Agar MASTER roli bo'lsa va boshqa patok uchun qabul qilmoqchi bo'lsa, cheklaymiz
    if master.role == User.Role.MASTER and master.assigned_screen_number:
        if sn != master.assigned_screen_number:
            p_code_allowed = get_patok_code(master.assigned_screen_number)
            return JsonResponse({
                'success': False,
                'message': f"Siz faqat o'zingizga biriktirilgan {p_code_allowed}-Patok uchun pastal qabul qila olasiz!"
            }, status=403)

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

    batches_qs = CuttingBatch.objects.filter(
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
        accepted_at__year=selected_year,
        accepted_at__month=selected_month,
        accepted_by=master,
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    )

    if master.assigned_screen_number:
        batches_open_on_screen = CuttingBatch.objects.filter(
            assigned_screen=master.assigned_screen_number,
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
        ).exclude(order_item__order__status=Order.Status.CANCELLED)
        batches_qs = (batches_qs | batches_open_on_screen).distinct()

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
        'scope': 'MY',
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

    master = _get_active_master(request)
    if master and master.role == User.Role.MASTER and master.assigned_screen_number:
        if batch.assigned_screen and batch.assigned_screen != master.assigned_screen_number:
            p_code_allowed = get_patok_code(master.assigned_screen_number)
            return JsonResponse({
                'success': False,
                'message': f"Ushbu pastal boshqa patokka tegishli. Siz faqat {p_code_allowed}-Patok pastallarini ko'ra olasiz!"
            }, status=403)

    order = batch.order_item.order if batch.order_item else None
    article = batch.order_item.article if batch.order_item else None
    customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
    p_code = get_patok_code(batch.assigned_screen) if batch.assigned_screen else '—'

    boxes_qs = _get_batch_active_boxes(batch).prefetch_related(
        'tickets__article_operation__operation',
        'tickets__worker'
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
        bx_operations = []

        for t in all_bx_tickets:
            op = t.article_operation.operation if t.article_operation else None
            op_name = op.name if op else "Operatsiya"
            op_code = op.code if op else ""
            seq = t.article_operation.sequence if t.article_operation else 0

            worker_name = ""
            if t.worker:
                worker_name = t.worker.full_name or t.worker.worker_id or "Ishchi"

            patok_code = get_patok_code(t.screen_number) if t.screen_number else "—"
            scanned_at_str = timezone.localtime(t.scanned_at).strftime("%d.%m.%Y %H:%M") if t.scanned_at else ""

            bx_operations.append({
                'ticket_id': t.id,
                'ticket_code': t.ticket_code,
                'stiker_code': t.stiker_code or "",
                'sequence': seq,
                'operation_name': op_name,
                'operation_code': op_code,
                'is_scanned': (t.status == Ticket.Status.SCANNED),
                'worker_name': worker_name,
                'patok_code': patok_code,
                'screen_number': t.screen_number,
                'scanned_at': scanned_at_str,
            })

            if t.status == Ticket.Status.SCANNED:
                if is_main_dazmol_operation(op_name):
                    has_dazmol = True
            else:
                if op_name and op_name not in pending_ops:
                    pending_ops.append(op_name)

        bx_operations.sort(key=lambda x: (x['sequence'] or 999, x['ticket_id']))

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
            'operations': bx_operations,
            'status_mode': status_mode,
            'status_text': status_text,
        })

    is_pastal_closed = (total_boxes > 0 and closed_boxes_count == total_boxes)
    can_reject = (
        batch.dispatch_status in (CuttingBatch.DispatchStatus.DISPATCHED, CuttingBatch.DispatchStatus.ACCEPTED)
        and total_scanned_tickets_all == 0
        and closed_boxes_count == 0
    )

    article_image_url = ""
    if article and getattr(article, 'image', None):
        try:
            article_image_url = article.image.url
        except Exception:
            article_image_url = ""

    size_stats = {}
    for bx in boxes:
        sz = bx.razmer or "Noma'lum"
        if sz not in size_stats:
            size_stats[sz] = {'size': sz, 'boxes_count': 0, 'quantity': 0}
        size_stats[sz]['boxes_count'] += 1
        size_stats[sz]['quantity'] += bx.quantity
    sizes_summary = sorted(list(size_stats.values()), key=lambda x: str(x['size']))

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
            'article_image_url': article_image_url,
            'cutter_name': batch.cutter_name or '—',
            'actual_weight_kg': float(batch.actual_weight_kg) if batch.actual_weight_kg else 0.0,
            'dispatch_status': batch.dispatch_status,
            'assigned_screen': batch.assigned_screen,
            'assigned_branch': batch.assigned_branch,
            'patok_code': p_code,
            'total_units': total_boxes_units or batch.total_real_quantity or batch.total_quantity,
            'total_boxes': total_boxes,
            'closed_boxes': closed_boxes_count,
            'unclosed_boxes': unclosed_boxes_count,
            'repair_boxes': repair_boxes_count,
            'repair_units': total_repair_units,
            'is_pastal_closed': is_pastal_closed,
            'can_accept': (batch.dispatch_status == CuttingBatch.DispatchStatus.DISPATCHED),
            'can_reject': can_reject,
            'created_at': timezone.localtime(batch.created_at).strftime("%d.%m.%Y %H:%M") if batch.created_at else '—',
            'dispatched_at': timezone.localtime(batch.dispatched_at).strftime("%d.%m.%Y %H:%M") if batch.dispatched_at else '—',
            'dispatched_by': batch.dispatched_by or 'Bichuv',
            'accepted_at': timezone.localtime(batch.accepted_at).strftime("%d.%m.%Y %H:%M") if batch.accepted_at else '—',
            'accepted_by_name': (batch.accepted_by.get_full_name() or batch.accepted_by.username) if batch.accepted_by else 'Master',
            'sizes_summary': sizes_summary,
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

    master = _get_active_master(request)
    if master and master.role == User.Role.MASTER and master.assigned_screen_number:
        if batch.assigned_screen and batch.assigned_screen != master.assigned_screen_number:
            p_code_allowed = get_patok_code(master.assigned_screen_number)
            return JsonResponse({
                'success': False,
                'message': f"Ushbu pastal boshqa patokka tegishli. Siz faqat {p_code_allowed}-Patok pastallarini ochishingiz mumkin!"
            }, status=403)

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


def _get_master_statistics_data(master, month_start_date, month_end_date, month_start_dt, month_end_dt, screen_day_stats, article_targets):
    """
    Bitta master foydalanuvchi bo'yicha oylik to'liq hisobotni yig'adi:
    1. Ish kunlari (faqat kamida 1 ta stiker urilgan kunlar) va o'rtacha patok quvvati (%)
    2. Pastallar balansi: Kirdi (Meto), Chiqdi (1-s + 2-s), Ta'mir, Brak, Yo'qotish (Farq)
    3. Yopilgan va ochiq pastallar
    4. Gibrid: Oddiy tikuvchi bo'lib ishlagan kunlari va topgan ishbay maoshi
    """
    today = timezone.localdate()

    # 1. Master biriktirilgan kunlar va patoklar xaritasi: {cur_date: screen_number}
    histories = list(MasterPatokHistory.objects.filter(
        user=master,
        start_date__lte=month_end_date
    ).filter(
        Q(end_date__gte=month_start_date) | Q(end_date__isnull=True)
    ).order_by('start_date'))

    day_screen_map = {}
    if histories:
        for h in histories:
            s_date = max(h.start_date, month_start_date)
            e_date = min(h.end_date or today, month_end_date)
            cur = s_date
            while cur <= e_date:
                day_screen_map[cur] = h.screen_number
                cur += timezone.timedelta(days=1)
    else:
        if master.role == User.Role.MASTER and master.assigned_screen_number:
            sn = master.assigned_screen_number
            s_date = max(master.date_joined.date(), month_start_date)
            e_date = min(today, month_end_date)
            cur = s_date
            while cur <= e_date:
                day_screen_map[cur] = sn
                cur += timezone.timedelta(days=1)

    # 2. Master kelmagan / kasal bo'lgan kunlari (MasterAbsentDay)
    absent_dates = set(
        MasterAbsentDay.objects.filter(
            user=master,
            date__range=(month_start_date, month_end_date)
        ).values_list('date', flat=True)
    )

    for abs_d in absent_dates:
        if abs_d not in day_screen_map:
            day_screen_map[abs_d] = master.assigned_screen_number or 1

    # 3. Master ish kunlari va kunlik patok quvvati
    working_days = []
    quvvat_sum = 0.0
    active_working_days_count = 0

    for cur_date, sn in sorted(day_screen_map.items()):
        stats = screen_day_stats.get((sn, cur_date))
        is_absent = (cur_date in absent_dates)

        if (stats and stats['tickets'] > 0) or is_absent:
            if stats and stats['tickets'] > 0:
                grand_total_earnings = stats['earnings']
                article_ids = stats['article_ids']
                target_reja_values = [article_targets[aid] for aid in article_ids if aid in article_targets]
                if target_reja_values:
                    avg_daily_target = sum(target_reja_values) / len(target_reja_values)
                    quvvat = round((grand_total_earnings / avg_daily_target) * 100, 1) if avg_daily_target > 0 else 0.0
                else:
                    quvvat = 0.0
                tickets_cnt = stats['tickets']
            else:
                grand_total_earnings = 0
                quvvat = 0.0
                tickets_cnt = 0

            # Muhim qoida: Agar master kelmagan / kasal bo'lsa, bu kun o'rtacha arifmetik hisob-kitobdan chiqariladi!
            if not is_absent and tickets_cnt > 0:
                quvvat_sum += quvvat
                active_working_days_count += 1

            working_days.append({
                'date': cur_date.strftime("%Y-%m-%d"),
                'date_str': cur_date.strftime("%d.%m.%Y"),
                'screen_number': sn,
                'patok_code': get_patok_code(sn),
                'patok_name': get_patok_name(sn),
                'tickets_count': tickets_cnt,
                'earnings': grand_total_earnings,
                'earnings_formatted': f"{grand_total_earnings:,.0f}".replace(",", " "),
                'quvvat': quvvat,
                'is_absent': is_absent,
            })

    master_working_days_count = active_working_days_count
    avg_quvvat_percent = round(quvvat_sum / master_working_days_count, 1) if master_working_days_count > 0 else 0.0
    absent_days_count = len([w for w in working_days if w['is_absent']])

    # 3. Pastallar monitoringi (Yopilgan / Ochiq / Yo'qotish farqi)
    batches_qs = CuttingBatch.objects.filter(
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED
    ).exclude(
        order_item__order__status=Order.Status.CANCELLED
    )

    batches_accepted_in_month = batches_qs.filter(
        accepted_by=master,
        accepted_at__range=(month_start_dt, month_end_dt)
    )

    if master.role == User.Role.MASTER and master.assigned_screen_number:
        batches_open_on_screen = batches_qs.filter(
            assigned_screen=master.assigned_screen_number
        )
        combined_batches = (batches_accepted_in_month | batches_open_on_screen).distinct()
    else:
        combined_batches = batches_accepted_in_month.distinct()

    combined_batches = combined_batches.select_related(
        'order_item__order__customer',
        'order_item__article__model',
        'accepted_by'
    ).prefetch_related('items').order_by('-accepted_at')

    pastals_list = []
    closed_pastals_count = 0
    open_pastals_count = 0
    waiting_otk_count = 0
    in_sewing_count = 0

    total_in_qty = 0
    total_out_qty = 0
    total_first_sort = 0
    total_second_sort = 0
    total_repair = 0
    total_defect = 0

    for b in combined_batches:
        st = _compute_batch_box_and_ticket_stats(b)
        boxes = list(_get_batch_active_boxes(b))
        is_closed = st['is_pastal_closed']

        in_qty = b.total_real_quantity or sum(bx.quantity for bx in boxes) or b.total_quantity
        first_sort = sum(bx.controlled_first_sort_qty for bx in boxes)
        second_sort = sum(bx.controlled_second_sort_qty for bx in boxes)
        repair = sum(bx.controlled_repair_qty for bx in boxes)
        defect = sum(bx.controlled_defect_qty for bx in boxes)
        out_qty = first_sort + second_sort
        diff = out_qty - in_qty

        if is_closed:
            closed_pastals_count += 1
            status_label = "Yopilgan"
            status_color = "emerald"
        else:
            open_pastals_count += 1
            if st['progress_pct'] >= 100 or (st['total_tickets'] > 0 and st['total_tickets'] == st['scanned_tickets']):
                waiting_otk_count += 1
                status_label = "OTK kutmoqda"
                status_color = "amber"
            else:
                in_sewing_count += 1
                status_label = "Tikuvda"
                status_color = "sky"

        total_in_qty += in_qty
        total_out_qty += out_qty
        total_first_sort += first_sort
        total_second_sort += second_sort
        total_repair += repair
        total_defect += defect

        order = b.order_item.order if b.order_item else None
        article = b.order_item.article if b.order_item else None
        customer_name = order.customer.name if (order and order.customer) else (order.client_name if order else '—')
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
            'in_qty': in_qty,
            'out_qty': out_qty,
            'first_sort': first_sort,
            'second_sort': second_sort,
            'repair': repair,
            'defect': defect,
            'diff': diff,
            'is_closed': is_closed,
            'status_label': status_label,
            'status_color': status_color,
            'progress_pct': st['progress_pct'],
            'total_boxes': st['total_boxes'],
            'closed_boxes': st['closed_boxes'],
            'accepted_at': timezone.localtime(b.accepted_at).strftime("%d.%m.%Y %H:%M") if b.accepted_at else '—',
        })

    total_diff = total_out_qty - total_in_qty
    loss_pct = round((abs(total_diff) / total_in_qty * 100), 1) if (total_in_qty > 0 and total_diff < 0) else 0.0

    # 4. Gibrid: Oddiy tikuvchi sifatida ishlagan kunlari va topgan puli
    worker = Worker.objects.filter(user=master).first()
    if worker:
        worker_tickets = Ticket.objects.filter(
            worker=worker,
            status=Ticket.Status.SCANNED,
            scanned_at__range=(month_start_dt, month_end_dt)
        )
        sewer_tickets_count = worker_tickets.count()
        sewer_earnings = int(worker_tickets.aggregate(tot=Sum('total_amount'))['tot'] or 0)
        sewer_days_count = worker_tickets.annotate(d=TruncDate('scanned_at')).values('d').distinct().count()
    else:
        sewer_tickets_count = 0
        sewer_earnings = 0
        sewer_days_count = 0

    return {
        'master_id': master.id,
        'master_name': master.get_full_name() or master.username,
        'username': master.username,
        'current_role': master.role,
        'role_display': master.get_role_display(),
        'is_currently_master': (master.role == User.Role.MASTER),
        'branch': master.branch or 'HQ',
        'branch_display': "Kosonsoy" if master.branch == 'HQ' else "Uychi",
        'assigned_screen': master.assigned_screen_number,
        'assigned_patok_name': master.assigned_patok_name or "—",
        'master_working_days': master_working_days_count,
        'absent_days_count': absent_days_count,
        'avg_quvvat_percent': avg_quvvat_percent,
        'working_days': working_days,
        'total_pastals': len(pastals_list),
        'closed_pastals': closed_pastals_count,
        'open_pastals': open_pastals_count,
        'waiting_otk_pastals': waiting_otk_count,
        'in_sewing_pastals': in_sewing_count,
        'total_in_qty': total_in_qty,
        'total_out_qty': total_out_qty,
        'total_first_sort': total_first_sort,
        'total_second_sort': total_second_sort,
        'total_repair': total_repair,
        'total_defect': total_defect,
        'total_diff': total_diff,
        'loss_pct': loss_pct,
        'pastals_list': pastals_list,
        'sewer_days': sewer_days_count,
        'sewer_earnings': sewer_earnings,
        'sewer_earnings_formatted': f"{sewer_earnings:,.0f}".replace(",", " "),
        'sewer_tickets': sewer_tickets_count,
    }


@login_required(login_url='root_login')
def master_statistics_view(request):
    """
    Superadmin & Filial Admini uchun Masterlar Statistikasi Dashboardi:
    - Oylik filtr (?month=YYYY-MM)
    - Filial filtri (?branch=HQ|UYCHI|ALL)
    - Ish kunlari arifmetik o'rtacha Patok Quvvati (%)
    - Pastallar yopilishi va yo'qotish (farq) tahlili
    - Tikuvchi ishbay maoshi gibrid ko'rinishi
    """
    user = request.user
    is_super = user.is_superadmin() or user.is_superuser
    is_branch_admin = (user.role == User.Role.BRANCH_ADMIN)
    is_mgr = getattr(user, 'is_manager', lambda: False)() or user.role in [
        User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.BRANCH_ADMIN, User.Role.MANAGER, User.Role.PLAN, User.Role.TECHNOLOGIST
    ]
    if not (is_super or is_mgr):
        messages.error(request, "Ushbu sahifaga kirish uchun ruxsat yo'q!")
        return redirect('root_login')

    today = timezone.localdate()
    month_str = request.GET.get('month', '').strip()
    if not month_str or not re.match(r'^\d{4}-\d{2}$', month_str):
        month_str = today.strftime('%Y-%m')

    year, month = map(int, month_str.split('-'))
    _, last_day = calendar.monthrange(year, month)
    month_start_date = date(year, month, 1)
    month_end_date = date(year, month, last_day)

    tz = timezone.get_current_timezone()
    month_start_dt = timezone.make_aware(datetime.combine(month_start_date, time.min), tz)
    month_end_dt = timezone.make_aware(datetime.combine(month_end_date, time.max), tz)

    # Oldingi va keyingi oylar
    prev_month_date = (month_start_date - timezone.timedelta(days=1)).replace(day=1)
    prev_month_str = prev_month_date.strftime('%Y-%m')
    next_month_date = (month_end_date + timezone.timedelta(days=1))
    next_month_str = next_month_date.strftime('%Y-%m')

    uzbek_months = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
    }
    month_name_uz = f"{uzbek_months[month]} {year}"

    # Filial filtri
    branch_filter = request.GET.get('branch', 'ALL')
    if is_branch_admin:
        branch_filter = user.branch or 'HQ'

    # Reja va norma xaritalari
    article_operation_prices = {}
    for row in ArticleOperation.objects.filter(is_active=True).values('article_id').annotate(tot_price=Sum('price_per_unit')):
        article_operation_prices[row['article_id']] = float(row['tot_price'] or 0.0)

    article_targets = {}
    for art in Article.objects.select_related('model').all():
        norm = art.daily_norm or (art.model.daily_norm if art.model else None) or 1000
        u_price = article_operation_prices.get(art.id, 0.0)
        article_targets[art.id] = (u_price * float(norm)) if (u_price > 0 and norm > 0) else 1000.0

    # Shu oy bo'yicha barcha skanerlangan biletlar
    ticket_logs = Ticket.objects.filter(
        scanned_at__range=(month_start_dt, month_end_dt),
        status=Ticket.Status.SCANNED,
        box__assigned_screen__isnull=False
    ).annotate(
        scan_day=TruncDate('scanned_at')
    ).values(
        'box__assigned_screen',
        'scan_day',
        'article_operation__article_id'
    ).annotate(
        tickets_count=Count('id'),
        earnings=Sum('total_amount')
    )

    screen_day_stats = {}
    for row in ticket_logs:
        key = (row['box__assigned_screen'], row['scan_day'])
        if key not in screen_day_stats:
            screen_day_stats[key] = {'tickets': 0, 'earnings': 0, 'article_ids': set()}
        screen_day_stats[key]['tickets'] += row['tickets_count']
        screen_day_stats[key]['earnings'] += int(row['earnings'] or 0)
        if row['article_operation__article_id']:
            screen_day_stats[key]['article_ids'].add(row['article_operation__article_id'])

    # Shu oyda ishlagan barcha masterlar
    master_histories = MasterPatokHistory.objects.filter(
        start_date__lte=month_end_date
    ).filter(
        Q(end_date__gte=month_start_date) | Q(end_date__isnull=True)
    ).select_related('user')

    master_user_ids = set(master_histories.values_list('user_id', flat=True))

    current_masters = User.objects.filter(role=User.Role.MASTER)
    master_user_ids.update(current_masters.values_list('id', flat=True))

    accepted_master_ids = CuttingBatch.objects.filter(
        accepted_at__range=(month_start_dt, month_end_dt),
        accepted_by__isnull=False
    ).values_list('accepted_by_id', flat=True)
    master_user_ids.update(accepted_master_ids)

    masters_qs = User.objects.filter(id__in=master_user_ids).order_by('first_name', 'last_name')
    if branch_filter in ['HQ', 'UYCHI']:
        masters_qs = masters_qs.filter(branch=branch_filter)

    masters_data = []
    for m in masters_qs:
        m_stat = _get_master_statistics_data(
            m, month_start_date, month_end_date, month_start_dt, month_end_dt,
            screen_day_stats, article_targets
        )
        masters_data.append(m_stat)

    # Eng yuqori quvvat va ish kuni bo'yicha tartiblash
    masters_data.sort(key=lambda x: (x['avg_quvvat_percent'], x['master_working_days']), reverse=True)

    # Umumiy KPI lar
    grand_total_masters = len(masters_data)
    grand_working_days = sum(m['master_working_days'] for m in masters_data)
    all_quvvats = [m['avg_quvvat_percent'] for m in masters_data if m['master_working_days'] > 0]
    grand_avg_quvvat = round(sum(all_quvvats) / len(all_quvvats), 1) if all_quvvats else 0.0

    grand_total_pastals = sum(m['total_pastals'] for m in masters_data)
    grand_closed_pastals = sum(m['closed_pastals'] for m in masters_data)
    grand_open_pastals = sum(m['open_pastals'] for m in masters_data)
    grand_waiting_otk = sum(m['waiting_otk_pastals'] for m in masters_data)
    grand_in_sewing = sum(m['in_sewing_pastals'] for m in masters_data)

    grand_in_qty = sum(m['total_in_qty'] for m in masters_data)
    grand_out_qty = sum(m['total_out_qty'] for m in masters_data)
    grand_diff = grand_out_qty - grand_in_qty
    grand_sewer_earnings = sum(m['sewer_earnings'] for m in masters_data)

    context = {
        'month_str': month_str,
        'year': year,
        'month': month,
        'month_name_uz': month_name_uz,
        'prev_month_str': prev_month_str,
        'next_month_str': next_month_str,
        'branch_filter': branch_filter,
        'is_branch_admin': is_branch_admin,
        'user_branch': user.branch,
        'masters_data': masters_data,
        'masters_data_json': json.dumps(masters_data, ensure_ascii=False),
        'grand_total_masters': grand_total_masters,
        'grand_working_days': grand_working_days,
        'grand_avg_quvvat': grand_avg_quvvat,
        'grand_total_pastals': grand_total_pastals,
        'grand_closed_pastals': grand_closed_pastals,
        'grand_open_pastals': grand_open_pastals,
        'grand_waiting_otk': grand_waiting_otk,
        'grand_in_sewing': grand_in_sewing,
        'grand_in_qty': grand_in_qty,
        'grand_out_qty': grand_out_qty,
        'grand_diff': grand_diff,
        'grand_sewer_earnings': grand_sewer_earnings,
        'grand_sewer_earnings_formatted': f"{grand_sewer_earnings:,.0f}".replace(",", " "),
    }
    return render(request, 'production/master_statistics.html', context)


@login_required(login_url='root_login')
@require_GET
def api_master_statistics_detail(request, user_id):
    """
    Modal uchun Master tafsiloti API si
    """
    master = get_object_or_404(User, id=user_id)
    today = timezone.localdate()
    month_str = request.GET.get('month', '').strip()
    if not month_str or not re.match(r'^\d{4}-\d{2}$', month_str):
        month_str = today.strftime('%Y-%m')

    year, month = map(int, month_str.split('-'))
    _, last_day = calendar.monthrange(year, month)
    month_start_date = date(year, month, 1)
    month_end_date = date(year, month, last_day)

    tz = timezone.get_current_timezone()
    month_start_dt = timezone.make_aware(datetime.combine(month_start_date, time.min), tz)
    month_end_dt = timezone.make_aware(datetime.combine(month_end_date, time.max), tz)

    article_operation_prices = {}
    for row in ArticleOperation.objects.filter(is_active=True).values('article_id').annotate(tot_price=Sum('price_per_unit')):
        article_operation_prices[row['article_id']] = float(row['tot_price'] or 0.0)

    article_targets = {}
    for art in Article.objects.select_related('model').all():
        norm = art.daily_norm or (art.model.daily_norm if art.model else None) or 1000
        u_price = article_operation_prices.get(art.id, 0.0)
        article_targets[art.id] = (u_price * float(norm)) if (u_price > 0 and norm > 0) else 1000.0

    ticket_logs = Ticket.objects.filter(
        scanned_at__range=(month_start_dt, month_end_dt),
        status=Ticket.Status.SCANNED,
        box__assigned_screen__isnull=False
    ).annotate(
        scan_day=TruncDate('scanned_at')
    ).values(
        'box__assigned_screen',
        'scan_day',
        'article_operation__article_id'
    ).annotate(
        tickets_count=Count('id'),
        earnings=Sum('total_amount')
    )

    screen_day_stats = {}
    for row in ticket_logs:
        key = (row['box__assigned_screen'], row['scan_day'])
        if key not in screen_day_stats:
            screen_day_stats[key] = {'tickets': 0, 'earnings': 0, 'article_ids': set()}
        screen_day_stats[key]['tickets'] += row['tickets_count']
        screen_day_stats[key]['earnings'] += int(row['earnings'] or 0)
        if row['article_operation__article_id']:
            screen_day_stats[key]['article_ids'].add(row['article_operation__article_id'])

    data = _get_master_statistics_data(
        master, month_start_date, month_end_date, month_start_dt, month_end_dt,
        screen_day_stats, article_targets
    )
    return JsonResponse({'success': True, 'data': data})


@login_required(login_url='root_login')
@require_POST
def api_toggle_master_absent_day(request):
    """
    Superadmin, Admin yoki Katta Texnolog tomonidan masterning ma'lum kungi kelmaganligini (kasallik / sababli)
    belgilash yoki qaytarib ish kuni qilish uchun toggle API.
    Kutiladi: POST { "user_id": 12, "date": "2026-10-15", "reason": "Kasal bo'ldi" }
    """
    user = request.user
    is_allowed = (
        user.is_superadmin() or user.is_superuser or
        user.role in [
            User.Role.SUPER_ADMIN,
            User.Role.ADMIN,
            User.Role.BRANCH_ADMIN,
            User.Role.TECHNOLOGIST,
            User.Role.MANAGER,
        ]
    )
    if not is_allowed:
        return JsonResponse({'success': False, 'message': "Ruxsat etilmagan amal!"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    user_id = data.get('user_id')
    date_str = (data.get('date') or '').strip()
    reason = (data.get('reason') or '').strip()

    if not user_id or not date_str:
        return JsonResponse({'success': False, 'message': "Master ID yoki sana berilmadi"}, status=400)

    try:
        absent_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({'success': False, 'message': "Sana formati noto'g'ri (YYYY-MM-DD kutiladi)"}, status=400)

    master = get_object_or_404(User, id=user_id)

    # Agar filial admini bo'lsa va master boshqa filialda bo'lsa cheklash
    if user.role == User.Role.BRANCH_ADMIN and user.branch and master.branch != user.branch:
        return JsonResponse({'success': False, 'message': "Siz faqat o'z filialingiz masterlarini o'zgartira olasiz!"}, status=403)

    existing = MasterAbsentDay.objects.filter(user=master, date=absent_date).first()
    if existing:
        existing.delete()
        is_absent = False
        msg = f"{master.get_full_name() or master.username} uchun {absent_date.strftime('%d.%m.%Y')} sanasi ISH KUNI deb belgilandi (o'rtacha quvvatga qo'shildi)."
    else:
        MasterAbsentDay.objects.create(
            user=master,
            date=absent_date,
            reason=reason or "Kelmagan / Kasal",
            created_by=user
        )
        is_absent = True
        msg = f"{master.get_full_name() or master.username} uchun {absent_date.strftime('%d.%m.%Y')} sanasi KELMAGAN (KASAL) deb belgilandi va hisob-kitobdan chiqarildi."

    return JsonResponse({
        'success': True,
        'is_absent': is_absent,
        'message': msg,
        'date': date_str,
    })



