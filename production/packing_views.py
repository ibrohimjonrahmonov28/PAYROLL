"""
Beta qadoqlash tartibi (egasi talabi 2026-11-12) — bitta /beta/ sahifasi, tablar bilan; faqat Order.packing_beta zakazlar.
Superadmin bitta akkaunt bilan hamma tabni ko'radi (test uchun akkaunt almashtirmaydi):
- OTK           (superadmin): CONTROL stikeri / TAMIR cheki → 2-sort va ta'mir donalari → saqlash (/control/ ning o'sha
                              server funksiyalari); ta'mir bo'lsa chek A4 ga chiqadi; cheklarni qayta chop etish;
- Taxlovchi     (PACKING):    avval badge, keyin CONTROL stikeri yoki TAMIR cheki;
- Sanoqchi      (COUNTER):    patoklardagi 2-sortni (brak ham) sanab qabul qiladi, savatni upakovkaga jo'natadi,
                              xato urilgan taxlovchi skanini bekor qiladi;
- Upakovka      (PACKING):    qadoqlash kutayotgan ishlar, bugun kim qancha taxladi, kelgan 2-sort;
- Beta zakazlar (superadmin): qaysi zakazlar beta tartibda ishlashini belgilaydi.
Muzlatilgan /control/ sahifasi o'zgarmaydi. Taxlovchi puli hozircha yo'q (keyin operatsiya shabloni orqali).
"""
import functools
import json
from collections import OrderedDict, defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User, Worker
from production.control_views import control_box_lookup_api, control_submit_inspection_api
from production.models import (Box, BoxQualityInspectionLog, Order, PackingScan, RepairReceipt, SecondSortBatch,
                               _generate_qr_data_uri)
from production.packing_services import (available_for_packing, find_receipt, packed_total, parse_packing_code,
                                         portion_total)
from production.services import get_patok_code
from production.sewing_statistics_views import natural_size_sort_key
from production.terminal_views import extract_worker_code

HUB_ROLES = (User.Role.PACKING, User.Role.COUNTER)

# (tab, nomi, kimga ochiq — superadmin hammasini ko'radi)
TABS = (
    ('otk', "OTK", ()),
    ('taxlovchi', "Taxlovchi", (User.Role.PACKING,)),
    ('sanoqchi', "Sanoqchi", (User.Role.COUNTER,)),
    ('upakovka', "Upakovka", (User.Role.PACKING,)),
    ('zakazlar', "Beta zakazlar", ()),
)


def _role_required(*roles):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"{reverse('root_login')}?next={request.path}")
            if user.is_superadmin() or user.role in roles:
                return view(request, *args, **kwargs)
            if '/api/' in request.path:
                return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat yo'q"}, status=403)
            messages.error(request, "Bu bo'limga ruxsatingiz yo'q.")
            return redirect('packing:hub' if user.role in HUB_ROLES else 'root_login')
        return wrapper
    return decorator


def _hub_url(**params):
    query = urlencode({key: value for key, value in params.items() if value not in (None, '')})
    return f"{reverse('packing:hub')}?{query}" if query else reverse('packing:hub')


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return request.POST


def _today_range():
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timezone.timedelta(days=1)


def _patok_label(screen):
    return get_patok_code(screen) if screen else 'Patoksiz'


def _worker_today_total(worker):
    start, end = _today_range()
    return PackingScan.objects.filter(worker=worker, is_cancelled=False, scanned_at__gte=start,
                                      scanned_at__lt=end).aggregate(s=Sum('quantity'))['s'] or 0


def _find_worker(raw):
    code = extract_worker_code(raw or '')
    if not code:
        return None
    workers = Worker.objects.filter(is_active=True).select_related('user')
    worker = workers.filter(Q(worker_id__iexact=code) | Q(user__uid=code)).first()
    if not worker and code.isdigit():
        worker = workers.filter(Q(worker_id__iexact=f"W-{int(code):03d}") | Q(worker_id__iexact=f"W-{code}")).first()
    return worker


def _article_card(box):
    article = box.target_article
    image = ''
    if article and article.image:
        try:
            image = article.image.url
        except ValueError:
            image = ''
    return article, image


