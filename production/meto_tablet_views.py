"""
Tayyorlov Bo'limi → Meto Planshet (Tablet) Interfeysi (/tayyorlov/tablet/).
Planshet uchun maxsus sensorli (touch-first) va shtrix-kod skaneri bilan tezkor ishlash stoli:
1. Quti bilan konteynerni birlashtirish (Box ↔ Container linking & unlinking).
2. Yuk xatidagi ishlarni tekshirish (Koson ↔ Uychi jo'natish & qabul qilish).
3. Patokka berilayotgan yuklarni tekshirish va chiqarish (Line dispatch).
"""
import functools
from collections import OrderedDict

from django.contrib import messages
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from production import meto_flow as flow_logic
from production.meto_flow import can, get_flow, get_patok_code, is_v2_batch
from production.models import (
    Box,
    Container,
    ContainerEvent,
    CuttingBatch,
    PastalFlow,
    Shipment,
    ShipmentPastal,
)


def tablet_access_required(view_func):
    """Meto Planshetga kirish ruxsati (Sklad, Uychi, Meto, Admin, Superadmin)."""
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect(f"{reverse('root_login')}?next={request.path}")
        allowed = (
            user.is_superadmin()
            or user.role in (
                User.Role.ADMIN,
                User.Role.METO,
                User.Role.METO_SKLAD,
                User.Role.UYCHI_SKLAD,
                User.Role.BRANCH_ADMIN,
                User.Role.STICKER,
                User.Role.TASNIF,
            )
            or can(user, 'sklad')
            or can(user, 'uychi')
        )
        if not allowed:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
                return JsonResponse({'status': 'FORBIDDEN', 'message': "Planshetga kirish ruxsatingiz yo'q."}, status=403)
            messages.error(request, "Planshetga kirish uchun yetarli ruxsatingiz yo'q.")
            return redirect('meto_hub_flow')
        return view_func(request, *args, **kwargs)
    return wrapper


def _tablet_branch(request):
    """Foydalanuvchi filiali (HQ yoki UYCHI), admin/superadmin url parametri orqali o'zgartirishi mumkin."""
    req_branch = (request.GET.get('branch') or '').strip().upper()
    user_branch = getattr(request.user, 'branch', None)
    if req_branch in ('HQ', 'UYCHI') and (request.user.is_superadmin() or request.user.role == User.Role.ADMIN or not user_branch):
        return req_branch
    if user_branch in ('HQ', 'UYCHI'):
        return user_branch
    return 'HQ'


def _tablet_card(flow):
    """Planshet uchun pastal kartochkasi ma'lumotlari."""
    batch = flow.batch
    order = batch.order_item.order if batch.order_item else None
    article = batch.order_item.article if batch.order_item else None
    customer = (order.customer.name if order.customer_id else order.client_name) if order else ''
    image = ''
    if article and article.image:
        try:
            image = article.image.url
        except Exception:
            image = ''

    sizes = []
    for item in sorted(batch.items.all(), key=lambda it: (it.order_item_size_id, it.id)):
        size = getattr(item, 'flow_size', None)
        sizes.append({
            'id': item.id,
            'name': item.order_item_size.size_name if item.order_item_size else '—',
            'cut': item.quantity,
            'meto': size.meto_qty if size else None,
            'tasnif': size.tasnif_qty if size else None,
        })

    boxes = [b for item in batch.items.all() for b in item.boxes.all() if b.status != Box.Status.CANCELLED]
    linked = {c.box_id for c in Container.objects.filter(box__in=boxes)} if boxes else set()
    total_cut = sum(s['cut'] for s in sizes)
    total_meto = sum(s['meto'] or 0 for s in sizes)
    total_tasnif = sum(s['tasnif'] or 0 for s in sizes)

    # Tayyorlik sharti (patokka berish yoki yuk xatiga yuklash uchun)
    ready = (
        len(boxes) > 0
        and all(b.is_printed for b in boxes)
        and len(linked) == len(boxes)
        and (flow.branch != 'HQ' or bool(flow.sklad_received_at and batch.actual_weight_kg))
    )

    return {
        'id': batch.id,
        'batch_id': batch.id,
        'flow_id': flow.id,
        'pastal_code': batch.pastal_code or batch.name,
        'order_number': order.order_number if order else '—',
        'customer': customer,
        'article_code': article.code if article else '—',
        'article_name': article.name if article else '—',
        'image': image,
        'sizes': sizes,
        'total_cut': total_cut,
        'total_meto': total_meto,
        'total_tasnif': total_tasnif,
        'boxes_count': len(boxes),
        'printed_count': sum(1 for b in boxes if b.is_printed),
        'linked_count': len(linked),
        'weight_kg': batch.actual_weight_kg,
        'sklad_received': bool(flow.sklad_received_at and batch.actual_weight_kg),
        'ready': ready,
        'branch': flow.branch,
        'stage': flow.stage,
    }


