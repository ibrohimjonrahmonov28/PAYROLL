"""
Yangi meto oqimi (egasi talabi 2026-11-19). Beta: faqat Order.meto_beta zakazning belgi yoqilgandan keyin kesilgan
pastallari; eski /meto/, eski Meto Markazi tablari va Telegram bot o'zgarmaydi.

  kesim → meto masteri (METO): qabul, meto soni, tugatish (son qulflanadi)
        → tasnif masteri (TASNIF): qabul, real son, tugatish → pastal avtomatik meto skladga
        → stikerchi (STICKER): tasnif sonini qutilarga bo'ladi (stiker chop etilguncha tasnif sonini o'zgartirish mumkin)
        → meto sklad (METO_SKLAD): qabul + real kg, har stikerli qutini bitta katta konteynerga biriktiradi
        → Koson patogi (master planshetida qabul) yoki Uychi yuk xati → Uychi sklad (UYCHI_SKLAD) → Uychi patogi.

Patokka berish, yuklash va qabul qilishda pastalning hamma konteyneri skaner qilinadi: bittasi yo'q yoki konteynersiz
quti bo'lsa — pastal to'xtaydi (kamchilik bilan qabul yo'q). Konteyner bo'shashi: OTK v2 (1-sort yo'q bo'lsa darhol),
sanoqchi qog'ozni urganda (production/packing_services.take_paper). "Patok ichida" — konteyner holati, qutining stikeri
urilganidan aniqlanadi (terminalga tegilmaydi). Quti va stiker kodlari hech qachon o'zgarmaydi (oltin qoida).
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone

from accounts.models import User
from production.container_services import parse_container_code
from production.models import (ArticleOperation, Box, Container, ContainerEvent, CuttingBatch, CuttingBatchItem,
                               PastalFlow, PastalFlowSize, Shipment, ShipmentPastal, Ticket)
from production.services import auto_generate_boxes_for_batch_item, get_patok_code

KOSON_SCREENS = range(1, 14)    # K1..K13
UYCHI_SCREENS = range(14, 41)   # U1..U27

# Bosqich → kim bajaradi (superadmin va admin hammasini)
STEP_ROLES = {
    'meto': (User.Role.METO,),
    'tasnif': (User.Role.TASNIF,),
    'stiker': (User.Role.STICKER,),
    'sklad': (User.Role.METO_SKLAD, User.Role.METO),
    'uychi': (User.Role.UYCHI_SKLAD, User.Role.BRANCH_ADMIN),
    'zakazlar': (),
}
FLOW_ROLES = (User.Role.METO, User.Role.TASNIF, User.Role.STICKER, User.Role.METO_SKLAD, User.Role.UYCHI_SKLAD,
              User.Role.BRANCH_ADMIN)


class FlowError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato (sahifada xabar yoki JSON)."""


def can(user, step):
    return bool(user.is_authenticated and (user.is_superadmin() or user.role == User.Role.ADMIN
                                           or user.role in STEP_ROLES[step]))


def user_name(user):
    return (user.get_full_name() or user.username) if user else ''


# ------------------------------------------------------------------ qaysi pastal yangi oqimda

def v2_batches():
    return CuttingBatch.objects.filter(
        order_item__order__meto_beta=True, order_item__order__meto_beta_since__isnull=False,
        created_at__gte=F('order_item__order__meto_beta_since'))


def is_v2_batch(batch):
    order = batch.order_item.order
    return bool(order.meto_beta and order.meto_beta_since and batch.created_at >= order.meto_beta_since)


def is_v2_box(box):
    item = box.cutting_batch_item
    return bool(item is not None and is_v2_batch(item.batch))


def get_flow(batch):
    flow, _ = PastalFlow.objects.get_or_create(batch=batch)
    return flow


def ensure_flows():
    """Beta zakazning yangi kesilgan pastallari uchun oqim yozuvi (kesim portaliga tegmaslik uchun — shu yerda)."""
    missing_ids = list(
        v2_batches().filter(order_item__isnull=False, flow__isnull=True).values_list('id', flat=True)
    )
    if missing_ids:
        PastalFlow.objects.bulk_create([PastalFlow(batch_id=bid) for bid in missing_ids], ignore_conflicts=True)