def _group_by_order_article(boxes_with_qty, notes_by_box=None):
    """[(box, qty)] -> zakaz → artikul (rasmi bilan) → razmerlar va qutilar bo'yicha son."""
    groups = OrderedDict()
    notes_by_box = notes_by_box or {}
    for box, qty in boxes_with_qty:
        article, image = _article_card(box)
        key = (box.order_id, article.id if article else 0)
        card = groups.setdefault(key, {
            'order': box.order, 'article': article, 'image': image,
            'sizes': OrderedDict(), 'boxes_map': OrderedDict(), 'total': 0,
        })
        size = box.razmer or '—'
        card['sizes'][size] = card['sizes'].get(size, 0) + qty
        box_entry = card['boxes_map'].setdefault(box.id, {
            'box_code': box.box_code, 'razmer': size, 'qty': 0, 'notes': notes_by_box.get(box.id, []),
        })
        box_entry['qty'] += qty
        card['total'] += qty
    for card in groups.values():
        card['sizes'] = OrderedDict(sorted(card['sizes'].items(), key=lambda item: natural_size_sort_key(item[0])))
        card['boxes'] = sorted(card.pop('boxes_map').values(),
                               key=lambda b: (natural_size_sort_key(b['razmer']), b['box_code']))
        card['box_count'] = len(card['boxes'])
    return list(groups.values())


BOX_RELATED = ('box__order', 'box__article', 'box__cutting_batch_item__batch__order_item__article')
SCAN_RELATED = BOX_RELATED + ('worker',)


def _pending_second_sort():
    return BoxQualityInspectionLog.objects.filter(
        box__order__packing_beta=True, second_sort_qty__gt=0, second_sort_batch__isnull=True)


def _open_basket(user, create=False):
    basket = SecondSortBatch.objects.filter(counter=user, status=SecondSortBatch.Status.BASKET).first()
    if not basket and create:
        basket = SecondSortBatch.objects.create(counter=user)
    return basket


def _screen_filter(screen):
    return Q(box__assigned_screen__isnull=True) if not screen else Q(box__assigned_screen=screen)


def _today_beta_scans(limit=15):
    start, end = _today_range()
    qs = (PackingScan.objects.filter(is_cancelled=False, box__order__packing_beta=True,
                                     scanned_at__gte=start, scanned_at__lt=end)
          .select_related(*SCAN_RELATED).order_by('-scanned_at'))
    total = qs.aggregate(s=Sum('quantity'))['s'] or 0
    return list(qs[:limit]), total


def _tab_badges(allowed_keys):
    """Tab sarlavhalaridagi jonli ko'rsatkichlar (0 bo'lsa ko'rsatilmaydi)."""
    badges = {}
    if 'otk' in allowed_keys:
        badges['otk'] = RepairReceipt.objects.filter(box__order__packing_beta=True, returned_at__isnull=True).count()
    if 'sanoqchi' in allowed_keys:
        badges['sanoqchi'] = _pending_second_sort().aggregate(s=Sum('second_sort_qty'))['s'] or 0
    if 'upakovka' in allowed_keys:
        badges['upakovka'] = (PackingScan.objects.filter(is_cancelled=False, box__order__packing_beta=True)
                              .aggregate(s=Sum('quantity'))['s'] or 0)
    if 'zakazlar' in allowed_keys:
        badges['zakazlar'] = (Order.objects.exclude(status__in=[Order.Status.ARCHIVED, Order.Status.CANCELLED])
                              .filter(packing_beta=True).count())
    return badges


# ------------------------------------------------------------------ bitta sahifa, tablar

def _otk_context(request):
    since = timezone.now() - timezone.timedelta(days=7)
    receipts = list(RepairReceipt.objects.filter(created_at__gte=since, box__order__packing_beta=True)
                    .select_related(*BOX_RELATED, 'inspector').order_by('-created_at')[:100])
    active_count = 0
    for receipt in receipts:
        box = receipt.box
        receipt.patok_code = _patok_label(box.assigned_screen) if box.assigned_screen else '—'
        receipt.is_repair_active = receipt.is_open                 # har ta'mir aylanmasi — alohida chek
        receipt.remaining_repair_qty = receipt.quantity if receipt.is_open else 0
        receipt.article_code = box.target_article.code if box.target_article else ''
        if receipt.is_repair_active:
            active_count += 1
    return {'receipts': receipts, 'active_repairs_count': active_count}


def _taxlovchi_context(request):
    recent_scans, today_total = _today_beta_scans(limit=12)
    return {'recent_scans': recent_scans, 'today_total': today_total}


def _log_notes_map(logs):
    notes = defaultdict(list)
    for log in logs:
        if log.notes:
            text = log.notes.strip()
            if text and text not in notes[log.box_id]:
                notes[log.box_id].append(text)
    return notes