def _tablet_sklad_cards(branch):
    """Skladdagi pastallar ro'yxati (eng yangilari tepada)."""
    qs = (
        PastalFlow.objects.filter(stage=PastalFlow.Stage.SKLAD, branch=branch)
        .select_related(
            'batch__order_item__order__customer',
            'batch__order_item__article',
            'sklad_received_by',
        )
        .prefetch_related(
            'batch__items__order_item_size',
            'batch__items__flow_size',
            'batch__items__boxes',
        )
        .order_by('-batch__created_at', '-id')[:100]
    )
    return [_tablet_card(f) for f in qs]


def _tablet_incoming(branch):
    """Kelayotgan yuk xatlari (in-transit)."""
    qs = (
        ShipmentPastal.objects.filter(
            status=ShipmentPastal.Status.IN_TRANSIT,
            shipment__destination_branch=branch,
        )
        .select_related(
            'shipment',
            'batch__order_item__order__customer',
            'batch__order_item__article',
        )
        .prefetch_related('batch__items__boxes')
        .order_by('-shipment_id', '-id')[:100]
    )
    lines = []
    for sp in qs:
        boxes = [b for item in sp.batch.items.all() for b in item.boxes.all() if b.status != Box.Status.CANCELLED]
        lines.append({
            'line_id': sp.id,
            'shipment_id': sp.shipment_id,
            'shipment_number': sp.shipment.number,
            'source_branch': sp.shipment.source_branch,
            'destination_branch': sp.shipment.destination_branch,
            'driver': sp.shipment.driver,
            'vehicle': sp.shipment.vehicle,
            'phone': sp.shipment.phone,
            'created_at': sp.shipment.created_at,
            'batch_id': sp.batch_id,
            'pastal_code': sp.batch.pastal_code or sp.batch.name,
            'order_number': sp.batch.order_item.order.order_number if sp.batch.order_item else '—',
            'article_code': sp.batch.order_item.article.code if sp.batch.order_item and sp.batch.order_item.article else '—',
            'article_name': sp.batch.order_item.article.name if sp.batch.order_item and sp.batch.order_item.article else '—',
            'boxes_count': len(boxes),
            'qty': sum(b.quantity for b in boxes),
        })
    return lines


def _tablet_patok_rows(branch):
    """Patokka berilgan faol pastallar (oxirgi 60 tasi)."""
    qs = (
        PastalFlow.objects.filter(stage=PastalFlow.Stage.AT_PATOK, branch=branch)
        .select_related(
            'batch__order_item__order',
            'batch__order_item__article',
            'patok_given_by',
        )
        .prefetch_related('batch__items__boxes')
        .order_by('-patok_given_at', '-id')[:60]
    )
    rows = []
    for flow in qs:
        card = _tablet_card(flow)
        containers = Container.objects.filter(box__cutting_batch_item__batch=card['batch_id'])
        states = OrderedDict()
        for c in containers:
            st = flow_logic.container_state(c)
            states[st] = states.get(st, 0) + 1
        card['patok_code'] = get_patok_code(flow.batch.assigned_screen) if flow.batch.assigned_screen else '—'
        card['container_states'] = list(states.items())
        card['patok_given_at'] = flow.patok_given_at
        card['patok_given_by'] = flow.patok_given_by.get_full_name() or flow.patok_given_by.username if flow.patok_given_by else '—'
        rows.append(card)
    return rows