def sync_rejected_flows():
    """Master planshetda rad etsa pastal patoksiz WAITING ga qaytadi — yangi oqimda u skladga qaytadi."""
    for flow in PastalFlow.objects.filter(stage=PastalFlow.Stage.AT_PATOK).select_related('batch'):
        batch = flow.batch
        if batch.dispatch_status == CuttingBatch.DispatchStatus.WAITING and batch.assigned_screen is None:
            with transaction.atomic():
                flow.stage = PastalFlow.Stage.SKLAD
                flow.save(update_fields=['stage', 'updated_at'])
                for container in Container.objects.filter(box__cutting_batch_item__batch=batch):
                    _set_status(container, Container.Status.FILLED, ContainerEvent.Action.RETURNED, None,
                                note=f"Master qaytardi: {batch.rejection_reason[:150]}")


def _items(batch):
    return list(batch.items.select_related('order_item_size').order_by('order_item_size__id', 'id'))


def _size(item):
    size, _ = PastalFlowSize.objects.get_or_create(item=item)
    return size


def _counts(items, counts):
    """{item_id: '12'} -> {item_id: 12}; har razmer son, kamida bittasi > 0."""
    result = {}
    for item in items:
        raw = str(counts.get(item.id, counts.get(str(item.id), ''))).strip()
        if not raw.isdigit():
            raise FlowError(f"{item.order_item_size.size_name} razmeri uchun sonni kiriting.")
        result[item.id] = int(raw)
    if not any(result.values()):
        raise FlowError("Kamida bitta razmerda son 0 dan katta bo'lishi kerak.")
    return result


def _require(flow, stage, message):
    if flow.stage != stage:
        raise FlowError(message)


# ------------------------------------------------------------------ meto va tasnif

def take_meto(batch, user):
    if not is_v2_batch(batch):
        raise FlowError("Bu pastal Tayyorlov (yangi meto oqimi) da emas.")
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        if flow.stage == PastalFlow.Stage.METO_WORK and flow.meto_taken_by_id:
            raise FlowError(f"Bu pastal allaqachon qabul qilingan ({user_name(flow.meto_taken_by)}).")
        _require(flow, PastalFlow.Stage.METO_WAIT, "Bu pastal meto kutmoqda holatida emas.")
        flow.stage, flow.meto_taken_by, flow.meto_taken_at = PastalFlow.Stage.METO_WORK, user, timezone.now()
        flow.save()
    return flow


def cancel_meto(batch, user):
    """Adashib qabul qilingan pastalni 'Meto kutmoqda' holatiga qaytarish."""
    if not is_v2_batch(batch):
        raise FlowError("Bu pastal Tayyorlov (yangi meto oqimi) da emas.")
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.METO_WORK, "Faqat meto bo'layotgan pastalni bekor qilish mumkin.")
        flow.stage, flow.meto_taken_by, flow.meto_taken_at = PastalFlow.Stage.METO_WAIT, None, None
        flow.save(update_fields=['stage', 'meto_taken_by', 'meto_taken_at', 'updated_at'])
    return flow


def finish_meto(batch, counts, user, note=''):
    if not is_v2_batch(batch):
        raise FlowError("Bu pastal Tayyorlov (yangi meto oqimi) da emas.")
    clean_note = (note or '').strip()[:300]
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.METO_WORK, "Bu pastal meto bo'lmoqda holatida emas.")
        items = _items(batch)
        values = _counts(items, counts)
        for item in items:
            val = values[item.id]
            max_reasonable = min(max(item.quantity * 2 + 50, 100), 100000)
            if val > max_reasonable:
                raise FlowError(
                    f"{item.order_item_size.size_name} razmerida meto soni ({val}) "
                    f"kesimdan ({item.quantity}) haddan tashqari katta — raqamni tekshiring."
                )
        for item in items:
            size = _size(item)
            size.meto_qty = values[item.id]
            size.save(update_fields=['meto_qty'])
        flow.stage, flow.meto_done_by, flow.meto_done_at = PastalFlow.Stage.TASNIF_WAIT, user, timezone.now()
        flow.meto_note = clean_note
        flow.save()
    return flow


