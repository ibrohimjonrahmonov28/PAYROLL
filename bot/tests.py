from decimal import Decimal
from django.test import TestCase
from accounts.models import Worker, User
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket
from bot.services import identify_worker, scan_ticket_item, finalize_and_route, clear_session
from screens.views import get_screen_data


class MasterScanningPipelineTest(TestCase):
    def setUp(self):
        clear_session("test_master_session")

        # Worker
        self.worker = Worker.objects.create(
            worker_id="W-001",
            first_name="Malika",
            last_name="Karimova",
            is_active=True
        )

        # Master
        self.master = User.objects.create_user(
            username="master1",
            role=User.Role.MASTER,
            telegram_user_id=987654321
        )

        # Production models
        self.article = Article.objects.create(code="ART-01", name="Ayollar Ko'ylagi")
        self.op1 = Operation.objects.create(code="OP-01", name="Yoqa tikish")
        self.art_op1 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal("1200.00"),
            sequence=1
        )
        self.order = Order.objects.create(
            order_number="ORD-BOT-01",
            article=self.article,
            total_quantity=100
        )
        self.box = Box.objects.create(
            order=self.order,
            box_number=1,
            quantity=100
        )
        self.ticket1 = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op1,
            quantity=50,
            split_index=1,
            total_splits=2,
            price_per_unit=Decimal("1200.00"),
            status=Ticket.Status.PENDING
        )
        self.ticket2 = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op1,
            quantity=50,
            split_index=2,
            total_splits=2,
            price_per_unit=Decimal("1200.00"),
            status=Ticket.Status.PENDING
        )

    def test_worker_identification(self):
        session_key = "test_master_session"
        
        # Test QR payload format: WORKER:W-001
        success, msg, worker = identify_worker(session_key, "WORKER:W-001")
        self.assertTrue(success)
        self.assertEqual(worker, self.worker)

        # Non-existent worker
        success2, msg2, worker2 = identify_worker(session_key, "WORKER:W-999")
        self.assertFalse(success2)
        self.assertIn("topilmadi", msg2)

    def test_ticket_scanning_and_duplicate_prevention(self):
        session_key = "test_master_session"
        identify_worker(session_key, "W-001")

        # Scan Ticket 1
        success, msg, data = scan_ticket_item(session_key, f"TICKET:{self.ticket1.ticket_code}")
        self.assertTrue(success)
        self.assertEqual(data['total_tickets_count'], 1)
        self.assertEqual(data['running_total_amount'], Decimal("60000.00")) # 50 * 1200

        # Duplicate scan in same session -> MUST BE REJECTED!
        success_dup, msg_dup, data_dup = scan_ticket_item(session_key, self.ticket1.ticket_code)
        self.assertFalse(success_dup)
        self.assertIn("allaqachon skanerlangan", msg_dup)

        # Scan Ticket 2
        success2, msg2, data2 = scan_ticket_item(session_key, self.ticket2.ticket_code)
        self.assertTrue(success2)
        self.assertEqual(data2['total_tickets_count'], 2)
        self.assertEqual(data2['running_total_amount'], Decimal("120000.00"))

    def test_finalize_and_screen_routing(self):
        session_key = "test_master_session"
        identify_worker(session_key, "W-001")
        scan_ticket_item(session_key, self.ticket1.ticket_code)
        scan_ticket_item(session_key, self.ticket2.ticket_code)

        # Finalize and route to Screen 3
        success, msg, summary = finalize_and_route(session_key, 3, self.master)
        self.assertTrue(success)
        self.assertEqual(summary['screen_number'], 3)
        self.assertEqual(summary['total_amount'], Decimal("120000.00"))
        self.assertEqual(summary['total_units'], 100)

        # Verify tickets in DB
        self.ticket1.refresh_from_db()
        self.ticket2.refresh_from_db()
        self.assertEqual(self.ticket1.status, Ticket.Status.SCANNED)
        self.assertEqual(self.ticket1.worker, self.worker)
        self.assertEqual(self.ticket1.screen_number, 3)
        self.assertEqual(self.ticket1.scanned_by, self.master)

        # Verify attempt to scan again from a new session -> MUST BE REJECTED (Already processed!)
        new_session = "another_master_session"
        identify_worker(new_session, "W-001")
        success_rep, msg_rep, _ = scan_ticket_item(new_session, self.ticket1.ticket_code)
        self.assertFalse(success_rep)
        self.assertIn("allaqachon qabul qilingan", msg_rep)

        # Verify Screen 3 data
        screen3_data = get_screen_data(3)
        self.assertEqual(screen3_data['active_workers_count'], 1)
        self.assertEqual(screen3_data['workers'][0]['worker_id'], 'W-001')
        self.assertEqual(screen3_data['grand_total_earnings'], 120000)

        # Verify other screen (e.g. Screen 4) has 0 workers
        screen4_data = get_screen_data(4)
        self.assertEqual(screen4_data['active_workers_count'], 0)
