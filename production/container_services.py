"""
Meto konteynerlari (egasi talabi 2026-11-19), 1-bosqich: yaratish, yorliq va kuzatish.

Konteyner ID — KONT-000123: ketma-ket raqam, hech qachon o'zgarmaydi (yorliq yo'qolsa o'sha ID qayta chop etiladi).
QR matni ham shu ID. Skaner "-" ni "+" qilib yuborsa ham, ":" bilan yoki tiresiz yozilsa ham taniladi.
Keyingi bosqichlarda konteyner stikerli quti (Box) bilan 1:1 bog'lanadi; harakatlar ContainerEvent ga yoziladi.
"""
import re

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q
from django.utils import timezone

from production.models import Container, ContainerEvent

MAX_CREATE = 1000   # bir martada yaratiladigan eng ko'p konteyner
MAX_PRINT = 250     # bitta yorliq sahifasidagi eng ko'p konteyner (har biriga 2 ta yorliq — 500 sahifa)

_CODE_RE = re.compile(r'^KONT\s*[-:+]?\s*(\d{1,9})$', re.IGNORECASE)
_DASHES = str.maketrans({c: '-' for c in '‐‑‒–—―−－'})


def parse_container_code(raw):
    """'KONT-000123', 'kont+123', 'KONT:000123', 'KONT000123' -> 123; boshqa narsa -> None.
    Planshet klaviaturasi qo'ygan uzun chiziqcha (–, —, −) ham oddiy "-" deb olinadi."""
    match = _CODE_RE.match((raw or '').strip().translate(_DASHES))
    return int(match.group(1)) if match else None


def create_containers(count, kind, user):
    """count ta yangi konteyner (raqamlar davomidan) yaratadi; (birinchi, oxirgi) raqamni qaytaradi."""
    for attempt in range(3):
        try:
            with transaction.atomic():
                first = (Container.objects.aggregate(m=Max('number'))['m'] or 0) + 1
                Container.objects.bulk_create(
                    [Container(number=first + i, kind=kind, created_by=user) for i in range(count)])
                ContainerEvent.objects.bulk_create([
                    ContainerEvent(container=container, action=ContainerEvent.Action.CREATED,
                                   branch=container.branch, user=user)
                    for container in Container.objects.filter(number__gte=first, number__lt=first + count)])
            return first, first + count - 1
        except IntegrityError:   # bir vaqtda ikkinchi kishi ham yaratgan — raqamlar qaytadan olinadi
            if attempt == 2:
                raise


def mark_printed(containers, user):
    """Yorliq sahifasi ochilganda: har konteynerga "Yorliq chiqarildi" yozuvi."""
    Container.objects.filter(id__in=[c.id for c in containers]).update(
        print_count=F('print_count') + 1, last_printed_at=timezone.now())
    ContainerEvent.objects.bulk_create([
        ContainerEvent(container=c, action=ContainerEvent.Action.PRINTED, branch=c.branch, user=user)
        for c in containers])


def container_summary():
    return Container.objects.aggregate(
        total=Count('id'),
        empty=Count('id', filter=Q(status=Container.Status.EMPTY)),
        filled=Count('id', filter=Q(status=Container.Status.FILLED)),
        in_transit=Count('id', filter=Q(status=Container.Status.IN_TRANSIT)),
        at_patok=Count('id', filter=Q(status=Container.Status.AT_PATOK)),
        in_repair=Count('id', filter=Q(status=Container.Status.IN_REPAIR)),
        big=Count('id', filter=Q(kind=Container.Kind.BIG)),
        small=Count('id', filter=Q(kind=Container.Kind.SMALL)),
        uychi=Count('id', filter=Q(branch='UYCHI')),
        unprinted=Count('id', filter=Q(print_count=0)),
    )
