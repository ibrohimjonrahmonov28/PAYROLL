from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Worker, WorkerPayout
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket


class SuperAdminPanelTest(TestCase):
    def setUp(self):
        self.client = Client()

        # Superadmin user
        self.superadmin = User.objects.create_superuser(
            username="super_test",
            password="testpassword123",
            role=User.Role.SUPER_ADMIN
        )

        # Normal master user
        self.master = User.objects.create_user(
            username="master_test",
            password="testpassword123",
            role=User.Role.MASTER
        )

        # Worker
        self.worker = Worker.objects.create(
            worker_id="W-100",
            first_name="Zilola",
            last_name="Saidova"
        )

        # Article & Operation
        self.article = Article.objects.create(code="ART-TEST", name="Test Model")
        self.operation = Operation.objects.create(code="OP-TEST", name="Test Operation")
        self.art_op = ArticleOperation.objects.create(
            article=self.article,
            operation=self.operation,
            price_per_unit=Decimal("1000.00"),
            sequence=1
        )

        # Order, Box, Ticket scanned
        self.order = Order.objects.create(
            order_number="ORD-TEST-SA",
            article=self.article,
            total_quantity=100
        )
        self.box = Box.objects.create(
            order=self.order,
            box_number=1,
            quantity=100
        )
        self.ticket = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op,
            quantity=100,
            split_index=1,
            total_splits=1,
            price_per_unit=Decimal("1000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            screen_number=1,
            scanned_at=timezone.now()
        )

    def test_superadmin_access_control(self):
        # 1. Anonymous user redirected to login
        res = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(res.status_code, 302)

        # 2. Master user redirected with warning
        self.client.force_login(self.master)
        res2 = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(res2.status_code, 302)

        # 3. Super admin user gets 200 OK
        self.client.force_login(self.superadmin)
        res3 = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(res3.status_code, 200)

    def test_superadmin_create_staff_user(self):
        self.client.force_login(self.superadmin)
        data = {
            'action': 'create_user',
            'username': 'new_master_1',
            'password': 'password123',
            'first_name': 'Botir',
            'last_name': 'Aliyev',
            'role': 'MASTER',
            'phone_number': '+998901234567',
            'telegram_user_id': '99887766'
        }
        res = self.client.post(reverse('superadmin_users'), data)
        self.assertEqual(res.status_code, 302)

        new_user = User.objects.get(username='new_master_1')
        self.assertEqual(new_user.role, User.Role.MASTER)
        self.assertEqual(new_user.telegram_user_id, 99887766)

    def test_worker_payout_and_balance_calculation(self):
        self.client.force_login(self.superadmin)

        # Worker earned 100,000 UZS from ticket (100 * 1000)
        self.assertEqual(self.worker.total_earned, Decimal("100000.00"))
        self.assertEqual(self.worker.total_paid, 0)
        self.assertEqual(self.worker.balance, Decimal("100000.00"))

        # Super Admin pays advance of 30,000 UZS
        payout_data = {
            'worker_id': self.worker.id,
            'amount': '30000.00',
            'payout_type': 'ADVANCE',
            'note': 'Avans tolandi'
        }
        res = self.client.post(reverse('superadmin_payout_create'), payout_data)
        self.assertEqual(res.status_code, 302)

        # Check updated balance: 100,000 - 30,000 = 70,000 UZS
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.total_paid, Decimal("30000.00"))
        self.assertEqual(self.worker.balance, Decimal("70000.00"))

    def test_superadmin_payroll_csv_export(self):
        self.client.force_login(self.superadmin)
        res = self.client.get(reverse('superadmin_payroll_export'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8-sig')
        content = res.content.decode('utf-8-sig')
        self.assertIn("Zilola Saidova", content)
        self.assertIn("100000", content)

    def test_superadmin_pricing_update(self):
        self.client.force_login(self.superadmin)
        update_data = {
            'article_operation_id': self.art_op.id,
            'price_per_unit': '1500.00',
            'sequence': '2'
        }
        res = self.client.post(reverse('superadmin_pricing'), update_data)
        self.assertEqual(res.status_code, 302)

        self.art_op.refresh_from_db()
        self.assertEqual(self.art_op.price_per_unit, Decimal("1500.00"))
        self.assertEqual(self.art_op.sequence, 2)
