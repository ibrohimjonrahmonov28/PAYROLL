"""
Tayyorlov Bo'limi → Meto Planshet (Tablet) Interfeysi (/tayyorlov/tablet/).
Planshet uchun maxsus sensorli (touch-first) va shtrix-kod skaneri bilan tezkor ishlash stoli:
1. Quti bilan konteynerni birlashtirish (Box ↔ Container linking & unlinking).
2. Yuk xatidagi ishlarni tekshirish (Koson ↔ Uychi jo'natish & qabul qilish).
3. Patokka berilayotgan yuklarni tekshirish va chiqarish (Line dispatch).
"""
import functools
import json

from django.contrib import messages
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from production import meto_flow as flow_logic
from production.meto_flow import can, get_patok_code
from production.models import (
    Box,
    Container,
    ContainerEvent,
    PastalFlow,
    Shipment,
    ShipmentPastal,
)

# Sahifaga <script> ichida qo'yiladigan JSON: nomlardagi "</script>" sahifani buzmasin (json_script bilan bir xil)
_JSON_SCRIPT_ESCAPES = {ord('<'): '\\u003C', ord('>'): '\\u003E', ord('&'): '\\u0026'}


def _script_json(value):
    return json.dumps(value).translate(_JSON_SCRIPT_ESCAPES)


# Planshet kartochkalari uchun umumiy prefetch (har kartochkaga alohida so'rov bo'lmasin)
TABLET_CARD_SELECT = ('batch__order_item__order__customer', 'batch__order_item__article', 'sklad_received_by')
TABLET_CARD_PREFETCH = ('batch__items__order_item_size', 'batch__items__flow_size', 'batch__items__boxes__container')


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
    """Foydalanuvchi filiali (HQ yoki UYCHI). Sklad rollari — rolidan (User.branch sukut bo'yicha HQ, Uychi sklad
    foydalanuvchisida filial qo'yilmay qolsa planshet Kosonni ochardi)."""
    req_branch = (request.GET.get('branch') or '').strip().upper()
    user = request.user
    if user.role == User.Role.UYCHI_SKLAD:
        return 'UYCHI'
    if user.role == User.Role.METO_SKLAD:
        return 'HQ'
    user_branch = getattr(user, 'branch', None)
    if req_branch in ('HQ', 'UYCHI'):
        if user.is_superadmin() or user.role == User.Role.ADMIN or not user_branch:
            return req_branch
        if req_branch == 'UYCHI' and can(user, 'uychi'):
            return 'UYCHI'
        if req_branch == 'HQ' and can(user, 'sklad'):
            return 'HQ'
    if user_branch in ('HQ', 'UYCHI'):
        return user_branch
    if can(user, 'uychi') and not can(user, 'sklad'):
        return 'UYCHI'
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
    containers_qs = flow_logic.box_containers(boxes)
    linked = {c.box_id for c in containers_qs}
    container_codes = [c.code for c in containers_qs]
    total_cut = sum(s['cut'] for s in sizes)
    total_meto = sum(s['meto'] or 0 for s in sizes)
    total_tasnif = sum(s['tasnif'] or 0 for s in sizes)

    boxes_count = len(boxes)
    printed_count = sum(1 for b in boxes if b.is_printed)
    linked_count = len(linked)
    sklad_received = bool(flow.sklad_received_at and batch.actual_weight_kg)
    kg_pending = bool(flow.branch == 'HQ' and not sklad_received)

    if boxes_count == 0:
        packaging_status = 'packet'
        packaging_label = "Paketda (Qutisiz)"
    elif printed_count < boxes_count:
        packaging_status = 'stiker_pending'
        packaging_label = "Stiker kutilmoqda"
    elif linked_count < boxes_count:
        packaging_status = 'container_pending'
        packaging_label = "Konteyner kutilmoqda"
    else:
        packaging_status = 'ready'
        packaging_label = "To'liq tayyor"

    # Tayyorlik sharti (patokka berish yoki yuk xatiga yuklash uchun). Tekshiruvga yuborilgan pastal tayyor emas —
    # aks holda uni patokka berib, keyin yuk xatini ham jo'natib yuborish mumkin edi
    pending = flow_logic.pending_shipment(batch)
    ready = (
        boxes_count > 0
        and printed_count == boxes_count
        and linked_count == boxes_count
        and (flow.branch != 'HQ' or sklad_received)
        and not pending
    )

    weight_str = f"{batch.actual_weight_kg:.2f}".rstrip('0').rstrip('.') if batch.actual_weight_kg is not None else None

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
        'boxes_count': boxes_count,
        'printed_count': printed_count,
        'linked_count': linked_count,
        'containers': container_codes,
        'weight_kg': weight_str,
        'sklad_received': sklad_received,
        'sklad_received_at': flow.sklad_received_at.strftime('%d.%m.%Y %H:%M') if flow.sklad_received_at else None,
        'sklad_received_by': (flow.sklad_received_by.get_full_name() or flow.sklad_received_by.username) if flow.sklad_received_by else '',
        'kg_pending': kg_pending,
        'packaging_status': packaging_status,
        'packaging_label': packaging_label,
        'ready': ready,
        'pending_verification': pending.number if pending else None,
        'branch': flow.branch,
        'stage': flow.stage,
    }