def _sanoqchi_context(request):
    per_patok = defaultdict(int)
    for screen, qty in _pending_second_sort().values_list('box__assigned_screen', 'second_sort_qty'):
        per_patok[screen or 0] += qty
    patoks = [{'screen': sn, 'code': _patok_label(sn), 'qty': qty} for sn, qty in sorted(per_patok.items())]

    selected = _int_or_none(request.GET.get('patok'))
    cards = []
    if selected is not None:
        logs = list(_pending_second_sort().filter(_screen_filter(selected)).select_related(*BOX_RELATED))
        cards = _group_by_order_article(((log.box, log.second_sort_qty) for log in logs), _log_notes_map(logs))

    basket = _open_basket(request.user)
    basket_cards = []
    if basket:
        b_logs = list(basket.inspection_logs.select_related(*BOX_RELATED))
        basket_cards = _group_by_order_article(
            ((log.box, log.second_sort_qty) for log in b_logs), _log_notes_map(b_logs))

    cancel_code = request.GET.get('cancel', '').strip()
    source, box_code, cycle = parse_packing_code(cancel_code)
    cancel_scans = []
    if source:
        scans = PackingScan.objects.filter(box__box_code__iexact=box_code, source=source, is_cancelled=False)
        if cycle:
            scans = scans.filter(repair_receipt__cycle=cycle)
        cancel_scans = list(scans.select_related(*SCAN_RELATED).order_by('-scanned_at'))
    return {
        'patoks': patoks,
        'selected_patok': selected,
        'selected_patok_code': _patok_label(selected) if selected is not None else '',
        'cards': cards,
        'basket_cards': basket_cards,
        'basket_total': sum(card['total'] for card in basket_cards),
        'cancel_code': cancel_code,
        'cancel_bad_code': bool(cancel_code) and not source,
        'cancel_scans': cancel_scans,
    }


def _upakovka_context(request):
    scans = PackingScan.objects.filter(is_cancelled=False, box__order__packing_beta=True).select_related(*BOX_RELATED)
    waiting = _group_by_order_article((s.box, s.quantity) for s in scans.order_by('box__order__order_number', 'box__razmer'))

    start, end = _today_range()
    today_packers = list(PackingScan.objects.filter(is_cancelled=False, box__order__packing_beta=True,
                                                    scanned_at__gte=start, scanned_at__lt=end)
                         .values('worker__worker_id', 'worker__first_name', 'worker__last_name')
                         .annotate(pieces=Sum('quantity'), scans=Count('id')).order_by('-pieces'))
    today_scans, today_total = _today_beta_scans(limit=15)

    sent_logs = BoxQualityInspectionLog.objects.filter(
        box__order__packing_beta=True, second_sort_batch__status=SecondSortBatch.Status.SENT
    ).select_related(*BOX_RELATED)
    second_sort = _group_by_order_article((log.box, log.second_sort_qty) for log in sent_logs)
    return {
        'waiting': waiting,
        'waiting_total': sum(card['total'] for card in waiting),
        'today_packers': today_packers,
        'today_scans': today_scans,
        'today_total': today_total,
        'second_sort': second_sort,
        'second_sort_total': sum(card['total'] for card in second_sort),
    }


def _zakazlar_context(request):
    search = request.GET.get('q', '').strip()
    filter_mode = request.GET.get('filter', 'all')
    if filter_mode not in ('all', 'beta', 'off'):
        filter_mode = 'all'
    base = Order.objects.exclude(status__in=[Order.Status.ARCHIVED, Order.Status.CANCELLED])
    if search:
        base = base.filter(Q(order_number__icontains=search) | Q(client_name__icontains=search))
    beta_count = base.filter(packing_beta=True).count()
    total_count = base.count()
    orders = base
    if filter_mode == 'beta':
        orders = orders.filter(packing_beta=True)
    elif filter_mode == 'off':
        orders = orders.filter(packing_beta=False)
    orders = orders.annotate(
        boxes_count=Count('boxes', filter=~Q(boxes__status=Box.Status.CANCELLED), distinct=True),
        articles_count=Count('items__article', distinct=True),
    )
    return {
        'orders': orders.order_by('-packing_beta', '-created_at')[:200],
        'search': search,
        'filter_mode': filter_mode,
        'beta_count': beta_count,
        'total_count': total_count,
    }


TAB_CONTEXT = {
    'otk': _otk_context,
    'taxlovchi': _taxlovchi_context,
    'sanoqchi': _sanoqchi_context,
    'upakovka': _upakovka_context,
    'zakazlar': _zakazlar_context,
}


