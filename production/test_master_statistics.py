from decimal import Decimal
from datetime import date, datetime, time
from django.test import TestCase, Client
from django.utils import timezone
from django.urls import reverse

from accounts.models import User, Worker, MasterPatokHistory, MasterAbsentDay
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
        self.assertEqual(master_ali['sewer_earnings'], 10000)
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

    def test_master_monthly_work_api_only_returns_own_work(self):
        session = self.client.session
        session['master_user_id'] = self.master_user.id
        session.save()

        tz = timezone.get_current_timezone()
        b1 = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=91,
            pastal_code="P-ALI-91",
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
            accepted_by=self.master_user,
            assigned_screen=1,
            accepted_at=timezone.make_aware(datetime(2026, 10, 8, 10, 0), tz)
        )

        other_master = User.objects.create_user(username='other_master', password='Password123!', role=User.Role.MASTER)
        b2 = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=92,
            pastal_code="P-OTH-92",
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
            accepted_by=other_master,
            assigned_screen=2,
            accepted_at=timezone.make_aware(datetime(2026, 10, 8, 11, 0), tz)
        )

        url = reverse('production:master_tablet_monthly_work') + '?year=2026&month=10'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])

        batch_ids = [p['batch_id'] for p in data['pastals']]
        self.assertIn(b1.id, batch_ids)
        self.assertNotIn(b2.id, batch_ids)

    def test_pastal_passport_modal_api_and_acceptance(self):
        # 1. Pastal yaratamiz (DISPATCHED - Kutilmoqda holatida)
        batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=99,
            pastal_code="P-TEST-99",
            dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED,
            assigned_screen=1,
            assigned_branch=User.Branch.HQ,
            actual_weight_kg=Decimal("45.50"),
            cutter_name="Jasur Bichuvchi"
        )
        batch_item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=self.size,
            quantity=200,
            real_quantity=200
        )
        box1 = Box.objects.create(
            order=self.order,
            article=self.article,
            cutting_batch_item=batch_item,
            box_code="BX-TEST-1",
            box_number=1,
            razmer="M",
            quantity=100
        )
        box2 = Box.objects.create(
            order=self.order,
            article=self.article,
            cutting_batch_item=batch_item,
            box_code="BX-TEST-2",
            box_number=2,
            razmer="M",
            quantity=100
        )

        # 2. api_get_patok_detail tekshiramiz
        url_patok = reverse('production:master_tablet_patok_detail', kwargs={'screen_number': 1})
        res_patok = self.client.get(url_patok)
        self.assertEqual(res_patok.status_code, 200)
        data_patok = res_patok.json()
        self.assertTrue(data_patok['success'])
        waiting = data_patok['waiting_pastals']
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]['batch_id'], batch.id)
        self.assertEqual(waiting[0]['cutter_name'], "Jasur Bichuvchi")
        self.assertEqual(waiting[0]['actual_weight_kg'], 45.5)

        # 3. api_get_pastal_boxes_detail (Pasport modal API) tekshiramiz
        url_boxes = reverse('production:master_tablet_pastal_boxes', kwargs={'batch_id': batch.id})
        res_boxes = self.client.get(url_boxes)
        self.assertEqual(res_boxes.status_code, 200)
        data_boxes = res_boxes.json()
        self.assertTrue(data_boxes['success'])
        pastal = data_boxes['pastal']
        self.assertEqual(pastal['cutter_name'], "Jasur Bichuvchi")
        self.assertEqual(pastal['actual_weight_kg'], 45.5)
        self.assertTrue(pastal['can_accept'])
        self.assertEqual(len(pastal['sizes_summary']), 1)
        self.assertEqual(pastal['sizes_summary'][0]['size'], "M")
        self.assertEqual(pastal['sizes_summary'][0]['boxes_count'], 2)
        self.assertEqual(pastal['sizes_summary'][0]['quantity'], 200)

        # 4. Master qabul qilishni tasdiqlaydi
        session = self.client.session
        session['master_user_id'] = self.master_user.id
        session.save()

        url_accept = reverse('production:master_tablet_accept')
        res_accept = self.client.post(
            url_accept,
            data={'batch_id': batch.id, 'screen_number': 1},
            content_type='application/json'
        )
        self.assertEqual(res_accept.status_code, 200)
        self.assertTrue(res_accept.json()['success'])

        batch.refresh_from_db()
        self.assertEqual(batch.dispatch_status, CuttingBatch.DispatchStatus.ACCEPTED)
        self.assertEqual(batch.accepted_by, self.master_user)

    def test_strictly_master_isolation_and_box_operations(self):
        # 1. Master Ali (assigned_screen = 1 / K1-Patok)
        session = self.client.session
        session['master_user_id'] = self.master_user.id
        session.save()

        # 2. Master summary FAQAT 1-patokni qaytarishi kerak
        url_summary = reverse('production:master_tablet_patoks')
        res_summary = self.client.get(url_summary)
        self.assertEqual(res_summary.status_code, 200)
        data_summary = res_summary.json()
        self.assertTrue(data_summary['success'])
        self.assertEqual(len(data_summary['patoks']), 1)
        self.assertEqual(data_summary['patoks'][0]['screen_number'], 1)
        self.assertEqual(data_summary['patoks'][0]['patok_code'], 'K1')

        # 3. Master begona patokni (masalan 2-patok) ochishga urinsa 403 olishi kerak
        url_foreign_patok = reverse('production:master_tablet_patok_detail', kwargs={'screen_number': 2})
        res_foreign = self.client.get(url_foreign_patok)
        self.assertEqual(res_foreign.status_code, 403)
        self.assertFalse(res_foreign.json()['success'])

        # 4. Master o'zining 1-patog'ini muammosiz ko'ra oladi
        url_own_patok = reverse('production:master_tablet_patok_detail', kwargs={'screen_number': 1})
        res_own = self.client.get(url_own_patok)
        self.assertEqual(res_own.status_code, 200)
        self.assertTrue(res_own.json()['success'])

        # 5. Quti va uning operatsiyasini tekshiramiz
        batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=11,
            pastal_code="P-11",
            assigned_screen=1,
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
            accepted_by=self.master_user
        )
        batch_item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=self.size,
            quantity=50,
            real_quantity=50
        )
        box = Box.objects.create(
            order=self.order,
            box_number=1,
            box_code="BX-11-1",
            razmer="M",
            quantity=50,
            cutting_batch_item=batch_item
        )
        worker = Worker.objects.create(
            worker_id="TIK-007",
            first_name="Nodira",
            last_name="Tikuvchi",
            is_active=True
        )
        ticket = Ticket.objects.create(
            box=box,
            article_operation=self.art_op,
            ticket_code="TCK-11-1-1",
            quantity=50,
            status=Ticket.Status.SCANNED,
            worker=worker,
            screen_number=1,
            scanned_at=timezone.now()
        )

        # 6. Pasport modalida har bir qutining operatsiyalari, kim qilgani va patogi chiqishi kerak
        url_boxes = reverse('production:master_tablet_pastal_boxes', kwargs={'batch_id': batch.id})
        res_boxes = self.client.get(url_boxes)
        self.assertEqual(res_boxes.status_code, 200)
        data_boxes = res_boxes.json()
        self.assertTrue(data_boxes['success'])

        boxes_list = data_boxes['boxes']
        self.assertEqual(len(boxes_list), 1)
        b_item = boxes_list[0]
        self.assertEqual(b_item['scanned_tickets_count'], 1)
        self.assertEqual(len(b_item['operations']), 1)
        op_info = b_item['operations'][0]
        self.assertEqual(op_info['ticket_id'], ticket.id)
        self.assertTrue(op_info['is_scanned'])
        self.assertEqual(op_info['worker_name'], "Nodira Tikuvchi")
        self.assertEqual(op_info['patok_code'], "K1")
        self.assertTrue(len(op_info['scanned_at']) > 0)

        # 7. Begona patokka tegishli pastalni lookup qilganda 403 olishi kerak
        foreign_batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=22,
            pastal_code="P-22",
            assigned_screen=2,
            dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED
        )
        url_lookup = reverse('production:master_tablet_lookup_pastal')
        res_lookup = self.client.post(
            url_lookup,
            data={'code': f"PASTAL:{foreign_batch.id}"},
            content_type='application/json'
        )
        self.assertEqual(res_lookup.status_code, 403)
        self.assertFalse(res_lookup.json()['success'])

    def test_technologist_role_and_cross_patok_acceptance(self):
        """
        Katta Texnolog tekshiruvi:
        1. Beydjigi orqali Master Tablet sessiyasiga kira oladi.
        2. Mas’ul master kasal bo'lsa, Katta Texnolog ixtiyoriy patok (masalan Screen 2) uchun pastal qabul qila oladi.
        """
        technologist = User.objects.create_user(
            username='texnolog_karim',
            password='Password123!',
            role=User.Role.TECHNOLOGIST,
            uid='TECH-999',
            first_name='Karim',
            last_name='Texnologov',
            branch=User.Branch.HQ
        )

        # 1. Planshetga login (identify)
        res_id = self.client.post(
            reverse('production:master_tablet_identify'),
            data={'code': 'USER:TECH-999'},
            content_type='application/json'
        )
        self.assertEqual(res_id.status_code, 200)
        self.assertTrue(res_id.json()['success'])
        self.assertFalse(res_id.json()['master']['is_strictly_master'])

        # 2. Begona patokka (Screen 2) pastal jo'natilgan
        batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=55,
            pastal_code="P-55",
            assigned_screen=2,
            dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED
        )
        box = Box.objects.create(
            order=self.order,
            box_code="BX-55-1",
            box_number=1,
            quantity=50,
            assigned_screen=2
        )

        # 3. Katta Texnolog 2-patok uchun pastalni qabul qiladi
        res_accept = self.client.post(
            reverse('production:master_tablet_accept'),
            data={'batch_id': batch.id, 'screen_number': 2},
            content_type='application/json'
        )
        self.assertEqual(res_accept.status_code, 200)
        self.assertTrue(res_accept.json()['success'])

        batch.refresh_from_db()
        self.assertEqual(batch.dispatch_status, CuttingBatch.DispatchStatus.ACCEPTED)
        self.assertEqual(batch.accepted_by, technologist)
        self.assertEqual(batch.assigned_screen, 2)

    def test_master_absent_day_exclusion_from_working_days_and_quvvat(self):
        """
        Master kelmagan / kasal bo'lgan kun o'rtacha arifmetik quvvatdan chiqarilishi shart:
        - 2 kun ish bo'lgan
        - 1-kun 100% quvvat, 2-kun 50% quvvat bo'lsa (o'rtacha 75%)
        - 2-kun MasterAbsentDay deb belgilansa, ish kuni 1 kun, o'rtacha quvvat 100% bo'lishi kerak.
        """
        tz = timezone.get_current_timezone()
        day1 = date(2026, 10, 10)
        dt1 = timezone.make_aware(datetime.combine(day1, time(10, 0)), tz)
        day2 = date(2026, 10, 11)
        dt2 = timezone.make_aware(datetime.combine(day2, time(10, 0)), tz)

        box1 = Box.objects.create(
            order=self.order,
            box_code="BX-ABS-1",
            box_number=1,
            quantity=1000,
            assigned_screen=1
        )
        # Day 1: 1000 dona x 200 = 200,000 UZS (100% quvvat, chunki norm = 1000)
        Ticket.objects.create(
            box=box1,
            article_operation=self.art_op,
            quantity=1000,
            total_amount=Decimal("200000.00"),
            status=Ticket.Status.SCANNED,
            scanned_at=dt1
        )
        # Day 2: 500 dona x 200 = 100,000 UZS (50% quvvat)
        Ticket.objects.create(
            box=box1,
            article_operation=self.art_op,
            quantity=500,
            total_amount=Decimal("100000.00"),
            status=Ticket.Status.SCANNED,
            scanned_at=dt2
        )

        # Dastlabki holat: 2 kun, o'rtacha quvvat 75%
        url = reverse('production:master_statistics') + '?month=2026-10'
        res = self.client.get(url)
        master_stat = next(m for m in res.context['masters_data'] if m['master_id'] == self.master_user.id)
        self.assertEqual(master_stat['master_working_days'], 2)
        self.assertEqual(master_stat['avg_quvvat_percent'], 75.0)
        self.assertEqual(master_stat['absent_days_count'], 0)

        # 2-kunni KELMAGAN / KASAL (MasterAbsentDay) deb belgilash API orqali
        toggle_url = reverse('production:api_master_statistics_toggle_absent')
        res_toggle = self.client.post(
            toggle_url,
            data={'user_id': self.master_user.id, 'date': '2026-10-11', 'reason': 'Kasal bo\'ldi'},
            content_type='application/json'
        )
        self.assertEqual(res_toggle.status_code, 200)
        self.assertTrue(res_toggle.json()['success'])
        self.assertTrue(res_toggle.json()['is_absent'])

        # Endi statistika tekshiriladi: Day 2 tashlab ketiladi!
        res_after = self.client.get(url)
        master_stat_after = next(m for m in res_after.context['masters_data'] if m['master_id'] == self.master_user.id)
        # Ish kuni faqat 1 kun
        self.assertEqual(master_stat_after['master_working_days'], 1)
        # O'rtacha quvvat endi 75% emas, balki to'liq 100.0%!
        self.assertEqual(master_stat_after['avg_quvvat_percent'], 100.0)
        self.assertEqual(master_stat_after['absent_days_count'], 1)

        # Detail API da ham is_absent=True ko'rinishi
        detail_url = reverse('production:api_master_statistics_detail', kwargs={'user_id': self.master_user.id}) + '?month=2026-10'
        res_detail = self.client.get(detail_url)
        d_data = res_detail.json()['data']
        absent_day_row = next(w for w in d_data['working_days'] if w['date'] == '2026-10-11')
        self.assertTrue(absent_day_row['is_absent'])


