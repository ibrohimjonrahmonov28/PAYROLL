from decimal import Decimal
from django.core.management.base import BaseCommand
from accounts.models import User, Worker
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket
from production.services import generate_box_tickets, create_boxes_for_order


class Command(BaseCommand):
    help = "Dona-bay Payroll tizimi uchun boshlang'ich demo ma'lumotlarni to'ldirish"

    def handle(self, *args, **options):
        self.stdout.write("Boshlang'ich ma'lumotlar yuklanmoqda...")

        # 1. Superadmin & Master yaratish
        superadmin, _ = User.objects.get_or_create(
            username="admin",
            defaults={
                'email': "admin@factory.uz",
                'role': User.Role.SUPER_ADMIN,
                'is_staff': True,
                'is_superuser': True,
            }
        )
        superadmin.set_password("admin123")
        superadmin.save()

        master, _ = User.objects.get_or_create(
            username="master",
            defaults={
                'email': "master@factory.uz",
                'role': User.Role.MASTER,
                'telegram_user_id': 123456789,
                'is_staff': False,
            }
        )
        master.set_password("master123")
        master.save()

        # 2. Xodimlar (Tikuvchilar)
        workers_data = [
            ("W-001", "Malika", "Karimova", "+998 90 111 22 33"),
            ("W-002", "Dilnoza", "Rahimova", "+998 90 222 33 44"),
            ("W-003", "Shaxnoza", "Yusupova", "+998 90 333 44 55"),
            ("W-004", "Gulnoza", "Axmedova", "+998 90 444 55 66"),
            ("W-005", "Nilufar", "Qosimova", "+998 90 555 66 77"),
        ]

        for wid, fn, ln, ph in workers_data:
            Worker.objects.get_or_create(
                worker_id=wid,
                defaults={'first_name': fn, 'last_name': ln, 'phone_number': ph, 'is_active': True}
            )

        # 3. Operatsiyalar
        ops_data = [
            ("OP-01", "Old va orqa bo'laklarni birlashtirish"),
            ("OP-02", "Yoqa tikish va qaytarish"),
            ("OP-03", "Yeng ulash va manjet tikish"),
            ("OP-04", "Yon choklarni yopish"),
            ("OP-05", "Etak buklash va tugma qadash"),
        ]
        created_ops = {}
        for code, name in ops_data:
            op, _ = Operation.objects.get_or_create(code=code, defaults={'name': name})
            created_ops[code] = op

        # 4. Modellar (Artikullar)
        article, _ = Article.objects.get_or_create(
            code="POLO-2026",
            defaults={
                'name': "Erkaklar Polo Futbolkasi (Paxta)",
                'description': "100% paxta, premium sifatli klassik polo futbolka."
            }
        )

        # Narxlar
        rates = [
            ("OP-01", Decimal("800.00"), 1),
            ("OP-02", Decimal("1500.00"), 2),
            ("OP-03", Decimal("1200.00"), 3),
            ("OP-04", Decimal("900.00"), 4),
            ("OP-05", Decimal("600.00"), 5),
        ]
        for op_code, price, seq in rates:
            ArticleOperation.objects.get_or_create(
                article=article,
                operation=created_ops[op_code],
                defaults={'price_per_unit': price, 'sequence': seq}
            )

        # 5. Buyurtma yaratish
        order, _ = Order.objects.get_or_create(
            order_number="ORD-2026-001",
            defaults={
                'article': article,
                'total_quantity': 1000,
                'client_name': "Samo Garments MCHJ",
                'status': Order.Status.IN_PROGRESS
            }
        )

        # 6. Qutilarga bo'lish (10 ta quti, har biri 100 donadan)
        if not order.boxes.exists():
            create_boxes_for_order(order, [100] * 10)

        # 7. Quti #1 va Quti #2 uchun QR stikerlarni avtomatik split bilan yaratish
        box1 = order.boxes.filter(box_number=1).first()
        if box1 and not box1.tickets.exists():
            # OP-01: split 2 (50, 50), OP-02: split 3 (34, 33, 33), OP-03: split 2 (50, 50)
            splits = {}
            for ao in article.article_operations.all():
                if ao.operation.code == "OP-02":
                    splits[ao.id] = 3
                elif ao.operation.code in ["OP-01", "OP-03"]:
                    splits[ao.id] = 2
                else:
                    splits[ao.id] = 1
            generate_box_tickets(box1, splits)

        box2 = order.boxes.filter(box_number=2).first()
        if box2 and not box2.tickets.exists():
            generate_box_tickets(box2, {})

        self.stdout.write(self.style.SUCCESS("✅ Demo ma'lumotlar muvaffaqiyatli yuklandi!"))
        self.stdout.write(self.style.SUCCESS("Superadmin: login 'admin', parol 'admin123'"))