@tablet_access_required
def meto_tablet_view(request):
    """
    Meto Planshet Bosh Sahifasi (/tayyorlov/tablet/):
    Planshet / sensorli ekranlar uchun maxsus 3 rejimli stol:
    1. Biriktirish (Quti ↔ Konteyner)
    2. Yuk Xatlari (Koson ↔ Uychi)
    3. Patokka Berish (Tikuvga tekshirib chiqarish)
    """
    flow_logic.sync_rejected_flows()
    branch = _tablet_branch(request)
    can_switch_branch = request.user.is_superadmin() or request.user.role == User.Role.ADMIN

    # Konteynerlar statistikasi
    empty_c = Container.objects.filter(status=Container.Status.EMPTY).count()
    filled_c = Container.objects.filter(status=Container.Status.FILLED).count()
    patok_c = Container.objects.filter(status=Container.Status.AT_PATOK).count()
    total_c = Container.objects.count()

    # Patoklar (ekranlar) ro'yxati
    screen_range = flow_logic.KOSON_SCREENS if branch == 'HQ' else flow_logic.UYCHI_SCREENS
    screens = [(n, get_patok_code(n)) for n in screen_range]

    # Skladdagi pastallar, kelayotgan yuklar va patokdagi pastallar
    sklad_cards = _tablet_sklad_cards(branch)
    incoming = _tablet_incoming(branch)
    patok_rows = _tablet_patok_rows(branch)

    # Oxirgi konteyner voqealari (oxirgi 15 tasi)
    recent_events = (
        ContainerEvent.objects.select_related('container', 'box', 'user')
        .order_by('-id')[:15]
    )

    context = {
        'branch': branch,
        'branch_label': "Koson Bosh Bino" if branch == 'HQ' else "Uychi Filiali",
        'can_switch_branch': can_switch_branch,
        'empty_containers': empty_c,
        'filled_containers': filled_c,
        'patok_containers': patok_c,
        'total_containers': total_c,
        'sklad_cards': sklad_cards,
        'ready_cards': [c for c in sklad_cards if c['ready']],
        'incoming': incoming,
        'patok_rows': patok_rows,
        'screens': screens,
        'recent_events': recent_events,
    }
    return render(request, 'meto_hub/tablet.html', context)


@tablet_access_required
def meto_tablet_state_api(request):
    """Planshet uchun jonli yangilanish API (/tayyorlov/api/tablet/state/)."""
    flow_logic.sync_rejected_flows()
    branch = _tablet_branch(request)
    empty_c = Container.objects.filter(status=Container.Status.EMPTY).count()
    filled_c = Container.objects.filter(status=Container.Status.FILLED).count()
    patok_c = Container.objects.filter(status=Container.Status.AT_PATOK).count()
    total_c = Container.objects.count()

    sklad_cards = _tablet_sklad_cards(branch)
    incoming = _tablet_incoming(branch)
    patok_rows = _tablet_patok_rows(branch)

    return JsonResponse({
        'status': 'OK',
        'branch': branch,
        'empty_containers': empty_c,
        'filled_containers': filled_c,
        'patok_containers': patok_c,
        'total_containers': total_c,
        'sklad_cards': sklad_cards,
        'ready_cards': [c for c in sklad_cards if c['ready']],
        'incoming': incoming,
        'patok_rows': patok_rows,
    })
