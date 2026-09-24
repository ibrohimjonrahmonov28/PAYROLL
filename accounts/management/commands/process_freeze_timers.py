from django.core.management.base import BaseCommand
from accounts.freeze_services import check_and_apply_freeze_timers


class Command(BaseCommand):
    help = "7 kunlik taymeri tugagan oylarning barcha stikerlarini avtomatik muzlatish (freeze)"

    def handle(self, *args, **options):
        self.stdout.write("7 kunlik muzlatish taymerlari tekshirilmoqda...")
        frozen_months, frozen_tickets = check_and_apply_freeze_timers()
        self.stdout.write(
            self.style.SUCCESS(
                f"Tekshiruv yakunlandi: {frozen_months} ta oy yopildi va {frozen_tickets} ta stiker to'liq muzlatildi."
            )
        )
