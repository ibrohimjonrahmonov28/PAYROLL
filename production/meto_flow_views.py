"""
Meto Markazi → "Yangi oqim (beta)" (egasi talabi 2026-11-19; mantiq: production/meto_flow.py). Bitta sahifa, tablar:
Meto, Tasnif, Stiker, Koson sklad, Uychi, Yuk xatlari, Beta zakazlar — har rol faqat o'z tabini ko'radi, superadmin
hammasini. Skanerli amallar (biriktirish, konteyner tekshiruvi, patokka berish, yuk xati, qabul) — JSON API.
"""
import functools
import json
import re
import time
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, Prefetch, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.models import User, Worker
from production import meto_flow as flow_logic
from production.container_services import parse_container_code
from production.meto_flow import FlowError, can
from production.models import (Box, Container, CuttingBatch, CuttingBatchItem, Order, PastalFlow, PastalFlowSize, Shipment,
                               ShipmentPastal)
from production.services import get_patok_code
from production.shifts import get_shift_for_timestamp, get_shift_info, get_work_day_shifts

SECTIONS = (
    {
        'key': 'meto',
        'label': "Meto",
        'step': 'meto',
        'url_name': 'meto_hub_flow_meto',
        'zone': 'bosh',
        'step_num': '01',
        'group': "Bosh bino · 1-bosqich",
        'icon': 'fa-tags',
        'accent': 'amber',
        'desc': "Kesimdan kelgan pastallarni qabul qilish, sanash va tasnifga uzatish.",
    },
    {
        'key': 'tasnif',
        'label': "Tasnif",
        'step': 'tasnif',
        'url_name': 'meto_hub_flow_tasnif',
        'zone': 'bosh',
        'step_num': '02',
        'group': "Bosh bino · 2-bosqich",
        'icon': 'fa-layer-group',
        'accent': 'cyan',
        'desc': "Meto sonini tekshirib real son kiritish va bosh skladga berish.",
    },
    {
        'key': 'stiker',
        'label': "Stiker",
        'step': 'stiker',
        'url_name': 'meto_hub_flow_stiker',
        'zone': 'bosh',
        'step_num': '03',
        'group': "Bosh bino · 3-bosqich",
        'icon': 'fa-qrcode',
        'accent': 'sky',
        'desc': "Tasnif sonini qutilarga bo'lish va stikerlarni chop etish.",
    },
    {
        'key': 'sklad',
        'label': "Bosh sklad",
        'step': 'sklad',
        'url_name': 'meto_hub_flow_sklad',
        'zone': 'bosh',
        'step_num': '04',
        'group': "Bosh bino · 4-bosqich",
        'icon': 'fa-warehouse',
        'accent': 'emerald',
        'desc': "Tasnifdan kelgan pastallar: kg, konteynerga biriktirish, Koson patoklariga berish va Uychiga jo'natish.",
    },
    {
        'key': 'uychi',
        'label': "Uychi filiali",
        'step': 'uychi',
        'url_name': 'meto_hub_flow_uychi',
        'zone': 'filial',
        'step_num': '',
        'group': "Uychi filiali",
        'icon': 'fa-truck-ramp-box',
        'accent': 'violet',
        'desc': "Kosondan kelgan yukni konteynerlar bo'yicha qabul qilish va U-patoklarga berish.",
    },
    {
        'key': 'yukxat',
        'label': "Yuk xatlari",
        'step': None,
        'url_name': 'meto_hub_flow_yukxat',
        'zone': 'filial',
        'step_num': '',
        'group': "Logistika",
        'icon': 'fa-file-invoice',
        'accent': 'indigo',
        'desc': "Koson ↔ Uychi yuk xatlari ro'yxati, holati va A4 chop etish.",
    },
    {
        'key': 'zakazlar',
        'label': "Beta zakazlar",
        'step': 'zakazlar',
        'url_name': 'meto_hub_flow_zakazlar',
        'zone': 'utility',
        'step_num': '',
        'group': "Sozlama",
        'icon': 'fa-sliders',
        'accent': 'rose',
        'desc': "Tayyorlov oqimiga tushadigan zakazlarni yoqish yoki o'chirish.",
    },
)
SECTION_BY_KEY = {s['key']: s for s in SECTIONS}
TABS = tuple((s['key'], s['label'], s['step']) for s in SECTIONS)

METO_PAGE_SIZE = 30


def _can_view_section(user, section):
    step = section['step']
    if step is None:
        return can(user, 'sklad') or can(user, 'uychi')
    return can(user, step)


def _allowed_sections(user):
    return [s for s in SECTIONS if _can_view_section(user, s)]


def _allowed_tabs(user):
    return [(s['key'], s['label']) for s in _allowed_sections(user)]


def flow_access_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(f"{reverse('root_login')}?next={request.path}")
        if not _allowed_tabs(user):
            if '/api/' in request.path:
                return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat yo'q"}, status=403)
            messages.error(request, "Bu bo'limga ruxsatingiz yo'q.")
            return redirect('root_login')
        return view(request, *args, **kwargs)
    return wrapper


def _flow_url(tab, **params):
    section = SECTION_BY_KEY.get(tab)
    base = reverse(section['url_name']) if section else reverse('meto_hub_flow')
    clean = {k: v for k, v in params.items() if v not in (None, '')}
    return f"{base}?{urlencode(clean)}" if clean else base


def _deny(request, tab):
    messages.error(request, "Bu amalga ruxsatingiz yo'q.")
    return redirect(_flow_url(tab))


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def _allowed(user, step):
    if step == 'ship_any':   # Tayyorlov skladi yoki Uychi sklad (qaysi biri — amalning o'zida tekshiriladi)
        return can(user, 'sklad') or can(user, 'uychi')
    return can(user, step)