def take_tasnif(batch, user):
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.TASNIF_WAIT, "Bu pastal tasnif kutmoqda holatida emas.")
        flow.stage, flow.tasnif_taken_by, flow.tasnif_taken_at = PastalFlow.Stage.TASNIF_WORK, user, timezone.now()
        flow.save()
    return flow


def finish_tasnif(batch, counts, user):
    """Tasnif real sonni beradi — pastal avtomatik meto skladga (Koson) o'tadi."""
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.TASNIF_WORK, "Bu pastal tasnif bo'lmoqda holatida emas.")
        items = _items(batch)
        values = _counts(items, counts)
        for item in items:
            size = _size(item)
            size.tasnif_qty = values[item.id]
            size.save(update_fields=['tasnif_qty'])
        flow.stage, flow.branch = PastalFlow.Stage.SKLAD, 'HQ'
        flow.tasnif_done_by, flow.tasnif_done_at = user, timezone.now()
        flow.save()
    return flow


def _active_boxes(batch):
    return Box.objects.filter(cutting_batch_item__batch=batch).exclude(status=Box.Status.CANCELLED)


def update_tasnif_counts(batch, counts, user):
    """Stiker chop etilguncha tasnif sonini o'zgartirish; bo'lingan (chop etilmagan) qutilar bekor qilinadi."""
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.SKLAD, "Tasnif sonini faqat skladdagi pastalda o'zgartirish mumkin.")
        boxes = _active_boxes(batch)
        if boxes.filter(is_printed=True).exists():
            raise FlowError("Stikerlar chop etilgan — tasnif sonini o'zgartirib bo'lmaydi.")
        if Container.objects.filter(box__in=boxes).exists():
            raise FlowError("Qutilar konteynerga biriktirilgan — avval biriktirishni bekor qiling.")
        items = _items(batch)
        values = _counts(items, counts)
        for box in boxes:   # chop etilmagan qutilar — yangidan bo'linadi (stikerlari hali qog'ozda yo'q)
            box.status = Box.Status.CANCELLED
            box.save(update_fields=['status'])
            box.tickets.filter(status=Ticket.Status.PENDING).update(status=Ticket.Status.CANCELLED)
        for item in items:
            size = _size(item)
            size.tasnif_qty = values[item.id]
            size.save(update_fields=['tasnif_qty'])
            item.boxes_created_qty = 0
            item.status = CuttingBatchItem.Status.CUT_ENTERED
            item.save(update_fields=['boxes_created_qty', 'status'])
        flow.boxes_split_by, flow.boxes_split_at = None, None
        flow.save()
    return flow


def split_boxes(batch, plan, user):
    """Stikerchi: har razmer tasnif sonini qutilarga bo'ladi (quti soni yoki sig'im) — qutilar va stikerlar yaratiladi."""
    with transaction.atomic():
        flow = get_flow(CuttingBatch.objects.select_for_update().get(id=batch.id))
        _require(flow, PastalFlow.Stage.SKLAD, "Qutilarga faqat skladdagi pastal bo'linadi.")
        if flow.boxes_split_at or _active_boxes(batch).exists():
            raise FlowError("Bu pastal allaqachon qutilarga bo'lingan.")
        if not ArticleOperation.objects.filter(article=batch.order_item.article).exists():
            raise FlowError("Artikulda operatsiyalar (narxlar) yo'q — avval narxlar matritsasidan biriktiring.")
        pastal_number = batch.pastal_code or str(batch.batch_number)
        created = 0
        for item in _items(batch):
            qty = _size(item).tasnif_qty or 0
            if qty <= 0:
                item.real_quantity = 0
                item.boxes_created_qty = 0
                item.status = CuttingBatchItem.Status.STICKERS_PRINTED
                item.save(update_fields=['real_quantity', 'boxes_created_qty', 'status'])
                continue
            rule = plan.get(item.id) or plan.get(str(item.id)) or {}
            box_count = _positive(rule.get('boxes')) or 1
            capacity = _positive(rule.get('capacity'))
            item.real_quantity = qty
            item.meto_number_start, item.meto_number_end = '1', str(qty)
            item.meto_worker_name = user_name(flow.meto_done_by)
            item.meto_completed_at, item.meto_completed_by = flow.meto_done_at, flow.meto_done_by
            item.boxes_created_qty = 0
            item.save()
            created += len(auto_generate_boxes_for_batch_item(
                batch_item=item, split_count=min(box_count, qty), box_capacity=capacity, pastal_number=pastal_number))
        flow.boxes_split_by, flow.boxes_split_at = user, timezone.now()
        flow.save()
    return created


