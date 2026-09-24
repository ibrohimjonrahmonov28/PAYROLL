import datetime
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from accounts.models import MonthlyClosing, WorkerPayout
from production.models import Ticket


def check_and_apply_freeze_timers():
    """
    7 kunlik taymer muddati tugagan barcha oylarni avtomatik ravishda
    FROZEN (muzlatilgan) holatiga o'tkazish va ularning stikerlarini
    to'liq is_frozen=True qilib muhrlash.
    """
    now = timezone.now()
    expired_closings = MonthlyClosing.objects.filter(
        status=MonthlyClosing.Status.PENDING_FREEZE,
        freeze_deadline__lte=now
    )

    frozen_count = 0
    total_tickets_frozen = 0

    for closing in expired_closings:
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
    7 kunlik taymerni ishga tushiradi (PENDING_FREEZE).
    """
    now = timezone.now()
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
    Agar mavjud bo'lmasa, OPEN (ochiq) deb oladi.
    """
    closing = MonthlyClosing.objects.filter(year=year, month=month).first()
    if not closing:
        return {
            'status': MonthlyClosing.Status.OPEN,
            'status_display': 'Ochiq (Aktiv / Qayta hisoblash mumkin)',
            'closing': None,
            'is_timer_active': False,
            'time_remaining': ''
        }
    return {
        'status': closing.status,
        'status_display': closing.get_status_display(),
        'closing': closing,
        'is_timer_active': closing.is_timer_active,
        'time_remaining': closing.time_remaining_display
    }
