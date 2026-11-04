from decimal import Decimal
from datetime import datetime, time
from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from production.models import (
    Operation, OperationGroup, Article, ArticleOperation, Order, OrderItem, OrderItemSize, Box, Ticket,
    CuttingBatch, CuttingBatchItem,
)
from accounts.models import Worker, User
from screens.views import get_screen_data


class ScreenMonitorDazmolAndLayoutTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username="admin_screen",
            password="adminpassword123",
            role=User.Role.SUPER_ADMIN
        )
        self.worker1 = Worker.objects.create(
            worker_id="W-001",
            first_name="Zuxra",
            last_name="Sattorova"
        )
        self.worker2 = Worker.objects.create(
            worker_id="W-002",
            first_name="Ziyoda",
            last_name="Qosimova"
        )

        self.op_sewing = Operation.objects.create(
            name="1 TOMON YELKA",
            code="OP-YELKA"
        )
        self.op_dazmol = Operation.objects.create(
            name="DAZMOL",
            code="DAZMOL"
        )

        self.article = Article.objects.create(
            name="Test Polo",
            code="ART-POLO",
            daily_norm=1000
        )
        self.art_op_sewing = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op_sewing,
            price_per_unit=Decimal("1000.00"),
            sequence=1
        )
        self.art_op_dazmol = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op_dazmol,
            price_per_unit=Decimal("500.00"),
            sequence=2
        )

        self.order = Order.objects.create(
            order_number="ORD-SCR-01",
            article=self.article,
            total_quantity=200
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            article=self.article,
            quantity=200,
            norm=1000
        )
        self.box = Box.objects.create(
            order=self.order,
            box_number=1,
            quantity=50
        )

    def test_grand_total_units_reflects_dazmol_operation(self):
        # 1. Worker 1 scans sewing operation for 50 pieces on Screen 1
        Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op_sewing,
            quantity=50,
            price_per_unit=Decimal("1000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker1,
            screen_number=1,
            scanned_at=timezone.now()
        )

        # At this stage, 50 sewing units scanned, but 0 DAZMOL units -> falls back to Tikuv (50)
        data = get_screen_data(1)
        self.assertEqual(data['grand_total_units'], 50)
        self.assertEqual(data['units_label'], "Tikuv:")
        self.assertEqual(data['dazmol_units'], 0)
        self.assertEqual(data['active_workers_count'], 1)

        # 2. Worker 2 scans DAZMOL operation for 44 pieces on Screen 1
        Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op_dazmol,
            quantity=44,
            price_per_unit=Decimal("500.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker2,
            screen_number=1,
            scanned_at=timezone.now()
        )

        # Now, grand_total_units must be 44 (the sum of DAZMOL passed pieces), labeled as Dazmol:
        data_after = get_screen_data(1)
        self.assertEqual(data_after['grand_total_units'], 44)
        self.assertEqual(data_after['units_label'], "Dazmol:")
        self.assertEqual(data_after['dazmol_units'], 44)
        self.assertEqual(data_after['active_workers_count'], 2)

    def test_screen_aniqlik_and_patok_kpi_calculation(self):
        # 1. Worker 1 scans on Screen 1
        Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op_sewing,
            quantity=50,
            price_per_unit=Decimal("1000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker1,
            screen_number=1,
            scanned_at=timezone.now()
        )
        data = get_screen_data(1)
        # article has 2 operations with default difficulty 1.0 each -> aniqlik = 2
        self.assertEqual(data['aniqlik'], 2)
        self.assertEqual(data['davomat'], 1)
        # 1 / 2 * 100 = 50.0%
        self.assertEqual(data['foiz'], 50.0)
        # patok_kpi equals patok_quvvati (50000 / (1500 * 1000) * 100 = 3.3%)
        self.assertEqual(data['patok_kpi'], data['patok_quvvati'])
        self.assertEqual(data['patok_quvvati'], 3.3)

    def test_screen_template_layout_elements(self):
        # View screen 1
        res = self.client.get(reverse('screens:screen_view', args=[1]))
        self.assertEqual(res.status_code, 200)

        html = res.content.decode('utf-8')
        # Check that Patok switcher nav is removed
        self.assertNotIn("Patoklar (1-40):", html)
        self.assertNotIn("Barcha 40 ta Patok", html)

        # Check that large clock exists next to Terry Jar and Patok badge
        self.assertIn("live-clock", html)
        self.assertIn("TERRY JAR", html)
        self.assertIn("K1-PATOK", html)

        # Check that Online badge and countdown timer are removed
        self.assertNotIn(">Online<", html)
        self.assertNotIn("timer-countdown", html)

        # Check that summary stats exist (DAVOMAD/FOIZ was removed per user request)
        self.assertIn("OPERATSION", html)
        self.assertIn("DAVOMAD", html)
        self.assertNotIn("CHEVARLAR KPI", html)
        self.assertNotIn("Bugun Tikilgan", html)
        self.assertIn("Model Bo'yicha Norma", html)
        self.assertIn("Umumiy Norma Foizi", html)
        # Egasi talabi 2026-11-04: Patok quvvati o'rniga patok normasi, asosiy dazmol va patok foizi
        self.assertNotIn("PATOK QUVVATI", html)
        self.assertIn("PATOK NORMASI", html)
        self.assertIn("ASOSIY DAZMOL", html)
        self.assertIn("PATOK FOIZI", html)

        # View screen 14 -> must show U1-PATOK
        res14 = self.client.get(reverse('screens:screen_view', args=[14]))
        self.assertEqual(res14.status_code, 200)
        html14 = res14.content.decode('utf-8')
        self.assertIn("U1-PATOK", html14)