def _positive(value):
    text = str(value or '').strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def sklad_receive(batch, kg, user):
    """Meto sklad pastalni qabul qiladi va real kg ni yozadi."""
    try:
        weight = Decimal(str(kg).replace(',', '.').strip())
    except (InvalidOperation, AttributeError):
        raise FlowError("Og'irlikni (kg) to'g'ri kiriting.")
    if not weight.is_finite() or weight <= 0 or weight >= 100000:
        raise FlowError("Og'irlik 0 dan katta bo'lishi kerak.")
    with transaction.atomic():
        batch = CuttingBatch.objects.select_for_update().get(id=batch.id)
        flow = get_flow(batch)
        _require(flow, PastalFlow.Stage.SKLAD, "Pastal skladda emas.")
        batch.actual_weight_kg = weight
        batch.save(update_fields=['actual_weight_kg'])
        flow.sklad_received_by, flow.sklad_received_at = user, timezone.now()
        flow.save()
    return flow


# ------------------------------------------------------------------ konteyner bilan biriktirish

_TICKET_PREFIX = re.compile(r'^(TICKET|TK)\s*[:+]\s*', re.IGNORECASE)
_BOX_PREFIX = re.compile(r'^(CONTROL|BOX|QUTI)\s*[:+]\s*', re.IGNORECASE)


def resolve_box(raw):
    """Qutining istalgan stikeri: CONTROL:<quti>, TICKET:<bilet>, 8 belgili stiker kodi yoki quti kodi -> Box."""
    text = (raw or '').strip()
    if not text:
        return None
    if _BOX_PREFIX.match(text):
        code = _BOX_PREFIX.sub('', text).replace('+', '-')
        return Box.objects.filter(box_code__iexact=code).first()
    if _TICKET_PREFIX.match(text):
        code = _TICKET_PREFIX.sub('', text).replace('+', '-')
        ticket = Ticket.objects.filter(ticket_code__iexact=code).select_related('box').first() \
            or Ticket.objects.filter(stiker_code__iexact=code).select_related('box').first()
        return ticket.box if ticket else None
    code = text.replace('+', '-')
    box = Box.objects.filter(box_code__iexact=code).first()
    if box:
        return box
    ticket = Ticket.objects.filter(stiker_code__iexact=code).select_related('box').first() \
        or Ticket.objects.filter(ticket_code__iexact=code).select_related('box').first()
    return ticket.box if ticket else None


def find_container(raw):
    number = parse_container_code(raw)
    return Container.objects.filter(number=number).first() if number is not None else None


def _set_status(container, status, action, user, *, box=None, note='', branch=None, **fields):
    container.status = status
    if branch:
        container.branch = branch
    for name, value in fields.items():
        setattr(container, name, value)
    container.save()
    ContainerEvent.objects.create(container=container, action=action, branch=container.branch,
                                  box=box or container.box, user=user, note=note[:200])