def _api(step):
    """JSON API: ruxsat (bosqich) + FlowError -> 400."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not _allowed(request.user, step):
                return JsonResponse({'status': 'FORBIDDEN', 'message': "Bu amalga ruxsatingiz yo'q."}, status=403)
            try:
                return view(request, *args, **kwargs)
            except FlowError as exc:
                return JsonResponse({'status': 'ERROR', 'message': str(exc)}, status=400)
        return flow_access_required(wrapper)
    return decorator


# ------------------------------------------------------------------ ko'rinish ma'lumotlari

FLOW_RELATED = (
    'batch__order_item__order',
    'batch__order_item__order__customer',
    'batch__order_item__article',
)


def _card(flow):
    """Pastal kartochkasi: zakaz, artikul (rasm), razmerlar (kesim / meto / tasnif soni), qutilar va konteynerlar."""
    batch = flow.batch
    order = batch.order_item.order
    article = batch.order_item.article
    image = ''
    if article and article.image:
        try:
            image = article.image.url
        except ValueError:
            image = ''
    sizes = []
    for item in sorted(batch.items.all(), key=lambda it: (it.order_item_size_id, it.id)):
        size = getattr(item, 'flow_size', None)
        sizes.append({
            'id': item.id, 'name': item.order_item_size.size_name, 'cut': item.quantity,
            'meto': size.meto_qty if size else None, 'tasnif': size.tasnif_qty if size else None,
        })
    boxes = [b for item in batch.items.all() for b in item.boxes.all() if b.status != Box.Status.CANCELLED]
    containers = flow_logic.box_containers(boxes)
    linked = {c.box_id for c in containers}
    customer = order.customer.name if order.customer_id else (order.client_name or '')
    has_ops = bool(article and len(article.article_operations.all()) > 0)
    total_cut = sum(s['cut'] for s in sizes)
    total_meto = sum(s['meto'] or 0 for s in sizes)
    total_tasnif = sum(s['tasnif'] or 0 for s in sizes)
    meto_shift = get_shift_for_timestamp(flow.meto_done_at) if flow.meto_done_at else None
    tasnif_shift = get_shift_for_timestamp(flow.tasnif_done_at) if flow.tasnif_done_at else None
    pending_shipment = flow_logic.pending_shipment(batch)

    boxes_count = len(boxes)
    printed_count = sum(1 for b in boxes if b.is_printed)
    linked_count = len(linked)
    if boxes_count == 0:
        packaging_status = 'packet'
    elif printed_count < boxes_count:
        packaging_status = 'stiker_pending'
    elif linked_count < boxes_count:
        packaging_status = 'container_pending'
    else:
        packaging_status = 'ready'

    kg_ok = flow.branch != 'HQ' or bool(flow.sklad_received_at and batch.actual_weight_kg)
    ready = (
        boxes_count > 0
        and printed_count == boxes_count
        and linked_count == boxes_count
        and kg_ok
        and not pending_shipment
    )
    reasons = []   # nega hali jo'natib bo'lmaydi (ro'yxatda tugma ustida ko'rinadi)
    if boxes_count == 0:
        reasons.append("Stikerchi hali qutilarga bo'lmagan (paketda).")
    else:
        if printed_count < boxes_count:
            reasons.append(f"{boxes_count - printed_count} ta stiker chop etilmagan.")
        if linked_count < boxes_count:
            reasons.append(f"{boxes_count - linked_count} ta quti konteynersiz.")
    if not kg_ok:
        reasons.append("Kg kiritilmagan.")
    if pending_shipment:
        reasons.append(f"{pending_shipment.number} — planshetda tekshirilmoqda.")

    return {
        'flow': flow, 'batch': batch, 'order': order, 'article': article, 'image': image, 'customer': customer,
        'has_ops': has_ops, 'sizes': sizes, 'total_cut': total_cut,
        'total_meto': total_meto, 'meto_diff': total_meto - total_cut,
        'total_tasnif': total_tasnif, 'tasnif_diff': total_tasnif - total_meto if total_meto else 0,
        'boxes_count': boxes_count, 'printed_count': printed_count,
        'linked_count': linked_count,
        'containers': [c.code for c in containers],
        'kg_ok': kg_ok,
        'reasons': reasons,
        'packaging_status': packaging_status,
        'meto_shift': meto_shift,
        'tasnif_shift': tasnif_shift,
        'ready': ready,
        'pending_verification': pending_shipment,
    }


def _flows_qs(**filters):
    return (PastalFlow.objects.filter(**filters)
            .select_related(*FLOW_RELATED, 'meto_taken_by', 'meto_done_by', 'tasnif_taken_by', 'tasnif_done_by',
                            'tasnif_returned_by', 'sklad_received_by')
            .prefetch_related('batch__items__order_item_size', 'batch__items__flow_size', 'batch__items__boxes__container',
                              'batch__order_item__article__article_operations', flow_logic.pending_lines_prefetch())
            .order_by('-batch__created_at', '-id'))


def _flows(**filters):
    return [_card(f) for f in _flows_qs(**filters)[:200]]


def _search_flows_qs(qs, q='', zakaz=''):
    if q:
        qs = qs.filter(
            Q(batch__pastal_code__icontains=q)
            | Q(batch__name__icontains=q)
            | Q(batch__partiya_number__icontains=q)
            | Q(batch__cutter_name__icontains=q)
            | Q(batch__order_item__order__order_number__icontains=q)
            | Q(batch__order_item__order__client_name__icontains=q)
            | Q(batch__order_item__order__customer__name__icontains=q)
            | Q(batch__order_item__article__code__icontains=q)
            | Q(batch__order_item__article__name__icontains=q)
        )
    if zakaz and zakaz.isdigit():
        qs = qs.filter(batch__order_item__order_id=int(zakaz))
    return qs


def _incoming(destination):
    return (ShipmentPastal.objects.filter(status=ShipmentPastal.Status.IN_TRANSIT,
                                          shipment__destination_branch=destination)
            .select_related('shipment', 'batch__order_item__order', 'batch__order_item__article')
            .order_by('shipment_id', 'id'))


def _patok_rows(branch):
    """Patokdagi oxirgi 60 pastal (AT_PATOK — oxirgi bosqich, ro'yxat o'sib boradi: faqat 60 tasi yasaladi)."""
    cards = [_card(f) for f in _flows_qs(stage=PastalFlow.Stage.AT_PATOK, branch=branch)[:60]]
    states = flow_logic.container_states_by_batch([card['batch'].id for card in cards])
    for card in cards:
        card['patok_code'] = get_patok_code(card['batch'].assigned_screen) if card['batch'].assigned_screen else '—'
        card['container_states'] = states.get(card['batch'].id, [])
    return cards


def _ctx_meto(request):
    flow_logic.ensure_flows()
    q = request.GET.get('q', '').strip()
    holat = request.GET.get('holat', 'faol')
    if holat not in ('faol', 'wait', 'work', 'done'):
        holat = 'faol'
    zakaz = request.GET.get('zakaz', '').strip()
    smena = request.GET.get('smena', '').strip()

    current_shift = get_shift_info()
    day_shifts = get_work_day_shifts(current_shift['work_date'])

    wait_base = _search_flows_qs(_flows_qs(stage=PastalFlow.Stage.METO_WAIT), q, zakaz)
    work_base = _search_flows_qs(_flows_qs(stage=PastalFlow.Stage.METO_WORK).order_by('-meto_taken_at', '-id'), q, zakaz)
    done_base = _search_flows_qs(
        _flows_qs(meto_done_at__isnull=False).order_by('-meto_done_at', '-id'), q, zakaz
    )

    if smena == 'current':
        done_base = done_base.filter(meto_done_at__gte=current_shift['shift_start'], meto_done_at__lt=current_shift['shift_end'])
    elif smena == 'day':
        done_base = done_base.filter(meto_done_at__gte=current_shift['day_start'], meto_done_at__lt=current_shift['day_end'])
    elif smena == 's1':
        done_base = done_base.filter(meto_done_at__gte=day_shifts['shift1']['start'], meto_done_at__lt=day_shifts['shift1']['end'])
    elif smena == 's2':
        done_base = done_base.filter(meto_done_at__gte=day_shifts['shift2']['start'], meto_done_at__lt=day_shifts['shift2']['end'])

    wait_page = Paginator(wait_base, METO_PAGE_SIZE).get_page(request.GET.get('pw'))
    done_page = Paginator(done_base, 25).get_page(request.GET.get('pd'))

    # Joriy smena metrikalari (08:00–18:00 yoki 18:00–08:00)
    shift_done_count = PastalFlow.objects.filter(
        meto_done_at__gte=current_shift['shift_start'],
        meto_done_at__lt=current_shift['shift_end'],
    ).count()
    shift_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__meto_done_at__gte=current_shift['shift_start'],
            item__batch__flow__meto_done_at__lt=current_shift['shift_end'],
        ).aggregate(t=Sum('meto_qty'))['t'] or 0
    )

    # 24 soatlik ish kuni (1-smena + 2-smena)
    day_done_count = PastalFlow.objects.filter(
        meto_done_at__gte=current_shift['day_start'],
        meto_done_at__lt=current_shift['day_end'],
    ).count()
    day_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__meto_done_at__gte=current_shift['day_start'],
            item__batch__flow__meto_done_at__lt=current_shift['day_end'],
        ).aggregate(t=Sum('meto_qty'))['t'] or 0
    )

    # Bugungi ish kunining 1- va 2-smenalari alohida
    s1_done_count = PastalFlow.objects.filter(
        meto_done_at__gte=day_shifts['shift1']['start'],
        meto_done_at__lt=day_shifts['shift1']['end'],
    ).count()
    s1_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__meto_done_at__gte=day_shifts['shift1']['start'],
            item__batch__flow__meto_done_at__lt=day_shifts['shift1']['end'],
        ).aggregate(t=Sum('meto_qty'))['t'] or 0
    )
    s2_done_count = PastalFlow.objects.filter(
        meto_done_at__gte=day_shifts['shift2']['start'],
        meto_done_at__lt=day_shifts['shift2']['end'],
    ).count()
    s2_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__meto_done_at__gte=day_shifts['shift2']['start'],
            item__batch__flow__meto_done_at__lt=day_shifts['shift2']['end'],
        ).aggregate(t=Sum('meto_qty'))['t'] or 0
    )

    kpi = {
        'wait_count': PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WAIT).count(),
        'wait_qty': (CuttingBatchItem.objects.filter(batch__flow__stage=PastalFlow.Stage.METO_WAIT)
                     .aggregate(t=Sum('quantity'))['t'] or 0),
        'work_count': PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WORK).count(),
        'work_qty': (CuttingBatchItem.objects.filter(batch__flow__stage=PastalFlow.Stage.METO_WORK)
                     .aggregate(t=Sum('quantity'))['t'] or 0),
        # Joriy smena ("bugun tugatilganda shu smenadagi hisoblanadi"):
        'shift_done_count': shift_done_count,
        'shift_done_qty': shift_done_qty,
        'today_done_count': shift_done_count,
        'today_done_qty': shift_done_qty,
        # 24 soatlik ish kuni:
        'day_done_count': day_done_count,
        'day_done_qty': day_done_qty,
        's1_done_count': s1_done_count,
        's1_done_qty': s1_done_qty,
        's2_done_count': s2_done_count,
        's2_done_qty': s2_done_qty,
    }
    orders_filter = Order.objects.filter(meto_beta=True).order_by('-created_at')[:100]

    return {
        'waiting': [_card(f) for f in wait_page],
        'wait_page': wait_page,
        'working': [_card(f) for f in work_base[:100]],
        'done_cards': [_card(f) for f in done_page],
        'done_page': done_page,
        'kpi': kpi,
        'current_shift': current_shift,
        'day_shifts': day_shifts,
        'q': q,
        'holat': holat,
        'smena': smena,
        'zakaz': zakaz,
        'orders_filter': orders_filter,
    }


def _ctx_tasnif(request):
    flow_logic.ensure_flows()
    q = request.GET.get('q', '').strip()
    holat = request.GET.get('holat', 'faol')
    if holat not in ('faol', 'wait', 'work', 'done'):
        holat = 'faol'
    zakaz = request.GET.get('zakaz', '').strip()
    smena = request.GET.get('smena', '').strip()

    current_shift = get_shift_info()
    day_shifts = get_work_day_shifts(current_shift['work_date'])

    wait_base = _search_flows_qs(_flows_qs(stage=PastalFlow.Stage.TASNIF_WAIT), q, zakaz)
    work_base = _search_flows_qs(_flows_qs(stage=PastalFlow.Stage.TASNIF_WORK).order_by('-tasnif_taken_at', '-id'), q, zakaz)
    done_base = _search_flows_qs(
        _flows_qs(tasnif_done_at__isnull=False).order_by('-tasnif_done_at', '-id'), q, zakaz
    )

    if smena == 'current':
        done_base = done_base.filter(tasnif_done_at__gte=current_shift['shift_start'], tasnif_done_at__lt=current_shift['shift_end'])
    elif smena == 'day':
        done_base = done_base.filter(tasnif_done_at__gte=current_shift['day_start'], tasnif_done_at__lt=current_shift['day_end'])
    elif smena == 's1':
        done_base = done_base.filter(tasnif_done_at__gte=day_shifts['shift1']['start'], tasnif_done_at__lt=day_shifts['shift1']['end'])
    elif smena == 's2':
        done_base = done_base.filter(tasnif_done_at__gte=day_shifts['shift2']['start'], tasnif_done_at__lt=day_shifts['shift2']['end'])

    wait_page = Paginator(wait_base, METO_PAGE_SIZE).get_page(request.GET.get('pw'))
    done_page = Paginator(done_base, 25).get_page(request.GET.get('pd'))

    # Tasnif kutayotgan dona (meto soni bo'yicha)
    wait_qty = (
        PastalFlowSize.objects.filter(item__batch__flow__stage=PastalFlow.Stage.TASNIF_WAIT)
        .aggregate(t=Sum('meto_qty'))['t'] or 0
    )
    # Tasnif bo'layotgan dona (meto soni bo'yicha)
    work_qty = (
        PastalFlowSize.objects.filter(item__batch__flow__stage=PastalFlow.Stage.TASNIF_WORK)
        .aggregate(t=Sum('meto_qty'))['t'] or 0
    )

    # Joriy smena metrikalari (08:00–18:00 yoki 18:00–08:00)
    shift_done_count = PastalFlow.objects.filter(
        tasnif_done_at__gte=current_shift['shift_start'],
        tasnif_done_at__lt=current_shift['shift_end'],
    ).count()
    shift_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__tasnif_done_at__gte=current_shift['shift_start'],
            item__batch__flow__tasnif_done_at__lt=current_shift['shift_end'],
        ).aggregate(t=Sum('tasnif_qty'))['t'] or 0
    )

    # 24 soatlik ish kuni (1-smena + 2-smena)
    day_done_count = PastalFlow.objects.filter(
        tasnif_done_at__gte=current_shift['day_start'],
        tasnif_done_at__lt=current_shift['day_end'],
    ).count()
    day_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__tasnif_done_at__gte=current_shift['day_start'],
            item__batch__flow__tasnif_done_at__lt=current_shift['day_end'],
        ).aggregate(t=Sum('tasnif_qty'))['t'] or 0
    )

    # Bugungi ish kunining 1- va 2-smenalari alohida
    s1_done_count = PastalFlow.objects.filter(
        tasnif_done_at__gte=day_shifts['shift1']['start'],
        tasnif_done_at__lt=day_shifts['shift1']['end'],
    ).count()
    s1_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__tasnif_done_at__gte=day_shifts['shift1']['start'],
            item__batch__flow__tasnif_done_at__lt=day_shifts['shift1']['end'],
        ).aggregate(t=Sum('tasnif_qty'))['t'] or 0
    )
    s2_done_count = PastalFlow.objects.filter(
        tasnif_done_at__gte=day_shifts['shift2']['start'],
        tasnif_done_at__lt=day_shifts['shift2']['end'],
    ).count()
    s2_done_qty = (
        PastalFlowSize.objects.filter(
            item__batch__flow__tasnif_done_at__gte=day_shifts['shift2']['start'],
            item__batch__flow__tasnif_done_at__lt=day_shifts['shift2']['end'],
        ).aggregate(t=Sum('tasnif_qty'))['t'] or 0
    )

    kpi = {
        'wait_count': PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WAIT).count(),
        'wait_qty': wait_qty,
        'work_count': PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WORK).count(),
        'work_qty': work_qty,
        'shift_done_count': shift_done_count,
        'shift_done_qty': shift_done_qty,
        'today_done_count': shift_done_count,
        'today_done_qty': shift_done_qty,
        'day_done_count': day_done_count,
        'day_done_qty': day_done_qty,
        's1_done_count': s1_done_count,
        's1_done_qty': s1_done_qty,
        's2_done_count': s2_done_count,
        's2_done_qty': s2_done_qty,
    }
    orders_filter = Order.objects.filter(meto_beta=True).order_by('-created_at')[:100]

    return {
        'waiting': [_card(f) for f in wait_page],
        'wait_page': wait_page,
        'working': [_card(f) for f in work_base[:100]],
        'done_cards': [_card(f) for f in done_page],
        'done_page': done_page,
        'kpi': kpi,
        'current_shift': current_shift,
        'day_shifts': day_shifts,
        'q': q,
        'holat': holat,
        'smena': smena,
        'zakaz': zakaz,
        'orders_filter': orders_filter,
    }


def _ctx_stiker(request):
    from production.tayyorlov_stiker_views import stiker_dashboard_context
    return stiker_dashboard_context(request)


# ------------------------------------------------------------------ Bosh sklad / Uychi skladi ro'yxati

SKLAD_FILTERS = (
    ('all', "Barchasi"),
    ('kg', "Kg kiritilmagan"),            # faqat Koson (bosh sklad)
    ('stiker', "Stiker chiqmagan"),
    ('kont', "Konteyner birikmagan"),
    ('tayyor', "Tayyor"),
    ('tekshiruv', "Planshetda tekshiruvda"),
)
SKLAD_LIST_LIMIT = 150
SKLAD_LIVE = {'sklad': 'HQ', 'uychi': 'UYCHI'}   # jonli yangilanadigan bo'limlar (?partial=1)
_BOX_CODE_RE = re.compile(r'^[A-Z]{1,3}\d{1,2}-\d{1,5}$')
_BOX_PREFIX_RE = re.compile(r'^(CONTROL|BOX|QUTI)\s*[:+]\s*', re.IGNORECASE)


def _sklad_search(qs, q):
    """Pastal / zakaz / artikul bo'yicha; konteyner QR yoki quti stikeri urilsa — o'sha qutining pastali."""
    if not q:
        return qs
    batch_ids = set()
    container = flow_logic.find_container(q)
    if container is not None:
        if container.box_id and container.box.cutting_batch_item_id:
            batch_ids.add(container.box.cutting_batch_item.batch_id)
    else:
        code = _BOX_PREFIX_RE.sub('', q).replace('+', '-').upper()
        if _BOX_CODE_RE.match(code):   # aniq quti kodi (indeks bo'yicha, katta jadvalni aylanib chiqmaydi)
            batch_ids.update(Box.objects.filter(box_code=code, cutting_batch_item__isnull=False)
                             .values_list('cutting_batch_item__batch_id', flat=True))
    found = _search_flows_qs(qs, q)
    return (found | qs.filter(batch_id__in=batch_ids)) if batch_ids else found


def _sklad_flags(boxes, printed, linked, kg_ok, pending):
    """Filtrlar (_card dagi tayyorlik qoidasi bilan bir xil)."""
    return {
        'all': True,
        'kg': not kg_ok,
        'stiker': boxes == 0 or printed < boxes,
        'kont': boxes > 0 and linked < boxes,
        'tayyor': boxes > 0 and printed == boxes and linked == boxes and kg_ok and not pending,
        'tekshiruv': pending,
    }


def _sklad_list_ctx(request, branch):
    """Skladdagi pastallar ro'yxati: holat filtrlari va sanoq bazada (3 ta yengil so'rov), kartochka — faqat
    ko'rsatiladigan qatorlar uchun (pastallar ko'payib ketsa ham sahifa og'irlashmaydi)."""
    filters = [(key, label) for key, label in SKLAD_FILTERS if branch == 'HQ' or key != 'kg']
    current = request.GET.get('f', 'all')
    if current not in dict(filters):
        current = 'all'
    q = request.GET.get('q', '').strip()[:60]
    base = PastalFlow.objects.filter(stage=PastalFlow.Stage.SKLAD, branch=branch)
    flows = list(_sklad_search(base, q).order_by('-batch__created_at', '-id')
                 .values('id', 'batch_id', 'sklad_received_at', 'batch__actual_weight_kg'))
    batch_ids = [flow['batch_id'] for flow in flows]
    box_stats = {row['batch_ref']: row for row in (
        Box.objects.filter(cutting_batch_item__batch_id__in=batch_ids).exclude(status=Box.Status.CANCELLED)
        .values(batch_ref=F('cutting_batch_item__batch_id'))
        .annotate(n=Count('id'), printed=Count('id', filter=Q(is_printed=True)),
                  linked=Count('id', filter=Q(container__isnull=False))))}
    pending = set(ShipmentPastal.objects.filter(
        batch_id__in=batch_ids, status=ShipmentPastal.Status.PENDING, shipment__status=Shipment.Status.PENDING,
    ).values_list('batch_id', flat=True))
    counts = {key: 0 for key, _ in filters}
    shown = []
    for flow in flows:
        stats = box_stats.get(flow['batch_id'], {})
        kg_ok = branch != 'HQ' or bool(flow['sklad_received_at'] and flow['batch__actual_weight_kg'])
        flags = _sklad_flags(stats.get('n', 0), stats.get('printed', 0), stats.get('linked', 0), kg_ok,
                             flow['batch_id'] in pending)
        for key in counts:
            counts[key] += flags[key]
        if flags[current]:
            shown.append(flow['id'])
    return {
        'branch': branch,
        'cards': [_card(f) for f in _flows_qs(id__in=shown[:SKLAD_LIST_LIMIT])],
        'sklad_filters': [(key, label, counts[key]) for key, label in filters],
        'sklad_filter': current,
        'sklad_q': q,
        'sklad_total': len(shown),
        'sklad_hidden': max(0, len(shown) - SKLAD_LIST_LIMIT),
    }


