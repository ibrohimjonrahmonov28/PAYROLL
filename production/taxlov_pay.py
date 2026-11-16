"""
Taxlovchi puli (egasi talabi 2026-11-14, yangi qadoqlash tartibining 4-bosqichi).

Sanoqchi taxlovchining qog'ozlarini urib "Saqlash va yuborish" bosganda ro'yxatdagi har qator taxlovchi hisobiga
yoziladi: dona × qutining narx shablonidagi "OTK Control/Taxlov" narxi (hozircha 1 so'm), yuborilgan kunga.
Chevar stikerlari bilan bir xil qoidalar: shablondagi narx o'zgarsa muzlatilmagan yozuvlar yangi narxga o'tadi,
oy muzlatilsa qulflanadi (accounts.freeze_services), kunlik yopishda muhrlanadi (close_daily_payroll).
Maosh sahifasi, Excel tabel va ishchi tarixida alohida (sariq) ko'rsatiladi. Ochiq ro'yxat va eski taxlovchi tabi
skanlari (credited_at bo'sh) pul emas.
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import TruncDate

from production.models import PackingScan

ZERO = Decimal('0.00')


def taxlov_price_for_box(box):
    """Qutining artikul shablonidagi Control/Taxlov narxi; shablon yo'q bo'lsa 0."""
    article = box.target_article
    group = article.operation_group if article else None
    return group.taxlov_price if group else ZERO


def credit_batch(batch):
    """Yuborilgan ro'yxatni pulga aylantiradi (batch.sent_at qo'yilgandan keyin chaqiriladi)."""
    credited = 0
    for scan in batch.scans.filter(is_cancelled=False).select_related(
            'box__article__operation_group', 'box__cutting_batch_item__batch__order_item__article__operation_group',
            'box__order__article__operation_group'):
        price = taxlov_price_for_box(scan.box)
        scan.price_per_unit = price
        scan.total_amount = price * scan.quantity
        scan.credited_at = batch.sent_at
        scan.save(update_fields=['price_per_unit', 'total_amount', 'credited_at'])
        credited += 1
    return credited


def paid_scans():
    """Pulga aylangan (yuborilgan, bekor qilinmagan) taxlov yozuvlari."""
    return PackingScan.objects.filter(is_cancelled=False, credited_at__isnull=False)


def reprice_taxlov(group):
    """Shablondagi Control/Taxlov narxi o'zgardi: muzlatilmagan yozuvlar yangi narxga (chevar stikerlari kabi)."""
    of_group = (Q(box__article__operation_group=group)
                | Q(box__article__isnull=True, box__cutting_batch_item__batch__order_item__article__operation_group=group))
    total = ExpressionWrapper(F('quantity') * Value(group.taxlov_price),
                              output_field=DecimalField(max_digits=14, decimal_places=2))
    return paid_scans().filter(of_group, is_frozen=False).update(price_per_unit=group.taxlov_price, total_amount=total)


def taxlov_by_worker(start=None, end=None, worker_ids=None):
    """{worker_id: {'units', 'amount', 'days'}} — [start, end) oralig'ida (bo'sh bo'lsa butun davr)."""
    qs = paid_scans()
    if start is not None:
        qs = qs.filter(credited_at__gte=start)
    if end is not None:
        qs = qs.filter(credited_at__lt=end)
    if worker_ids is not None:
        qs = qs.filter(worker_id__in=worker_ids)
    rows = qs.values('worker_id').annotate(units=Sum('quantity'), amount=Sum('total_amount'),
                                           days=Count(TruncDate('credited_at'), distinct=True))
    return {row['worker_id']: {'units': row['units'] or 0, 'amount': row['amount'] or ZERO, 'days': row['days'] or 0}
            for row in rows}


def taxlov_by_worker_day(start, end, worker_ids=None):
    """{(worker_id, sana): {'units', 'amount'}} — kunlik (Toshkent vaqti) taxlov puli."""
    qs = paid_scans().filter(credited_at__gte=start, credited_at__lt=end)
    if worker_ids is not None:
        qs = qs.filter(worker_id__in=worker_ids)
    rows = qs.annotate(day=TruncDate('credited_at')).values('worker_id', 'day').annotate(
        units=Sum('quantity'), amount=Sum('total_amount'))
    return {(row['worker_id'], row['day']): {'units': row['units'] or 0, 'amount': row['amount'] or ZERO} for row in rows}


def worker_taxlov_total(worker):
    return paid_scans().filter(worker=worker).aggregate(s=Sum('total_amount'))['s'] or ZERO