def link_container(raw_box, raw_container, user):
    """Stiker (quti) → konteyner: bitta konteynerga faqat bitta quti; konteyner bo'sh va shu filialda bo'lishi kerak."""
    with transaction.atomic():
        box = resolve_box(raw_box)
        if box is None:
            raise FlowError("Stiker bo'yicha quti topilmadi.")
        # PostgreSQL: bo'sh bo'lishi mumkin bog'lanish (LEFT JOIN) bilan FOR UPDATE — faqat o'z qatorini qulflash
        box = Box.objects.select_for_update(of=('self',)).select_related('cutting_batch_item__batch').get(id=box.id)
        if box.status == Box.Status.CANCELLED:
            raise FlowError(f"{box.box_code} qutisi bekor qilingan — yangi stikerni uring.")
        if not is_v2_box(box):
            raise FlowError("Bu quti yangi meto oqimida emas.")
        flow = get_flow(box.cutting_batch_item.batch)
        if flow.stage != PastalFlow.Stage.SKLAD:
            raise FlowError("Biriktirish faqat skladdagi pastal qutilari uchun.")
        existing = Container.objects.filter(box=box).first()
        if existing:
            raise FlowError(f"{box.box_code} allaqachon {existing.code} konteynerida.")
        container = find_container(raw_container)
        if container is None:
            raise FlowError("Konteyner topilmadi — konteyner QR kodini uring.")
        container = Container.objects.select_for_update().get(id=container.id)
        if container.kind != Container.Kind.BIG:
            raise FlowError(f"{container.code} — kichik (ta'mir) konteyner; katta konteynerni uring.")
        if container.status != Container.Status.EMPTY:
            raise FlowError(f"{container.code} bo'sh emas.")
        if container.branch != flow.branch:
            raise FlowError(f"{container.code} boshqa filialda.")
        container.box, container.linked_at = box, timezone.now()
        _set_status(container, Container.Status.FILLED, ContainerEvent.Action.LINKED, user, box=box)
    return container, box


def unlink(raw, user):
    """Biriktirishni bekor qilish (sababsiz): konteyner QR yo'qolsa — stikerni uring; quti bekor bo'lsa — konteynerni.
    Faqat skladdagi konteyner (yoki qutisi bekor qilingan konteyner)."""
    with transaction.atomic():
        container = find_container(raw)
        if container is None:
            box = resolve_box(raw)
            container = Container.objects.filter(box=box).first() if box else None
        if container is None:
            raise FlowError("Biriktirilgan konteyner topilmadi.")
        container = Container.objects.select_for_update(of=('self',)).select_related('box').get(id=container.id)
        if container.box is None:
            raise FlowError(f"{container.code} hech qaysi qutiga biriktirilmagan.")
        box_cancelled = container.box.status == Box.Status.CANCELLED
        if container.status != Container.Status.FILLED and not box_cancelled:
            raise FlowError(f"{container.code} skladda emas ({container.get_status_display()}).")
        box = container.box
        container.box, container.linked_at = None, None
        _set_status(container, Container.Status.EMPTY, ContainerEvent.Action.UNLINKED, user, box=box,
                    note="quti bekor qilingan" if box_cancelled else '')
    return container, box


# ------------------------------------------------------------------ tekshiruv (yashil / qizil)

def check_list(batch):
    """Pastalning faol qutilari va ularning konteynerlari (konteynersiz quti — qizil)."""
    rows = []
    containers = {c.box_id: c for c in Container.objects.filter(box__cutting_batch_item__batch=batch)}
    for box in _active_boxes(batch).select_related('cutting_batch_item').order_by('box_number', 'id'):
        container = containers.get(box.id)
        rows.append({'box_id': box.id, 'box_code': box.box_code, 'razmer': box.razmer or '—', 'qty': box.quantity,
                     'container': container.code if container else None, 'printed': box.is_printed})
    return rows


def _unscanned(rows, scanned):
    numbers = {parse_container_code(code) for code in scanned or []}
    return [r['container'] for r in rows if r['container'] and parse_container_code(r['container']) not in numbers]