def _sklad_sections(request, branch):
    """Bosh sklad / Uychi sahifasining jonli qismlari — sahifa ochilganda ham, ?partial=1 yangilanishida ham."""
    if branch == 'HQ':
        flow_logic.sync_rejected_flows()
    ctx = _sklad_list_ctx(request, branch)
    ctx.update({'incoming': list(_incoming(branch)), 'patok_rows': _patok_rows(branch)})
    if branch == 'HQ':
        ctx['pending_shipments'] = (
            Shipment.objects.filter(source_branch='HQ', destination_branch='UYCHI', status=Shipment.Status.PENDING)
            .prefetch_related('lines__batch__order_item__article', 'lines__batch__items__boxes')
            .order_by('-id'))
        ctx['recent_dispatched'] = (
            Shipment.objects.filter(source_branch='HQ', destination_branch='UYCHI', status=Shipment.Status.IN_TRANSIT)
            .prefetch_related('lines__batch')
            .order_by('-id')[:5])
    return ctx


def _sklad_partial(request, tab):
    """Jonli yangilanish (biriktirilgach darhol, aks holda har 20 soniyada): bo'laklar HTML + filtr sanoqlari."""
    ctx = _sklad_sections(request, SKLAD_LIVE[tab])
    sections = {name: render_to_string(f'meto_hub/flow/_sklad_{name}.html', ctx, request=request)
                for name in ('top', 'list', 'bottom')}
    return JsonResponse({'status': 'OK', 'sections': sections, 'total': ctx['sklad_total'],
                         'counts': {key: n for key, _, n in ctx['sklad_filters']}})