class ScreenPatokNormDazmolTest(TestCase):
    """
    TV ekran sarlavhasi (egasi talabi 2026-11-04): patok normasi, asosiy dazmol va patok foizi istalgan patok uchun
    18:00 hisobot bilan bir xil — ekran hisobotning o'z funksiyalarini ishlatadi.
    """
    def setUp(self):
        cache.clear()
        worker = Worker.objects.create(worker_id="W-101", first_name="Dilnoza", last_name="Karimova")
        sew = Operation.objects.create(name="YON CHOK", code="YON CHOK")
        dz = Operation.objects.create(name="DAZMOL", code="DAZMOL")
        futbolka = Article.objects.create(
            code="ART-FUT", name="Futbolka", daily_norm=1000, operation_group=OperationGroup.objects.create(name="Futbolka shabloni")
        )
        tunika = Article.objects.create(
            code="ART-TUN", name="Tunika", daily_norm=1500, operation_group=OperationGroup.objects.create(name="Tunika shabloni")
        )
        begona = Article.objects.create(code="ART-BEG", name="Begona", daily_norm=700)
        aos = {}
        for art in (futbolka, tunika, begona):
            aos[(art.code, 'sew')] = ArticleOperation.objects.create(article=art, operation=sew, price_per_unit=Decimal('100'), sequence=1)
            aos[(art.code, 'dz')] = ArticleOperation.objects.create(article=art, operation=dz, price_per_unit=Decimal('50'), sequence=2)
        order = Order.objects.create(order_number="ORD-NORM", article=futbolka, total_quantity=160)

        def box(n, article, owner, item=None):
            return Box.objects.create(order=order, box_number=n, quantity=40, article=article, cutting_batch_item=item,
                                      assigned_screen=owner, is_received_by_patok=True)

        b_fut, b_tun, b_beg = box(1, futbolka, 3), box(2, tunika, 3), box(3, begona, 7)

        # K9: qabul qilingan, lekin bugun dazmoli yo'q pastal (hisobot shu modelning normasini 0 dona bilan ko'rsatadi)
        tunika_item = OrderItem.objects.create(order=order, article=tunika, quantity=40)
        batch = CuttingBatch.objects.create(
            order_item=tunika_item, batch_number=1, assigned_screen=9,
            dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED, accepted_at=timezone.now(),
        )
        size = OrderItemSize.objects.create(order_item=tunika_item, size_name="M", planned_quantity=40)
        box(4, tunika, 9, item=CuttingBatchItem.objects.create(batch=batch, order_item_size=size, quantity=40))

        now = timezone.now()

        def scan(b, code, kind, terminal):
            Ticket.objects.create(box=b, article_operation=aos[(code, kind)], quantity=40, price_per_unit=Decimal('100'),
                                  status=Ticket.Status.SCANNED, worker=worker, screen_number=terminal, scanned_at=now)

        scan(b_fut, 'ART-FUT', 'sew', 3)
        scan(b_tun, 'ART-TUN', 'sew', 3)   # tikilyapti, lekin dazmoli yo'q — 18:00 hisobot kabi normaga kirmaydi
        scan(b_beg, 'ART-BEG', 'sew', 3)   # K3 terminalida, lekin K7 qabul qilgan pastal
        scan(b_fut, 'ART-FUT', 'dz', 5)    # K3 pastali K5 da dazmollangan — K3 ning asosiy dazmoli

    def test_screen_matches_1800_report_for_every_patok(self):
        from production.daily_image_reports import get_branch_daily_report_data
        from screens.views import get_screen_patok_metrics

        today = timezone.localdate()
        report = {r['screen_number']: r for r in get_branch_daily_report_data('HQ', today)['rows']}
        for sn in range(1, 14):
            m = get_screen_patok_metrics(sn, today)
            row = report[sn]
            norms = [p['norm'] for p in row['products'] if p['norm']]
            self.assertEqual(m['patok_foiz'], row['foiz'], f"K{sn}")
            self.assertEqual(m['asosiy_dazmol'], row['dazmol_total'], f"K{sn}")
            self.assertEqual(m['patok_norm'], round(sum(norms) / len(norms)) if norms else 0, f"K{sn}")

        def tup(sn):
            m = get_screen_patok_metrics(sn, today)
            return m['patok_norm'], m['asosiy_dazmol'], m['patok_foiz']

        self.assertEqual(tup(3), (1000, 40, 4.0))   # 40 / 1000
        self.assertEqual(tup(9), (1500, 0, 0.0))    # dazmol yo'q: qabul qilingan pastal normasi, foiz 0
        self.assertEqual(tup(7), (0, 0, 0.0))       # na dazmol, na qabul qilingan pastal

    def test_monitor_page_and_api_show_norm_dazmol_foiz(self):
        res = self.client.get(reverse('screens:screen_view', args=[3]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.context['patok_norm'], res.context['asosiy_dazmol'], res.context['patok_foiz']), (1000, 40, 4.0))
        html = res.content.decode('utf-8')
        self.assertIn("PATOK NORMASI", html)
        self.assertIn("ASOSIY DAZMOL", html)
        self.assertIn("PATOK FOIZI", html)
        self.assertNotIn("PATOK QUVVATI", html)

        api = self.client.get(reverse('screens:screen_api', args=[3])).json()
        self.assertEqual((api['patok_norm'], api['asosiy_dazmol'], api['patok_foiz']), (1000, 40, 4.0))


