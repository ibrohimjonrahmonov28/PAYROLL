"""
Beta qadoqlash tartibi (egasi talabi 2026-11-12, qayta loyiha 2026-11-14) — bitta /beta/ sahifasi, tablar bilan; faqat
beta qutilar (eski zakaz belgisi yoki beta narx shabloni). Superadmin bitta akkaunt bilan hamma tabni ko'radi:
- OTK           (superadmin): CONTROL stikeri / TAMIRn cheki → 2-sort va ta'mir donalari → saqlash (/control/ ning o'sha
                              server funksiyalari); ta'mir bo'lsa chek A4 ga chiqadi; cheklarni qayta chop etish;
- Sanoqchi      (COUNTER):    1-sort — taxlovchi badge'i, keyin uning CONTROL / TAMIRn qog'ozlari → "Saqlash va yuborish"
                              (taxlovchi puli, upakovkaning 1-sort balansi); 2-sort — patok → zakaz × model × razmer
                              qatorlari → savat (SV-123456) → upakovkaga jo'natish; xato skanni bekor qilish;
- Upakovka      (PACKING, PACKING_MASTER): qadoqlash kutayotgan 1-sort, bugun kim qancha taxladi, 2-sort balansi;
                              master sanoqchi savatlarini tekshirib qabul qiladi yoki sabab bilan qaytaradi;
- Beta zakazlar (superadmin): qaysi zakazlar beta tartibda ishlashini belgilaydi (keyinchalik faqat shablon belgisi).
Taxlovchi tizimda hech narsa urmaydi (chevar kabi). Muzlatilgan /control/ sahifasi o'zgarmaydi.
"""
import functools
import json
from collections import OrderedDict, defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User, Worker
from production.control_views import control_box_lookup_api, control_submit_inspection_api
from production.models import (Box, BoxQualityInspectionLog, Order, PackerScreen, PackingBatch, PackingScan,
                               RepairReceipt, SecondSortBatch, _generate_qr_data_uri)
from production.packing_services import (SENT_SCANS, beta_box_q, is_beta_box, paper_label, paper_status,
                                         parse_packing_code, take_paper)
from production.services import get_patok_code, parse_patok_number
from production.taxlov_pay import credit_batch
from production.sewing_statistics_views import natural_size_sort_key
from production.terminal_views import extract_worker_code

HUB_ROLES = (User.Role.PACKING, User.Role.COUNTER, User.Role.PACKING_MASTER)