@_role_required(*HUB_ROLES)
def hub_view(request):
    user = request.user
    tabs = [(key, label) for key, label, roles in TABS if user.is_superadmin() or user.role in roles]
    tab = request.GET.get('tab')
    if tab not in dict(tabs):
        tab = tabs[0][0]
    badges = _tab_badges({key for key, _ in tabs})
    tab_items = [{'key': key, 'label': label, 'badge': badges.get(key, 0)} for key, label in tabs]
    context = {
        'tabs': tabs,
        'tab_items': tab_items,
        'tab': tab,
        'tab_label': dict(tabs)[tab],
        'tab_template': f"packing/tabs/{tab}.html",
    }
    context.update(TAB_CONTEXT[tab](request))
    return render(request, 'packing/hub.html', context)


# ------------------------------------------------------------------ OTK tabi

@_role_required()
def repair_receipt_view(request, receipt_id):
    """Ta'mir cheki: hozircha A4 (?print=1 — ochilishi bilan chop etish oynasi), keyin chek apparatiga o'tkaziladi."""
    receipt = get_object_or_404(RepairReceipt.objects.select_related('box__order', 'box__article', 'inspector'),
                                id=receipt_id)
    box = receipt.box
    return render(request, 'packing/repair_receipt.html', {
        'receipt': receipt,
        'box': box,
        'article': box.target_article,
        'patok_code': _patok_label(box.assigned_screen) if box.assigned_screen else '—',
        'qr_data_uri': _generate_qr_data_uri(receipt.qr_text, box_size=10, border=2),
        'auto_print': request.GET.get('print') == '1',
    })


def _not_beta_response(box):
    if box.order and box.order.packing_beta:
        return None
    order = box.order.order_number if box.order else '—'
    return JsonResponse({'status': 'NOT_BETA', 'message': (
        f"«{box.box_code}» qutisining zakazi ({order}) beta tartibda emas — «Beta zakazlar» tabida yoqing.")}, status=400)


def _receipt_summary(receipt, box):
    return {
        'id': receipt.id,
        'box_code': box.box_code,
        'patok_code': _patok_label(box.assigned_screen) if box.assigned_screen else '—',
        'order_number': box.order.order_number if box.order else '',
        'article_code': box.target_article.code if box.target_article else '',
        'razmer': box.razmer or '—',
        'quantity': receipt.quantity,
        'remaining': receipt.quantity if receipt.is_open else 0,
        'code': receipt.qr_text,
        'cycle': receipt.cycle,
        'details': receipt.details or [],
        'operator_name': receipt.operator_name or '',
        'inspector': receipt.inspector.get_full_name() or receipt.inspector.username if receipt.inspector else '',
        'created_at': timezone.localtime(receipt.created_at).strftime('%d.%m %H:%M'),
        'receipt_url': f"{reverse('packing:repair_receipt', args=[receipt.id])}?print=1",
    }


@_role_required()
def api_otk_lookup(request):
    """/control/ qidiruvining o'zi (CONTROL:, TAMIR: yoki quti kodi), faqat beta zakaz qutilari uchun."""
    response = control_box_lookup_api(request)
    if response.status_code != 200:
        return response
    payload = json.loads(response.content)
    box = Box.objects.select_related('order', 'article', 'cutting_batch_item__batch__order_item__article').get(
        id=payload['box']['id'])
    error = _not_beta_response(box)
    if error:
        return error
    payload['box']['patok_code'] = _patok_label(box.assigned_screen) if box.assigned_screen else '—'
    receipt = box.repair_receipts.select_related('inspector').order_by('-cycle').first()
    if receipt:
        payload['existing_receipt'] = _receipt_summary(receipt, box)
    return JsonResponse(payload)


@_role_required()
@require_POST
def api_otk_submit(request):
    """/control/ saqlash funksiyasining o'zi; ta'mir bo'lsa javobda chek manzili (A4 ga chop etiladi)."""
    data = _json_body(request)
    box = Box.objects.select_related('order', 'article', 'cutting_batch_item__batch__order_item__article').filter(
        id=_int_or_none(data.get('box_id'))).first()
    if not box:
        return JsonResponse({'status': 'NOT_FOUND', 'message': "Quti topilmadi."}, status=404)
    error = _not_beta_response(box)
    if error:
        return error
    response = control_submit_inspection_api(request)
    if response.status_code != 200:
        return response
    payload = json.loads(response.content)
    # shu saqlashda chiqqan ta'mir cheki: birlamchi ta'mir (TAMIR1) yoki qayta ta'mir (TAMIR2, ...)
    last_log = box.quality_inspection_logs.order_by('-id').first()
    receipt = (RepairReceipt.objects.filter(box=box, created_log=last_log).select_related('inspector').first()
               if last_log else None)
    if receipt:
        summary = _receipt_summary(receipt, box)
        payload['receipt_url'] = summary['receipt_url']
        payload['receipt_meta'] = summary
    else:
        payload['receipt_url'] = None
    return JsonResponse(payload)


