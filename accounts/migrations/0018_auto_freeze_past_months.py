from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def apply_auto_freeze_past_months(apps, schema_editor):
    Ticket = apps.get_model('production', 'Ticket')
    MonthlyClosing = apps.get_model('accounts', 'MonthlyClosing')

    now = timezone.now()
    local_now = timezone.localtime(now)
    current_year = local_now.year
    current_month = local_now.month

    # 1. Joriy oydan oldingi (yakunlangan) barcha oylardagi skanerlangan stikerlarni to'liq muzlatish
    past_tickets = Ticket.objects.filter(
        status='SCANNED',
        is_frozen=False
    ).filter(
        Q(scanned_at__year__lt=current_year) |
        Q(scanned_at__year=current_year, scanned_at__month__lt=current_month)
    )
    past_tickets.update(
        is_frozen=True,
        frozen_at=now
    )

    # 2. Joriy oydan oldingi barcha oylarning MonthlyClosing holatini FROZEN ga o'tkazish
    past_months = Ticket.objects.filter(
        status='SCANNED'
    ).filter(
        Q(scanned_at__year__lt=current_year) |
        Q(scanned_at__year=current_year, scanned_at__month__lt=current_month)
    ).values('scanned_at__year', 'scanned_at__month').distinct()

    for item in past_months:
        y = item['scanned_at__year']
        m = item['scanned_at__month']
        if not y or not m:
            continue
        closing, _ = MonthlyClosing.objects.get_or_create(year=y, month=m)
        if closing.status != 'FROZEN':
            closing.status = 'FROZEN'
            closing.frozen_at = closing.frozen_at or now
            closing.save()

    # 3. Joriy oy (current_year, current_month) stikerlarini tekshirish:
    # Joriy oy davom etayotgani sababli undagi biletlar aktiv (is_frozen=False) bo'lishi shart!
    accidentally_frozen_current = Ticket.objects.filter(
        status='SCANNED',
        is_frozen=True,
        scanned_at__year=current_year,
        scanned_at__month=current_month
    )
    accidentally_frozen_current.update(
        is_frozen=False,
        frozen_at=None,
        frozen_payout=None
    )

    current_closing = MonthlyClosing.objects.filter(year=current_year, month=current_month).first()
    if current_closing and current_closing.status != 'OPEN':
        current_closing.status = 'OPEN'
        current_closing.freeze_deadline = None
        current_closing.frozen_at = None
        current_closing.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_alter_user_role'),
        ('production', '0019_ticket_frozen_at_ticket_frozen_payout_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_auto_freeze_past_months, noop_reverse),
    ]
