from decimal import Decimal
from django.test import TestCase
from .models import Article, Operation, ArticleOperation, Order, Box, Ticket
from .services import allocate_ticket_quantities, generate_box_tickets


class AllocationAlgorithmTest(TestCase):
    def test_srs_exact_allocation_100_units_3_splits(self):
        # 100 units split into 3: 100 // 3 = 33, remainder 1 -> [34, 33, 33]
        allocations = allocate_ticket_quantities(100, 3)
        self.assertEqual(allocations, [34, 33, 33])
        self.assertEqual(sum(allocations), 100)

    def test_srs_exact_allocation_1000_units_7_splits(self):
        # 1000 // 7 = 142, remainder 6 -> 6 splits of 143 and 1 split of 142
        allocations = allocate_ticket_quantities(1000, 7)
        self.assertEqual(len(allocations), 7)
        self.assertEqual(sum(allocations), 1000)
        self.assertEqual(allocations, [143, 143, 143, 143, 143, 143, 142])

    def test_srs_single_split(self):
        allocations = allocate_ticket_quantities(50, 1)
        self.assertEqual(allocations, [50])
        self.assertEqual(sum(allocations), 50)

    def test_no_fractional_garments(self):
        # Tests various quantities and splits
        for q in [1, 5, 17, 53, 100, 250, 1000]:
            for n in [1, 2, 3, 4, 5, 8, 10]:
                res = allocate_ticket_quantities(q, n)
                self.assertEqual(sum(res), q, f"Failed for Q={q}, N={n}")
                self.assertTrue(all(isinstance(x, int) for x in res))


class BoxTicketGenerationTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(code="ART-TDD", name="Test T-Shirt")
        self.op1 = Operation.objects.create(code="OP-01", name="Yoqa tikish")
        self.op2 = Operation.objects.create(code="OP-02", name="Yeng ulash")

        self.art_op1 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal("500.00"),
            sequence=1
        )
        self.art_op2 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op2,
            price_per_unit=Decimal("800.00"),
            sequence=2
        )

        self.order = Order.objects.create(
            order_number="ORD-TEST-01",
            article=self.article,
            total_quantity=100
        )
        self.box = Box.objects.create(
            order=self.order,
            box_number=1,
            quantity=100
        )

    def test_generate_tickets_with_splits(self):
        # Split OP-01 into 2 splits (50 + 50)
        # Split OP-02 into 3 splits (34 + 33 + 33)
        splits = {
            self.art_op1.id: 2,
            self.art_op2.id: 3,
        }
        tickets = generate_box_tickets(self.box, splits)
        self.assertEqual(len(tickets), 5) # 2 + 3 = 5 tickets

        op1_tickets = [t for t in tickets if t.article_operation == self.art_op1]
        self.assertEqual([t.quantity for t in op1_tickets], [50, 50])
        self.assertEqual(op1_tickets[0].total_amount, Decimal("25000.00")) # 50 * 500

        op2_tickets = [t for t in tickets if t.article_operation == self.art_op2]
        self.assertEqual([t.quantity for t in op2_tickets], [34, 33, 33])
        self.assertEqual(sum(t.quantity for t in op2_tickets), 100)
        
        # QR code is generated
        self.assertTrue(bool(tickets[0].qr_code_image))
        self.assertTrue(tickets[0].ticket_code.startswith("TK-ORD-TEST-01-1-"))
