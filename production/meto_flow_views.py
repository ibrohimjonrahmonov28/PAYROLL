"""
Meto Markazi → "Yangi oqim (beta)" (egasi talabi 2026-11-19; mantiq: production/meto_flow.py). Bitta sahifa, tablar:
Meto, Tasnif, Stiker, Koson sklad, Uychi, Yuk xatlari, Beta zakazlar — har rol faqat o'z tabini ko'radi, superadmin
hammasini. Skanerli amallar (biriktirish, konteyner tekshiruvi, patokka berish, yuk xati, qabul) — JSON API.
"""
import functools
import json
from collections import OrderedDict
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from production import meto_flow as flow_logic
from production.meto_flow import FlowError, can
from production.models import Box, Container, Order, PastalFlow, Shipment, ShipmentPastal
from production.services import get_patok_code

TABS = (
    ('meto', "Meto", 'meto'),
    ('tasnif', "Tasnif", 'tasnif'),
    ('stiker', "Stiker", 'stiker'),
    ('sklad', "Koson sklad", 'sklad'),
    ('uychi', "Uychi", 'uychi'),
    ('yukxat', "Yuk xatlari", None),
    ('zakazlar', "Beta zakazlar", 'zakazlar'),
)


def _allowed_tabs(user):
    tabs = []
    for key, label, step in TABS:
        if step is None:
            ok = can(user, 'sklad') or can(user, 'uychi')
        else:
            ok = can(user, step)
        if ok:
            tabs.append((key, label))
    return tabs


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
    query = urlencode({'tab': tab, **{k: v for k, v in params.items() if v not in (None, '')}})
    return f"{reverse('meto_hub_flow')}?{query}"


def _deny(request, tab):
    messages.error(request, "Bu amalga ruxsatingiz yo'q.")
    return redirect(_flow_url(tab))


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def _allowed(user, step):
    if step == 'ship_any':   # Koson sklad yoki Uychi sklad (qaysi biri — amalning o'zida tekshiriladi)
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

FLOW_RELATED = ('batch__order_item__order', 'batch__order_item__article')


def _card(flow):
    """Pastal kartochkasi: zakaz, artikul (rasm), razmerlar (kesim / meto / tasnif soni), qutilar va konteynerlar."""
    batch = flow.batch
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
    linked = {c.box_id for c in Container.objects.filter(box__in=boxes)} if boxes else set()
    return {
        'flow': flow, 'batch': batch, 'order': batch.order_item.order, 'article': article, 'image': image,
        'sizes': sizes, 'total_cut': sum(s['cut'] for s in sizes),
        'total_meto': sum(s['meto'] or 0 for s in sizes), 'total_tasnif': sum(s['tasnif'] or 0 for s in sizes),
        'boxes_count': len(boxes), 'printed_count': sum(1 for b in boxes if b.is_printed),
        'linked_count': len(linked),
        'ready': (len(boxes) > 0 and all(b.is_printed for b in boxes) and len(linked) == len(boxes)
                  and (flow.branch != 'HQ' or bool(flow.sklad_received_at and batch.actual_weight_kg))),
    }


def _flows(**filters):
    qs = (PastalFlow.objects.filter(**filters)
          .select_related(*FLOW_RELATED, 'meto_taken_by', 'meto_done_by', 'tasnif_taken_by', 'tasnif_done_by',
                          'sklad_received_by')
          .prefetch_related('batch__items__order_item_size', 'batch__items__flow_size', 'batch__items__boxes')
          .order_by('-batch__created_at'))
    return [_card(f) for f in qs[:200]]


def _incoming(destination):
    return (ShipmentPastal.objects.filter(status=ShipmentPastal.Status.IN_TRANSIT,
                                          shipment__destination_branch=destination)
            .select_related('shipment', 'batch__order_item__order', 'batch__order_item__article')
            .order_by('shipment_id', 'id'))


def _patok_rows(branch):
    rows = []
    for card in _flows(stage=PastalFlow.Stage.AT_PATOK, branch=branch)[:60]:
        containers = Container.objects.filter(box__cutting_batch_item__batch=card['batch'])
        states = OrderedDict()
        for c in containers:
            state = flow_logic.container_state(c)
            states[state] = states.get(state, 0) + 1
        card['patok_code'] = get_patok_code(card['batch'].assigned_screen) if card['batch'].assigned_screen else '—'
        card['container_states'] = list(states.items())
        rows.append(card)
    return rows


def _ctx_meto(request):
    flow_logic.ensure_flows()
    return {'waiting': _flows(stage=PastalFlow.Stage.METO_WAIT), 'working': _flows(stage=PastalFlow.Stage.METO_WORK)}


def _ctx_tasnif(request):
    return {'waiting': _flows(stage=PastalFlow.Stage.TASNIF_WAIT),
            'working': _flows(stage=PastalFlow.Stage.TASNIF_WORK)}


def _ctx_stiker(request):
    cards = _flows(stage=PastalFlow.Stage.SKLAD, branch='HQ')
    return {'to_split': [c for c in cards if not c['boxes_count']],
            'to_print': [c for c in cards if c['boxes_count'] and c['printed_count'] < c['boxes_count']]}


def _ctx_sklad(request):
    flow_logic.sync_rejected_flows()
    return {'cards': _flows(stage=PastalFlow.Stage.SKLAD, branch='HQ'), 'incoming': list(_incoming('HQ')),
            'patok_rows': _patok_rows('HQ'), 'screens': [(n, get_patok_code(n)) for n in flow_logic.KOSON_SCREENS]}


