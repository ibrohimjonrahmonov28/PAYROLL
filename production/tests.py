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

    def test_create_box_when_gaps_exist(self):
        from production.services import create_box_with_tickets
        # Box 1 already exists. Create box 2 and box 3.
        boxes = create_box_with_tickets(order=self.order, article=self.article, quantity=50, count=2)
        self.assertEqual([b.box_number for b in boxes], [2, 3])
        # Delete box 1 and box 2. Only box 3 remains (count = 1).
        self.box.delete()
        boxes[0].delete()
        self.assertEqual(self.order.boxes.count(), 1)
        self.assertEqual(self.order.boxes.first().box_number, 3)
        # Creating a new box should get box_number 4, NOT box_number 2 (which count+1 would give)
        new_boxes = create_box_with_tickets(order=self.order, article=self.article, quantity=50, count=1)
        self.assertEqual(new_boxes[0].box_number, 4)


import json
from django.urls import reverse
from accounts.models import User


class OrdersAdminAccessTest(TestCase):
    def setUp(self):
        self.article = Article.objects.create(code="ART-TEST-ADMIN", name="Test Model")
        self.order_admin = User.objects.create_user(
            username='test_order_admin',
            password='test_password123',
            role=User.Role.ADMIN,
            is_staff=False,
            is_superuser=False
        )
        self.client.login(username='test_order_admin', password='test_password123')

    def test_orders_admin_can_access_orders_html(self):
        response = self.client.get(reverse('production:order_list'))
        self.assertEqual(response.status_code, 200)

    def test_orders_admin_can_access_orders_json_api(self):
        response = self.client.get(reverse('production:order_list'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'SUCCESS')
        self.assertIn('orders', data)

    def test_orders_admin_can_create_order_via_json_api(self):
        payload = {
            'order_number': 'ORD-API-TEST-999',
            'article_id': self.article.id,
            'total_quantity': 500,
            'client_name': 'Test Client'
        }
        response = self.client.post(
            reverse('production:order_list'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'SUCCESS')
        self.assertTrue(Order.objects.filter(order_number='ORD-API-TEST-999').exists())

    def test_orders_admin_cannot_access_terminal(self):
        # Browser request should be redirected to /orders/
        response = self.client.get(reverse('production:terminal_home'))
        self.assertRedirects(response, reverse('production:order_list'))

        # API / AJAX request to terminal should return 403 Forbidden
        response_api = self.client.get(reverse('production:terminal_home'), HTTP_ACCEPT='application/json')
        self.assertEqual(response_api.status_code, 403)
        self.assertEqual(response_api.json()['status'], 'FORBIDDEN')

    def test_orders_admin_cannot_access_superadmin(self):
        response = self.client.get('/superadmin/')
        self.assertRedirects(response, reverse('production:order_list'))

    def test_orders_admin_cannot_access_screens(self):
        response = self.client.get('/screens/monitor/')
        self.assertRedirects(response, reverse('production:order_list'))


class OrderPrintSeparationTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='super_admin',
            password='super_password123',
            role=User.Role.SUPER_ADMIN
        )
        self.client.login(username='super_admin', password='super_password123')

        self.art1 = Article.objects.create(code="ART-01", name="Model Bir")
        self.art2 = Article.objects.create(code="ART-02", name="Model Ikki")

        self.op1 = Operation.objects.create(code="OP-A", name="Tikish A")
        self.op2 = Operation.objects.create(code="OP-B", name="Tikish B")

        self.ao1 = ArticleOperation.objects.create(article=self.art1, operation=self.op1, price_per_unit=Decimal("500"), sequence=1)
        self.ao2 = ArticleOperation.objects.create(article=self.art2, operation=self.op2, price_per_unit=Decimal("600"), sequence=1)

        self.order = Order.objects.create(order_number="ORD-SEP-01", total_quantity=200)

        # Create 1 box for art1 and 1 box for art2
        self.box1 = Box.objects.create(order=self.order, article=self.art1, box_number=1, quantity=100)
        self.box2 = Box.objects.create(order=self.order, article=self.art2, box_number=2, quantity=100)

        generate_box_tickets(self.box1, {self.ao1.id: 1})
        generate_box_tickets(self.box2, {self.ao2.id: 1})

    def test_print_all_stickers_view_unfiltered(self):
        url = reverse('production:order_print_all_stickers', args=[self.order.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('articles_grouped_list', res.context)
        # Should have 2 article groups
        self.assertEqual(len(res.context['articles_grouped_list']), 2)
        # Total tickets = 2
        self.assertEqual(len(res.context['tickets']), 2)

    def test_print_all_stickers_filtered_by_article(self):
        url = reverse('production:order_print_all_stickers', args=[self.order.id])
        res = self.client.get(url, {'article_id': self.art1.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.context['articles_grouped_list']), 1)
        self.assertEqual(res.context['articles_grouped_list'][0]['article_code'], "ART-01")
        self.assertEqual(len(res.context['tickets']), 1)
        self.assertEqual(res.context['tickets'][0].box.box_number, 1)

    def test_download_all_stickers_pdf_filtered(self):
        url = reverse('production:order_download_all_stickers_pdf', args=[self.order.id])
        res = self.client.get(url, {'article_id': self.art1.id})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertIn('ART-01_BARCHA_QUTILAR', res['Content-Disposition'])

    def test_single_box_print_title_and_pdf_filename(self):
        # 1. Print page should have title containing Artikul and Box Number
        url_print = reverse('production:box_print_stickers', args=[self.box1.id])
        res_print = self.client.get(url_print)
        self.assertContains(res_print, "<title>ART-01_QUTI_1</title>")


        # 2. PDF download filename should begin with Artikul and Box number
        url_pdf = reverse('production:box_download_stickers_pdf', args=[self.box1.id])
        res_pdf = self.client.get(url_pdf)
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf['Content-Type'], 'application/pdf')
        self.assertIn(f'ART-01_QUTI_1_{self.box1.box_code}_ORD-SEP-01.pdf', res_pdf['Content-Disposition'])


class BoxRazmerFeatureTest(TestCase):
    def setUp(self):
        from accounts.models import User
        self.user = User.objects.create_superuser(username="admin_test", password="password123")
        self.client.force_login(self.user)

        self.article = Article.objects.create(code="ART-RZ", name="Razmerli Polo")
        self.op = Operation.objects.create(code="OP-RZ", name="Asosiy tikish")
        self.ao = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op,
            price_per_unit=Decimal("1200.00"),
            sequence=1
        )
        self.order = Order.objects.create(
            order_number="ORD-RZ-01",
            article=self.article,
            total_quantity=500
        )

    def test_create_box_with_razmer_service(self):
        from production.services import create_box_with_tickets
        boxes = create_box_with_tickets(
            order=self.order,
            article=self.article,
            quantity=100,
            count=1,
            razmer="XL"
        )
        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertEqual(box.razmer, "XL")
        self.assertEqual(box.tickets.count(), 1)
        ticket = box.tickets.first()
        self.assertEqual(ticket.box.razmer, "XL")

        # HTML print view contains RAZMER: XL
        url_print = reverse('production:box_print_stickers', args=[box.id])
        res_print = self.client.get(url_print)
        self.assertEqual(res_print.status_code, 200)
        self.assertContains(res_print, "RAZMER: XL")

        # PDF download generation
        url_pdf = reverse('production:box_download_stickers_pdf', args=[box.id])
        res_pdf = self.client.get(url_pdf)
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf['Content-Type'], 'application/pdf')

    def test_order_detail_view_create_box_with_razmer(self):
        url = reverse('production:order_detail', args=[self.order.id])
        res = self.client.post(url, {
            'action': 'create_boxes',
            'article_id': self.article.id,
            'box_size': '150',
            'box_count': '2',
            'razmer': '42'
        })
        self.assertEqual(res.status_code, 302)
        created_boxes = self.order.boxes.filter(razmer="42")
        self.assertEqual(created_boxes.count(), 2)
        for b in created_boxes:
            self.assertEqual(b.quantity, 150)
            self.assertEqual(b.razmer, "42")

    def test_box_split_wizard_updates_razmer(self):
        from production.services import create_box_with_tickets
        boxes = create_box_with_tickets(
            order=self.order,
            article=self.article,
            quantity=100,
            count=1,
            razmer="M"
        )
        box = boxes[0]
        url = reverse('production:box_split_wizard', args=[box.id])
        res = self.client.post(url, {
            'razmer': 'L',
            f'split_{self.ao.id}': '2'
        })
        self.assertEqual(res.status_code, 302)
        box.refresh_from_db()
        self.assertEqual(box.razmer, "L")
        self.assertEqual(box.tickets.count(), 2)