def _ctx_sklad(request):
    ctx = _sklad_sections(request, 'HQ')
    ctx['screens'] = [(n, get_patok_code(n)) for n in flow_logic.KOSON_SCREENS]
    return ctx


def _ctx_uychi(request):
    ctx = _sklad_sections(request, 'UYCHI')
    ctx['screens'] = [(n, get_patok_code(n)) for n in flow_logic.UYCHI_SCREENS]
    return ctx


def _ctx_yukxat(request):
    shipments = (Shipment.objects.select_related('created_by')
                 .prefetch_related(Prefetch('lines', queryset=ShipmentPastal.objects.select_related(
                     'batch__order_item__order', 'batch__order_item__article', 'resolved_by')))
                 .order_by('-id')[:100])
    return {'shipments': shipments}


def _ctx_zakazlar(request):
    search = request.GET.get('q', '').strip()
    orders = Order.objects.exclude(status__in=[Order.Status.ARCHIVED, Order.Status.CANCELLED])
    if search:
        orders = orders.filter(Q(order_number__icontains=search) | Q(client_name__icontains=search))
    return {'orders': orders.order_by('-meto_beta', '-created_at')[:200], 'search': search}


TAB_CONTEXT = {'meto': _ctx_meto, 'tasnif': _ctx_tasnif, 'stiker': _ctx_stiker, 'sklad': _ctx_sklad,
               'uychi': _ctx_uychi, 'yukxat': _ctx_yukxat, 'zakazlar': _ctx_zakazlar}

CONTAINER_SECTION = {
    'key': 'konteynerlar',
    'label': "Konteynerlar",
    'url_name': 'meto_hub_containers',
    'zone': 'utility',
    'step_num': '',
    'group': "Inventar · 10 000 quti",
    'icon': 'fa-box',
    'accent': 'amber',
    'desc': "QR kod yaratish, yorliq chop etish va konteynerlar qayerdaligini kuzatish.",
}


def _can_open_containers(user):
    limited = user.is_meto_flow_role() or user.is_branch_admin()
    return not limited or user.role in (User.Role.METO_SKLAD, User.Role.UYCHI_SKLAD)


