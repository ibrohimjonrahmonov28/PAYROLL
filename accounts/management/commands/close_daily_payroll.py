import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Sum, Count
from production.models import PackingScan, Ticket
from accounts.models import Worker, DailyWorkerClosing


def perform_daily_closing(target_date: datetime.date):
    """
    Berilgan sana uchun barcha xodimlarning skanerlangan biletlarini
    DailyWorkerClosing reestriga muhrlab yopish.
    Idempotent: bir necha marta chaqirilsa ham summalarni takrorlamaydi,
    mavjud yozuvni yangilaydi.
    """
    tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date,
        worker__isnull=False
    )

    stats = {s['worker']: s for s in tickets.values('worker').annotate(
        total_units=Sum('quantity'),
        total_amount=Sum('total_amount'),
        ticket_count=Count('id')
    )}
    # Taxlovchi puli (sanoqchi yuborgan ro'yxatlar): kunlik jami pulga qo'shiladi, alohida ustunda ham saqlanadi
    taxlov = {t['worker']: t for t in PackingScan.objects.filter(
        is_cancelled=False, credited_at__date=target_date
    ).values('worker').annotate(units=Sum('quantity'), amount=Sum('total_amount'))}

    closed_records = []
    total_day_amount = Decimal('0.00')
    total_day_units = 0

    for worker_id in stats.keys() | taxlov.keys():
        s = stats.get(worker_id, {})
        t = taxlov.get(worker_id, {})
        units = s.get('total_units') or 0
        taxlov_amount = t.get('amount') or Decimal('0.00')
        amount = (s.get('total_amount') or Decimal('0.00')) + taxlov_amount
        count = s.get('ticket_count') or 0

        closing, created = DailyWorkerClosing.objects.update_or_create(
            worker_id=worker_id,
            date=target_date,
            defaults={
                'total_units': units,
                'total_amount': amount,
                'ticket_count': count,
                'taxlov_units': t.get('units') or 0,
                'taxlov_amount': taxlov_amount,
            }
        )
        closed_records.append(closing)
        total_day_amount += amount
        total_day_units += units

    return {
        'date': target_date,
        'workers_count': len(closed_records),
        'total_units': total_day_units,
        'total_amount': total_day_amount,
        'records': closed_records,
    }


class Command(BaseCommand):
    help = "Har kuni kechqurun 00:00 da (yoki kun yakunida) xodimlarning kunlik ish haqini yopish va balansga muhrlash"

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help="Yopilishi kerak bo'lgan sana (YYYY-MM-DD formatida). Ko'rsatilmasa, kechagi yoki bugungi kun olinadi."
        )
        parser.add_argument(
            '--all-unclosed',
            action='store_true',
            help="Bazada skanerlangan lekin yopilmagan barcha o'tgan kunlarni yopish"
        )

    def handle(self, *args, **options):
        date_str = options.get('date')
        all_unclosed = options.get('all_unclosed')

        if all_unclosed:
            # Barcha skanerlangan biletlar sanalarini tekshirish
            dates = (
                Ticket.objects.filter(status=Ticket.Status.SCANNED, scanned_at__isnull=False)
                .values_list('scanned_at__date', flat=True)
                .distinct()
                .order_by('scanned_at__date')
            )
            total_closed_days = 0
            for d in dates:
                res = perform_daily_closing(d)
                if res['workers_count'] > 0:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ {d.strftime('%d.%m.%Y')}: {res['workers_count']} xodim, "
                            f"{res['total_units']} dona, {res['total_amount']:,.0f} UZS yopildi."
                        )
                    )
                    total_closed_days += 1
            self.stdout.write(self.style.SUCCESS(f"Jami {total_closed_days} ta kun muvaffaqiyatli yopildi."))
            return

        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Noto'g'ri sana formati: {date_str}. YYYY-MM-DD bo'lishi kerak."))
                return
        else:
            now = timezone.localtime()
            # Agar tongda (masalan 00:01 da) ishga tushsa, kechagi kunni yopadi
            if now.hour < 12:
                target_date = now.date() - datetime.timedelta(days=1)
            else:
                target_date = now.date()

        res = perform_daily_closing(target_date)
        self.stdout.write(
            self.style.SUCCESS(
                f"Kunlik hisob yopildi ({target_date.strftime('%d.%m.%Y')}):\n"
                f" - Faol xodimlar: {res['workers_count']} nafar\n"
                f" - Tikilgan jami dona: {res['total_units']:,}\n"
                f" - Jami hisoblangan summa: {res['total_amount']:,.0f} UZS"
            )
        )

