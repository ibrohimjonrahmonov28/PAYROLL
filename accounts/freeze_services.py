import datetime
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Q
from accounts.models import MonthlyClosing, WorkerPayout
from production.models import Ticket


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

    # 1. Joriy oydan oldingi barcha oylardagi skanerlangan ochiq biletlarni muzlatish
    past_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        is_frozen=False
    ).filter(
        Q(scanned_at__year__lt=current_year) |
        Q(scanned_at__year=current_year, scanned_at__month__lt=current_month)
    )
    frozen_tickets_cnt = past_tickets.update(
        is_frozen=True,
        frozen_at=now
    )

    # 2. Joriy oydan oldingi barcha oylarning MonthlyClosing holatini FROZEN ga o'tkazish
    past_months = Ticket.objects.filter(
        status=Ticket.Status.SCANNED
    ).filter(
        Q(scanned_at__year__lt=current_year) |
        Q(scanned_at__year=current_year, scanned_at__month__lt=current_month)
    ).values('scanned_at__year', 'scanned_at__month').distinct()

    past_closings_updated = 0
    for item in past_months:
        y = item['scanned_at__year']
        m = item['scanned_at__month']
        if not y or not m:
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
        scanned_at__year=current_year,
        scanned_at__month=current_month
    )
    unfrozen_current_cnt = accidentally_frozen_current.update(
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

        with transaction.atomic():
            tickets_qs = Ticket.objects.filter(
                status=Ticket.Status.SCANNED,
                scanned_at__year=closing.year,
                scanned_at__month=closing.month,
                is_frozen=False
            )
            cnt = tickets_qs.update(
                is_frozen=True,
                frozen_at=now
            )
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
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=year,
        scanned_at__month=month
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