def _tablet_kg_pending_cards():
    """Tasnifdan chiqqan, lekin hali kg kiritilmagan HQ pastallari ro'yxati."""
    qs = (
        PastalFlow.objects.filter(
            stage=PastalFlow.Stage.SKLAD,
            branch='HQ',
        ).filter(
            Q(sklad_received_at__isnull=True) | Q(batch__actual_weight_kg__isnull=True)
        )
        .select_related(*TABLET_CARD_SELECT)
        .prefetch_related(*TABLET_CARD_PREFETCH, flow_logic.pending_lines_prefetch())
        .order_by('-batch__created_at', '-id')[:100]
    )
    return [_tablet_card(f) for f in qs]


def _tablet_sklad_cards(branch):
    """Skladda saqlanayotgan pastallar ro'yxati (HQ da kg si kiritilganlar, eng yangilari tepada)."""
    qs = PastalFlow.objects.filter(stage=PastalFlow.Stage.SKLAD, branch=branch)
    if branch == 'HQ':
        qs = qs.filter(sklad_received_at__isnull=False, batch__actual_weight_kg__isnull=False)
    qs = (
        qs.select_related(*TABLET_CARD_SELECT)
        .prefetch_related(*TABLET_CARD_PREFETCH, flow_logic.pending_lines_prefetch())
        .order_by('-batch__created_at', '-id')[:150]
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
            'driver': sp.shipment.driver_name,
            'vehicle': sp.shipment.vehicle_number,
            'phone': sp.shipment.driver_phone,
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


def _tablet_pending_shipments():
    """HQ da tekshiruv kutayotgan yuk xatlari (Tayyorlov bo'limidan tekshirishga yuborilgan)."""
    shipments = (
        Shipment.objects.filter(
            source_branch='HQ',
            status=Shipment.Status.PENDING,
        )
        .prefetch_related(
            'lines__batch__order_item__order',
            'lines__batch__order_item__article',
            'lines__batch__items__boxes',
        )
        .order_by('-id')[:20]
    )
    result = []
    for s in shipments:
        pastals_data = []
        for line in s.lines.all():
            batch = line.batch
            order = batch.order_item.order if batch.order_item else None
            article = batch.order_item.article if batch.order_item else None
            boxes = [b for item in batch.items.all() for b in item.boxes.all() if b.status != Box.Status.CANCELLED]
            containers = list(Container.objects.filter(box__in=boxes).select_related('box').order_by('number'))
            c_codes = [c.code for c in containers]
            box_rows = []
            for b in boxes:
                c = next((cnt for cnt in containers if cnt.box_id == b.id), None)
                box_rows.append({
                    'id': b.id,
                    'code': b.box_code,
                    'number': b.box_number,
                    'qty': b.quantity,
                    'container': c.code if c else None,
                })
            pastals_data.append({
                'batch_id': batch.id,
                'pastal_code': batch.pastal_code or batch.name,
                'order_number': order.order_number if order else '—',
                'article_code': article.code if article else '—',
                'article_name': article.name if article else '—',
                'boxes_count': len(boxes),
                'boxes': box_rows,
                'containers': c_codes,
            })
        result.append({
            'shipment_id': s.id,
            'shipment_number': s.number,
            'destination_branch': s.destination_branch,
            'created_at': s.created_at.strftime('%H:%M · %d.%m.%Y'),
            'pastals_count': len(pastals_data),
            'total_boxes': sum(p['boxes_count'] for p in pastals_data),
            'pastals': pastals_data,
        })
    return result


def _tablet_incoming_shipments(branch):
    """Filialga kelayotgan yuk xatlari (Yuk xati bo'yicha to'liq guruhlangan)."""
    shipments = (
        Shipment.objects.filter(
            destination_branch=branch,
            status=Shipment.Status.IN_TRANSIT,
        )
        .prefetch_related(
            'lines__batch__order_item__order',
            'lines__batch__order_item__article',
            'lines__batch__items__boxes',
        )
        .order_by('-id')[:20]
    )
    result = []
    for s in shipments:
        pastals_data = []
        for line in s.lines.filter(status=ShipmentPastal.Status.IN_TRANSIT):
            batch = line.batch
            order = batch.order_item.order if batch.order_item else None
            article = batch.order_item.article if batch.order_item else None
            boxes = [b for item in batch.items.all() for b in item.boxes.all() if b.status != Box.Status.CANCELLED]
            containers = list(Container.objects.filter(box__in=boxes).select_related('box').order_by('number'))
            c_codes = [c.code for c in containers]
            box_rows = []
            for b in boxes:
                c = next((cnt for cnt in containers if cnt.box_id == b.id), None)
                box_rows.append({
                    'id': b.id,
                    'code': b.box_code,
                    'number': b.box_number,
                    'qty': b.quantity,
                    'container': c.code if c else None,
                })
            pastals_data.append({
                'line_id': line.id,
                'batch_id': batch.id,
                'pastal_code': batch.pastal_code or batch.name,
                'order_number': order.order_number if order else '—',
                'article_code': article.code if article else '—',
                'article_name': article.name if article else '—',
                'boxes_count': len(boxes),
                'qty': sum(b.quantity for b in boxes),
                'boxes': box_rows,
                'containers': c_codes,
            })
        result.append({
            'shipment_id': s.id,
            'shipment_number': s.number,
            'source_branch': s.source_branch,
            'destination_branch': s.destination_branch,
            'driver': s.driver_name,
            'vehicle': s.vehicle_number,
            'phone': s.driver_phone,
            'created_at': s.created_at.strftime('%H:%M · %d.%m.%Y'),
            'pastals_count': len(pastals_data),
            'total_boxes': sum(p['boxes_count'] for p in pastals_data),
            'total_qty': sum(p['qty'] for p in pastals_data),
            'pastals': pastals_data,
        })
    return result


def _tablet_patok_rows(branch):
    """Patokka berilgan faol pastallar (oxirgi 60 tasi)."""
    qs = list(
        PastalFlow.objects.filter(stage=PastalFlow.Stage.AT_PATOK, branch=branch)
        .select_related(*TABLET_CARD_SELECT, 'patok_given_by')
        .prefetch_related(*TABLET_CARD_PREFETCH, flow_logic.pending_lines_prefetch())
        .order_by('-patok_given_at', '-id')[:60]
    )
    states_by_batch = flow_logic.container_states_by_batch([flow.batch_id for flow in qs])
    rows = []
    for flow in qs:
        card = _tablet_card(flow)
        card['patok_code'] = get_patok_code(flow.batch.assigned_screen) if flow.batch.assigned_screen else '—'
        card['container_states'] = states_by_batch.get(flow.batch_id, [])
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

    # Konteynerlar statistikasi filial bo'yicha
    branch_c = Container.objects.filter(branch=branch)
    empty_c = branch_c.filter(status=Container.Status.EMPTY).count()
    filled_c = branch_c.filter(status=Container.Status.FILLED).count()
    patok_c = branch_c.filter(status=Container.Status.AT_PATOK).count()
    total_c = branch_c.count()

    # Patoklar (ekranlar) ro'yxati
    screen_range = flow_logic.KOSON_SCREENS if branch == 'HQ' else flow_logic.UYCHI_SCREENS
    screens = [(n, get_patok_code(n)) for n in screen_range]

    # Skladdagi pastallar, kg kutilayotganlar, kelayotgan yuklar, kutilayotgan yuk xatlari va patokdagi pastallar
    kg_pending_cards = _tablet_kg_pending_cards() if branch == 'HQ' else []
    sklad_cards = _tablet_sklad_cards(branch)
    packet_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'packet')
    stiker_pending_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'stiker_pending')
    container_pending_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'container_pending')
    ready_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'ready')

    incoming = _tablet_incoming(branch)
    incoming_shipments = _tablet_incoming_shipments(branch)
    pending_shipments = _tablet_pending_shipments() if branch == 'HQ' else []
    patok_rows = _tablet_patok_rows(branch)

    # Boshlang'ich rejim (default_mode)
    req_mode = request.GET.get('mode')
    if req_mode in ('link', 'storage', 'shipment', 'patok'):
        default_mode = req_mode
    elif branch == 'UYCHI':
        default_mode = 'shipment' if incoming_shipments or incoming else 'storage'
    else:
        default_mode = 'link'

    # Oxirgi konteyner voqealari (oxirgi 15 tasi)
    recent_events = (
        ContainerEvent.objects.filter(branch=branch)
        .select_related('container', 'box', 'user')
        .order_by('-id')[:15]
    )

    context = {
        'branch': branch,
        'branch_label': "Koson Bosh Bino" if branch == 'HQ' else "Uychi Filiali",
        'can_switch_branch': can_switch_branch,
        'default_mode': default_mode,
        'empty_containers': empty_c,
        'filled_containers': filled_c,
        'patok_containers': patok_c,
        'total_containers': total_c,
        'kg_pending_cards': kg_pending_cards,
        'stored_cards': sklad_cards,
        'sklad_cards': sklad_cards,
        'packet_count': packet_count,
        'stiker_pending_count': stiker_pending_count,
        'container_pending_count': container_pending_count,
        'ready_count': ready_count,
        'ready_cards': [c for c in sklad_cards if c['ready']],
        'incoming': incoming,
        'incoming_shipments': incoming_shipments,
        'pending_shipments': pending_shipments,
        'pending_shipments_json': _script_json(pending_shipments),
        'incoming_shipments_json': _script_json(incoming_shipments),
        'kg_pending_cards_json': _script_json(kg_pending_cards),
        'stored_cards_json': _script_json(sklad_cards),
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
    branch_c = Container.objects.filter(branch=branch)
    empty_c = branch_c.filter(status=Container.Status.EMPTY).count()
    filled_c = branch_c.filter(status=Container.Status.FILLED).count()
    patok_c = branch_c.filter(status=Container.Status.AT_PATOK).count()
    total_c = branch_c.count()

    kg_pending_cards = _tablet_kg_pending_cards() if branch == 'HQ' else []
    sklad_cards = _tablet_sklad_cards(branch)
    packet_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'packet')
    stiker_pending_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'stiker_pending')
    container_pending_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'container_pending')
    ready_count = sum(1 for c in sklad_cards if c['packaging_status'] == 'ready')

    incoming = _tablet_incoming(branch)
    incoming_shipments = _tablet_incoming_shipments(branch)
    pending_shipments = _tablet_pending_shipments() if branch == 'HQ' else []
    patok_rows = _tablet_patok_rows(branch)

    return JsonResponse({
        'status': 'OK',
        'branch': branch,
        'empty_containers': empty_c,
        'filled_containers': filled_c,
        'patok_containers': patok_c,
        'total_containers': total_c,
        'kg_pending_cards': kg_pending_cards,
        'stored_cards': sklad_cards,
        'sklad_cards': sklad_cards,
        'packet_count': packet_count,
        'stiker_pending_count': stiker_pending_count,
        'container_pending_count': container_pending_count,
        'ready_count': ready_count,
        'ready_cards': [c for c in sklad_cards if c['ready']],
        'incoming': incoming,
        'incoming_shipments': incoming_shipments,
        'pending_shipments': pending_shipments,
        'patok_rows': patok_rows,
    })


@tablet_access_required
@require_POST
def api_tablet_kg_receive(request):
    """Meto Planshet orqali tasnifdan chiqqan pastalga kg kiritish va skladga qabul qilish (faqat Tayyorlov skladi)."""
    if not can(request.user, 'sklad'):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Kg kiritish faqat Tayyorlov skladiga ruxsat etilgan."},
                            status=403)
    try:
        data = json.loads(request.body) if request.body else request.POST
    except Exception:
        data = request.POST
    batch_id = str(data.get('batch_id') or '').strip()
    kg = data.get('kg')
    if not batch_id.isdigit():
        return JsonResponse({'status': 'ERROR', 'message': "Pastal tanlanmadi."}, status=400)
    batch = get_object_or_404(flow_logic.v2_batches(), id=int(batch_id))
    try:
        flow = flow_logic.sklad_receive(batch, kg, request.user)
        card = _tablet_card(flow)
        return JsonResponse({
            'status': 'OK',
            'message': f"{card['pastal_code']} qabul qilindi ({card['weight_kg']} kg). Skladga tushdi.",
            'card': card,
        })
    except flow_logic.FlowError as e:
        return JsonResponse({'status': 'ERROR', 'message': str(e)}, status=400)
