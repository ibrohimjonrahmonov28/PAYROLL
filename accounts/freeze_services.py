import datetime
import logging
import threading
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db import connection
from django.db.models import Sum, Q
from accounts.models import MonthlyClosing, WorkerPayout
from production.models import Ticket

logger = logging.getLogger(__name__)

# 2026-11-11: oktyabrning 208 ming stikerini bitta UPDATE bilan sayt so'rovi ichida muzlatish butun saytni 504 qildi.
# Endi muzlatish 5000 tadan bo'lib (har bo'lak qisqa yozuv) va tugmadan bosilganda fonda bajariladi.
BATCH_SIZE = 5000


def month_bounds(year: int, month: int):
    """Oy boshi va keyingi oy boshi (Toshkent vaqti): scanned_at indeksi bilan tez qidirish uchun."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime(year, month, 1), tz)
    end = timezone.make_aware(datetime.datetime(year + month // 12, month % 12 + 1, 1), tz)
    return start, end


def update_in_batches(queryset, **values):
    """
    Biletlarni BATCH_SIZE tadan yangilaydi: har bo'lak alohida qisqa yozuv, jadval daqiqalab band bo'lmaydi.
    queryset yangilanayotgan maydon bo'yicha filtrlangan bo'lishi shart (masalan is_frozen=False), aks holda tugamaydi.
    """
    total = 0
    while True:
        ids = list(queryset.order_by().values_list('id', flat=True)[:BATCH_SIZE])
        if not ids:
            return total
        updated = queryset.filter(id__in=ids).update(**values)
        if not updated:
            return total
        total += updated


def run_in_background(func, *args, **kwargs):
    """Og'ir ishni sayt so'rovidan tashqarida (alohida oqimda) bajaradi; testlarda (FREEZE_IN_BACKGROUND=False) darhol."""
    if not getattr(settings, 'FREEZE_IN_BACKGROUND', True):
        return func(*args, **kwargs)

    def runner():
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception("Fondagi muzlatish ishida xatolik")
        finally:
            connection.close()

    threading.Thread(target=runner, daemon=True).start()
    return None


def freeze_month_tickets(year: int, month: int, now=None):
    """Oyning barcha skanerlangan, hali muzlamagan stikerlarini bo'lib-bo'lib muzlatadi."""
    start, end = month_bounds(year, month)
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED, is_frozen=False, scanned_at__gte=start, scanned_at__lt=end
    )
    return update_in_batches(tickets_qs, is_frozen=True, frozen_at=now or timezone.now())


def unfreeze_month_tickets(year: int, month: int):
    """Oyning muzlagan stikerlarini bo'lib-bo'lib muzdan chiqaradi."""
    start, end = month_bounds(year, month)
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED, is_frozen=True, scanned_at__gte=start, scanned_at__lt=end
    )
    return update_in_batches(tickets_qs, is_frozen=False, frozen_at=None)


def auto_freeze_past_months(now=None):
    """
    Hozirgi oydan oldingi (yakunlangan) barcha oylarning skanerlangan stikerlarini
    to'liq muzlatish (is_frozen=True) va tegishli MonthlyClosing reestrlarini FROZEN qilish.
    Shunda narxlarni qayta hisoblashda (recalculate) o'tgan oylarga mutlaqo ta'sir qilmaydi.
    Shuningdek, agar joriy oydan xatolik bilan biletlar muzlatilgan bo'lsa, ularni muzdan chiqaradi (is_frozen=False).
    """
    if now is None:
        now = timezone.now()
    local_now = timezone.localtime(now)
    current_year = local_now.year
    current_month = local_now.month

    # Admin tomonidan qasddan "Muzlatishni bekor qilish" (status=OPEN) qilib ochilgan o'tgan oylarni aniqlash
    manually_opened_past_months = set(
        MonthlyClosing.objects.filter(
            status=MonthlyClosing.Status.OPEN
        ).filter(
            Q(year__lt=current_year) |
            Q(year=current_year, month__lt=current_month)
        ).values_list('year', 'month')
    )

    # 1. Joriy oydan oldingi barcha oylardagi skanerlangan ochiq biletlarni muzlatish
    # (faqat admin qasddan ochib qo'ymagan oylarni muzlatamiz). Sana oralig'i bilan (indeks) va bo'lib-bo'lib.
    current_month_start, _ = month_bounds(current_year, current_month)
    past_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        is_frozen=False,
        scanned_at__lt=current_month_start
    )
    for (y, m) in manually_opened_past_months:
        opened_start, opened_end = month_bounds(y, m)
        past_tickets = past_tickets.exclude(scanned_at__gte=opened_start, scanned_at__lt=opened_end)

    frozen_tickets_cnt = update_in_batches(past_tickets, is_frozen=True, frozen_at=now)

    # 2. Joriy oydan oldingi barcha oylarning MonthlyClosing holatini FROZEN ga o'tkazish
    # (faqat admin ochib qo'ymagan bo'lsa)
    past_months = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__lt=current_month_start
    ).values('scanned_at__year', 'scanned_at__month').order_by().distinct()

    past_closings_updated = 0
    for item in past_months:
        y = item['scanned_at__year']
        m = item['scanned_at__month']
        if not y or not m or (y, m) in manually_opened_past_months:
            continue
        closing, _ = MonthlyClosing.objects.get_or_create(year=y, month=m)
        if closing.status != MonthlyClosing.Status.FROZEN:
            closing.status = MonthlyClosing.Status.FROZEN
            closing.frozen_at = closing.frozen_at or now
            closing.save()
            past_closings_updated += 1

    # 3. Joriy oy (current_year, current_month) stikerlarini tekshirish:
    # Joriy oy hali davom etayotgani sababli undagi biletlar aktiv (is_frozen=False) bo'lishi shart!
    accidentally_frozen_current = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        is_frozen=True,
        scanned_at__gte=current_month_start
    )
    unfrozen_current_cnt = update_in_batches(
        accidentally_frozen_current,
        is_frozen=False,
        frozen_at=None,
        frozen_payout=None
    )

    # Joriy oy MonthlyClosing reestrini ochish (agar adashib FROZEN yoki PENDING_FREEZE bo'lib qolgan bo'lsa)
    current_closing = MonthlyClosing.objects.filter(year=current_year, month=current_month).first()
    if current_closing and current_closing.status != MonthlyClosing.Status.OPEN:
        current_closing.status = MonthlyClosing.Status.OPEN
        current_closing.freeze_deadline = None
        current_closing.frozen_at = None
        current_closing.save()

    return {
        'frozen_tickets_cnt': frozen_tickets_cnt,
        'unfrozen_current_cnt': unfrozen_current_cnt,
        'past_closings_updated': past_closings_updated
    }


def check_and_apply_freeze_timers():
    """
    1) Avvalo o'tgan oylarni to'liq muzlatish va joriy oyni ochish.
    2) 7 kunlik taymer muddati tugagan barcha oylarni avtomatik ravishda
    FROZEN (muzlatilgan) holatiga o'tkazish va ularning stikerlarini
    to'liq is_frozen=True qilib muhrlash.
    """
    now = timezone.now()
    auto_freeze_past_months(now=now)

    expired_closings = MonthlyClosing.objects.filter(
        status=MonthlyClosing.Status.PENDING_FREEZE,
        freeze_deadline__lte=now
    )

    frozen_count = 0
    total_tickets_frozen = 0

    for closing in expired_closings:
        local_now = timezone.localtime(now)
        is_past = (closing.year < local_now.year) or (closing.year == local_now.year and closing.month < local_now.month)
        if not is_past:
            closing.status = MonthlyClosing.Status.OPEN
            closing.freeze_deadline = None
            closing.save()
            continue

        # Bo'lib-bo'lib muzlatiladi (bitta katta tranzaksiya jadvalni band qilmasin); uzilib qolsa keyingi tekshiruv davom ettiradi
        cnt = freeze_month_tickets(closing.year, closing.month, now=now)
        closing.status = MonthlyClosing.Status.FROZEN
        closing.frozen_at = now
        closing.save()
        frozen_count += 1
        total_tickets_frozen += cnt

    return frozen_count, total_tickets_frozen


def start_monthly_freeze_timer(year: int, month: int, user=None):
    """
    Buxgalter 'Oylik berildi' deb tasdiqlaganda chaqiriladi:
    Faqat O'TGAN (yakunlangan) oylar uchun 7 kunlik taymerni ishga tushiradi (PENDING_FREEZE).
    Joriy oy uchun taymer boshlanmaydi!
    """
    now = timezone.now()
    local_now = timezone.localtime(now)
    # Joriy yoki kelgusi oy bo'lsa, rad etamiz
    if (year > local_now.year) or (year == local_now.year and month >= local_now.month):
        return None, False

    deadline = now + datetime.timedelta(days=7)

    closing, created = MonthlyClosing.objects.get_or_create(
        year=year,
        month=month
    )
    # Agar allaqachon muzlatilgan bo'lsa, qayta o'zgartirmaymiz
    if closing.status == MonthlyClosing.Status.FROZEN:
        return closing, False

    # Ushbu oy uchun umumiy statistikani jamlash
    month_start, month_end = month_bounds(year, month)
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__gte=month_start,
        scanned_at__lt=month_end
    )
    total_units = tickets_qs.aggregate(s=Sum('quantity'))['s'] or 0
    total_gross = tickets_qs.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    workers_count = tickets_qs.values('worker_id').distinct().count()

    payouts = WorkerPayout.objects.filter(
        payout_date__year=year,
        payout_date__month=month
    )
    advances = payouts.filter(payout_type=WorkerPayout.PayoutType.ADVANCE).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    salaries = payouts.filter(payout_type=WorkerPayout.PayoutType.SALARY).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

    closing.status = MonthlyClosing.Status.PENDING_FREEZE
    closing.payout_marked_at = now
    closing.payout_marked_by = user
    closing.freeze_deadline = deadline
    closing.total_workers_count = workers_count
    closing.total_units = total_units
    closing.total_gross_amount = total_gross
    closing.total_advances = advances
    closing.total_paid_salary = salaries
    closing.save()

    return closing, True


def get_monthly_closing_status(year: int, month: int):
    """
    Berilgan yil va oy uchun yopilish/muzlatish holatini qaytaradi.
    Joriy oy har doim OPEN bo'ladi (davom etayotgan faol oy).
    O'tgan oy muzlatilgan (FROZEN) bo'lishi kerak.
    """
    today = timezone.localdate()
    is_current = (year == today.year and month == today.month)
    is_future = (year > today.year) or (year == today.year and month > today.month)
    is_past = (year < today.year) or (year == today.year and month < today.month)

    if is_current:
        return {
            'status': MonthlyClosing.Status.OPEN,
            'status_display': 'Joriy oy (Davom etmoqda — Aktiv)',
            'closing': None,
            'is_timer_active': False,
            'time_remaining': '',
            'is_current_month': True,
            'is_past_month': False,
            'is_future_month': False,
        }

    closing = MonthlyClosing.objects.filter(year=year, month=month).first()
    if not closing:
        if is_past:
            return {
                'status': MonthlyClosing.Status.FROZEN,
                'status_display': 'Muzlatilgan (Arxiv / Qulflangan)',
                'closing': None,
                'is_timer_active': False,
                'time_remaining': '',
                'is_current_month': False,
                'is_past_month': True,
                'is_future_month': False,
            }
        return {
            'status': MonthlyClosing.Status.OPEN,
            'status_display': 'Ochiq',
            'closing': None,
            'is_timer_active': False,
            'time_remaining': '',
            'is_current_month': False,
            'is_past_month': False,
            'is_future_month': is_future,
        }

    return {
        'status': closing.status,
        'status_display': closing.get_status_display(),
        'closing': closing,
        'is_timer_active': closing.is_timer_active,
        'time_remaining': closing.time_remaining_display,
        'is_current_month': False,
        'is_past_month': is_past,
        'is_future_month': is_future,
    }