def _verify(batches, scanned):
    """Hamma qutida konteyner bo'lishi va hamma konteyner skaner qilingan bo'lishi kerak."""
    problems = []
    for batch in batches:
        rows = check_list(batch)
        name = batch.pastal_code or batch.name
        if not rows:
            problems.append(f"{name}: qutilar yo'q (stiker hali bo'linmagan).")
        unlinked = [r['box_code'] for r in rows if not r['container']]
        if unlinked:
            problems.append(f"{name}: konteynersiz qutilar — {', '.join(unlinked)}.")
        missing = _unscanned(rows, scanned)
        if missing:
            problems.append(f"{name}: skaner qilinmagan konteynerlar — {', '.join(missing)}.")
    if problems:
        raise FlowError(' '.join(problems))


def _require_ready(batch, flow):
    if flow.stage != PastalFlow.Stage.SKLAD:
        raise FlowError(f"{batch.pastal_code or batch.name}: pastal skladda emas.")
    if flow.branch == 'HQ' and not (flow.sklad_received_at and batch.actual_weight_kg):
        raise FlowError(f"{batch.pastal_code or batch.name}: sklad hali qabul qilmagan (kg kiritilmagan).")
    if _active_boxes(batch).filter(is_printed=False).exists():
        raise FlowError(f"{batch.pastal_code or batch.name}: hamma stiker chop etilmagan.")


# ------------------------------------------------------------------ patokka berish

def give_to_patok(batch, screen, scanned, user):
    with transaction.atomic():
        batch = CuttingBatch.objects.select_for_update().get(id=batch.id)
        flow = get_flow(batch)
        _require_ready(batch, flow)
        allowed = KOSON_SCREENS if flow.branch == 'HQ' else UYCHI_SCREENS
        if screen not in allowed:
            raise FlowError("Patok shu filialniki bo'lishi kerak.")
        _verify([batch], scanned)
        now = timezone.now()
        batch.assigned_branch, batch.assigned_screen = flow.branch, screen
        batch.dispatched_at, batch.dispatched_by = now, user_name(user) or 'Meto sklad'
        batch.rejection_reason, batch.rejected_at = '', None
        fields = ['assigned_branch', 'assigned_screen', 'dispatched_at', 'dispatched_by', 'rejection_reason',
                  'rejected_at', 'dispatch_status']
        if flow.branch == 'HQ':
            batch.dispatch_status = CuttingBatch.DispatchStatus.DISPATCHED   # master planshetida qabul qiladi
        else:   # Uychi: patokka berish qabul hisoblanadi (egasi qarori 2026-10-29)
            batch.dispatch_status = CuttingBatch.DispatchStatus.ACCEPTED
            batch.accepted_at, batch.accepted_by = now, user
            fields += ['accepted_at', 'accepted_by']
        batch.save(update_fields=fields)
        _active_boxes(batch).update(assigned_screen=screen)
        for container in Container.objects.filter(box__cutting_batch_item__batch=batch):
            _set_status(container, Container.Status.AT_PATOK, ContainerEvent.Action.DISPATCHED, user,
                        note=f"{get_patok_code(screen)}-patok")
        flow.stage, flow.patok_given_by, flow.patok_given_at = PastalFlow.Stage.AT_PATOK, user, now
        flow.save()
    return flow


# ------------------------------------------------------------------ yuk xati (Koson → Uychi, qaytarish)

def _new_shipment(**fields):
    for attempt in range(3):
        try:
            with transaction.atomic():
                last = Shipment.objects.aggregate(m=Max('id'))['m'] or 0
                return Shipment.objects.create(number=f"YX-{last + 1:06d}", **fields)
        except IntegrityError:
            if attempt == 2:
                raise