def _hub_stats(allowed_keys, can_containers):
    """Har bir bo'lim uchun jonli ko'rsatkichlar: (asosiy son, birlik, pill lar ro'yxati)."""
    stats = {}
    if 'meto' in allowed_keys:
        flow_logic.ensure_flows()
        wait = PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WAIT).count()
        work = PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WORK).count()
        stats['meto'] = (wait + work, 'pastal', [('Kutmoqda', wait, 'amber'), ('Ishda', work, 'cyan')])
    if 'tasnif' in allowed_keys:
        wait = PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WAIT).count()
        work = PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WORK).count()
        stats['tasnif'] = (wait + work, 'pastal', [('Kutmoqda', wait, 'amber'), ('Ishda', work, 'cyan')])
    hq_ids = set()
    if 'stiker' in allowed_keys or 'sklad' in allowed_keys:
        hq_ids = set(
            PastalFlow.objects.filter(stage=PastalFlow.Stage.SKLAD, branch='HQ').values_list('batch_id', flat=True)
        )
    if 'stiker' in allowed_keys:
        active_hq = Box.objects.filter(cutting_batch_item__batch_id__in=hq_ids).exclude(status=Box.Status.CANCELLED)
        with_boxes = set(active_hq.values_list('cutting_batch_item__batch_id', flat=True).distinct()) if hq_ids else set()
        unprinted = (
            set(active_hq.filter(is_printed=False).values_list('cutting_batch_item__batch_id', flat=True).distinct())
            if hq_ids
            else set()
        )
        need_split = len(hq_ids - with_boxes)
        need_print = len(unprinted)
        stats['stiker'] = (need_split + need_print, 'pastal', [("Bo'lish", need_split, 'sky'), ('Chop etish', need_print, 'amber')])
    if 'sklad' in allowed_keys:
        ret = ShipmentPastal.objects.filter(
            status=ShipmentPastal.Status.IN_TRANSIT, shipment__destination_branch='HQ'
        ).count()
        stats['sklad'] = (len(hq_ids), 'skladda', [('Skladda', len(hq_ids), 'emerald'), ('Qaytayotgan', ret, 'rose')])
    if 'uychi' in allowed_keys:
        inc = ShipmentPastal.objects.filter(
            status=ShipmentPastal.Status.IN_TRANSIT, shipment__destination_branch='UYCHI'
        ).count()
        sk = PastalFlow.objects.filter(stage=PastalFlow.Stage.SKLAD, branch='UYCHI').count()
        stats['uychi'] = (inc + sk, 'pastal', [('Kelayotgan', inc, 'amber'), ('Skladda', sk, 'violet')])
    if 'yukxat' in allowed_keys:
        open_ship = Shipment.objects.filter(status=Shipment.Status.IN_TRANSIT).count()
        total_ship = Shipment.objects.count()
        stats['yukxat'] = (open_ship, "yo'lda", [("Yo'lda", open_ship, 'sky'), ('Jami', total_ship, 'slate')])
    if 'zakazlar' in allowed_keys:
        beta_cnt = Order.objects.filter(meto_beta=True).exclude(
            status__in=[Order.Status.ARCHIVED, Order.Status.CANCELLED]
        ).count()
        stats['zakazlar'] = (beta_cnt, 'faol zakaz', [('Faol', beta_cnt, 'rose')])
    if can_containers:
        empty_c = Container.objects.filter(status=Container.Status.EMPTY).count()
        tot_c = Container.objects.count()
        used_c = max(tot_c - empty_c, 0)
        stats['konteynerlar'] = (empty_c, "bo'sh", [("Bo'sh", empty_c, 'emerald'), ('Band', used_c, 'amber'), ('Jami', tot_c, 'slate')])
    return stats


@flow_access_required
def flow_view(request):
    """Tayyorlov bo'limi bosh menyusi (/tayyorlov/): ruxsatli bo'limlar guruhlangan holda yangi tabda ochiladi."""
    tabs = _allowed_tabs(request.user)
    requested_tab = (request.GET.get('tab') or '').strip()
    if requested_tab:
        if requested_tab in dict(tabs):
            params = request.GET.copy()
            params.pop('tab', None)
            qs = params.urlencode()
            target = reverse(SECTION_BY_KEY[requested_tab]['url_name'])
            return redirect(f"{target}?{qs}" if qs else target)
        messages.error(request, "Bu bo'limga ruxsatingiz yo'q.")
        return redirect('meto_hub_flow')

    allowed = _allowed_sections(request.user)
    allowed_keys = {s['key'] for s in allowed}
    can_containers = _can_open_containers(request.user)
    stats = _hub_stats(allowed_keys, can_containers)

    all_sections = list(allowed)
    if can_containers:
        # Inventar kartochkasi sozlamadan oldin tursin
        insert_at = next((i for i, item in enumerate(all_sections) if item['zone'] == 'utility'), len(all_sections))
        all_sections.insert(insert_at, CONTAINER_SECTION)

    hub_cards = []
    for s in all_sections:
        count, unit, pills = stats.get(s['key'], (0, '', []))
        hub_cards.append({**s, 'url': reverse(s['url_name']), 'count': count, 'unit': unit, 'pills': pills})

    bosh_cards = [c for c in hub_cards if c['zone'] == 'bosh']
    filial_cards = [c for c in hub_cards if c['zone'] == 'filial']
    utility_cards = [c for c in hub_cards if c['zone'] == 'utility']

    summary_pills = []
    if bosh_cards:
        bosh_active = PastalFlow.objects.filter(
            Q(stage__in=[
                PastalFlow.Stage.METO_WAIT, PastalFlow.Stage.METO_WORK,
                PastalFlow.Stage.TASNIF_WAIT, PastalFlow.Stage.TASNIF_WORK,
            ])
            | Q(stage=PastalFlow.Stage.SKLAD, branch='HQ')
        ).count()
        summary_pills.append(("Bosh bino faol", f"{bosh_active} pastal", 'amber'))
    if filial_cards:
        in_transit_cnt = ShipmentPastal.objects.filter(
            status=ShipmentPastal.Status.IN_TRANSIT, shipment__destination_branch='UYCHI'
        ).count()
        summary_pills.append(("Uychiga yo'lda", f"{in_transit_cnt} pastal", 'violet'))
    if can_containers and 'konteynerlar' in stats:
        empty_c, _, pills = stats['konteynerlar']
        tot_c = pills[-1][1]
        summary_pills.append(("Bo'sh konteyner", f"{empty_c} / {tot_c}", 'emerald'))

    return render(request, 'meto_hub/hub.html', {
        'tabs': tabs,
        'hub_cards': hub_cards,
        'bosh_cards': bosh_cards,
        'filial_cards': filial_cards,
        'utility_cards': utility_cards,
        'summary_pills': summary_pills,
        'can_mt_kiosk': _can_use_mt_kiosk(request.user),
    })


@flow_access_required
def section_view(request, tab):
    """Tayyorlov bo'limining mustaqil sahifasi (/tayyorlov/meto/, /tasnif/, ...): boshqa bo'limlarga link yo'q."""
    section = SECTION_BY_KEY.get(tab)
    if not section or not _can_view_section(request.user, section):
        messages.error(request, "Bu bo'limga ruxsatingiz yo'q.")
        return redirect('meto_hub_flow')
    if tab in SKLAD_LIVE and request.GET.get('partial') == '1':
        return _sklad_partial(request, tab)
    allowed = _allowed_sections(request.user)
    context = {
        'tabs': [(s['key'], s['label']) for s in allowed],
        'section': section,
        'tab': tab,
        'tab_label': section['label'],
        'tab_template': f"meto_hub/flow/{tab}.html",
        'section_reset_url': reverse(section['url_name']),
        'kiosk_mode': False,
    }
    context.update(TAB_CONTEXT[tab](request))
    return render(request, 'meto_hub/flow.html', context)


# ------------------------------------------------------------------ Meto & Tasnif umumiy QR-birka plansheti

MT_KIOSK_ROLES = (User.Role.METO, User.Role.TASNIF)
MT_KIOSK_STEP_ROLE = {'meto': User.Role.METO, 'tasnif': User.Role.TASNIF}
MT_KIOSK_HOST_ROLES = (User.Role.ADMIN, User.Role.METO, User.Role.TASNIF, User.Role.METO_SKLAD)
MT_KIOSK_SESSION_USER_KEY = 'mt_kiosk_user_id'
MT_KIOSK_SESSION_AT_KEY = 'mt_kiosk_last_active'
MT_KIOSK_IDLE_SECONDS = 120          # 2 daqiqa harakatsizlikdan so'ng kutish ekraniga qaytadi
MT_KIOSK_SERVER_MAX_IDLE = 600       # 10 daqiqa server zaxira muddati (planshet uxlab qolsa)

_CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')
_LONG_DASHES_RE = re.compile(r'[\u2010-\u2015\u2212\uFF0D]')
_BADGE_PREFIX_RE = re.compile(r'^(USER|WORKER|MASTER)\s*[:+\-]\s*', re.IGNORECASE)


def _can_use_mt_kiosk(user):
    """Umumiy Meto/Tasnif planshetini kim ochib qo'ya oladi (dastlabki kirish)."""
    return bool(user.is_authenticated and (user.is_superadmin() or user.role in MT_KIOSK_HOST_ROLES))


def _clear_mt_kiosk(request):
    request.session.pop(MT_KIOSK_SESSION_USER_KEY, None)
    request.session.pop(MT_KIOSK_SESSION_AT_KEY, None)
    request.session.modified = True


def _get_mt_kiosk_user(request):
    """Sessiyadagi faol Meto/Tasnif masterini qaytaradi (muddati o'tgan yoki roli o'zgargan bo'lsa tozalaydi)."""
    uid = request.session.get(MT_KIOSK_SESSION_USER_KEY)
    if not uid:
        return None
    last_at = request.session.get(MT_KIOSK_SESSION_AT_KEY)
    if last_at is not None:
        try:
            if int(time.time()) - int(last_at) > MT_KIOSK_SERVER_MAX_IDLE:
                _clear_mt_kiosk(request)
                return None
        except (TypeError, ValueError):
            _clear_mt_kiosk(request)
            return None
    user = User.objects.filter(id=uid, is_active=True, role__in=MT_KIOSK_ROLES).first()
    if not user:
        _clear_mt_kiosk(request)
        return None
    return user