class ScreenAccountAuthAndAccessRestrictionTest(TestCase):
    def setUp(self):
        # Migration 0013 creates ekran1..ekran40 with password 111 and SCREEN role
        self.ekran1 = User.objects.get(username='ekran1')
        self.ekran5 = User.objects.get(username='ekran5')
        self.superadmin = User.objects.create_superuser(
            username='super_screen_admin',
            password='adminpassword123',
            role=User.Role.SUPER_ADMIN
        )

    def test_login_redirects_to_assigned_screen(self):
        res = self.client.post(reverse('root_login'), {
            'username': 'ekran1',
            'password': '111'
        })
        self.assertRedirects(res, reverse('screens:screen_view', args=[1]))

        self.client.logout()

        res5 = self.client.post(reverse('root_login'), {
            'username': 'ekran5',
            'password': '111'
        })
        self.assertRedirects(res5, reverse('screens:screen_view', args=[5]))

    def test_screen_user_can_access_own_screen_and_api(self):
        self.client.login(username='ekran1', password='111')

        res_page = self.client.get(reverse('screens:screen_view', args=[1]))
        self.assertEqual(res_page.status_code, 200)

        res_api = self.client.get(reverse('screens:screen_api', args=[1]))
        self.assertEqual(res_api.status_code, 200)

    def test_screen_user_cannot_access_other_screens(self):
        self.client.login(username='ekran1', password='111')

        # Trying to access screen 2 should redirect to screen 1
        res = self.client.get(reverse('screens:screen_view', args=[2]))
        self.assertRedirects(res, reverse('screens:screen_view', args=[1]))

        # Trying to access screen 2 API should return 403 Forbidden
        res_api = self.client.get(reverse('screens:screen_api', args=[2]))
        self.assertEqual(res_api.status_code, 403)
        self.assertEqual(res_api.json()['status'], 'FORBIDDEN')

        # Trying to access all screens overview should redirect to screen 1
        res_overview = self.client.get(reverse('screens:overview'))
        self.assertRedirects(res_overview, reverse('screens:screen_view', args=[1]))

    def test_screen_user_cannot_access_other_modules(self):
        self.client.login(username='ekran1', password='111')

        # Orders page -> redirect to screen 1
        res_orders = self.client.get(reverse('production:order_list'))
        self.assertRedirects(res_orders, reverse('screens:screen_view', args=[1]))

        # Superadmin -> redirect to screen 1
        res_super = self.client.get('/superadmin/')
        self.assertRedirects(res_super, reverse('screens:screen_view', args=[1]))

        # Control -> redirect to screen 1
        res_ctrl = self.client.get(reverse('production:control_home'))
        self.assertRedirects(res_ctrl, reverse('screens:screen_view', args=[1]))

        # Terminal -> redirect to screen 1
        res_term = self.client.get(reverse('production:terminal_home'))
        self.assertRedirects(res_term, reverse('screens:screen_view', args=[1]))

    def test_superadmin_can_access_any_screen(self):
        self.client.login(username='super_screen_admin', password='adminpassword123')

        res1 = self.client.get(reverse('screens:screen_view', args=[1]))
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.get(reverse('screens:screen_view', args=[2]))
        self.assertEqual(res2.status_code, 200)

        res_overview = self.client.get(reverse('screens:overview'))
        self.assertEqual(res_overview.status_code, 200)
