from decimal import Decimal
from datetime import date, datetime, time
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse

from accounts.models import User, Worker, MasterPatokHistory
from production.models import (
    Article, Operation, ArticleOperation, Order, OrderItem, Box, Ticket,
    Customer, OrderItemSize, CuttingBatch, CuttingBatchItem
)


class MasterStatisticsComprehensiveTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username='admin_boss',
            password='Password123!',
            role=User.Role.SUPER_ADMIN,
            first_name='Super',
            last_name='Admin'
        )
        self.client.force_login(self.superadmin)

        # Master foydalanuvchi (Screen 1 / K1-Patok)
        self.master_user = User.objects.create_user(
            username='master_ali',
            password='Password123!',
            role=User.Role.MASTER,
            assigned_screen=1,
            first_name='Ali',
            last_name='Masterov',
            branch=User.Branch.HQ
        )
        MasterPatokHistory.objects.create(
            user=self.master_user,
            screen_number=1,
            start_date=date(2026, 10, 1),
            is_active=True
        )

        # Mahsulot va operatsiyalar
        self.article = Article.objects.create(code="ART-TEST", name="Kofta", daily_norm=1000)
        self.op1 = Operation.objects.create(code="OP-10", name="Tikish")
        self.art_op = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal("200.00"),
            sequence=1,
            is_active=True
        )

        self.order = Order.objects.create(order_number="ORD-101", total_quantity=500)
        self.order_item = OrderItem.objects.create(order=self.order, article=self.article, quantity=500)
        self.size = OrderItemSize.objects.create(order_item=self.order_item, size_name="M", planned_quantity=500)

    def test_user_assigned_screen_properties(self):
        self.assertEqual(self.master_user.assigned_screen_number, 1)
        self.assertEqual(self.master_user.assigned_patok_code, "K1")
        self.assertEqual(self.master_user.assigned_patok_name, "K1-Patok")

        # Uychi patoki (Screen 14 -> U1-Patok)
        self.master_user.assigned_screen = 14
        self.master_user.save()
        self.assertEqual(self.master_user.assigned_patok_code, "U1")
        self.assertEqual(self.master_user.assigned_patok_name, "U1-Patok")

    def test_working_days_quvvat_and_zero_scan_days_exclusion(self):
        """
        Qoida: Agar patokda shu kun kamida 1 dona stiker scan bo'lsa, u ish kuni bo'ladi.
        0 ta stiker bo'lgan kunlar esa o'rta arifmetik hisobidan chetlashtiriladi.
        """
        tz = timezone.get_current_timezone()
        # Kun 1: 2026-10-05 da biletlar skanerlandi (Quvvat hisoblanadi)
        day1 = date(2026, 10, 5)
        dt1 = timezone.make_aware(datetime.combine(day1, time(10, 0)), tz)

        box1 = Box.objects.create(
            order=self.order,
            box_code="BX-001",
            box_number=1,
            quantity=100,
            assigned_screen=1
        )
        t1 = Ticket.objects.create(
            box=box1,
            article_operation=self.art_op,
            quantity=100,
            total_amount=Decimal("20000.00"),
            status=Ticket.Status.SCANNED,
            scanned_at=dt1
        )

        # Kun 2: 2026-10-06 (0 ta stiker scan, ish kuni bo'lmaydi)

        # Kun 3: 2026-10-07 da biletlar skanerlandi
        day3 = date(2026, 10, 7)
        dt3 = timezone.make_aware(datetime.combine(day3, time(11, 0)), tz)
        t2 = Ticket.objects.create(
            box=box1,
            article_operation=self.art_op,
            quantity=50,
            total_amount=Decimal("10000.00"),
            status=Ticket.Status.SCANNED,
            scanned_at=dt3
        )

        url = reverse('production:master_statistics') + '?month=2026-10'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        masters = res.context['masters_data']
        master_ali = next((m for m in masters if m['master_id'] == self.master_user.id), None)
        self.assertIsNotNone(master_ali)

        # Jami ish kuni aniq 2 kun bo'lishi kerak (0 stiker bo'lgan 2026-10-06 kirmasligi kerak)
        self.assertEqual(master_ali['master_working_days'], 2)
        # O'rtacha quvvat ikkala ish kunining o'rta arifmetigi bo'lishi shart
        self.assertGreater(master_ali['avg_quvvat_percent'], 0.0)

    def test_pastals_reconciliation_and_yoqotish_calculation(self):
        """
        Pastal kirishi va chiqishi (1-sort + 2-sort) balansi va yo'qotish (farq):
        Yo'qotish = (OTK 1-Sort + OTK 2-Sort) - Meto Kirdi Soni
        """
        tz = timezone.get_current_timezone()
        accept_time = timezone.make_aware(datetime(2026, 10, 2, 9, 0), tz)

        batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=1,
            pastal_code="P-101",
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
            accepted_by=self.master_user,
            assigned_screen=1,
            accepted_at=accept_time
        )
        item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=self.size,
            quantity=100,
            real_quantity=100  # Meto tasdiqlagan soni
        )

        # 2 ta quti: har birida 50 tadan
        box1 = Box.objects.create(
            order=self.order,
            cutting_batch_item=item,
            box_code="BX-P1-1",
            box_number=1,
            quantity=50,
            pastal_number="P-101",
            assigned_screen=1,
            is_controlled=True,
            controlled_first_sort_qty=47,
            controlled_second_sort_qty=1,
            controlled_repair_qty=0,
            controlled_defect_qty=2
        )
        box2 = Box.objects.create(
            order=self.order,
            cutting_batch_item=item,
            box_code="BX-P1-2",
            box_number=2,
            quantity=50,
            pastal_number="P-101",
            assigned_screen=1,
            is_controlled=True,
            controlled_first_sort_qty=49,
            controlled_second_sort_qty=1,
            controlled_repair_qty=0,
            controlled_defect_qty=0
        )

        url = reverse('production:master_statistics') + '?month=2026-10'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        master_ali = next((m for m in res.context['masters_data'] if m['master_id'] == self.master_user.id), None)
        self.assertIsNotNone(master_ali)

        # Kirdi: 100 dona
        self.assertEqual(master_ali['total_in_qty'], 100)
        # Chiqdi: (47+1) + (49+1) = 98 dona
        self.assertEqual(master_ali['total_out_qty'], 98)
        # Yo'qotish (Farq): 98 - 100 = -2 dona
        self.assertEqual(master_ali['total_diff'], -2)
        # Hamma qutilari tekshirilgani uchun to'liq yopilgan: 1 ta
        self.assertEqual(master_ali['closed_pastals'], 1)
        self.assertEqual(master_ali['open_pastals'], 0)

    def test_unclosed_pastal_with_pending_otk_and_repair(self):
        """
        Agar 1 dona quti bo'lsa ham OTK dan o'tmagan yoki ta'miri qolgan bo'lsa,
        pastal Yopilgan deb hisoblanmaydi (Ochiq qoladi).
        """
        tz = timezone.get_current_timezone()
        batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=2,
            pastal_code="P-102",
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
            accepted_by=self.master_user,
            assigned_screen=1,
            accepted_at=timezone.make_aware(datetime(2026, 10, 3, 10, 0), tz)
        )
        item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=self.size,
            quantity=60,
            real_quantity=60
        )
        box = Box.objects.create(
            order=self.order,
            cutting_batch_item=item,
            box_code="BX-P2-1",
            box_number=1,
            quantity=60,
            pastal_number="P-102",
            assigned_screen=1,
            is_controlled=True,
            controlled_first_sort_qty=55,
            controlled_repair_qty=5  # Ta'miri bor!
        )

        url = reverse('production:master_statistics') + '?month=2026-10'
        res = self.client.get(url)
        master_ali = next((m for m in res.context['masters_data'] if m['master_id'] == self.master_user.id), None)
        self.assertIsNotNone(master_ali)

        # Yopilgan emas, ochiq bo'lishi shart
        self.assertEqual(master_ali['closed_pastals'], 0)
        self.assertEqual(master_ali['open_pastals'], 1)

    def test_gibrid_master_and_ordinary_sewer_work(self):
        """
        Agar master oy davomida oddiy tikuvchi bo'lib ham ishlagan bo'lsa,
        uning tikuvchilik kunlari va stikerdan topgan ishbay maoshi gibrid ko'rinishda chiqadi.
        """
        worker = Worker.objects.create(
            user=self.master_user,
            worker_id="W-999",
            first_name=self.master_user.first_name,
            last_name=self.master_user.last_name,
            is_active=True
        )

        tz = timezone.get_current_timezone()
        dt = timezone.make_aware(datetime(2026, 10, 15, 14, 0), tz)
        box = Box.objects.create(order=self.order, box_code="BX-SEW-1", box_number=5, quantity=50)

        Ticket.objects.create(
            box=box,
            worker=worker,
            article_operation=self.art_op,
            quantity=50,
            total_amount=Decimal("150000.00"),
            status=Ticket.Status.SCANNED,
            scanned_at=dt
        )

        url = reverse('production:master_statistics') + '?month=2026-10'
        res = self.client.get(url)
        master_ali = next((m for m in res.context['masters_data'] if m['master_id'] == self.master_user.id), None)

        self.assertEqual(master_ali['sewer_days'], 1)
        self.assertEqual(master_ali['sewer_earnings'], 150000)
        self.assertEqual(master_ali['sewer_tickets'], 1)

    def test_api_master_statistics_detail(self):
        url = reverse('production:api_master_statistics_detail', kwargs={'user_id': self.master_user.id}) + '?month=2026-10'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['data']['master_id'], self.master_user.id)
        self.assertEqual(data['data']['assigned_screen'], 1)
        self.assertIn('working_days', data['data'])
        self.assertIn('pastals_list', data['data'])