# ------------------------------------------------------------------ Taxlovchi tabi

@_role_required(User.Role.PACKING)
@require_POST
def api_identify(request):
    worker = _find_worker(_json_body(request).get('code'))
    if not worker:
        return JsonResponse({'status': 'NOT_FOUND', 'message': "Xodim topilmadi — badge'ni qayta uring."}, status=404)
    return JsonResponse({'status': 'OK', 'worker': {
        'id': worker.id, 'name': worker.full_name, 'code': worker.worker_id, 'today': _worker_today_total(worker),
    }})


@_role_required(User.Role.PACKING)
@require_POST
def api_scan(request):
    data = _json_body(request)
    worker = Worker.objects.filter(id=_int_or_none(data.get('worker_id')), is_active=True).first()
    if not worker:
        return JsonResponse({'status': 'NO_WORKER', 'message': "Avval o'z badge'ingizni uring."}, status=400)
    source, box_code, cycle = parse_packing_code(data.get('code'))
    if not source:
        return JsonResponse({'status': 'BAD_CODE', 'message': (
            "Bu tikuvchi stikeri — CONTROL stikeri, TAMIR cheki yoki quti kodini uring.")}, status=400)

    with transaction.atomic():
        box = Box.objects.select_for_update(of=('self',)).select_related('order', 'article').filter(
            box_code__iexact=box_code).first()
        if not box:
            return JsonResponse({'status': 'NOT_FOUND', 'message': f"«{box_code}» quti topilmadi."}, status=404)
        if not box.order or not box.order.packing_beta:
            return JsonResponse({'status': 'NOT_BETA', 'message': "Bu zakaz yangi qadoqlash tartibida emas."}, status=400)
        if box.status == Box.Status.CANCELLED:
            return JsonResponse({'status': 'CANCELLED', 'message': "Bu quti bekor qilingan."}, status=400)

        receipt = find_receipt(box, cycle) if source == PackingScan.Source.TAMIR else None
        if source == PackingScan.Source.TAMIR and receipt is None:
            return JsonResponse({'status': 'NOT_FOUND', 'message': f"«TAMIR{cycle}:{box.box_code}» cheki yo'q."}, status=404)
        available = available_for_packing(box, source, receipt)
        if available <= 0:
            if source == PackingScan.Source.CONTROL and not portion_total(box, source):
                message = "OTK bu qutini hali tekshirmagan (yoki 1-sort yo'q)."
            elif receipt is not None and receipt.is_open:
                message = f"{receipt.cycle}-ta'mir cheki hali ta'mirdan qaytmagan."
            elif receipt is not None and not receipt.first_sort_qty:
                message = f"{receipt.cycle}-ta'mir chekidan 1-sort chiqmagan."
            else:
                message = f"Bu qog'oz allaqachon urilgan: {packed_total(box, source, receipt)} dona qadoqlangan."
            return JsonResponse({'status': 'NOTHING', 'message': message}, status=400)

        PackingScan.objects.create(box=box, source=source, repair_receipt=receipt, worker=worker, quantity=available,
                                   terminal_user=request.user)

    article = box.target_article
    return JsonResponse({'status': 'OK', 'message': f"{worker.full_name}: +{available} dona", 'scan': {
        'quantity': available,
        'source': f"{receipt.cycle}-ta'mir cheki" if receipt else PackingScan.Source(source).label,
        'box_code': box.box_code,
        'order': box.order.order_number,
        'article': f"{article.code} — {article.name}" if article else '',
        'size': box.razmer or '—',
    }, 'worker_today': _worker_today_total(worker)})


# ------------------------------------------------------------------ Sanoqchi tabi