def _ctx_uychi(request):
    return {'cards': _flows(stage=PastalFlow.Stage.SKLAD, branch='UYCHI'), 'incoming': list(_incoming('UYCHI')),
            'patok_rows': _patok_rows('UYCHI'), 'screens': [(n, get_patok_code(n)) for n in flow_logic.UYCHI_SCREENS]}


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


@flow_access_required
def flow_view(request):
    tabs = _allowed_tabs(request.user)
    tab = request.GET.get('tab')
    if tab not in dict(tabs):
        tab = tabs[0][0]
    context = {'tabs': tabs, 'tab': tab, 'tab_label': dict(tabs)[tab], 'tab_template': f"meto_hub/flow/{tab}.html"}
    context.update(TAB_CONTEXT[tab](request))
    return render(request, 'meto_hub/flow.html', context)


# ------------------------------------------------------------------ formalar (sahifaga qaytadi)

def _form_action(step, tab):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method != 'POST':
                return redirect(_flow_url(tab))
            if not can(request.user, step):
                return _deny(request, tab)
            try:
                message = view(request, *args, **kwargs)
                if message:
                    messages.success(request, message)
            except FlowError as exc:
                messages.error(request, str(exc))
            return redirect(_flow_url(tab))
        return flow_access_required(wrapper)
    return decorator


def _batch(batch_id):
    return get_object_or_404(flow_logic.v2_batches(), id=batch_id)


def _posted_counts(request):
    return {key[len('qty_'):]: value for key, value in request.POST.items() if key.startswith('qty_')}


def _name(batch):
    return batch.pastal_code or batch.name


@_form_action('meto', 'meto')
def meto_take(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.take_meto(batch, request.user)
    return f"{_name(batch)}: meto boshlandi."


@_form_action('meto', 'meto')
def meto_finish(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.finish_meto(batch, _posted_counts(request), request.user)
    return f"{_name(batch)}: meto tugadi, tasnifga uzatildi."


@_form_action('tasnif', 'tasnif')
def tasnif_take(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.take_tasnif(batch, request.user)
    return f"{_name(batch)}: tasnif boshlandi."


@_form_action('tasnif', 'tasnif')
def tasnif_finish(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.finish_tasnif(batch, _posted_counts(request), request.user)
    return f"{_name(batch)}: tasnif tugadi, pastal meto skladga o'tdi."


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
    created = flow_logic.split_boxes(batch, plan, request.user)
    return f"{_name(batch)}: {created} ta quti va stikerlar yaratildi — endi chop eting."


@_form_action('sklad', 'sklad')
def sklad_receive(request, batch_id):
    batch = _batch(batch_id)
    flow_logic.sklad_receive(batch, request.POST.get('kg'), request.user)
    return f"{_name(batch)}: sklad qabul qildi ({request.POST.get('kg')} kg)."


@_form_action('zakazlar', 'zakazlar')
def beta_toggle(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    order.meto_beta = not order.meto_beta
    if order.meto_beta and not order.meto_beta_since:
        order.meto_beta_since = timezone.now()   # belgi yoqilgandan keyin kesilgan pastallar yangi oqimda
    order.save(update_fields=['meto_beta', 'meto_beta_since'])
    state = "yoqildi (shundan keyin kesilgan pastallar)" if order.meto_beta else "o'chirildi"
    return f"«{order.order_number}»: yangi meto oqimi {state}."


# ------------------------------------------------------------------ JSON API (skaner)

def _require_branch(user, branch):
    if not can(user, 'sklad' if branch == 'HQ' else 'uychi'):
        raise FlowError("Bu filial skladi uchun ruxsatingiz yo'q.")


@_api('ship_any')
def api_resolve(request):
    code = request.GET.get('code', '')
    container = flow_logic.find_container(code)
    if container is not None:
        return JsonResponse({'status': 'OK', 'kind': 'container', 'code': container.code,
                             'container_kind': container.get_kind_display(), 'state': flow_logic.container_state(container),
                             'box': container.box.box_code if container.box_id else None})
    box = flow_logic.resolve_box(code)
    if box is None:
        raise FlowError("Stiker yoki konteyner topilmadi.")
    batch = box.cutting_batch_item.batch if box.cutting_batch_item_id else None
    current = Container.objects.filter(box=box).first()
    return JsonResponse({'status': 'OK', 'kind': 'box', 'box': box.box_code, 'razmer': box.razmer or '—',
                         'qty': box.quantity, 'pastal': (batch.pastal_code or batch.name) if batch else '—',
                         'cancelled': box.status == Box.Status.CANCELLED,
                         'container': current.code if current else None})


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
    ids = [int(x) for x in request.GET.get('batches', '').split(',') if x.strip().isdigit()]
    result = []
    for batch in flow_logic.v2_batches().filter(id__in=ids):
        result.append({'batch_id': batch.id, 'pastal': _name(batch), 'rows': flow_logic.check_list(batch)})
    return JsonResponse({'status': 'OK', 'batches': result})


@_api('ship_any')
@require_POST
def api_give(request):
    data = _json(request)
    batch = _batch(data.get('batch_id'))
    step = 'sklad' if flow_logic.get_flow(batch).branch == 'HQ' else 'uychi'
    if not can(request.user, step):
        raise FlowError("Bu filial patogiga berishga ruxsatingiz yo'q.")
    screen = int(data.get('screen') or 0)
    flow_logic.give_to_patok(batch, screen, data.get('scanned') or [], request.user)
    return JsonResponse({'status': 'OK', 'message': f"{_name(batch)} {get_patok_code(screen)}-patokka berildi."})


@_api('sklad')
@require_POST
def api_ship(request):
    data = _json(request)
    batches = list(flow_logic.v2_batches().filter(id__in=[int(x) for x in data.get('batch_ids') or []]))
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