# (tab, nomi, kimga ochiq — superadmin hammasini ko'radi)
TABS = (
    ('otk', "OTK", ()),
    ('sanoqchi', "Sanoqchi", (User.Role.COUNTER,)),
    ('upakovka', "Upakovka", (User.Role.PACKING, User.Role.PACKING_MASTER)),
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


def _parse_screen_number(value):
    screen = parse_patok_number(value)
    return screen if screen and 1 <= screen <= 40 else None


def _worker_today_total(worker):
    start, end = _today_range()
    return PackingScan.objects.filter(SENT_SCANS, worker=worker, is_cancelled=False, scanned_at__gte=start,
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
    return BoxQualityInspectionLog.objects.filter(beta_box_q(), second_sort_qty__gt=0, second_sort_batch__isnull=True)


def _open_basket(user, create=False):
    basket = SecondSortBatch.objects.filter(counter=user, status=SecondSortBatch.Status.BASKET).first()
    if not basket and create:
        basket = SecondSortBatch.objects.create(counter=user)
    return basket


def _screen_filter(screen):
    return Q(box__assigned_screen__isnull=True) if not screen else Q(box__assigned_screen=screen)


def _today_beta_scans(limit=15):
    start, end = _today_range()
    qs = (PackingScan.objects.filter(SENT_SCANS, beta_box_q(), is_cancelled=False, scanned_at__gte=start, scanned_at__lt=end)
          .select_related(*SCAN_RELATED).order_by('-scanned_at'))
    total = qs.aggregate(s=Sum('quantity'))['s'] or 0
    return list(qs[:limit]), total


def _tab_badges(allowed_keys):
    """Tab sarlavhalaridagi jonli ko'rsatkichlar (0 bo'lsa ko'rsatilmaydi)."""
    badges = {}
    if 'otk' in allowed_keys:
        badges['otk'] = RepairReceipt.objects.filter(beta_box_q(), returned_at__isnull=True).count()
    if 'sanoqchi' in allowed_keys:
        badges['sanoqchi'] = _pending_second_sort().aggregate(s=Sum('second_sort_qty'))['s'] or 0
    if 'upakovka' in allowed_keys:
        badges['upakovka'] = (PackingScan.objects.filter(SENT_SCANS, beta_box_q(), is_cancelled=False)
                              .aggregate(s=Sum('quantity'))['s'] or 0)
    if 'zakazlar' in allowed_keys:
        badges['zakazlar'] = (Order.objects.exclude(status__in=[Order.Status.ARCHIVED, Order.Status.CANCELLED])
                              .filter(packing_beta=True).count())
    return badges


# ------------------------------------------------------------------ bitta sahifa, tablar

def _otk_context(request):
    since = timezone.now() - timezone.timedelta(days=7)
    receipts = list(RepairReceipt.objects.filter(beta_box_q(), created_at__gte=since)
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
    returned = SecondSortBatch.objects.filter(counter=request.user, status=SecondSortBatch.Status.RETURNED).select_related('returned_by')
    search_code = request.GET.get('sv', '').strip()
    screen_map = {n: {'number': s.number, 'title': s.title} for s in PackerScreen.objects.all() for n in s.patok_list}
    hq_patok_choices = [{'number': n, 'code': get_patok_code(n), 'tv': screen_map.get(n)} for n in range(1, 14)]
    uychi_patok_choices = [{'number': n, 'code': get_patok_code(n), 'tv': screen_map.get(n)} for n in range(14, 41)]
    return {
        'basket': basket,
        'basket_view': _basket_view(basket) if basket else None,
        'returned_baskets': [_basket_view(b) for b in returned],
        'search_code': search_code,
        'search_basket': (_basket_view(found) if (found := _find_basket(search_code)) else None) if search_code else None,
        'patoks': patoks,
        'hq_patok_choices': hq_patok_choices,
        'uychi_patok_choices': uychi_patok_choices,
        'packer_screens_json': json.dumps({str(k): v for k, v in screen_map.items()}),
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
    scans = PackingScan.objects.filter(SENT_SCANS, beta_box_q(), is_cancelled=False).select_related(*BOX_RELATED)
    waiting = _group_by_order_article((s.box, s.quantity) for s in scans.order_by('box__order__order_number', 'box__razmer'))

    start, end = _today_range()
    today_packers = list(PackingScan.objects.filter(SENT_SCANS, beta_box_q(), is_cancelled=False,
                                                    scanned_at__gte=start, scanned_at__lt=end)
                         .values('worker__worker_id', 'worker__first_name', 'worker__last_name')
                         .annotate(pieces=Sum('quantity'), scans=Count('id')).order_by('-pieces'))
    today_scans, today_total = _today_beta_scans(limit=15)

    # 2-sort balansi — upakovka masteri qabul qilgan savatlar; kutilayotgan savatlar master tekshiruvida
    accepted_logs = BoxQualityInspectionLog.objects.filter(
        beta_box_q(), second_sort_batch__status=SecondSortBatch.Status.ACCEPTED
    ).select_related(*BOX_RELATED)
    second_sort = _group_by_order_article((log.box, log.second_sort_qty) for log in accepted_logs)
    waiting_baskets = [_basket_view(b) for b in SecondSortBatch.objects.filter(status=SecondSortBatch.Status.SENT)
                       .select_related('counter').order_by('sent_at')]
    recent_baskets = list(SecondSortBatch.objects.filter(status__in=[SecondSortBatch.Status.ACCEPTED,
                                                                     SecondSortBatch.Status.RETURNED])
                          .select_related('counter', 'accepted_by', 'returned_by').order_by('-sent_at')[:10])
    search_code = request.GET.get('sv', '').strip()
    return {
        'waiting_baskets': waiting_baskets,
        'waiting_baskets_total': sum(view['total'] for view in waiting_baskets),
        'recent_baskets': recent_baskets,
        'can_accept': request.user.is_superadmin() or request.user.role == User.Role.PACKING_MASTER,
        'search_code': search_code,
        'search_basket': (_basket_view(found) if (found := _find_basket(search_code)) else None) if search_code else None,
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
    if is_beta_box(box):
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

# ------------------------------------------------------------------ Sanoqchi tabi: taxlovchi qog'ozlari (1-sort)
# Egasi talabi 2026-11-14: taxlovchi tizimda hech narsa urmaydi (chevar kabi). Sanoqchi avval taxlovchi badge'ini, keyin
# uning qog'ozlarini uradi — CONTROL stikeri (qog'ozda 50, bazada OTK 1-sorti 40) va TAMIRn cheklari, har biri bir marta.
# "Saqlash va yuborish" — ro'yxat shu taxlovchi hisobiga yoziladi va upakovkaning 1-sort balansiga tushadi.

def _open_packing_batch(counter, worker, create=False):
    batch = PackingBatch.objects.filter(counter=counter, worker=worker, status=PackingBatch.Status.OPEN).first()
    if batch is None and create:
        batch = PackingBatch.objects.create(counter=counter, worker=worker)
    return batch


def _apply_batch_patok(batch, screen):
    if batch is None:
        return
    if screen is not None and batch.screen_number != screen:
        batch.screen_number = screen
        batch.save(update_fields=['screen_number'])
        batch.scans.filter(is_cancelled=False).update(screen_number=screen)
    elif batch.screen_number is not None:
        batch.scans.filter(is_cancelled=False, screen_number__isnull=True).update(screen_number=batch.screen_number)


def _scan_line(scan):
    box = scan.box
    article = box.target_article
    effective_screen = scan.screen_number or (scan.batch.screen_number if scan.batch_id else None) or box.assigned_screen
    return {
        'id': scan.id, 'box_code': box.box_code, 'paper': paper_label(scan.source, scan.repair_receipt),
        'quantity': scan.quantity, 'order': box.order.order_number if box.order_id else '',
        'article': article.code if article else '', 'size': box.razmer or '—',
        'patok': _patok_label(effective_screen),
        'box_patok': _patok_label(box.assigned_screen),
    }


def _batch_payload(batch):
    scans = (batch.scans.select_related(*BOX_RELATED, 'repair_receipt', 'batch').order_by('scanned_at') if batch else [])
    lines = [_scan_line(scan) for scan in scans]
    screen = batch.screen_number if batch else None
    return {
        'lines': lines,
        'total': sum(line['quantity'] for line in lines),
        'screen_number': screen,
        'patok_code': get_patok_code(screen) if screen else '',
    }


def _worker_payload(worker):
    return {'id': worker.id, 'name': worker.full_name, 'code': worker.worker_id, 'today': _worker_today_total(worker)}


def _box_papers_for_code(raw_code):
    """Xato bo'lsa ham sanoqchi qutining hamma qog'ozlarini ko'rsin (qaysi biri urilgan, qaysi biri yo'qolgan)."""
    source, box_code, _cycle = parse_packing_code(raw_code)
    box = Box.objects.select_related('order', 'article').filter(box_code__iexact=box_code).first() if source else None
    return paper_status(box) if box is not None and is_beta_box(box) else None


@_role_required(User.Role.COUNTER)
@require_POST
def api_counter_packer(request):
    data = _json_body(request)
    worker = _find_worker(data.get('code'))
    if not worker:
        return JsonResponse({'status': 'NOT_FOUND', 'message': "Taxlovchi topilmadi — badge'ni qayta uring."}, status=404)
    screen = _parse_screen_number(data.get('screen_number') or data.get('patok'))
    batch = _open_packing_batch(request.user, worker)
    _apply_batch_patok(batch, screen)
    effective_screen = screen if screen is not None else (batch.screen_number if batch else None)
    return JsonResponse({
        'status': 'OK',
        'worker': _worker_payload(worker),
        'batch': _batch_payload(batch),
        'screen_number': effective_screen,
        'patok_code': get_patok_code(effective_screen) if effective_screen else '',
    })


@_role_required(User.Role.COUNTER)
@require_POST
def api_counter_patok(request):
    """Sanoqchi tanlagan patokni saqlash (ochiq ro'yxat bo'lsa uning skanlariga ham darhol yoziladi)."""
    data = _json_body(request)
    screen = _parse_screen_number(data.get('screen_number') or data.get('patok'))
    if screen is None:
        return JsonResponse({'status': 'BAD_PATOK', 'message': "Patokni tanlang (K1..K13 yoki U1..U27)."}, status=400)
    worker = Worker.objects.filter(id=_int_or_none(data.get('worker_id')), is_active=True).first()
    batch = None
    if worker:
        with transaction.atomic():
            batch = _open_packing_batch(request.user, worker)
            _apply_batch_patok(batch, screen)
    return JsonResponse({
        'status': 'OK',
        'screen_number': screen,
        'patok_code': get_patok_code(screen),
        'batch': _batch_payload(batch) if batch else None,
    })


@_role_required(User.Role.COUNTER)
@require_POST
def api_counter_paper(request):
    data = _json_body(request)
    worker = Worker.objects.filter(id=_int_or_none(data.get('worker_id')), is_active=True).first()
    if not worker:
        return JsonResponse({'status': 'NO_WORKER', 'message': "Avval taxlovchi badge'ini uring."}, status=400)
    screen = _parse_screen_number(data.get('screen_number') or data.get('patok'))
    code = data.get('code')
    with transaction.atomic():
        batch = _open_packing_batch(request.user, worker, create=True)
        _apply_batch_patok(batch, screen)
        scan, error = take_paper(code, worker, request.user, batch=batch, screen_number=batch.screen_number)
        if error:
            transaction.set_rollback(True)          # qog'oz olinmadi — bo'sh ro'yxat ham qolmasin
    if error:
        status, message = error
        return JsonResponse({'status': status, 'message': message, 'box': _box_papers_for_code(code)},
                            status=404 if status == 'NOT_FOUND' else 400)
    return JsonResponse({'status': 'OK', 'line': _scan_line(scan), 'batch': _batch_payload(batch),
                         'box': paper_status(scan.box)})


@_role_required(User.Role.COUNTER)
@require_POST
def api_counter_remove(request):
    """Hali yuborilmagan ro'yxatdan qatorni olib tashlash (qog'oz yana bo'sh bo'ladi)."""
    scan = PackingScan.objects.filter(id=_int_or_none(_json_body(request).get('scan_id')), batch__counter=request.user,
                                      batch__status=PackingBatch.Status.OPEN).select_related('batch').first()
    if not scan:
        return JsonResponse({'status': 'NOT_FOUND', 'message': "Bu qator ro'yxatda yo'q yoki allaqachon yuborilgan."}, status=404)
    batch = scan.batch
    scan.delete()
    return JsonResponse({'status': 'OK', 'batch': _batch_payload(batch)})


@_role_required(User.Role.COUNTER)
@require_POST
def api_counter_send(request):
    data = _json_body(request)
    worker = Worker.objects.filter(id=_int_or_none(data.get('worker_id'))).first()
    batch = _open_packing_batch(request.user, worker) if worker else None
    if batch is None or not batch.scans.exists():
        return JsonResponse({'status': 'EMPTY', 'message': "Ro'yxat bo'sh — avval qog'ozlarni uring."}, status=400)
    screen = _parse_screen_number(data.get('screen_number') or data.get('patok'))
    total = batch.scans.aggregate(s=Sum('quantity'))['s'] or 0
    with transaction.atomic():
        _apply_batch_patok(batch, screen)
        batch.status, batch.sent_at = PackingBatch.Status.SENT, timezone.now()
        batch.save(update_fields=['status', 'sent_at'])
        credit_batch(batch)                          # taxlovchi puli: dona × shablondagi Control/Taxlov narxi
    today_iso = timezone.localdate().isoformat()
    cache.delete(f"packer_screens_overview_{today_iso}")
    for n in PackerScreen.objects.values_list('number', flat=True):
        cache.delete(f"packer_screen_api_{n}_{today_iso}")
    patok_suffix = f" ({get_patok_code(batch.screen_number)})" if batch.screen_number else ''
    return JsonResponse({'status': 'OK', 'message': f"{worker.full_name}{patok_suffix}: {total} dona yozildi va upakovkaga yuborildi.",
                         'worker': _worker_payload(worker)})


def _row_logs(logs, article_id, size=None):
    """Zakaz × model (× razmer) qatori: shu artikul va razmerdagi OTK 2-sort yozuvlari."""
    def matches(log):
        article = log.box.target_article
        return ((article.id if article else 0) == article_id
                and (size is None or (log.box.razmer or '—') == size))
    return [log for log in logs if matches(log)]


def _counter_basket(user, basket_id=None):
    """Sanoqchining ochiq savati yoki (basket_id bilan) upakovka qaytargan savati."""
    if basket_id:
        return SecondSortBatch.objects.filter(id=basket_id, counter=user, status=SecondSortBatch.Status.RETURNED).first()
    return _open_basket(user)


@_role_required(User.Role.COUNTER)
@require_POST
def counter_accept(request):
    """Patokdagi 2-sortni zakaz × model × razmer qatori (yoki butun patokni) tasdiqlab savatga olish. "Real son"
    maydoni hozircha yopiq (egasi talabi 2026-11-14 — keyinchalik ochiladi), shuning uchun OTK soni olinadi."""
    screen = _int_or_none(request.POST.get('screen')) or 0
    take_all = request.POST.get('all') == '1'
    order_id = _int_or_none(request.POST.get('order_id'))
    article_id = _int_or_none(request.POST.get('article_id')) or 0
    size = request.POST.get('size') or None
    back = redirect(_hub_url(tab='sanoqchi', patok=screen))
    with transaction.atomic():
        qs = _pending_second_sort().select_for_update(of=('self',)).filter(_screen_filter(screen)).select_related(*BOX_RELATED)
        if not take_all:
            qs = qs.filter(box__order_id=order_id)
        logs = list(qs) if take_all else _row_logs(qs, article_id, size)
        if not logs:
            messages.error(request, "Bu 2-sort allaqachon savatga olingan.")
            return back
        basket = _open_basket(request.user, create=True)
        BoxQualityInspectionLog.objects.filter(id__in=[log.id for log in logs]).update(second_sort_batch=basket)
    messages.success(request, f"{sum(log.second_sort_qty for log in logs)} dona 2-sort {basket.code} savatiga olindi.")
    return back


@_role_required(User.Role.COUNTER)
@require_POST
def counter_basket_remove(request):
    """Savatdagi qatorni (zakaz × model × razmer) patok navbatiga qaytarish — ochiq yoki upakovka qaytargan savatdan."""
    order_id = _int_or_none(request.POST.get('order_id'))
    article_id = _int_or_none(request.POST.get('article_id')) or 0
    size = request.POST.get('size') or None
    back = redirect(_hub_url(tab='sanoqchi', patok=_int_or_none(request.POST.get('patok'))))
    with transaction.atomic():
        basket = _counter_basket(request.user, _int_or_none(request.POST.get('basket_id')))
        if not basket:
            messages.error(request, "Savat topilmadi.")
            return back
        logs = _row_logs(basket.inspection_logs.select_for_update(of=('self',)).filter(box__order_id=order_id)
                         .select_related(*BOX_RELATED), article_id, size)
        if not logs:
            messages.error(request, "Bu qator savatda topilmadi.")
            return back
        BoxQualityInspectionLog.objects.filter(id__in=[log.id for log in logs]).update(second_sort_batch=None)
    messages.success(request, f"{sum(log.second_sort_qty for log in logs)} dona 2-sort {basket.code} savatidan patok navbatiga qaytarildi.")
    return back


@_role_required(User.Role.COUNTER)
@require_POST
def counter_send(request):
    """Savatni upakovkaga jo'natish (master qabul qilguncha "kutilmoqda"); qaytarilgan savatni tuzatib qayta jo'natish."""
    basket = _counter_basket(request.user, _int_or_none(request.POST.get('basket_id')))
    if not basket or not basket.inspection_logs.exists():
        messages.error(request, "Savat bo'sh.")
    else:
        basket.status, basket.sent_at = SecondSortBatch.Status.SENT, timezone.now()
        basket.save(update_fields=['status', 'sent_at'])
        total = basket.inspection_logs.aggregate(s=Sum('second_sort_qty'))['s'] or 0
        messages.success(request, f"{basket.code}: {total} dona 2-sort upakovkaga jo'natildi — master qabul qilishi kutilmoqda.")
    return redirect(_hub_url(tab='sanoqchi'))


# ------------------------------------------------------------------ Upakovka: master savatlarni qabul qiladi

@_role_required(User.Role.PACKING_MASTER)
@require_POST
def master_accept(request):
    """Upakovka masteri savatni tekshirib qabul qiladi — hammasi upakovkaning 2-sort balansiga tushadi."""
    with transaction.atomic():
        basket = (SecondSortBatch.objects.select_for_update()
                  .filter(id=_int_or_none(request.POST.get('basket_id')), status=SecondSortBatch.Status.SENT).first())
        if not basket:
            messages.error(request, "Bu savat kutilayotganlar orasida yo'q (allaqachon qabul qilingan yoki qaytarilgan).")
            return redirect(_hub_url(tab='upakovka'))
        basket.status, basket.accepted_at, basket.accepted_by = SecondSortBatch.Status.ACCEPTED, timezone.now(), request.user
        basket.save(update_fields=['status', 'accepted_at', 'accepted_by'])
    total = basket.inspection_logs.aggregate(s=Sum('second_sort_qty'))['s'] or 0
    messages.success(request, f"{basket.code}: {total} dona 2-sort qabul qilindi va upakovkaning 2-sort balansiga tushdi.")
    return redirect(_hub_url(tab='upakovka'))


@_role_required(User.Role.PACKING_MASTER)
@require_POST
def master_return(request):
    """Savatda kamchilik bo'lsa sabab bilan sanoqchiga qaytariladi — sanoqchi tuzatib qayta jo'natadi."""
    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, "Qaytarish sababini yozing.")
        return redirect(_hub_url(tab='upakovka'))
    with transaction.atomic():
        basket = (SecondSortBatch.objects.select_for_update()
                  .filter(id=_int_or_none(request.POST.get('basket_id')), status=SecondSortBatch.Status.SENT).first())
        if not basket:
            messages.error(request, "Bu savat kutilayotganlar orasida yo'q.")
            return redirect(_hub_url(tab='upakovka'))
        basket.status, basket.returned_at, basket.returned_by = SecondSortBatch.Status.RETURNED, timezone.now(), request.user
        basket.return_reason = reason[:300]
        basket.save(update_fields=['status', 'returned_at', 'returned_by', 'return_reason'])
    messages.success(request, f"{basket.code} sanoqchiga qaytarildi: {reason}")
    return redirect(_hub_url(tab='upakovka'))


def _find_basket(raw):
    """"SV-123456", "sv123456" yoki "123456" bo'yicha savat."""
    digits = ''.join(ch for ch in (raw or '') if ch.isdigit())
    return SecondSortBatch.objects.select_related('counter', 'accepted_by', 'returned_by').filter(
        code=f"SV-{digits}").first() if len(digits) == 6 else None


def _basket_view(basket):
    """Savat kartochkasi: kodi, holati, kim/qachon va zakaz × model × razmer bo'yicha tarkibi."""
    logs = list(basket.inspection_logs.select_related(*BOX_RELATED))
    cards = _group_by_order_article(((log.box, log.second_sort_qty) for log in logs), _log_notes_map(logs))
    return {'basket': basket, 'cards': cards, 'total': sum(card['total'] for card in cards)}


@_role_required(User.Role.COUNTER)
@require_POST
def counter_cancel(request):
    """Xato urilgan taxlovchi skanini bekor qilish (qog'oz urilgach, skan tanlanadi)."""
    scan = get_object_or_404(PackingScan.objects.select_related('worker'),
                             id=_int_or_none(request.POST.get('scan_id')), is_cancelled=False)
    if scan.is_frozen:                               # muzlatilgan oy puli o'zgarmaydi
        messages.error(request, "Bu skan muzlatilgan oyga tegishli — bekor qilib bo'lmaydi.")
        return redirect(_hub_url(tab='sanoqchi', cancel=request.POST.get('code', '')))
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
    # beta faqat yoqilgan vaqtdan: bundan oldin OTK dan o'tgan qutilar eski tartibda qoladi (qayta yoqilsa — yangidan)
    order.packing_beta_since = timezone.now() if order.packing_beta else None
    order.save(update_fields=['packing_beta', 'packing_beta_since'])
    state = "yoqildi" if order.packing_beta else "o'chirildi"
    messages.success(request, f"«{order.order_number}»: yangi qadoqlash tartibi {state}.")
    return redirect(_hub_url(tab='zakazlar', q=request.POST.get('q', ''), filter=request.POST.get('filter', '')))