def _is_mt_kiosk_post(request):
    next_url = (request.POST.get('next') or '').strip()
    return request.POST.get('kiosk') == '1' or next_url.startswith('/tayyorlov/meto-tasnif/')


def _action_user(request):
    """Kiosk POST da skanerlangan master, oddiy sahifada esa request.user."""
    return getattr(request, '_mt_actor', request.user)


def _parse_badge_user(raw_code):
    """QR birkadan Meto/Tasnif masterini topadi. Kirill harflar transliteratsiya qilinmaydi — ogohlantiriladi."""
    text = str(raw_code or '').strip()
    if not text:
        return None, "Iltimos, QR birkangizni skanerlang yoki 6 xonali UID kiriting.", 400
    if _CYRILLIC_RE.search(text):
        return None, "Klaviatura kirill (ruscha) rejimda! Lotin (EN) ga o'tkazib qayta skanerlang.", 400

    normalized = _LONG_DASHES_RE.sub('-', text)
    clean = _BADGE_PREFIX_RE.sub('', normalized).strip()
    if '+' in clean:
        clean = clean.replace('+', '-')
    if not clean:
        return None, "Birka kodi noto'g'ri. Qaytadan skanerlang.", 400

    user = User.objects.filter(Q(uid__iexact=clean) | Q(username__iexact=clean)).first()
    if not user:
        worker = Worker.objects.select_related('user').filter(
            Q(worker_id__iexact=clean) | Q(user__uid__iexact=clean)
        ).first()
        if worker and worker.user:
            user = worker.user
    if not user:
        return None, f"Birka topilmadi (Kod: {clean}). Qaytadan skanerlang.", 404
    if not user.is_active:
        return None, f"{flow_logic.user_name(user)} hisobi nofaol.", 403
    if user.role not in MT_KIOSK_ROLES:
        return (
            None,
            f"Bu planshet faqat Meto va Tasnif masterlari uchun! ({flow_logic.user_name(user)} — {user.get_role_display()})",
            403,
        )
    return user, '', 200


@ensure_csrf_cookie
def meto_tasnif_kiosk_view(request):
    """Meto va Tasnif uchun bitta umumiy planshet (login/parolsiz): kutish ekrani → QR birka → rolga mos oyna."""
    active_master = _get_mt_kiosk_user(request)
    if not active_master:
        flow_logic.ensure_flows()
        standby_stats = {
            'meto_wait': PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WAIT).count(),
            'meto_work': PastalFlow.objects.filter(stage=PastalFlow.Stage.METO_WORK).count(),
            'tasnif_wait': PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WAIT).count(),
            'tasnif_work': PastalFlow.objects.filter(stage=PastalFlow.Stage.TASNIF_WORK).count(),
        }
        return render(request, 'meto_hub/meto_tasnif_kiosk.html', {
            'kiosk_mode': True,
            'active_master': None,
            'current_shift': get_shift_info(),
            'standby_stats': standby_stats,
            'kiosk_idle_seconds': MT_KIOSK_IDLE_SECONDS,
        })

    tab = 'meto' if active_master.role == User.Role.METO else 'tasnif'
    section = SECTION_BY_KEY[tab]
    context = {
        'kiosk_mode': True,
        'active_master': active_master,
        'active_master_name': flow_logic.user_name(active_master),
        'active_master_initial': (active_master.first_name or active_master.username or 'M')[:1].upper(),
        'active_master_role_label': "METO MASTERI" if tab == 'meto' else "TASNIF MASTERI",
        'section': section,
        'tab': tab,
        'tab_label': section['label'],
        'tab_template': f"meto_hub/flow/{tab}.html",
        'section_reset_url': reverse('meto_hub_flow_kiosk'),
        'kiosk_idle_seconds': MT_KIOSK_IDLE_SECONDS,
    }
    context.update(TAB_CONTEXT[tab](request))
    return render(request, 'meto_hub/meto_tasnif_kiosk.html', context)


@require_POST
def api_meto_tasnif_scan(request):
    """QR birkani tekshirib, Meto yoki Tasnif masteri sessiyasini ochadi (login/parolsiz planshet)."""
    data = _json(request) if request.content_type and 'json' in request.content_type else request.POST
    raw_code = data.get('code', '')
    user, error, status_code = _parse_badge_user(raw_code)
    if not user:
        return JsonResponse({'status': 'ERROR', 'message': error}, status=status_code)

    request.session[MT_KIOSK_SESSION_USER_KEY] = user.id
    request.session[MT_KIOSK_SESSION_AT_KEY] = int(time.time())
    request.session.modified = True

    tab = 'meto' if user.role == User.Role.METO else 'tasnif'
    return JsonResponse({
        'status': 'OK',
        'redirect_url': reverse('meto_hub_flow_kiosk'),
        'master': {
            'id': user.id,
            'name': flow_logic.user_name(user),
            'uid': user.uid or '',
            'role': user.role,
            'role_label': "METO MASTERI" if tab == 'meto' else "TASNIF MASTERI",
            'tab': tab,
        },
    })


@require_POST
def api_meto_tasnif_ping(request):
    """Faol ish (yoki son kiritish oynasi) paytida kiosk sessiyasini tirik saqlaydi."""
    active_master = _get_mt_kiosk_user(request)
    if not active_master:
        return JsonResponse({'status': 'EXPIRED'}, status=401)
    request.session[MT_KIOSK_SESSION_AT_KEY] = int(time.time())
    request.session.modified = True
    return JsonResponse({'status': 'OK'})


@require_POST
def meto_tasnif_kiosk_exit(request):
    """Masterni chiqarib kutish ekraniga qaytaradi."""
    _clear_mt_kiosk(request)
    is_ajax = (
        request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('accept', '')
        or (request.content_type and 'json' in request.content_type)
        or request.GET.get('ajax') == '1'
    )
    if is_ajax:
        return JsonResponse({'status': 'OK', 'redirect_url': reverse('meto_hub_flow_kiosk')})
    return redirect('meto_hub_flow_kiosk')


# ------------------------------------------------------------------ formalar (sahifaga qaytadi)

def _form_action(step, tab):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            is_ajax = (
                request.headers.get('x-requested-with') == 'XMLHttpRequest'
                or request.POST.get('ajax') == '1'
                or request.GET.get('format') == 'json'
            )
            kiosk_post = request.method == 'POST' and step in MT_KIOSK_STEP_ROLE and _is_mt_kiosk_post(request)

            if kiosk_post:
                actor = _get_mt_kiosk_user(request)
                if not actor:
                    msg = "Sessiya yakunlangan — iltimos, QR birkangizni qayta skanerlang."
                    if is_ajax:
                        return JsonResponse({'status': 'UNAUTHORIZED', 'message': msg}, status=401)
                    messages.warning(request, msg)
                    return redirect('meto_hub_flow_kiosk')
                if actor.role != MT_KIOSK_STEP_ROLE[step]:
                    msg = "Sizning birkangiz bu bo'lim amali uchun ruxsatga ega emas."
                    if is_ajax:
                        return JsonResponse({'status': 'FORBIDDEN', 'message': msg}, status=403)
                    messages.error(request, msg)
                    return redirect('meto_hub_flow_kiosk')
                request.session[MT_KIOSK_SESSION_AT_KEY] = int(time.time())
                request.session.modified = True
                request._mt_actor = actor
            else:
                if not request.user.is_authenticated:
                    if is_ajax or '/api/' in request.path:
                        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat yo'q"}, status=403)
                    return redirect(f"{reverse('root_login')}?next={request.path}")
                if not _allowed_tabs(request.user):
                    if is_ajax or '/api/' in request.path:
                        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat yo'q"}, status=403)
                    messages.error(request, "Bu bo'limga ruxsatingiz yo'q.")
                    return redirect('root_login')
                if request.method != 'POST':
                    if is_ajax:
                        return JsonResponse({'status': 'ERROR', 'message': "Faqat POST so'rovi qabul qilinadi."}, status=405)
                    return redirect(_flow_url(tab))
                if not can(request.user, step):
                    if is_ajax:
                        return JsonResponse({'status': 'FORBIDDEN', 'message': "Bu amalga ruxsatingiz yo'q."}, status=403)
                    return _deny(request, tab)
                request._mt_actor = request.user

            try:
                message = view(request, *args, **kwargs)
                if is_ajax:
                    return JsonResponse({'status': 'OK', 'message': message or 'Bajarildi'})
                if message:
                    messages.success(request, message)
            except FlowError as exc:
                if is_ajax:
                    return JsonResponse({'status': 'ERROR', 'message': str(exc)}, status=400)
                messages.error(request, str(exc))
            next_url = (request.POST.get('next') or '').strip()
            if kiosk_post:
                if next_url.startswith('/tayyorlov/meto-tasnif/'):
                    return redirect(next_url)
                return redirect('meto_hub_flow_kiosk')
            if next_url.startswith('/tayyorlov/'):
                return redirect(next_url)
            return redirect(_flow_url(tab))
        return wrapper
    return decorator


