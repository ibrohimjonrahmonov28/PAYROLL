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

    def test_salary_payout_freezes_tickets(self):
        today = timezone.localdate()

        # Both tickets are unfrozen initially
        self.assertFalse(self.ticket_frozen.is_frozen)
        self.assertFalse(self.ticket_unfrozen.is_frozen)

        # Perform SALARY payout via superadmin view
        res = self.client.post(reverse('superadmin_payout_create'), {
            'worker_id': self.worker.id,
            'amount': '200000.00',
            'payout_type': 'SALARY',
            'selected_year': str(today.year),
            'selected_month': str(today.month),
            'auto_freeze': '1',
        })
        self.assertEqual(res.status_code, 302)

        # Check tickets are now frozen
        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()

        self.assertTrue(self.ticket_frozen.is_frozen)
        self.assertIsNotNone(self.ticket_frozen.frozen_at)
        self.assertIsNotNone(self.ticket_frozen.frozen_payout)

        self.assertTrue(self.ticket_unfrozen.is_frozen)
        self.assertIsNotNone(self.ticket_unfrozen.frozen_at)
        self.assertIsNotNone(self.ticket_unfrozen.frozen_payout)

        # Now change the operation price again to 2000
        self.art_op.price_per_unit = Decimal("2000.00")
        self.art_op.save()

        # Both frozen tickets must remain completely untouched
        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()
        self.assertEqual(self.ticket_frozen.price_per_unit, Decimal("1000.00"))
        self.assertEqual(self.ticket_unfrozen.price_per_unit, Decimal("1000.00"))

    def test_manual_ticket_freeze_toggle(self):
        today = timezone.localdate()

        # 1. Manually freeze
        res = self.client.post(reverse('superadmin_ticket_freeze_toggle'), {
            'worker_id': self.worker.id,
            'year': str(today.year),
            'month': str(today.month),
            'action_type': 'freeze'
        })
        self.assertEqual(res.status_code, 302)

        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()
        self.assertTrue(self.ticket_frozen.is_frozen)
        self.assertTrue(self.ticket_unfrozen.is_frozen)

        # 2. Manually unfreeze
        res2 = self.client.post(reverse('superadmin_ticket_freeze_toggle'), {
            'worker_id': self.worker.id,
            'year': str(today.year),
            'month': str(today.month),
            'action_type': 'unfreeze'
        })
        self.assertEqual(res2.status_code, 302)

        self.ticket_frozen.refresh_from_db()
        self.ticket_unfrozen.refresh_from_db()
        self.assertFalse(self.ticket_frozen.is_frozen)
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