def create_shipment(batches, scanned, user, vehicle='', driver='', phone='', notes=''):
    """Koson skladidagi tayyor pastallarni (hamma konteyneri skaner qilingan) Uychiga yuk xati bilan jo'natish."""
    if not batches:
        raise FlowError("Yuk xati uchun pastal tanlang.")
    with transaction.atomic():
        locked = list(CuttingBatch.objects.select_for_update().filter(id__in=[b.id for b in batches]))
        for batch in locked:
            flow = get_flow(batch)
            _require_ready(batch, flow)
            if flow.branch != 'HQ':
                raise FlowError(f"{batch.pastal_code or batch.name}: pastal Koson skladida emas.")
        _verify(locked, scanned)
        shipment = _new_shipment(source_branch='HQ', destination_branch='UYCHI', vehicle_number=vehicle.upper(),
                                 driver_name=driver, driver_phone=phone, notes=notes, created_by=user)
        for batch in locked:
            ShipmentPastal.objects.create(shipment=shipment, batch=batch)
            flow = get_flow(batch)
            flow.stage = PastalFlow.Stage.IN_TRANSIT
            flow.save(update_fields=['stage', 'updated_at'])
            for container in Container.objects.filter(box__cutting_batch_item__batch=batch):
                _set_status(container, Container.Status.IN_TRANSIT, ContainerEvent.Action.LOADED, user,
                            note=shipment.number)
    return shipment


def _close_if_done(shipment):
    if not shipment.lines.filter(status=ShipmentPastal.Status.IN_TRANSIT).exists():
        shipment.status, shipment.closed_at = Shipment.Status.CLOSED, timezone.now()
        shipment.save(update_fields=['status', 'closed_at'])


def receive_line(line, scanned, user):
    """Uychi pastalning hamma konteynerini skaner qiladi — to'liq bo'lsagina qabul (aks holda qaytaradi).
    Qaytgan pastalni Koson har holda qabul qiladi (kamchilikni skladda hal qiladi): skaner qilinmagan konteynerlar
    voqeada belgilanadi va qaytariladi. -> (line, skaner qilinmagan konteyner kodlari)"""
    with transaction.atomic():
        line = ShipmentPastal.objects.select_for_update(of=('self',)).select_related('shipment', 'batch').get(id=line.id)
        if line.status != ShipmentPastal.Status.IN_TRANSIT:
            raise FlowError("Bu pastal allaqachon ko'rib chiqilgan.")
        destination = line.shipment.destination_branch
        if destination == 'UYCHI':
            _verify([line.batch], scanned)
            missing = []
        else:
            missing = _unscanned(check_list(line.batch), scanned)
        line.status, line.resolved_by, line.resolved_at = ShipmentPastal.Status.ACCEPTED, user, timezone.now()
        line.save(update_fields=['status', 'resolved_by', 'resolved_at'])
        flow = get_flow(line.batch)
        flow.stage, flow.branch = PastalFlow.Stage.SKLAD, destination
        flow.save(update_fields=['stage', 'branch', 'updated_at'])
        for container in Container.objects.filter(box__cutting_batch_item__batch=line.batch):
            note = line.shipment.number + (" — qabulda skaner qilinmadi" if container.code in missing else '')
            _set_status(container, Container.Status.FILLED, ContainerEvent.Action.RECEIVED, user,
                        branch=destination, note=note)
        _close_if_done(line.shipment)
    return line, missing


def return_line(line, user, note=''):
    """Uychi to'liq bo'lmagan pastalni qabul qilmaydi — qaytarish yuk xati (Uychi → Koson) bilan orqaga yuboradi."""
    with transaction.atomic():
        line = ShipmentPastal.objects.select_for_update(of=('self',)).select_related('shipment', 'batch').get(id=line.id)
        if line.status != ShipmentPastal.Status.IN_TRANSIT:
            raise FlowError("Bu pastal allaqachon ko'rib chiqilgan.")
        if line.shipment.destination_branch != 'UYCHI':
            raise FlowError("Qaytarish yuk xatini qaytarib bo'lmaydi — uni Koson qabul qiladi.")
        line.status, line.resolved_by, line.resolved_at = ShipmentPastal.Status.RETURNED, user, timezone.now()
        line.note = note[:300]
        line.save(update_fields=['status', 'resolved_by', 'resolved_at', 'note'])
        back = _new_shipment(source_branch='UYCHI', destination_branch='HQ', created_by=user,
                             notes=f"Qaytarish: {line.shipment.number}. {note}".strip())
        ShipmentPastal.objects.create(shipment=back, batch=line.batch, note=note[:300])
        for container in Container.objects.filter(box__cutting_batch_item__batch=line.batch):
            ContainerEvent.objects.create(container=container, action=ContainerEvent.Action.RETURNED,
                                          branch='UYCHI', box=container.box, user=user,
                                          note=f"{back.number} {note}"[:200])
        _close_if_done(line.shipment)
    return back