def _batch(batch_id):
    batch = flow_logic.v2_batches().filter(id=batch_id).first()
    if batch is None:
        if CuttingBatch.objects.filter(id=batch_id).exists():   # ro'yxat eskirgan: eski /meto/ birinchi olgan
            raise FlowError("Bu pastal endi yangi oqimda emas — eski /meto/ da tasdiqlangan, u eski tartibda davom etadi.")
        raise Http404
    return batch


def _posted_counts(request):
    return {key[len('qty_'):]: value for key, value in request.POST.items() if key.startswith('qty_')}


def _name(batch):
    return batch.pastal_code or batch.name


@_form_action('meto', 'meto')
def meto_take(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.take_meto(batch, _action_user(request))
    return f"{_name(batch)}: meto boshlandi."


@_form_action('meto', 'meto')
def meto_cancel(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.cancel_meto(batch, _action_user(request))
    return f"{_name(batch)}: qabul bekor qilindi, «Meto kutmoqda» ga qaytdi."


@_form_action('meto', 'meto')
def meto_finish(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.finish_meto(batch, _posted_counts(request), _action_user(request), note=request.POST.get('meto_note', ''))
    return f"{_name(batch)}: meto tugadi, tasnifga uzatildi."


@_form_action('tasnif', 'tasnif')
def tasnif_take(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.take_tasnif(batch, _action_user(request))
    return f"{_name(batch)}: tasnif boshlandi."


@_form_action('tasnif', 'tasnif')
def tasnif_cancel(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.cancel_tasnif(batch, _action_user(request))
    return f"{_name(batch)}: qabul bekor qilindi, «Tasnif kutmoqda» ga qaytdi."


@_form_action('tasnif', 'tasnif')
def tasnif_return(request, batch_id):
    batch = _batch(batch_id)
    reason = request.POST.get('reason', '')
    flow_logic.return_tasnif(batch, _action_user(request), reason=reason)
    return f"{_name(batch)}: Meto bo'limiga qaytarildi."


@_form_action('tasnif', 'tasnif')
def tasnif_finish(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.finish_tasnif(batch, _posted_counts(request), _action_user(request))
    return f"{_name(batch)}: tasnif soni saqlandi, stiker chiqarish ro'yxati va Meto omboriga o'tdi."


@_form_action('stiker', 'stiker')
def tasnif_update(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.update_tasnif_counts(batch, _posted_counts(request), request.user)
    return f"{_name(batch)}: tasnif soni o'zgartirildi — qutilarga qaytadan bo'ling."


@_form_action('stiker', 'stiker')
def split(request, batch_id):
    batch = _batch(batch_id)
    plan = {}
    for key, value in request.POST.items():
        if key.startswith('boxes_'):
            plan.setdefault(key[len('boxes_'):], {})['boxes'] = value
        elif key.startswith('cap_'):
            plan.setdefault(key[len('cap_'):], {})['capacity'] = value
    counts = _posted_counts(request) or None
    created = flow_logic.split_boxes(batch, plan, request.user, counts=counts)
    return f"{_name(batch)}: {created} ta quti va stikerlar yaratildi — endi chop eting."


@_form_action('sklad', 'sklad')
def sklad_receive(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.sklad_receive(batch, request.POST.get('kg'), request.user)
    return f"{_name(batch)}: sklad qabul qildi ({request.POST.get('kg')} kg)."


@_form_action('zakazlar', 'zakazlar')
def beta_toggle(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    if order.meto_beta:
        # O'chirishdan oldin: yoqiq paytda kesilgan pastallarga oqim yozuvi — ular yangi oqimda tugaydi
        flow_logic.ensure_flows()
    order.meto_beta = not order.meto_beta
    if order.meto_beta:
        # Har yoqilganda "hozirdan": o'chiq paytda kesilib eski oqimda ishlangan pastallar yangi oqimga tushmaydi
        # (hali meto qilinmaganlari baribir tushadi — v2_batches)
        order.meto_beta_since = timezone.now()
    order.save(update_fields=['meto_beta', 'meto_beta_since'])
    state = ("yoqildi (hali meto qilinmagan va bundan keyin kesiladigan pastallar)" if order.meto_beta
             else "o'chirildi")
    return f"«{order.order_number}»: yangi meto oqimi {state}."


# ------------------------------------------------------------------ JSON API (skaner)

def _require_branch(user, branch):
    if not can(user, 'sklad' if branch == 'HQ' else 'uychi'):
        raise FlowError("Bu filial skladi uchun ruxsatingiz yo'q.")


def _ids(values):
    """JSON dagi id lar ro'yxati -> butun sonlar (raqam bo'lmaganlari tashlanadi, 500 xato bo'lmasin)."""
    if not isinstance(values, (list, tuple)):
        return []
    return [int(v) for v in values if str(v).strip().isdigit()]


_CYRILLIC = re.compile('[Ѐ-ӿ]')


def _container_problem(container):
    """Biriktirishga yaramasa — sababi (planshet konteynerni qutidan oldin ham qabul qiladi)."""
    if container.box_id:
        return f"{container.code} allaqachon {container.box.box_code} qutisiga biriktirilgan."
    if container.kind != Container.Kind.BIG:
        return f"{container.code} — kichik (ta'mir) konteyner; katta konteynerni uring."
    if container.status != Container.Status.EMPTY:
        return f"{container.code} bo'sh emas ({container.get_status_display()})."
    return None


def _box_problem(box, batch, current):
    if box.status == Box.Status.CANCELLED:
        return f"{box.box_code} qutisi bekor qilingan — yangi stikerni uring."
    if current:
        return f"{box.box_code} allaqachon {current.code} konteynerida."
    if batch is None or not flow_logic.is_v2_batch(batch):
        return f"{box.box_code} — bu quti yangi meto oqimida emas."
    flow = flow_logic.get_flow(batch)
    if flow.stage != PastalFlow.Stage.SKLAD:
        return (f"{batch.pastal_code or batch.name} pastali skladda emas ({flow.get_stage_display()}) — "
                f"biriktirish faqat skladdagi pastal qutilari uchun.")
    return None


@_api('ship_any')
def api_resolve(request):
    code = request.GET.get('code', '').strip()
    container = flow_logic.find_container(code)
    if container is not None:
        return JsonResponse({'status': 'OK', 'kind': 'container', 'code': container.code,
                             'container_kind': container.get_kind_display(), 'state': flow_logic.container_state(container),
                             'box': container.box.box_code if container.box_id else None,
                             'problem': _container_problem(container)})
    number = parse_container_code(code)
    if number is not None:   # format to'g'ri, lekin bunday konteyner yaratilmagan
        last = Container.objects.order_by('-number').values_list('number', flat=True).first()
        hint = f" Oxirgi yaratilgani: KONT-{last:06d}." if last else " Hali birorta konteyner yaratilmagan."
        raise FlowError(f"KONT-{number:06d} konteyneri tizimda yo'q — avval «Konteynerlar» bo'limida yaratiladi.{hint}")
    box = flow_logic.resolve_box(code)
    if box is None:
        if _CYRILLIC.search(code):
            raise FlowError(f"«{code[:40]}» — klaviatura kirill (rus) tilida. Lotin (EN) ga o'tkazib, qayta uring.")
        raise FlowError(f"«{code[:40]}» — bunday stiker yoki konteyner topilmadi.")
    batch = box.cutting_batch_item.batch if box.cutting_batch_item_id else None
    current = Container.objects.filter(box=box).first()
    return JsonResponse({'status': 'OK', 'kind': 'box', 'box': box.box_code, 'razmer': box.razmer or '—',
                         'qty': box.quantity, 'pastal': (batch.pastal_code or batch.name) if batch else '—',
                         'cancelled': box.status == Box.Status.CANCELLED,
                         'container': current.code if current else None,
                         'problem': _box_problem(box, batch, current)})


@_api('ship_any')
@require_POST
def api_link(request):
    data = _json(request)
    box = flow_logic.resolve_box(data.get('box'))
    if box is not None and flow_logic.is_v2_box(box):   # boshqa xatolarni link_container o'zi aytadi
        _require_branch(request.user, flow_logic.get_flow(box.cutting_batch_item.batch).branch)
    container, box = flow_logic.link_container(data.get('box'), data.get('container'), request.user)
    return JsonResponse({'status': 'OK', 'message': f"{box.box_code} → {container.code} biriktirildi."})


@_api('ship_any')
@require_POST
def api_unlink(request):
    code = _json(request).get('code')
    container = flow_logic.find_container(code)
    if container is None:
        box = flow_logic.resolve_box(code)
        container = Container.objects.filter(box=box).first() if box else None
    if container is not None:
        _require_branch(request.user, container.branch)
    container, box = flow_logic.unlink(code, request.user)
    return JsonResponse({'status': 'OK', 'message': f"{container.code} bo'shatildi ({box.box_code} bilan bog'lanish bekor)."})


@_api('ship_any')
def api_check(request):
    shipment_id = request.GET.get('shipment')
    if shipment_id and shipment_id.isdigit():
        shipment = get_object_or_404(Shipment.objects.prefetch_related('lines__batch'), id=int(shipment_id))
        batches = [line.batch for line in shipment.lines.all()]
    else:
        ids = [int(x) for x in request.GET.get('batches', '').split(',') if x.strip().isdigit()]
        batches = list(flow_logic.v2_batches().filter(id__in=ids))
    result = []
    for batch in batches:
        result.append({'batch_id': batch.id, 'pastal': _name(batch), 'rows': flow_logic.check_list(batch)})
    return JsonResponse({'status': 'OK', 'batches': result})


@_api('ship_any')
@require_POST
def api_give(request):
    data = _json(request)
    batch_ids = _ids([data.get('batch_id')])
    if not batch_ids:
        raise FlowError("Pastal tanlanmadi.")
    batch = _batch(batch_ids[0])
    step = 'sklad' if flow_logic.get_flow(batch).branch == 'HQ' else 'uychi'
    if not can(request.user, step):
        raise FlowError("Bu filial patogiga berishga ruxsatingiz yo'q.")
    screen_ids = _ids([data.get('screen')])
    if not screen_ids:
        raise FlowError("Patokni tanlang.")
    screen = screen_ids[0]
    flow_logic.give_to_patok(batch, screen, data.get('scanned') or [], request.user)
    return JsonResponse({'status': 'OK', 'message': f"{_name(batch)} {get_patok_code(screen)}-patokka berildi."})


@_api('sklad')
@require_POST
def api_shipment_request_verification(request):
    """Tayyorlov masteri pastallarni tanlab, Meto planshetiga tekshirishga yuboradi."""
    data = _json(request)
    batches = list(flow_logic.v2_batches().filter(id__in=_ids(data.get('batch_ids'))))
    notes = (data.get('notes') or '').strip()
    shipment = flow_logic.request_verification(batches, request.user, notes=notes)
    return JsonResponse({
        'status': 'OK',
        'shipment_id': shipment.id,
        'shipment_number': shipment.number,
        'message': f"{shipment.number}: {len(batches)} ta pastal Meto planshetiga tekshirishga yuborildi."
    })


@_api('sklad')
@require_POST
def api_shipment_cancel_verification(request, shipment_id):
    """Tekshiruv kutilayotgan yuk xatini bekor qilish."""
    shipment = get_object_or_404(Shipment, id=shipment_id)
    flow_logic.cancel_verification(shipment, request.user)
    return JsonResponse({'status': 'OK', 'message': f"{shipment.number} tekshiruvi bekor qilindi."})


@_api('sklad')   # Koson → Uychi yuk xatini faqat Koson (Tayyorlov skladi) jo'natadi
@require_POST
def api_shipment_dispatch(request, shipment_id):
    """Meto planshetida barcha konteynerlar tekshirilgach, yuk xati jo'natiladi."""
    data = _json(request)
    shipment = get_object_or_404(Shipment, id=shipment_id)
    scanned = data.get('scanned') or []
    shipment = flow_logic.dispatch_shipment(
        shipment, scanned, request.user,
        vehicle=data.get('vehicle', ''),
        driver=data.get('driver', ''),
        phone=data.get('phone', ''),
        notes=data.get('notes', '')
    )
    return JsonResponse({
        'status': 'OK',
        'message': f"{shipment.number} yuk xati Uychiga jo'natildi!",
        'print_url': reverse('meto_hub_shipment_print', args=[shipment.id])
    })


@_api('ship_any')
@require_POST
def api_shipment_receive_all(request, shipment_id):
    """Qabul qiluvchi filial skladi barcha konteynerlar skanerlangach yuk xatidagi barcha pastallarni qabul qiladi
    (Koson → Uychi — Uychi sklad, qaytarish Uychi → Koson — Tayyorlov skladi)."""
    data = _json(request)
    shipment = get_object_or_404(Shipment, id=shipment_id)
    _require_branch(request.user, shipment.destination_branch)
    scanned = data.get('scanned') or []
    shipment, _results = flow_logic.receive_shipment(shipment, scanned, request.user)
    where = "Uychi filial" if shipment.destination_branch == 'UYCHI' else "Koson sklad"
    return JsonResponse({
        'status': 'OK',
        'message': f"{shipment.number} bo'yicha barcha pastallar {where} balansiga qabul qilindi!"
    })


@_api('sklad')
@require_POST
def api_ship(request):
    data = _json(request)
    batches = list(flow_logic.v2_batches().filter(id__in=_ids(data.get('batch_ids'))))
    shipment = flow_logic.create_shipment(batches, data.get('scanned') or [], request.user,
                                          vehicle=data.get('vehicle', ''), driver=data.get('driver', ''),
                                          phone=data.get('phone', ''), notes=data.get('notes', ''))
    return JsonResponse({'status': 'OK', 'message': f"{shipment.number} yuk xati tuzildi.",
                         'print_url': reverse('meto_hub_shipment_print', args=[shipment.id])})


def _line(request, line_id):
    line = get_object_or_404(ShipmentPastal.objects.select_related('shipment'), id=line_id)
    step = 'uychi' if line.shipment.destination_branch == 'UYCHI' else 'sklad'
    if not can(request.user, step):
        raise FlowError("Bu yuk xatini qabul qilishga ruxsatingiz yo'q.")
    return line


@_api('ship_any')
@require_POST
def api_receive(request, line_id):
    line, missing = flow_logic.receive_line(_line(request, line_id), _json(request).get('scanned') or [], request.user)
    message = f"{_name(line.batch)} qabul qilindi."
    if missing:
        message += f" Skaner qilinmagan konteynerlar: {', '.join(missing)} — skladda tekshiring."
    return JsonResponse({'status': 'OK', 'message': message})


@_api('uychi')
@require_POST
def api_return(request, line_id):
    back = flow_logic.return_line(_line(request, line_id), request.user, (_json(request).get('note') or '').strip())
    return JsonResponse({'status': 'OK', 'message': f"Pastal qaytarildi — {back.number} (Uychi → Koson)."})


@flow_access_required
def shipment_print(request, shipment_id):
    shipment = get_object_or_404(Shipment.objects.select_related('created_by'), id=shipment_id)
    lines = []
    for line in shipment.lines.select_related('batch__order_item__order', 'batch__order_item__article'):
        batch = line.batch
        boxes = list(flow_logic._active_boxes(batch))
        lines.append({'line': line, 'batch': batch, 'boxes': len(boxes), 'qty': sum(b.quantity for b in boxes),
                      'containers': Container.objects.filter(box__in=boxes).count()})
    return render(request, 'meto_hub/shipment_print.html', {
        'shipment': shipment, 'lines': lines, 'total_boxes': sum(r['boxes'] for r in lines),
        'total_qty': sum(r['qty'] for r in lines), 'total_containers': sum(r['containers'] for r in lines)})
