from decimal import Decimal
from datetime import datetime, time
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from production.models import Operation, Article, ArticleOperation, Order, OrderItem, Box, Ticket
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

        # At this stage, 50 sewing units scanned, but 0 DAZMOL units
        data = get_screen_data(1)
        self.assertEqual(data['grand_total_units'], 0)
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

        # Now, grand_total_units must be 44 (the sum of DAZMOL passed pieces), NOT 50 + 44 = 94
        data_after = get_screen_data(1)
        self.assertEqual(data_after['grand_total_units'], 44)
        self.assertEqual(data_after['dazmol_units'], 44)
        self.assertEqual(data_after['active_workers_count'], 2)

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
        self.assertIn("1-PATOK", html)

        # Check that the stat label specifies Dazmol
        self.assertIn("Bugun Tikilgan (Dazmol)", html)


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