@_role_required(User.Role.COUNTER)
@require_POST
def counter_accept(request):
    screen = _int_or_none(request.POST.get('screen')) or 0
    order_id = _int_or_none(request.POST.get('order_id'))
    article_id = _int_or_none(request.POST.get('article_id')) or 0
    counted_text = (request.POST.get('counted') or '').strip()
    back = redirect(_hub_url(tab='sanoqchi', patok=screen))
    if not counted_text.isdigit():
        messages.error(request, "Sanagan soningizni yozing.")
        return back

    with transaction.atomic():
        logs = [log for log in _pending_second_sort().select_for_update(of=('self',))
                .filter(_screen_filter(screen), box__order_id=order_id).select_related(*BOX_RELATED)
                if (log.box.target_article.id if log.box.target_article else 0) == article_id]
        expected = sum(log.second_sort_qty for log in logs)
        if not logs:
            messages.error(request, "Bu kartochka allaqachon qabul qilingan.")
            return back
        if int(counted_text) != expected:
            messages.error(request, f"Sanalgan son ({counted_text}) OTK soniga ({expected}) teng emas — OTK topib berishi kerak.")
            return back
        basket = _open_basket(request.user, create=True)
        BoxQualityInspectionLog.objects.filter(id__in=[log.id for log in logs]).update(second_sort_batch=basket)
    messages.success(request, f"{expected} dona 2-sort savatga olindi.")
    return back


@_role_required(User.Role.COUNTER)
@require_POST
def counter_basket_remove(request):
    """Savatga adashib olingan kartochkani patok navbatiga qaytarish (jo'natilishidan oldin)."""
    order_id = _int_or_none(request.POST.get('order_id'))
    article_id = _int_or_none(request.POST.get('article_id')) or 0
    patok = _int_or_none(request.POST.get('patok'))
    back = redirect(_hub_url(tab='sanoqchi', patok=patok))
    with transaction.atomic():
        basket = _open_basket(request.user)
        if not basket:
            messages.error(request, "Savat bo'sh.")
            return back
        logs = [log for log in basket.inspection_logs.select_for_update(of=('self',))
                .filter(box__order_id=order_id).select_related(*BOX_RELATED)
                if (log.box.target_article.id if log.box.target_article else 0) == article_id]
        if not logs:
            messages.error(request, "Bu kartochka savatda topilmadi.")
            return back
        qty = sum(log.second_sort_qty for log in logs)
        BoxQualityInspectionLog.objects.filter(id__in=[log.id for log in logs]).update(second_sort_batch=None)
    messages.success(request, f"{qty} dona 2-sort savatdan patok navbatiga qaytarildi.")
    return back


@_role_required(User.Role.COUNTER)
@require_POST
def counter_send(request):
    basket = _open_basket(request.user)
    if not basket or not basket.inspection_logs.exists():
        messages.error(request, "Savat bo'sh.")
    else:
        basket.status = SecondSortBatch.Status.SENT
        basket.sent_at = timezone.now()
        basket.save(update_fields=['status', 'sent_at'])
        total = basket.inspection_logs.aggregate(s=Sum('second_sort_qty'))['s'] or 0
        messages.success(request, f"{total} dona 2-sort upakovkaning 2-sort omboriga jo'natildi.")
    return redirect(_hub_url(tab='sanoqchi'))


@_role_required(User.Role.COUNTER)
@require_POST
def counter_cancel(request):
    """Xato urilgan taxlovchi skanini bekor qilish (qog'oz urilgach, skan tanlanadi)."""
    scan = get_object_or_404(PackingScan.objects.select_related('worker'),
                             id=_int_or_none(request.POST.get('scan_id')), is_cancelled=False)
    scan.is_cancelled = True
    scan.cancelled_at = timezone.now()
    scan.cancelled_by = request.user
    scan.save(update_fields=['is_cancelled', 'cancelled_at', 'cancelled_by'])
    messages.success(request, f"{scan.worker.full_name}: {scan.quantity} dona bekor qilindi.")
    return redirect(_hub_url(tab='sanoqchi', patok=_int_or_none(request.POST.get('patok')),
                             cancel=request.POST.get('code', '')))


# ------------------------------------------------------------------ Beta zakazlar tabi

@_role_required()
@require_POST
def beta_toggle(request):
    order = get_object_or_404(Order, id=_int_or_none(request.POST.get('order_id')))
    order.packing_beta = not order.packing_beta
    order.save(update_fields=['packing_beta'])
    state = "yoqildi" if order.packing_beta else "o'chirildi"
    messages.success(request, f"«{order.order_number}»: yangi qadoqlash tartibi {state}.")
    return redirect(_hub_url(tab='zakazlar', q=request.POST.get('q', ''), filter=request.POST.get('filter', '')))