# ------------------------------------------------------------------ OTK v2 va sanoqchi: ta'mir konteyneri, bo'shash

def release(container, user, note=''):
    box = container.box
    container.box, container.repair_receipt, container.linked_at = None, None, None
    _set_status(container, Container.Status.EMPTY, ContainerEvent.Action.RELEASED, user, box=box, note=note)


def check_repair_container(raw):
    container = find_container(raw)
    if container is None:
        raise FlowError("Konteyner topilmadi — kichik konteyner QR kodini uring.")
    if container.kind != Container.Kind.SMALL:
        raise FlowError(f"{container.code} — katta konteyner; ta'mir uchun kichik konteynerni uring.")
    if container.status != Container.Status.EMPTY:
        raise FlowError(f"{container.code} bo'sh emas.")
    return container


def link_repair_container(receipt, raw, user):
    container = Container.objects.select_for_update().get(id=check_repair_container(raw).id)
    container.repair_receipt, container.linked_at = receipt, timezone.now()
    _set_status(container, Container.Status.IN_REPAIR, ContainerEvent.Action.REPAIR, user, box=receipt.box,
                note=f"{receipt.cycle}-ta'mir")
    return container


def after_initial_inspection(box, first_sort, user):
    """OTK birinchi tekshiruvi: 1-sort umuman bo'lmasa katta konteyner darhol bo'shaydi."""
    container = Container.objects.filter(box=box).first()
    if container and first_sort == 0:
        release(container, user, note="1-sort yo'q (OTK)")


def after_repair_return(box, closed_receipt, next_receipt, fixed_qty, user):
    """Ta'mirdan qaytish: qayta ta'mirda o'sha kichik konteyner keyingi chekka o'tadi; tuzalgani yo'q bo'lsa bo'shaydi."""
    if closed_receipt is None:
        return
    container = Container.objects.filter(repair_receipt=closed_receipt).first()
    if container is None:
        return
    if next_receipt is not None:
        container.repair_receipt = next_receipt
        _set_status(container, Container.Status.IN_REPAIR, ContainerEvent.Action.REPAIR, user, box=box,
                    note=f"qayta ta'mir: {next_receipt.cycle}-ta'mir")
    elif not fixed_qty:
        release(container, user, note="ta'mirdan 1-sort chiqmadi")
    else:
        _set_status(container, Container.Status.AT_PATOK, ContainerEvent.Action.RECEIVED, user, box=box,
                    note=f"{closed_receipt.cycle}-ta'mirdan qaytdi")


def release_for_scan(box, source, receipt, user):
    """Sanoqchi qog'ozni urdi: CONTROL — katta konteyner, TAMIRn — o'sha chekning kichik konteyneri bo'shaydi."""
    from production.models import PackingScan
    if source == PackingScan.Source.TAMIR:
        container = Container.objects.filter(repair_receipt=receipt).first() if receipt else None
    else:
        container = Container.objects.filter(box=box).first()
    if container is not None:
        release(container, user, note="sanoqchi qog'ozni urdi")


# ------------------------------------------------------------------ ko'rinish uchun

def container_state(container):
    """Konteyner holati so'zda: "Patok oldida" / "Patok ichida" (qutining stikeri urilgan bo'lsa) va boshqalar."""
    if container.status == Container.Status.AT_PATOK and container.box_id:
        scanned = Ticket.objects.filter(box_id=container.box_id, status=Ticket.Status.SCANNED).exists()
        return "Patok ichida" if scanned else "Patok oldida"
    return container.get_status_display()
