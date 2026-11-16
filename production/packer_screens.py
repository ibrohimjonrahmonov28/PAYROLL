"""
Taxlovchilar TV ekranlari (egasi talabi 2026-11-14, yangi qadoqlash tartibining 6-bosqichi).

Har ekranga patoklar bir marta biriktiriladi (PackerScreen, bitta patok — bitta ekran). Ma'lumot — sanoqchi bugun
yuborgan ro'yxatlar (pul hisoblangan PackingScan). Chevarlar TV'sidagidek har taxlovchi bitta ekranda chiqadi: bugungi
oxirgi yuborilgan qutisining patogi biriktirilgan ekranda, kunlik butun ishi bilan. Ball = dona × qutining narx
shablonidagi "OTK Control/Taxlov" qiyinligi, norma 1000, foiz = ball / norma; bugungi taxlov puli va oylik maosh.
"""
import datetime
from collections import OrderedDict
from decimal import Decimal

from django.db.models import F, FloatField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from production.models import PackerScreen, Ticket
from production.services import get_patok_code
from production.taxlov_pay import paid_scans, taxlov_by_worker

NORM = 1000
ZERO = Decimal('0.00')


def _day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), tz)
    return start, start + datetime.timedelta(days=1)


def _month_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime(day.year, day.month, 1), tz)
    end = timezone.make_aware(datetime.datetime(day.year + day.month // 12, day.month % 12 + 1, 1), tz)
    return start, end


def _money(value):
    return f"{int(value or 0):,}".replace(",", " ")


def _pct_color(pct):
    return ('text-emerald-400 glow-emerald' if pct >= 100 else
            'text-amber-400 glow-amber' if pct >= 70 else 'text-rose-400 glow-rose')


def _number(value):
    value = round(value, 1)
    return int(value) if value.is_integer() else value


def packer_screens_data(target_date=None):
    """{ekran raqami: TV ma'lumoti} — barcha taxlovchi ekranlari bitta hisobda."""
    target_date = target_date or timezone.localdate()
    screens = list(PackerScreen.objects.all())
    patok_screen = {}
    for screen in screens:
        for patok in screen.patok_list:
            patok_screen.setdefault(patok, screen.number)

    start, end = _day_bounds(target_date)
    day_scans = paid_scans().filter(credited_at__gte=start, credited_at__lt=end)
    scan_patok = Coalesce('screen_number', 'batch__screen_number', 'box__assigned_screen')
    owner = {}
    if patok_screen:
        for worker_pk, patok in (day_scans.annotate(effective_patok=scan_patok)
                                 .filter(effective_patok__in=list(patok_screen))
                                 .order_by('credited_at', 'id').values_list('worker_id', 'effective_patok')):
            owner[worker_pk] = patok_screen[patok]

    workers = OrderedDict()
    if owner:
        points = Coalesce(F('box__article__operation_group__taxlov_difficulty'), Value(1.0), output_field=FloatField())
        rows = (day_scans.filter(worker_id__in=list(owner))
                .annotate(effective_patok=scan_patok)
                .values('worker_id', 'worker__worker_id', 'worker__first_name', 'worker__last_name', 'effective_patok')
                .annotate(units=Sum('quantity'), amount=Sum('total_amount'),
                          points=Sum(F('quantity') * points, output_field=FloatField())))
        for row in rows:
            worker = workers.setdefault(row['worker_id'], {
                'worker_id': row['worker__worker_id'],
                'full_name': f"{row['worker__first_name']} {row['worker__last_name']}".strip(),
                'units': 0, 'points': 0.0, 'earnings': ZERO, 'patoks': {},
            })
            worker['units'] += row['units'] or 0
            worker['points'] += row['points'] or 0.0
            worker['earnings'] += row['amount'] or ZERO
            patok = row['effective_patok']
            worker['patoks'][patok] = worker['patoks'].get(patok, 0) + (row['units'] or 0)

    month = {}
    if workers:
        month_start, month_end = _month_bounds(target_date)
        taxlov = taxlov_by_worker(month_start, month_end, worker_ids=list(workers))
        tickets = dict(Ticket.objects.filter(worker_id__in=list(workers), status=Ticket.Status.SCANNED,
                                             scanned_at__gte=month_start, scanned_at__lt=month_end)
                       .values_list('worker_id').annotate(s=Sum('total_amount')))
        month = {pk: taxlov.get(pk, {}).get('amount', ZERO) + (tickets.get(pk) or ZERO) for pk in workers}

    per_screen = {screen.number: [] for screen in screens}
    for worker_pk, w in workers.items():
        points = _number(w['points'])
        pct = round(points / NORM * 100, 1)
        per_screen[owner[worker_pk]].append({
            'worker_id': w['worker_id'],
            'full_name': w['full_name'],
            'total_units': w['units'],
            'total_points': points,
            'total_percentage': pct,
            'is_above_norm': pct >= 100.0,
            'progress_width': min(100, int(pct)),
            'ratio_str': f"{points}/{NORM}",
            'patoks': [{'code': get_patok_code(p) if p else '—', 'units': units}
                       for p, units in sorted(w['patoks'].items(), key=lambda item: item[0] or 0)],
            'total_earnings_formatted': _money(w['earnings']),
            'month_earnings_formatted': _money(month.get(worker_pk, w['earnings'])),
        })

    result = {}
    for screen in screens:
        rows = sorted(per_screen[screen.number], key=lambda r: (r['total_percentage'], r['total_points']), reverse=True)
        for rank, row in enumerate(rows, start=1):
            row['rank'] = rank
        avg_pct = round(sum(r['total_percentage'] for r in rows) / len(rows), 1) if rows else 0.0
        result[screen.number] = {
            'number': screen.number,
            'title': screen.title,
            'patok_codes': [get_patok_code(n) for n in screen.patok_list],
            'date_str': target_date.strftime("%d.%m.%Y"),
            'target_date_iso': target_date.isoformat(),
            'workers': rows,
            'davomat': len(rows),
            'units_total': sum(r['total_units'] for r in rows),
            'norm': NORM,
            'avg_percentage': avg_pct,
            'avg_color_class': _pct_color(avg_pct),
        }
    return result


def packer_screen_data(screen, target_date=None):
    return packer_screens_data(target_date)[screen.number]
