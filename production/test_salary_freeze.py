from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Worker, WorkerPayout
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket, ProductModel, ProductModelOperation


class SalaryFreezeAndPriceRecalculationTest(TestCase):
    def setUp(self):
        self.client = Client()

        # Superadmin user
        self.superadmin = User.objects.create_superuser(
            username="admin_freeze",
            password="testpassword123",
            role=User.Role.SUPER_ADMIN
        )
        self.client.force_login(self.superadmin)

        # Worker
        self.worker = Worker.objects.create(
            worker_id="W-FRZ-01",
            first_name="Zulhumor",
            last_name="Karimova"
        )

        # Product Model & Operation
        self.model = ProductModel.objects.create(code="PM-FRZ", name="Muzlatish Modeli")
        self.article = Article.objects.create(code="ART-FRZ-1", name="Artikul Muzlatish", model=self.model)
        self.op = Operation.objects.create(code="OP-FRZ-1", name="Asosiy Tikish")
        self.art_op = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op,
            price_per_unit=Decimal("1000.00"),
            sequence=1
        )

        # Order, Box
        self.order = Order.objects.create(order_number="ORD-FRZ-01", article=self.article, total_quantity=200)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=200)

        # Ticket 1 (will be frozen)
        self.ticket_frozen = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op,
            quantity=100,
            split_index=1,
            total_splits=2,
            price_per_unit=Decimal("1000.00"),
            total_amount=Decimal("100000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            screen_number=1,
            scanned_at=timezone.now(),
            is_frozen=False
        )

        # Ticket 2 (remains unfrozen)
        self.ticket_unfrozen = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op,
            quantity=100,
            split_index=2,
            total_splits=2,
            price_per_unit=Decimal("1000.00"),
            total_amount=Decimal("100000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            screen_number=1,
            scanned_at=timezone.now(),
            is_frozen=False
        )

    def test_price_change_updates_unfrozen_and_preserves_frozen_tickets(self):
        # 1. Freeze ticket 1
        self.ticket_frozen.is_frozen = True
        self.ticket_frozen.save()

        # 2. Increase operation price from 1000 to 1500
        self.art_op.price_per_unit = Decimal("1500.00")
        self.art_op.save()

        # Refresh tickets from DB
        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()

        # Frozen ticket must still have old price (1000) and old total (100,000)
        self.assertEqual(self.ticket_frozen.price_per_unit, Decimal("1000.00"))
        self.assertEqual(self.ticket_frozen.total_amount, Decimal("100000.00"))

        # Unfrozen ticket must be updated to new price (1500) and new total (150,000)
        self.assertEqual(self.ticket_unfrozen.price_per_unit, Decimal("1500.00"))
        self.assertEqual(self.ticket_unfrozen.total_amount, Decimal("150000.00"))

    def test_salary_payout_in_current_month_keeps_tickets_unfrozen(self):
        today = timezone.localdate()

        # Both tickets are unfrozen initially
        self.assertFalse(self.ticket_frozen.is_frozen)
        self.assertFalse(self.ticket_unfrozen.is_frozen)

        # Perform SALARY payout for CURRENT month
        res = self.client.post(reverse('superadmin_payout_create'), {
            'worker_id': self.worker.id,
            'amount': '200000.00',
            'payout_type': 'SALARY',
            'selected_year': str(today.year),
            'selected_month': str(today.month),
            'auto_freeze': '1',
        })
        self.assertEqual(res.status_code, 302)

        # Tickets in current month must REMAIN UNFROZEN
        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()
        self.assertFalse(self.ticket_frozen.is_frozen)
        self.assertFalse(self.ticket_unfrozen.is_frozen)

    def test_auto_freeze_past_months_and_protect_current_month(self):
        import datetime
        from accounts.freeze_services import auto_freeze_past_months

        now = timezone.now()
        past_date = now - datetime.timedelta(days=60)

        # Set ticket_frozen to a past month
        Ticket.objects.filter(id=self.ticket_frozen.id).update(scanned_at=past_date, is_frozen=False)
        # Set ticket_unfrozen in current month, but simulate mistakenly frozen
        Ticket.objects.filter(id=self.ticket_unfrozen.id).update(scanned_at=now, is_frozen=True)

        res = auto_freeze_past_months(now=now)
        self.assertGreaterEqual(res['frozen_tickets_cnt'], 1)
        self.assertGreaterEqual(res['unfrozen_current_cnt'], 1)

        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()

        # Past month ticket must now be frozen
        self.assertTrue(self.ticket_frozen.is_frozen)
        self.assertIsNotNone(self.ticket_frozen.frozen_at)

        # Current month ticket must now be unfrozen
        self.assertFalse(self.ticket_unfrozen.is_frozen)

    def test_freeze_current_month_is_blocked(self):
        today = timezone.localdate()

        # Attempt to freeze current month via month_freeze_toggle
        res = self.client.post(reverse('superadmin_month_freeze_toggle'), {
            'year': str(today.year),
            'month': str(today.month),
            'action': 'freeze'
        })
        self.assertEqual(res.status_code, 302)

        self.ticket_unfrozen.refresh_from_db()
        self.assertFalse(self.ticket_unfrozen.is_frozen)

        # Attempt to freeze current month via ticket_freeze_toggle
        res2 = self.client.post(reverse('superadmin_ticket_freeze_toggle'), {
            'worker_id': self.worker.id,
            'year': str(today.year),
            'month': str(today.month),
            'action_type': 'freeze'
        })
        self.assertEqual(res2.status_code, 302)

        self.ticket_unfrozen.refresh_from_db()
        self.assertFalse(self.ticket_unfrozen.is_frozen)

    def test_recalculate_unfrozen_tickets_action(self):
        today = timezone.localdate()

        # Set ticket price to 500 without updating operation
        Ticket.objects.filter(id=self.ticket_unfrozen.id).update(price_per_unit=Decimal("500.00"), total_amount=Decimal("50000.00"))
        self.ticket_unfrozen.refresh_from_db()
        self.assertEqual(self.ticket_unfrozen.price_per_unit, Decimal("500.00"))

        # Trigger bulk recalculate
        res = self.client.post(reverse('superadmin_recalculate_unfrozen_tickets'), {
            'year': str(today.year),
            'month': str(today.month),
        })
        self.assertEqual(res.status_code, 302)

        # Ticket must now be re-synced to operation price (1000)
        self.ticket_unfrozen.refresh_from_db()
        self.assertEqual(self.ticket_unfrozen.price_per_unit, Decimal("1000.00"))
        self.assertEqual(self.ticket_unfrozen.total_amount, Decimal("100000.00"))

