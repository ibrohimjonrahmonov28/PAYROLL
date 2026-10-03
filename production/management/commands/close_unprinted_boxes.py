from django.core.management.base import BaseCommand
from production.models import Box
from production.services import close_boxes_as_controlled


class Command(BaseCommand):
    help = "Stiker chop etilmagan (yoki avvalgi) qutilarni OTK sifat nazoratidan 1-sort qilib o'tkazish va yopish"

    def add_arguments(self, parser):
        parser.add_argument(
            '--order-id',
            type=int,
            help="Faqat bitta buyurtma ID si bo'yicha qutilarni yopish (masalan: --order-id 5)"
        )
        parser.add_argument(
            '--order-number',
            type=str,
            help="Faqat bitta buyurtma raqami bo'yicha qutilarni yopish"
        )
        parser.add_argument(
            '--include-printed',
            action='store_true',
            help="Chop etilgan bo'lsa ham, barcha operatsiyalari tugagan va hali OTK o'tmagan qutilarni ham yopish"
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Haqiqatda bazaga saqlamasdan faqat ko'rib chiqish (simulyatsiya)"
        )
        parser.add_argument(
            '--yes', '-y',
            action='store_true',
            help="Tasdiqlash so'rovisiz darhol bajarish"
        )

    def handle(self, *args, **options):
        order_id = options.get('order_id')
        order_number = options.get('order_number')
        include_printed = options.get('include_printed')
        dry_run = options.get('dry_run')
        auto_yes = options.get('yes')

        qs = Box.objects.exclude(status=Box.Status.CANCELLED).filter(is_controlled=False)

        if not include_printed:
            qs = qs.filter(is_printed=False)

        if order_id:
            qs = qs.filter(order_id=order_id)

        if order_number:
            qs = qs.filter(order__order_number__icontains=order_number)

        total_boxes = qs.count()
        if total_boxes == 0:
            self.stdout.write(self.style.WARNING("⚠️ Yopilishi kerak bo'lgan mos qutilar topilmadi."))
            return

        orders_affected = qs.values('order__id', 'order__order_number').distinct()
        self.stdout.write(self.style.NOTICE(f"\n🔍 Topilgan qutilar soni: {total_boxes} ta"))
        self.stdout.write(self.style.NOTICE("Tegishli buyurtmalar:"))
        for o in orders_affected:
            b_cnt = qs.filter(order_id=o['order__id']).count()
            self.stdout.write(f"  - Zakaz #{o['order__order_number']} (ID: {o['order__id']}): {b_cnt} ta quti")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Baza o'zgartirilmadi."))
            return

        if not auto_yes:
            confirm = input(f"\nHaqiqatan ham ushbu {total_boxes} ta qutini OTKdan 1-sort qilib yopishni tasdiqlaysizmi? (yes/no): ")
            if confirm.strip().lower() not in ['yes', 'y']:
                self.stdout.write(self.style.WARNING("Bekor qilindi."))
                return

        note = "Avvalgi (stiker chiqarilmagan / controlsiz) partiya buyruq orqali yopildi"
        result = close_boxes_as_controlled(qs, user=None, mark_printed=True, note=note)

        self.stdout.write(self.style.SUCCESS(
            f"\n✅ Muvaffaqiyatli! Jami {result['closed_count']} ta quti ({result['closed_units']} dona) "
            f"OTK sifat nazoratidan o'tkazildi va 1-sort qilib yopildi!"
        ))
