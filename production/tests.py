from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from .models import (
    Article, Operation, ArticleOperation, Order, OrderItem, Box, Ticket,
    Customer, ProductModel, OrderItemSize, CuttingBatch, CuttingBatchItem
)
from .services import allocate_ticket_quantities, generate_box_tickets
from accounts.models import User, Worker


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
        
        # QR code is generated dynamically in memory
        self.assertTrue(bool(tickets[0].qr_code_data_uri))
        self.assertTrue(tickets[0].qr_code_data_uri.startswith("data:image/png;base64,"))
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

    def test_multiple_tickets_all_rendered_individually_with_control_sticker(self):
        # Qo'shimcha 3 ta operatsiya qo'shamiz
        op2 = Operation.objects.create(code="OP_TEST_2", name="Yoqa tikish")
        op3 = Operation.objects.create(code="OP_TEST_3", name="Yeng tikish")
        ao2 = ArticleOperation.objects.create(article=self.art1, operation=op2, price_per_unit=100, sequence=2)
        ao3 = ArticleOperation.objects.create(article=self.art1, operation=op3, price_per_unit=100, sequence=3)

        generate_box_tickets(self.box1, {ao2.id: 1, ao3.id: 1})
        self.assertEqual(self.box1.tickets.count(), 3)

        url = reverse('production:order_print_all_stickers', args=[self.order.id]) + f"?article_id={self.art1.id}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        content = res.content.decode('utf-8')
        # Barcha 3 ta operatsiya chiqishi kerak
        self.assertIn("Tikish A", content)
        self.assertIn("Yoqa tikish", content)
        self.assertIn("Yeng tikish", content)
        # CONTROL stikeri ham chiqishi kerak
        self.assertIn("CONTROL STIKER", content)

        # 3 ta bilet + 1 ta CONTROL stikeri = jami 4 ta sticker-card bo'lishi shart!
        self.assertEqual(content.count('class="sticker-card'), 4)


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

    def test_stiker_code_alphanumeric_and_legacy_compatibility(self):
        from production.services import create_box_with_tickets
        # 1. Yangi biletlar 8 xonali unikal harf/raqam aralashgan stiker kodi oladi
        boxes = create_box_with_tickets(
            order=self.order,
            article=self.article,
            quantity=50,
            count=1,
            razmer="S"
        )
        box = boxes[0]
        ticket_new = box.tickets.first()
        self.assertIsNotNone(ticket_new.stiker_code)
        self.assertEqual(len(ticket_new.stiker_code), 8)
        self.assertTrue(ticket_new.stiker_code.isalnum())
        self.assertEqual(ticket_new.stiker_id, f"#{ticket_new.stiker_code}")

        # 2. Eskilari odatiy qoladi: stiker_code yo'q bo'lsa #id qaytadi
        ticket_old = Ticket.objects.create(
            box=box,
            article_operation=self.ao,
            quantity=25,
            price_per_unit=Decimal("1200.00")
        )
        Ticket.objects.filter(id=ticket_old.id).update(stiker_code=None)
        ticket_old.refresh_from_db()
        self.assertIsNone(ticket_old.stiker_code)
        self.assertEqual(ticket_old.stiker_id, f"#{ticket_old.id}")

        # 3. Terminalda 8 xonali stiker kodi orqali topish
        from accounts.models import Worker
        worker = Worker.objects.create(worker_id="W-STIKER-TEST", first_name="Nodira", last_name="Aliyeva")
        self.client.force_login(self.user)
        session = self.client.session
        session['terminal_worker_id'] = worker.id
        session.save()

        url_scan = reverse('production:terminal_scan_ticket')
        res_new = self.client.post(url_scan, {
            'code': ticket_new.stiker_code,
        })
        self.assertEqual(res_new.status_code, 200)
        data_new = res_new.json()
        self.assertEqual(data_new['status'], 'OK')
        self.assertEqual(data_new['scanned_ticket']['id'], ticket_new.id)

        # 4. Terminalda eski odatiy #id orqali topish
        res_old = self.client.post(url_scan, {
            'code': f"#{ticket_old.id}",
        })
        self.assertEqual(res_old.status_code, 200)
        data_old = res_old.json()
        self.assertEqual(data_old['status'], 'OK')
        self.assertEqual(data_old['scanned_ticket']['id'], ticket_old.id)


class CustomerAndProductModelTest(TestCase):
    def test_customer_creation_and_order_sync(self):
        from .models import Customer, Order, Article
        customer = Customer.objects.create(name="DC Monetka", code="DCM", phone_number="+998901234567")
        article = Article.objects.create(code="ART-CUST", name="Customer Article")
        order = Order.objects.create(
            order_number="ORD-CUST-01",
            customer=customer,
            article=article,
            total_quantity=50
        )
        self.assertEqual(order.client_name, "DC Monetka")
        self.assertEqual(order.customer.code, "DCM")

    def test_product_model_and_article_operations_sync(self):
        from .models import ProductModel, ProductModelOperation, Operation, Article
        pm = ProductModel.objects.create(code="PM-DRESS", name="Ko'ylakcha", daily_norm=800)
        op1 = Operation.objects.create(code="OP-D1", name="Yelka biriktirish")
        op2 = Operation.objects.create(code="OP-D2", name="Etak tikish")
        ProductModelOperation.objects.create(model=pm, operation=op1, price_per_unit=Decimal("1500.00"), sequence=1, difficulty=1.2)
        ProductModelOperation.objects.create(model=pm, operation=op2, price_per_unit=Decimal("2000.00"), sequence=2, difficulty=1.0)

        # Create article linked to product model
        art = Article.objects.create(code="ART-D-RED", name="Qizil Ko'ylak", model=pm)
        self.assertEqual(art.daily_norm, 800)
        self.assertEqual(art.article_operations.count(), 2)

        ao1 = art.article_operations.get(operation=op1)
        self.assertEqual(ao1.price_per_unit, Decimal("1500.00"))
        self.assertEqual(ao1.difficulty, 1.2)


class BoxPipelineStatisticsTest(TestCase):
    def setUp(self):
        from accounts.models import User, Worker
        from .models import Customer, ProductModel, Operation, Article, ArticleOperation, Order, Box, Ticket

        self.user = User.objects.create_superuser(username="stat_admin", password="password123", role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.user)

        self.worker = Worker.objects.create(worker_id="W-STAT", first_name="Nargiza", last_name="Karimova")
        self.customer = Customer.objects.create(name="Terry Dreams", code="TD")
        self.model = ProductModel.objects.create(code="PM-STAT", name="Sport Kiyim", daily_norm=1000)
        self.op1 = Operation.objects.create(code="OP-S1", name="Bichish")
        self.op2 = Operation.objects.create(code="OP-S2", name="Tikish")

        self.article = Article.objects.create(code="ART-STAT-01", name="Ko'k Sport", model=self.model)
        self.ao1 = ArticleOperation.objects.create(article=self.article, operation=self.op1, price_per_unit=Decimal("1000"), sequence=1)
        self.ao2 = ArticleOperation.objects.create(article=self.article, operation=self.op2, price_per_unit=Decimal("2000"), sequence=2)

        self.order = Order.objects.create(order_number="ORD-STAT-99", customer=self.customer, article=self.article, total_quantity=100)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=50, razmer="XL")

        # Ticket 1: Scanned by worker
        self.t1 = Ticket.objects.create(
            box=self.box,
            article_operation=self.ao1,
            quantity=50,
            price_per_unit=Decimal("1000"),
            total_amount=Decimal("50000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_by=self.user,
            scanned_at=timezone.now(),
            screen_number=2
        )
        # Ticket 2: Pending
        self.t2 = Ticket.objects.create(
            box=self.box,
            article_operation=self.ao2,
            quantity=50,
            price_per_unit=Decimal("2000"),
            total_amount=Decimal("100000"),
            status=Ticket.Status.PENDING
        )

    def test_statistics_pipeline_view_renders_boxes_and_circles(self):
        url = reverse('production:statistics_pipeline')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Qutilar Vizual Statistikasi")
        self.assertContains(res, "ORD-STAT-99")
        self.assertContains(res, "Terry Dreams")
        self.assertContains(res, "XL")
        # Ticket 1 is scanned (blue)
        self.assertContains(res, self.t1.stiker_id)
        # Ticket 2 is pending (red)
        self.assertContains(res, self.t2.stiker_id)

    def test_statistics_search_by_sticker_id(self):
        url = reverse('production:statistics_pipeline') + f"?q={self.t1.stiker_code}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.t1.stiker_id)

    def test_api_ticket_scan_detail(self):
        url = reverse('production:api_ticket_scan_detail', kwargs={'code_or_id': self.t1.stiker_code})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['worker_name'], "Nargiza Karimova")
        self.assertEqual(data['worker_id'], "W-STAT")
        self.assertEqual(data['status'], "SCANNED")
        self.assertEqual(data['screen_number'], 2)


class ManagerAndCuttingWorkflowTest(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            username="superadmin_test",
            password="password123",
            role=User.Role.SUPER_ADMIN
        )
        self.manager = User.objects.create_user(
            username="manager_user",
            password="password123",
            role=User.Role.MANAGER
        )
        self.cutter = User.objects.create_user(
            username="cutter_user",
            password="password123",
            role=User.Role.CUTTER
        )
        self.model = ProductModel.objects.create(
            name="Polo futbolka",
            daily_norm=1500
        )
        self.customer = Customer.objects.create(
            name="TEX STYLE LLC"
        )
        self.op = Operation.objects.create(code="OP-SEW", name="Tikish")

    def test_manager_order_create_with_sizes(self):
        self.client.login(username="manager_user", password="password123")
        url = reverse('manager_order_create')
        data = {
            'order_number': 'ORD-MGR-101',
            'client_name': 'TEX STYLE LLC',
            'customer_id': self.customer.id,
            'product_model_id': self.model.id,
            'article_code[]': ['ART-POLO-01'],
            'article_name[]': ['Polo Classic Navy'],
            'size_name_0[]': ['S', 'M', 'L'],
            'size_qty_0[]': ['100', '250', '150'],
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        order = Order.objects.get(order_number='ORD-MGR-101')
        self.assertEqual(order.total_quantity, 500)
        item = order.items.first()
        self.assertEqual(item.article.code, 'ART-POLO-01')
        self.assertEqual(item.article.model, self.model)
        self.assertEqual(item.sizes.count(), 3)
        s_s = item.sizes.get(size_name='S')
        s_m = item.sizes.get(size_name='M')
        s_l = item.sizes.get(size_name='L')
        self.assertEqual(s_s.planned_quantity, 100)
        self.assertEqual(s_m.planned_quantity, 250)
        self.assertEqual(s_l.planned_quantity, 150)
        self.assertEqual(item.total_planned_quantity, 500)
        self.assertEqual(item.total_cut_quantity, 0)
        self.assertEqual(item.overall_cut_percentage, 0.0)

        # Verify manager order detail page displays sizes and 0%
        detail_url = reverse('manager_order_detail', kwargs={'order_id': order.id})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "ORD-MGR-101")
        self.assertContains(res_detail, "ART-POLO-01")
        self.assertContains(res_detail, "Polo futbolka")

    def test_manager_order_status_lifecycle_and_filtering(self):
        self.client.login(username="manager_user", password="password123")

        # 1. Create order with DRAFT status
        url_create = reverse('manager_order_create')
        data = {
            'order_number': 'ORD-STATUS-001',
            'client_name': 'TEX STYLE LLC',
            'customer_id': self.customer.id,
            'status': Order.Status.DRAFT,
            'article_code[]': ['ART-DRAFT-01'],
            'article_name[]': ['Draft Article'],
            'article_qty_0': '100',
        }
        res = self.client.post(url_create, data, follow=True)
        self.assertEqual(res.status_code, 200)

        order = Order.objects.get(order_number='ORD-STATUS-001')
        self.assertEqual(order.status, Order.Status.DRAFT)
        self.assertEqual(order.get_status_display(), 'Tayyorlanmoqda')

        # 2. Update status to IN_PROGRESS
        url_status = reverse('manager_order_update_status', kwargs={'order_id': order.id})
        res_update = self.client.post(url_status, {'status': Order.Status.IN_PROGRESS}, follow=True)
        self.assertEqual(res_update.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_PROGRESS)
        self.assertEqual(order.get_status_display(), 'Jarayonda')

        # 3. Update status to COMPLETED
        self.client.post(url_status, {'status': Order.Status.COMPLETED}, follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.assertEqual(order.get_status_display(), 'Tugatildi')

        # 4. Update status to ARCHIVED
        self.client.post(url_status, {'status': Order.Status.ARCHIVED}, follow=True)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.ARCHIVED)
        self.assertEqual(order.get_status_display(), 'Arxivlandi')

        # 5. Check Dashboard filtering by status
        url_dash = reverse('manager_dashboard')
        res_dash_all = self.client.get(url_dash)
        self.assertContains(res_dash_all, 'ORD-STATUS-001')
        self.assertContains(res_dash_all, 'Arxivlandi')

        res_dash_archived = self.client.get(url_dash, {'status': 'ARCHIVED'})
        self.assertContains(res_dash_archived, 'ORD-STATUS-001')

        res_dash_in_progress = self.client.get(url_dash, {'status': 'IN_PROGRESS'})
        self.assertNotContains(res_dash_in_progress, 'ORD-STATUS-001')

        # 6. Delete order
        url_delete = reverse('manager_order_delete', kwargs={'order_id': order.id})
        res_delete = self.client.post(url_delete, follow=True)
        self.assertEqual(res_delete.status_code, 200)
        self.assertFalse(Order.objects.filter(id=order.id).exists())

    def test_cutting_add_batches_and_progress_tracking(self):
        # Setup order with S: 100, M: 200
        order = Order.objects.create(order_number="ORD-CUT-01", customer=self.customer, client_name=self.customer.name)
        art = Article.objects.create(code="ART-CUT-01", name="Shirt", model=self.model)
        ArticleOperation.objects.create(article=art, operation=self.op, price_per_unit=Decimal("1000"), sequence=1)
        item = OrderItem.objects.create(order=order, article=art, quantity=300)
        size_s = OrderItemSize.objects.create(order_item=item, size_name="S", planned_quantity=100)
        size_m = OrderItemSize.objects.create(order_item=item, size_name="M", planned_quantity=200)

        meto_user = User.objects.create_user(username="meto_worker", password="password123", role=User.Role.METO)
        sticker_user = User.objects.create_user(username="sticker_worker", password="password123", role=User.Role.STICKER)

        self.client.login(username="cutter_user", password="password123")

        # 1. Kesimchi enters Kesim 1 (S: 50, M: 100) with pastal_code, partiya_number, and fabric info
        add_url = reverse('cutting_add_batch', kwargs={'order_id': order.id, 'order_item_id': item.id})
        
        # Test required pastal_code validation
        res_no_pastal = self.client.post(add_url, {
            'cutter_name': 'Ali Cutter',
            f'size_qty_{size_s.id}': '50',
        }, follow=True)
        self.assertContains(res_no_pastal, "Pastal kodi kiritilishi majburiy!")
        self.assertEqual(CuttingBatch.objects.filter(order_item=item).count(), 0)

        res1 = self.client.post(add_url, {
            'pastal_code': 'P-01',
            'partiya_number': '9-26-190',
            'cutter_name': 'Ali Cutter',
            'fabric_weight_kg': '200.50',
            'fabric_batch_code': 'DC-1001/TKDL102',
            'notes': 'Part 1 started',
            f'size_qty_{size_s.id}': '50',
            f'size_qty_{size_m.id}': '100',
        }, follow=True)
        self.assertEqual(res1.status_code, 200)

        b1 = CuttingBatch.objects.get(order_item=item, batch_number=1)
        self.assertEqual(b1.name, "Kesim 1")
        self.assertEqual(b1.pastal_code, "P-01")
        self.assertEqual(b1.partiya_number, "9-26-190")
        self.assertEqual(b1.fabric_weight_kg, Decimal("200.50"))
        self.assertEqual(b1.fabric_batch_code, 'DC-1001/TKDL102')
        self.assertEqual(b1.total_quantity, 150)
        size_s.refresh_from_db()
        size_m.refresh_from_db()
        self.assertEqual(size_s.total_cut_quantity, 50)
        self.assertEqual(size_m.total_cut_quantity, 100)

        # 2. Kesimchi edits batch before Meto confirms (M changed to 95)
        edit_url = reverse('cutting_edit_batch', kwargs={'order_id': order.id, 'batch_id': b1.id})
        b1_item_s = b1.items.get(order_item_size=size_s)
        b1_item_m = b1.items.get(order_item_size=size_m)
        res_edit = self.client.post(edit_url, {
            'pastal_code': 'P-01',
            'partiya_number': '9-26-190',
            'cutter_name': 'Ali Cutter',
            'fabric_weight_kg': '200.50',
            'fabric_batch_code': 'DC-1001/TKDL102',
            'notes': 'Corrected count',
            f'size_qty_{b1_item_s.id}': '50',
            f'size_qty_{b1_item_m.id}': '95',
        }, follow=True)
        self.assertEqual(res_edit.status_code, 200)
        b1_item_m.refresh_from_db()
        self.assertEqual(b1_item_m.quantity, 95)

        # 3. Metochi logs in, checks order detail and confirms M (with 5 brak -> real: 90, split into 2 boxes)
        self.client.login(username="meto_worker", password="password123")
        meto_detail_url = reverse('meto_order_detail', kwargs={'order_id': order.id})
        res_meto_detail = self.client.get(meto_detail_url)
        self.assertEqual(res_meto_detail.status_code, 200)
        self.assertContains(res_meto_detail, "DC-1001/TKDL102")
        self.assertContains(res_meto_detail, "P-01")
        self.assertContains(res_meto_detail, "9-26-190")

        confirm_url = reverse('meto_confirm_item', kwargs={'item_id': b1_item_m.id})
        res_confirm = self.client.post(confirm_url, {
            'real_quantity': '90',
            'meto_number_start': '1',
            'meto_number_end': '90',
            'meto_worker_name': 'Dilshod Meto',
            'meto_notes': '5 dona brak olindi',
            'split_mode': 'split_2',
        }, follow=True)
        self.assertEqual(res_confirm.status_code, 200)

        b1_item_m.refresh_from_db()
        self.assertEqual(b1_item_m.status, CuttingBatchItem.Status.METO_CONFIRMED)
        self.assertEqual(b1_item_m.real_quantity, 90)
        self.assertEqual(b1_item_m.meto_number_start, '1')
        self.assertEqual(b1_item_m.meto_number_end, '90')

        # Check 2 boxes automatically created with 45 units each and pastal_number == 'P-01'
        boxes = Box.objects.filter(cutting_batch_item=b1_item_m)
        self.assertEqual(boxes.count(), 2)
        for b in boxes:
            self.assertEqual(b.quantity, 45)
            self.assertEqual(b.razmer, 'M')
            self.assertEqual(b.meto_range, '#1-#90')
            self.assertEqual(b.pastal_number, 'P-01')
            self.assertEqual(b.tickets.count(), 1)
            self.assertEqual(b.tickets.first().box.razmer, 'M')

        # 4. Kesimchi tries to edit after Meto confirmed -> blocked!
        self.client.login(username="cutter_user", password="password123")
        res_edit_blocked = self.client.post(edit_url, {
            'cutter_name': 'Ali Cutter',
            f'size_qty_{b1_item_m.id}': '110',
        }, follow=True)
        self.assertEqual(res_edit_blocked.status_code, 200)
        b1_item_m.refresh_from_db()
        self.assertEqual(b1_item_m.quantity, 95)  # unchanged

        # 5. Sticker department views boxes and marks box 1 as printed (sewing dispatch)
        self.client.login(username="sticker_worker", password="password123")
        sticker_url = reverse('sticker_order_boxes', kwargs={'order_id': order.id})
        res_stk = self.client.get(sticker_url)
        self.assertEqual(res_stk.status_code, 200)
        self.assertContains(res_stk, "#1-#90")

        for b in boxes:
            mark_url = reverse('sticker_mark_box_printed', kwargs={'box_id': b.id})
            res_mark = self.client.post(mark_url, follow=True)
            self.assertEqual(res_mark.status_code, 200)
        box_1 = boxes.first()
        box_1.refresh_from_db()
        self.assertTrue(box_1.is_printed)
        b1_item_m.refresh_from_db()
        self.assertEqual(b1_item_m.status, CuttingBatchItem.Status.STICKERS_PRINTED)

    def test_pastal_code_duplicate_validation_and_api(self):
        order = Order.objects.create(order_number="ORD-DUP-01", customer=self.customer, client_name=self.customer.name)
        art1 = Article.objects.create(code="ART-DUP-01", name="Shirt 1", model=self.model)
        art2 = Article.objects.create(code="ART-DUP-02", name="Shirt 2", model=self.model)
        item1 = OrderItem.objects.create(order=order, article=art1, quantity=100)
        item2 = OrderItem.objects.create(order=order, article=art2, quantity=100)
        s1 = OrderItemSize.objects.create(order_item=item1, size_name="L", planned_quantity=100)
        s2 = OrderItemSize.objects.create(order_item=item2, size_name="L", planned_quantity=100)

        self.client.login(username="cutter_user", password="password123")

        # 1. API: Initial check for 'P-10' on item1 returns exists=False
        check_url = reverse('api_cutting_check_pastal')
        res_api_1 = self.client.get(check_url, {'order_item_id': item1.id, 'pastal_code': 'P-10'})
        self.assertEqual(res_api_1.status_code, 200)
        self.assertFalse(res_api_1.json()['exists'])
        self.assertTrue(res_api_1.json()['valid'])

        # 2. Add first batch with pastal_code 'P-10'
        add_url_1 = reverse('cutting_add_batch', kwargs={'order_id': order.id, 'order_item_id': item1.id})
        res_add_1 = self.client.post(add_url_1, {
            'pastal_code': 'P-10',
            f'size_qty_{s1.id}': '50',
        }, follow=True)
        self.assertEqual(res_add_1.status_code, 200)
        self.assertEqual(CuttingBatch.objects.filter(order_item=item1, pastal_code='P-10').count(), 1)
        b1 = CuttingBatch.objects.get(order_item=item1, pastal_code='P-10')

        # 3. API: check 'P-10' and case-insensitive 'p-10' on item1 returns exists=True
        res_api_dup = self.client.get(check_url, {'order_item_id': item1.id, 'pastal_code': 'p-10'})
        self.assertEqual(res_api_dup.status_code, 200)
        self.assertTrue(res_api_dup.json()['exists'])
        self.assertFalse(res_api_dup.json()['valid'])
        self.assertIn("allaqachon mavjud", res_api_dup.json()['message'])

        # With exclude_batch_id=b1.id: returns exists=False (editing self)
        res_api_exclude = self.client.get(check_url, {'order_item_id': item1.id, 'pastal_code': 'p-10', 'exclude_batch_id': b1.id})
        self.assertEqual(res_api_exclude.status_code, 200)
        self.assertFalse(res_api_exclude.json()['exists'])
        self.assertTrue(res_api_exclude.json()['valid'])

        # Different article (item2) allows 'P-10'
        res_api_item2 = self.client.get(check_url, {'order_item_id': item2.id, 'pastal_code': 'P-10'})
        self.assertEqual(res_api_item2.status_code, 200)
        self.assertFalse(res_api_item2.json()['exists'])

        # 4. Attempt to add duplicate pastal_code 'p-10' to item1 via POST -> BLOCKED!
        res_add_dup = self.client.post(add_url_1, {
            'pastal_code': 'p-10',
            f'size_qty_{s1.id}': '20',
        }, follow=True)
        self.assertEqual(res_add_dup.status_code, 200)
        self.assertContains(res_add_dup, "allaqachon mavjud! Bitta model uchun bir xil pastal kodini 2 marta kiritish taqiqlanadi")
        self.assertEqual(CuttingBatch.objects.filter(order_item=item1).count(), 1)

        # 5. Add second batch with unique pastal_code 'P-20'
        res_add_2 = self.client.post(add_url_1, {
            'pastal_code': 'P-20',
            f'size_qty_{s1.id}': '30',
        }, follow=True)
        self.assertEqual(res_add_2.status_code, 200)
        self.assertEqual(CuttingBatch.objects.filter(order_item=item1).count(), 2)
        b2 = CuttingBatch.objects.get(order_item=item1, pastal_code='P-20')

        # 6. Edit b2: try to rename pastal to 'P-10' -> BLOCKED!
        edit_url_b2 = reverse('cutting_edit_batch', kwargs={'order_id': order.id, 'batch_id': b2.id})
        b2_it = b2.items.first()
        res_edit_dup = self.client.post(edit_url_b2, {
            'pastal_code': 'P-10',
            f'size_qty_{b2_it.id}': '30',
        }, follow=True)
        self.assertEqual(res_edit_dup.status_code, 200)
        self.assertContains(res_edit_dup, "allaqachon mavjud! Bitta model uchun bir xil pastal kodini 2 marta kiritish taqiqlanadi")
        b2.refresh_from_db()
        self.assertEqual(b2.pastal_code, 'P-20')

        # 7. Edit b2: keep 'P-20' -> ALLOWED
        res_edit_ok = self.client.post(edit_url_b2, {
            'pastal_code': 'P-20',
            f'size_qty_{b2_it.id}': '35',
        }, follow=True)
        self.assertEqual(res_edit_ok.status_code, 200)
        b2_it.refresh_from_db()
        self.assertEqual(b2_it.quantity, 35)

    def test_meto_lazy_load_reset_and_pastal_print(self):
        # 1. Setup Order, Article, Operations, Batch
        meto_user = User.objects.create_user(username="meto_test_user", password="password123", role=User.Role.METO)
        self.client.login(username="meto_test_user", password="password123")
        order = Order.objects.create(order_number="ORD-TEST-PASTAL", status=Order.Status.IN_PROGRESS)
        art = Article.objects.create(code="ART-PST", name="Pastal Test Model")
        op = Operation.objects.create(code="OP-PST", name="Pastal Tikish")
        ArticleOperation.objects.create(article=art, operation=op, price_per_unit=Decimal("400.00"), sequence=1)
        ord_item = OrderItem.objects.create(order=order, article=art, quantity=100)
        size_m = OrderItemSize.objects.create(order_item=ord_item, size_name="L", planned_quantity=100)

        batch = CuttingBatch.objects.create(
            order_item=ord_item,
            batch_number=99,
            name="Kesim Pastal 99",
            pastal_code="P-99",
            partiya_number="12"
        )
        batch_item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=size_m,
            quantity=100
        )

        # 2. Meto confirms into 4 boxes
        confirm_url = reverse('meto_confirm_item', kwargs={'item_id': batch_item.id})
        res_conf = self.client.post(confirm_url, {
            'real_quantity': '100',
            'box_count': '4',
            'meto_number_start': '1',
            'meto_number_end': '100',
        }, follow=True)
        self.assertEqual(res_conf.status_code, 200)

        batch_item.refresh_from_db()
        self.assertEqual(batch_item.status, CuttingBatchItem.Status.METO_CONFIRMED)
        self.assertEqual(batch_item.boxes.count(), 4)

        # 3. Test meto_order_detail and meto_batch_items (lazy load)
        detail_url = reverse('meto_order_detail', kwargs={'order_id': order.id})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "PC: P-99")

        batch_items_url = reverse('meto_batch_items', kwargs={'batch_id': batch.id})
        res_batch_items = self.client.get(batch_items_url)
        self.assertEqual(res_batch_items.status_code, 200)
        self.assertContains(res_batch_items, "4 ta quti yaratilgan")
        self.assertIn("O'zgartirish", res_batch_items.content.decode('utf-8'))

        # 4. User made a mistake in box count! Test reset before stickers are printed
        reset_url = reverse('meto_reset_item', kwargs={'item_id': batch_item.id})
        res_reset = self.client.post(reset_url, follow=True)
        self.assertEqual(res_reset.status_code, 200)

        batch_item.refresh_from_db()
        self.assertEqual(batch_item.status, CuttingBatchItem.Status.CUT_ENTERED)
        self.assertEqual(batch_item.boxes.exclude(status=Box.Status.CANCELLED).count(), 0)  # Active boxes cancelled
        self.assertEqual(batch_item.boxes.filter(status=Box.Status.CANCELLED).count(), 4)

        # 5. Re-confirm with 2 boxes
        res_reconf = self.client.post(confirm_url, {
            'real_quantity': '100',
            'box_count': '2',
            'meto_number_start': '1',
            'meto_number_end': '100',
        }, follow=True)
        self.assertEqual(res_reconf.status_code, 200)
        batch_item.refresh_from_db()
        self.assertEqual(batch_item.boxes.exclude(status=Box.Status.CANCELLED).count(), 2)

        # 6. Test pastal-specific print and PDF
        print_pastal_url = reverse('production:order_print_all_stickers', kwargs={'order_id': order.id}) + "?pastal=P-99"
        res_print = self.client.get(print_pastal_url)
        self.assertEqual(res_print.status_code, 200)
        self.assertContains(res_print, "PASTAL: P-99")

        pdf_pastal_url = reverse('production:order_download_all_stickers_pdf', kwargs={'order_id': order.id}) + "?pastal=P-99"
        res_pdf = self.client.get(pdf_pastal_url)
        self.assertEqual(res_pdf.status_code, 200)
        self.assertIn("PASTAL_P-99", res_pdf['Content-Disposition'])

        # 7. When scanned by workers, reset is blocked!
        box_to_scan = batch_item.boxes.exclude(status=Box.Status.CANCELLED).first()
        ticket_to_scan = box_to_scan.tickets.first()
        ticket_to_scan.status = Ticket.Status.SCANNED
        ticket_to_scan.save(update_fields=['status'])

        res_reset_blocked = self.client.post(reset_url, follow=True)
        self.assertEqual(res_reset_blocked.status_code, 200)
        self.assertContains(res_reset_blocked, "allaqachon skanerlangan")
        self.assertEqual(batch_item.boxes.exclude(status=Box.Status.CANCELLED).count(), 2)  # Not changed!

    def test_sticker_lazy_load_by_pastal_and_batch_mark_printed(self):
        sticker_user = User.objects.create_user(username="sticker_lazy_user", password="password123", role=User.Role.STICKER)
        self.client.login(username="sticker_lazy_user", password="password123")

        order = Order.objects.create(order_number="ORD-STK-LAZY", status=Order.Status.IN_PROGRESS)
        art = Article.objects.create(code="ART-STK", name="Sticker Lazy Model")
        op = Operation.objects.create(code="OP-STK", name="Sticker Op")
        ArticleOperation.objects.create(article=art, operation=op, price_per_unit=Decimal("500.00"), sequence=1)
        ord_item = OrderItem.objects.create(order=order, article=art, quantity=200)
        size_xl = OrderItemSize.objects.create(order_item=ord_item, size_name="XL", planned_quantity=200)

        batch = CuttingBatch.objects.create(
            order_item=ord_item,
            batch_number=55,
            pastal_code="09-LAZY",
            name="Partiya 55"
        )
        batch_item = CuttingBatchItem.objects.create(
            batch=batch,
            order_item_size=size_xl,
            quantity=200,
            real_quantity=200,
            meto_number_start="1",
            meto_number_end="200",
            status=CuttingBatchItem.Status.METO_CONFIRMED
        )
        from production.services import auto_split_boxes_for_batch_item
        auto_split_boxes_for_batch_item(batch_item, box_count=4)

        # 1. Main sticker page loads without dumping all boxes
        stk_url = reverse('sticker_order_boxes', kwargs={'order_id': order.id})
        res_main = self.client.get(stk_url)
        self.assertEqual(res_main.status_code, 200)
        self.assertContains(res_main, "09-LAZY")
        self.assertContains(res_main, "4 ta quti")
        self.assertContains(res_main, "#1-#200")

        # 2. Ajax partial endpoint loads boxes for the batch
        boxes_url = reverse('sticker_batch_boxes', kwargs={'order_id': order.id, 'batch_id': batch.id})
        res_boxes = self.client.get(boxes_url)
        self.assertEqual(res_boxes.status_code, 200)
        self.assertContains(res_boxes, "XL")
        self.assertContains(res_boxes, "50 dona")
        self.assertContains(res_boxes, "Chop etilmagan")

        # 3. Mark entire batch printed
        mark_batch_url = reverse('sticker_mark_batch_printed', kwargs={'order_id': order.id, 'batch_id': batch.id})
        res_mark = self.client.post(mark_batch_url, follow=True)
        self.assertEqual(res_mark.status_code, 200)
        for b in batch_item.boxes.all():
            self.assertTrue(b.is_printed)

        # 4. Pastal passport view
        passport_url = reverse('production:pastal_passport', kwargs={'batch_id': batch.id})
        res_passport = self.client.get(passport_url)
        self.assertEqual(res_passport.status_code, 200)
        self.assertContains(res_passport, "PASTAL PASPORTI (A4 MARSHRUT VARAQASI)")
        self.assertContains(res_passport, "09-LAZY")
        self.assertContains(res_passport, "XL")

    def test_permissions_manager_and_cutter(self):
        user = User.objects.create_user(username="normal_user", password="password123", role=User.Role.USER)
        self.client.login(username="normal_user", password="password123")

        self.assertEqual(self.client.get(reverse('manager_dashboard')).status_code, 302)
        self.assertEqual(self.client.get(reverse('cutting_dashboard')).status_code, 302)
        self.assertEqual(self.client.get(reverse('meto_dashboard')).status_code, 302)
        self.assertEqual(self.client.get(reverse('sticker_dashboard')).status_code, 302)

        # Superadmin can access all
        self.client.login(username="superadmin_test", password="password123")
        self.assertEqual(self.client.get(reverse('manager_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('cutting_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('meto_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('sticker_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('norma_dashboard')).status_code, 200)


class NormaModuleTests(TestCase):
    def setUp(self):
        from accounts.models import User, Worker
        from production.models import ProductModel, Article, Operation, ArticleOperation, Order, Box, Ticket
        self.user = User.objects.create_superuser(username="super_norma", password="password123")
        self.client.force_login(self.user)

        self.model = ProductModel.objects.create(code="MOD-TEST-01", name="Test Model", daily_norm=1500)
        self.article = Article.objects.create(code="ART-TEST-01", name="Test Article", model=self.model, daily_norm=1500)
        self.op = Operation.objects.create(code="OP-TEST-01", name="Tikish")
        self.ao = ArticleOperation.objects.create(article=self.article, operation=self.op, price_per_unit=Decimal("500.00"), sequence=1)
        self.order = Order.objects.create(order_number="ORD-TEST-NORM", article=self.article, total_quantity=3000)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=1200)

        self.worker_user = User.objects.create_user(username="sewer_norma", password="password123", role=User.Role.USER, first_name="Norma", last_name="Tikuvchi")
        self.worker = Worker.objects.create(user=self.worker_user, worker_id="W-NORM-01")

    def test_norma_dashboard_and_models_list(self):
        res_dash = self.client.get(reverse('norma_dashboard'))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, "Jonli Norma Monitoringi")

        res_models = self.client.get(reverse('norma_models_list'))
        self.assertEqual(res_models.status_code, 200)
        self.assertContains(res_models, "MOD-TEST-01")

    def test_calculate_daily_model_progress_and_carryover(self):
        from production.norma_services import calculate_daily_model_progress
        import datetime

        day1 = datetime.date(2026, 9, 1)
        day2 = datetime.date(2026, 9, 2)

        # Day 1: 1200 dona tikildi (1200 / 1500 = 80%)
        t1 = Ticket.objects.create(
            box=self.box,
            article_operation=self.ao,
            quantity=1200,
            status=Ticket.Status.SCANNED,
            scanned_by=self.worker_user,
            scanned_at=datetime.datetime(2026, 9, 1, 14, 0, tzinfo=datetime.timezone.utc)
        )

        p1 = calculate_daily_model_progress(self.model, day1)
        self.assertEqual(p1.completed_units, 1200)
        self.assertEqual(p1.completion_percentage, Decimal('80.00'))
        self.assertEqual(p1.carried_over_percentage, Decimal('0.00'))
        self.assertEqual(p1.total_percentage, Decimal('80.00'))
        self.assertFalse(p1.is_completed)

        # Day 2: 1500 dona tikildi (100% + 20% qoldiq = 120%)
        t2 = Ticket.objects.create(
            box=self.box,
            article_operation=self.ao,
            quantity=1500,
            status=Ticket.Status.SCANNED,
            scanned_by=self.worker_user,
            scanned_at=datetime.datetime(2026, 9, 2, 14, 0, tzinfo=datetime.timezone.utc)
        )

        p2 = calculate_daily_model_progress(self.model, day2)
        self.assertEqual(p2.completed_units, 1500)
        self.assertEqual(p2.completion_percentage, Decimal('100.00'))
        self.assertEqual(p2.carried_over_percentage, Decimal('20.00'))
        self.assertEqual(p2.total_percentage, Decimal('120.00'))
        self.assertTrue(p2.is_completed)


class ModelOperationsAndSequenceTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin_norma', 'admin_norma@test.com', 'password123')
        self.client.force_login(self.admin)

        self.model = ProductModel.objects.create(
            code="MOD-SEQ-01",
            name="Test Model Sequence",
            daily_norm=1000
        )
        self.article = Article.objects.create(
            code="ART-SEQ-01",
            name="Test Article Sequence",
            model=self.model
        )
        self.op1 = Operation.objects.create(code="OP-SEQ-1", name="Yoqa tikish", order_number=1)
        self.op2 = Operation.objects.create(code="OP-SEQ-2", name="Yeng ulash", order_number=2)

    def test_model_operations_management_and_sync(self):
        from django.urls import reverse
        from production.models import ProductModelOperation, ArticleOperation

        # Modelga 1-operatsiyani biriktirish
        res_add1 = self.client.post(reverse('norma_model_add_operation', kwargs={'model_id': self.model.id}), {
            'operation_id': self.op1.id,
            'sequence': 1,
            'price_per_unit': '500',
            'difficulty': '1.0'
        })
        self.assertEqual(res_add1.status_code, 302)

        # Modelga 2-operatsiyani biriktirish
        res_add2 = self.client.post(reverse('norma_model_add_operation', kwargs={'model_id': self.model.id}), {
            'operation_id': self.op2.id,
            'sequence': 2,
            'price_per_unit': '800',
            'difficulty': '1.2'
        })
        self.assertEqual(res_add2.status_code, 302)

        # Tekshirish: ProductModelOperation 2 ta yaratildi
        self.assertEqual(self.model.model_operations.count(), 2)
        mo1 = self.model.model_operations.get(operation=self.op1)
        mo2 = self.model.model_operations.get(operation=self.op2)
        self.assertEqual(mo1.sequence, 1)
        self.assertEqual(mo2.sequence, 2)

        # Tekshirish: ArticleOperation larga ham sinxronlandi
        self.assertEqual(self.article.article_operations.count(), 2)
        ao1 = self.article.article_operations.get(operation=self.op1)
        ao2 = self.article.article_operations.get(operation=self.op2)
        self.assertEqual(ao1.sequence, 1)
        self.assertEqual(ao2.sequence, 2)

        # Quti va biletlar yaratilganda tartib bo'yicha chiqishi
        order = Order.objects.create(order_number="ORD-SEQ-101", article=self.article, total_quantity=100)
        box = Box.objects.create(order=order, article=self.article, box_number=1, quantity=100)
        tickets = generate_box_tickets(box)
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[0].article_operation.sequence, 1)
        self.assertEqual(tickets[1].article_operation.sequence, 2)

        # Stiker generatorida tartib raqami ko'rinishi
        from production.sticker_generator import render_single_box_ticket_100x60
        img = render_single_box_ticket_100x60(tickets[0])
        self.assertIsNotNone(img)

        # Bulk update orqali tartibni o'zgartirish (masalan 2 va 1 ga almashtirish)
        res_bulk = self.client.post(reverse('norma_model_update_operations', kwargs={'model_id': self.model.id}), {
            'mo_id': [mo1.id, mo2.id],
            f'sequence_{mo1.id}': 2,
            f'price_{mo1.id}': '600',
            f'difficulty_{mo1.id}': '1.1',
            f'sequence_{mo2.id}': 1,
            f'price_{mo2.id}': '900',
            f'difficulty_{mo2.id}': '1.3',
        })
        self.assertEqual(res_bulk.status_code, 302)

        mo1.refresh_from_db()
        mo2.refresh_from_db()
        self.assertEqual(mo1.sequence, 2)
        self.assertEqual(mo2.sequence, 1)

        # ArticleOperation larga ham o'tganini tekshirish
        ao1.refresh_from_db()
        ao2.refresh_from_db()
        self.assertEqual(ao1.sequence, 2)
        self.assertEqual(ao2.sequence, 1)


class NormaCanvasTest(TestCase):
    def setUp(self):
        User.objects.filter(username='admin_canvas_test').delete()
        self.admin = User.objects.create_superuser('admin_canvas_test', 'admin_canvas_test@test.com', 'password123')
        self.client.force_login(self.admin)

        self.model = ProductModel.objects.create(
            code="MOD-CANVAS-01",
            name="Canvas Test Model",
            daily_norm=0  # hali belgilanmagan
        )
        self.article = Article.objects.create(
            code="ART-CANVAS-01",
            name="Canvas Test Article",
            model=self.model,
            daily_norm=0
        )
        self.order = Order.objects.create(
            order_number="ORD-CANVAS-100",
            client_name="Canvas Client",
            status=Order.Status.IN_PROGRESS,
            total_quantity=200
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            article=self.article,
            quantity=200,
            norm=0
        )

    def test_canvas_view_renders_correctly(self):
        from django.urls import reverse
        res = self.client.get(reverse('norma_canvas'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "DB Sxema")
        self.assertContains(res, "ORD-CANVAS-100")
        self.assertContains(res, "ART-CANVAS-01")

    def test_canvas_save_validation_rejects_missing_norm_or_operations(self):
        import json
        from django.urls import reverse

        # 1. Normasiz so'rov
        res_no_norm = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps({
                'article_ids': [self.article.id],
                'daily_norm': 0,
                'operations': [{'name': 'Yoqa tikish', 'sequence': 1, 'price': 500, 'difficulty': 1.0}]
            }),
            content_type='application/json'
        )
        self.assertEqual(res_no_norm.status_code, 400)
        self.assertFalse(res_no_norm.json()['success'])
        self.assertIn("norma soni", res_no_norm.json()['error'].lower())

        # 2. Operatsiyalarsiz so'rov
        res_no_ops = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps({
                'article_ids': [self.article.id],
                'daily_norm': 1500,
                'operations': []
            }),
            content_type='application/json'
        )
        self.assertEqual(res_no_ops.status_code, 400)
        self.assertFalse(res_no_ops.json()['success'])
        self.assertIn("operatsiya", res_no_ops.json()['error'].lower())

    def test_canvas_save_success_updates_norm_operations_and_status(self):
        import json
        from django.urls import reverse

        payload = {
            'article_ids': [self.article.id],
            'daily_norm': 1500,
            'operations': [
                {'name': 'Yoqa tikish', 'sequence': 1, 'price': 450, 'difficulty': 1.0},
                {'name': 'Yeng ulash', 'sequence': 2, 'price': 600, 'difficulty': 1.2},
            ]
        }

        res = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['daily_norm'], 1500)
        self.assertEqual(data['operations_count'], 2)

        # Bazadagi o'zgarishlarni tekshirish
        self.article.refresh_from_db()
        self.model.refresh_from_db()
        self.order_item.refresh_from_db()

        self.assertEqual(self.article.daily_norm, 1500)
        self.assertEqual(self.model.daily_norm, 1500)
        self.assertEqual(self.order_item.norm, 1500)

        self.assertEqual(self.article.article_operations.count(), 2)
        self.assertEqual(self.model.model_operations.count(), 2)

    def test_canvas_2_step_save_operations_and_save_norma(self):
        import json
        from django.urls import reverse

        # 1-BOSQICH: Faqat operatsiyalarni saqlash (kunlik normasiz)
        ops_payload = {
            'action': 'save_operations',
            'article_ids': [self.article.id],
            'operations': [
                {'name': '1 TOMON YELKA', 'code': 'YELKA_1', 'sequence': 1, 'price': 300, 'difficulty': 1.0},
                {'name': 'BEYKA', 'code': 'BEYKA', 'sequence': 2, 'price': 400, 'difficulty': 1.1},
            ]
        }
        res_ops = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(ops_payload),
            content_type='application/json'
        )
        self.assertEqual(res_ops.status_code, 200)
        data_ops = res_ops.json()
        self.assertTrue(data_ops['success'])
        self.assertEqual(data_ops['operations_count'], 2)
        self.assertEqual(data_ops['unit_total_rate'], 700.0)

        self.article.refresh_from_db()
        self.model.refresh_from_db()
        self.assertEqual(self.article.article_operations.count(), 2)
        self.assertEqual(self.model.model_operations.count(), 2)

        # 2-BOSQICH: Kunlik normani saqlash va tasdiqlash
        norma_payload = {
            'action': 'save_norma',
            'article_ids': [self.article.id],
            'daily_norm': 1200,
        }
        res_norma = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(norma_payload),
            content_type='application/json'
        )
        self.assertEqual(res_norma.status_code, 200)
        data_norma = res_norma.json()
        self.assertTrue(data_norma['success'])
        self.assertEqual(data_norma['daily_norm'], 1200)

        self.article.refresh_from_db()
        self.model.refresh_from_db()
        self.order_item.refresh_from_db()
        self.assertEqual(self.article.daily_norm, 1200)
        self.assertEqual(self.model.daily_norm, 1200)
        self.assertEqual(self.order_item.norm, 1200)

    def test_delete_operations_when_not_in_sewing(self):
        import json
        from django.urls import reverse

        # Avval operatsiyalarni yaratish
        op = Operation.objects.create(code="OP-DEL-01", name="Delete Test Op")
        ao = ArticleOperation.objects.create(article=self.article, operation=op, sequence=1, price_per_unit=Decimal('500.00'))
        
        # PENDING bilet yaratish (hali tikuvga kirmagan)
        box = Box.objects.create(order=self.order, box_number=1, quantity=100)
        ticket = Ticket.objects.create(
            ticket_code="TK-TEST-DEL-01",
            box=box,
            article_operation=ao,
            quantity=100,
            price_per_unit=Decimal('500.00'),
            total_amount=Decimal('50000.00'),
            status=Ticket.Status.PENDING
        )

        # O'chirish so'rovi
        del_payload = {
            'action': 'delete_operations',
            'article_ids': [self.article.id],
        }
        res = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(del_payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['operations_count'], 0)

        # Bazada o'chirilganligini tekshirish
        self.assertEqual(ArticleOperation.objects.filter(article=self.article).count(), 0)
        self.assertEqual(Ticket.objects.filter(id=ticket.id).count(), 0)

    def test_delete_operations_blocked_when_in_sewing(self):
        import json
        from django.urls import reverse

        # Operatsiya va SKANERLANGAN bilet (tikuvga kirgan)
        op = Operation.objects.create(code="OP-SCAN-01", name="Scanned Op")
        ao = ArticleOperation.objects.create(article=self.article, operation=op, sequence=1, price_per_unit=Decimal('500.00'))
        
        box = Box.objects.create(order=self.order, box_number=2, quantity=100)
        Ticket.objects.create(
            ticket_code="TK-TEST-SCAN-01",
            box=box,
            article_operation=ao,
            quantity=100,
            price_per_unit=Decimal('500.00'),
            total_amount=Decimal('50000.00'),
            status=Ticket.Status.SCANNED, # Tikuvda bajarilgan!
            scanned_at=timezone.now()
        )

        # O'chirish so'rovi bloklanishi shart
        del_payload = {
            'action': 'delete_operations',
            'article_ids': [self.article.id],
        }
        res = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(del_payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data['success'])
        self.assertIn("tikuv jarayoniga kirgan", data['error'])

        # Operatsiya bazada o'chmasdan saqlanib qolishi kerak
        self.assertEqual(ArticleOperation.objects.filter(article=self.article).count(), 1)

    def test_delete_norma(self):
        import json
        from django.urls import reverse

        # Normani avval belgilash
        self.article.daily_norm = 1000
        self.article.save()
        self.model.daily_norm = 1000
        self.model.save()
        self.order_item.norm = 1000
        self.order_item.save()

        # Normani o'chirish
        del_payload = {
            'action': 'delete_norma',
            'article_ids': [self.article.id],
        }
        res = self.client.post(
            reverse('norma_canvas_save'),
            data=json.dumps(del_payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['daily_norm'], 0)

        # Bazada 0 bo'lganini tekshirish
        self.article.refresh_from_db()
        self.model.refresh_from_db()
        self.order_item.refresh_from_db()
        self.assertEqual(self.article.daily_norm, 0)
        self.assertEqual(self.model.daily_norm, 0)
        self.assertEqual(self.order_item.norm, 0)


class DailyExcelReportAndPricingSyncTest(TestCase):
    def setUp(self):
        from accounts.models import Worker
        from production.models import (
            ProductModel, Article, Operation, ArticleOperation,
            ModelOperation, Order, Box, Ticket
        )
        self.worker = Worker.objects.create(
            worker_id="TK-999",
            first_name="Nodirbek",
            last_name="Qodirov",
            phone_number="+998901234567"
        )
        self.model = ProductModel.objects.create(name="T-Shirt Classic", code="TS-001")
        self.article = Article.objects.create(code="ART-TS-01", name="White M", model=self.model)
        self.op1 = Operation.objects.create(code="OP-DAZMOL", name="Dazmol")
        self.op2 = Operation.objects.create(code="OP-METO", name="Meto")

        self.ao1 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal('500.00'),
            sequence=1
        )
        self.ao2 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op2,
            price_per_unit=Decimal('300.00'),
            sequence=2
        )

        self.order = Order.objects.create(order_number="ORD-2026-99")
        self.box = Box.objects.create(order=self.order, box_number=1, quantity=100)

        # Bugungi sana
        self.today = timezone.localdate()
        self.now = timezone.localtime()

        self.t1 = Ticket.objects.create(
            ticket_code="TK-TEST-001",
            box=self.box,
            article_operation=self.ao1,
            quantity=100,
            price_per_unit=Decimal('500.00'),
            total_amount=Decimal('50000.00'),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=self.now
        )
        self.t2 = Ticket.objects.create(
            ticket_code="TK-TEST-002",
            box=self.box,
            article_operation=self.ao2,
            quantity=200,
            price_per_unit=Decimal('300.00'),
            total_amount=Decimal('60000.00'),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=self.now
        )

    def test_pricing_sync_updates_tickets_and_worker_balance(self):
        """Ratsenka (narx) o'zgarganda barcha biletlar va xodim balansi avtomatik yangilanishi kerak"""
        # Boshlang'ich balans: 50,000 + 60,000 = 110,000 UZS
        self.assertEqual(self.worker.balance, Decimal('110000.00'))

        # ao1 narxini 500 dan 800 ga o'zgartiramiz
        self.ao1.price_per_unit = Decimal('800.00')
        self.ao1.save()

        self.t1.refresh_from_db()
        self.assertEqual(self.t1.price_per_unit, Decimal('800.00'))
        self.assertEqual(self.t1.total_amount, Decimal('80000.00'))

        # Xodim balansi ham avtomatik 80,000 + 60,000 = 140,000 UZS bo'lishi kerak
        self.assertEqual(self.worker.balance, Decimal('140000.00'))

    def test_model_operation_save_cascades_to_tickets(self):
        """ModelOperation narxi o'zgarganda barcha artikullardagi biletlar narxi yangilanishi kerak"""
        from production.models import ModelOperation
        m_op = ModelOperation.objects.create(
            model=self.model,
            operation=self.op1,
            price_per_unit=Decimal('1200.00'),
            sequence=1
        )
        m_op.save()

        self.ao1.refresh_from_db()
        self.assertEqual(self.ao1.price_per_unit, Decimal('1200.00'))

        self.t1.refresh_from_db()
        self.assertEqual(self.t1.price_per_unit, Decimal('1200.00'))
        self.assertEqual(self.t1.total_amount, Decimal('120000.00'))

    def test_compact_ticket_ids(self):
        """Stikerlar ro'yxati qisqa va ixcham ko'rinishda formatlanishi kerak"""
        from production.excel_reports import compact_ticket_ids
        # Ketma-ket biletlar
        compacted = compact_ticket_ids([self.t1, self.t2])
        self.assertIn("Quti: #1", compacted)
        self.assertIn("jami 2 ta", compacted)

    def test_excel_report_structure_and_formula(self):
        """Excel hisobotidagi 11 ta ustun va Joriy Balans formulasi =G{row}+F{row} to'g'ri ishlashi kerak"""
        import openpyxl
        from production.excel_reports import generate_month_to_date_excel_report

        buf = generate_month_to_date_excel_report(self.today)
        wb = openpyxl.load_workbook(buf, data_only=False)

        # Varaqlar mavjudligi
        self.assertIn("Oylik Umumiy Tabel", wb.sheetnames)
        day_sheet_name = f"{self.today.day:02d}.{self.today.month:02d}"
        self.assertIn(day_sheet_name, wb.sheetnames)
        self.assertIn("Barcha Stikerlar", wb.sheetnames)

        ws_day = wb[day_sheet_name]
        # 4-qatordagi 12 ta ustun sarlavhalari
        expected_headers = [
            "№", "Xodim UID", "F.I.SH", "Patok", "Ishlagan Kunlari",
            "Tikilgan Ishlar (Operatsiyalar)", "Bugungi Ish Haqi (UZS)",
            "Kechagi Balans (UZS)", "Joriy Balans (UZS)",
            "Qutilar Soni", "Stikerlar Soni", "Skanerlangan Stikerlar"
        ]
        actual_headers = [ws_day.cell(row=4, column=col).value for col in range(1, 13)]
        self.assertEqual(actual_headers, expected_headers)

        # Nodirbek Qodirov qatorini topish
        target_row = None
        for r in range(5, ws_day.max_row + 1):
            if ws_day.cell(row=r, column=3).value == "Nodirbek Qodirov":
                target_row = r
                break
        self.assertIsNotNone(target_row, "Nodirbek Qodirov xodimi varaqda topilmadi")
        expected_uid = str(self.worker.user.uid) if (self.worker.user and self.worker.user.uid) else "TK-999"
        self.assertEqual(ws_day.cell(row=target_row, column=2).value, expected_uid)
        # Operatsiyalar xulosasi
        ops_cell_val = ws_day.cell(row=target_row, column=6).value
        self.assertIn("Dazmol", ops_cell_val)
        self.assertIn("Meto", ops_cell_val)
        # Bugungi ish haqi (UZS)
        self.assertEqual(ws_day.cell(row=target_row, column=7).value, 110000.0)
        # Joriy Balans formulasi: =H{target_row}+G{target_row}
        formula_val = ws_day.cell(row=target_row, column=9).value
        self.assertEqual(formula_val, f"=H{target_row}+G{target_row}")

        # 1-varaq: Oylik Umumiy Tabel tekshiruvi (foydalanuvchi talabi bo'yicha 10 ta ustun)
        ws_month = wb["Oylik Umumiy Tabel"]
        expected_month_headers = [
            "№", "Xodim UID", "F.I.SH", "Ishlagan Kunlari",
            "Hisoblangan Ish Haqi (UZS)", "Bonus (UZS)", "Berilgan Avans (UZS)",
            "Magazin (UZS)", "To'langan Oylik (UZS)", "To'lanishi Kerak Qoldiq (UZS)"
        ]
        actual_month_headers = [ws_month.cell(row=4, column=col).value for col in range(1, 11)]
        self.assertEqual(actual_month_headers, expected_month_headers)

        target_m_row = None
        for r in range(5, ws_month.max_row + 1):
            if ws_month.cell(row=r, column=3).value == "Nodirbek Qodirov":
                target_m_row = r
                break
        self.assertIsNotNone(target_m_row, "Nodirbek Qodirov oylik tabelda topilmadi")
        self.assertEqual(ws_month.cell(row=target_m_row, column=2).value, expected_uid)
        self.assertEqual(ws_month.cell(row=target_m_row, column=5).value, 110000.0)  # Hisoblangan Ish Haqi
        self.assertEqual(ws_month.cell(row=target_m_row, column=8).value, 0)         # Magazin
        self.assertEqual(ws_month.cell(row=target_m_row, column=10).value, f"=E{target_m_row}+F{target_m_row}-G{target_m_row}-H{target_m_row}-I{target_m_row}")  # Qoldiq formulasi

        # Oxirgi qator: JAMI qatori formulalari
        last_row = ws_month.max_row
        self.assertEqual(ws_month.cell(row=last_row, column=1).value, "JAMI / BARCHASI:")
        self.assertIn("SUM(E5:", ws_month.cell(row=last_row, column=5).value)
        self.assertIn("SUM(J5:", ws_month.cell(row=last_row, column=10).value)


class ControlQualityInspectionWorkflowTest(TestCase):
    def setUp(self):
        import json
        from django.urls import reverse
        from accounts.models import Worker

        self.control_user = User.objects.create_user(
            username="inspector_otk", password="password123", role=User.Role.CONTROL
        )
        self.worker1 = Worker.objects.create(worker_id="TK-101", first_name="Zuhra", last_name="Karimova")
        self.worker2 = Worker.objects.create(worker_id="TK-102", first_name="Fotima", last_name="Karimova")

        self.article = Article.objects.create(code="ART-OTK-01", name="Palto Qishki")
        self.op1 = Operation.objects.create(code="OP-01", name="Bichish tekshiruvi")
        self.op2 = Operation.objects.create(code="OP-02", name="Tugma qadash")

        self.art_op1 = ArticleOperation.objects.create(
            article=self.article, operation=self.op1, price_per_unit=Decimal("1500.00"), sequence=1
        )
        self.art_op2 = ArticleOperation.objects.create(
            article=self.article, operation=self.op2, price_per_unit=Decimal("2000.00"), sequence=2
        )

        self.order = Order.objects.create(order_number="ORD-OTK-99", article=self.article, total_quantity=50)
        self.box = Box.objects.create(
            order=self.order,
            article=self.article,
            box_number=1,
            box_code="A9-777",
            quantity=50,
            razmer="XXL"
        )

        # 2 tickets: 1 is scanned by worker1, 2 is pending (no worker)
        self.t1 = Ticket.objects.create(
            ticket_code="TK-OTK-1",
            stiker_code="STK00001",
            box=self.box,
            article_operation=self.art_op1,
            quantity=50,
            price_per_unit=Decimal("1500.00"),
            total_amount=Decimal("75000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker1,
            scanned_at=timezone.now()
        )
        self.t2 = Ticket.objects.create(
            ticket_code="TK-OTK-2",
            stiker_code="STK00002",
            box=self.box,
            article_operation=self.art_op2,
            quantity=50,
            price_per_unit=Decimal("2000.00"),
            total_amount=Decimal("100000.00"),
            status=Ticket.Status.PENDING,
            worker=None
        )

    def test_lookup_by_box_code_and_by_stiker_code(self):
        from django.urls import reverse
        self.client.login(username="inspector_otk", password="password123")
        lookup_url = reverse('control:api_lookup')

        # 1. Box code bo'yicha qidiruv (CONTROL stikeri yoki Quti kodi)
        res1 = self.client.get(f"{lookup_url}?code=CONTROL:A9-777")
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1['status'], 'OK')
        self.assertEqual(data1['box']['box_code'], 'A9-777')
        self.assertEqual(data1['box']['total_tickets_count'], 2)
        self.assertEqual(data1['box']['scanned_tickets_count'], 1)
        self.assertEqual(data1['box']['missing_tickets_count'], 1)
        self.assertFalse(data1['box']['all_tickets_scanned'])

        # 2. Quti ID / raqami bo'yicha qidiruv
        res_id = self.client.get(f"{lookup_url}?code={self.box.id}")
        self.assertEqual(res_id.status_code, 200)
        self.assertEqual(res_id.json()['box']['box_code'], 'A9-777')

        # 3. Tikuvchi stiker kodi bo'yicha qidiruv (operatsiya stikeri) — TAQIQLANISHI SHART!
        res2 = self.client.get(f"{lookup_url}?code=STK00002")
        self.assertEqual(res2.status_code, 403)
        data2 = res2.json()
        self.assertEqual(data2['status'], 'PERMISSION_DENIED')
        self.assertIn("Sizda bunday huquq yo'q", data2['message'])

        # 4. TK- prefiksli bilet kodi bo'yicha ham taqiqlanishi shart
        res3 = self.client.get(f"{lookup_url}?code=TK-OTK-1")
        self.assertEqual(res3.status_code, 403)
        self.assertEqual(res3.json()['status'], 'PERMISSION_DENIED')

    def test_submit_blocked_when_operations_unscanned(self):
        import json
        from django.urls import reverse
        self.client.login(username="inspector_otk", password="password123")
        submit_url = reverse('control:api_submit')

        # Egasi yo'q operatsiya (Tugma qadash) bo'lganda tasdiqlash bloklanishi shart!
        res = self.client.post(
            submit_url,
            data=json.dumps({
                'box_id': self.box.id,
                'mode': 'INITIAL',
                'total_qty': 50,
                'second_sort_qty': 0,
                'repair_qty': 0
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertEqual(data['status'], 'UNSCANNED_OPERATIONS')
        self.assertIn("Tugma qadash", data['message'])
        self.assertIn("hech qaysi tikuvchini ayblab bo'lmaydi", data['message'])

        # Quti yopilmaganligini tekshirish
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_controlled)

    def test_submit_success_when_all_operations_scanned(self):
        import json
        from django.urls import reverse
        self.client.login(username="inspector_otk", password="password123")
        submit_url = reverse('control:api_submit')

        # 2-operatsiyani ham tikuvchi skaner qildi
        self.t2.status = Ticket.Status.SCANNED
        self.t2.worker = self.worker2
        self.t2.scanned_at = timezone.now()
        self.t2.save()

        # Endi tasdiqlash va real sonni to'g'irlash (masalan 48 dona, 1 ta 2-sort)
        res = self.client.post(
            submit_url,
            data=json.dumps({
                'box_id': self.box.id,
                'mode': 'INITIAL',
                'total_qty': 48,
                'second_sort_qty': 1,
                'repair_qty': 0
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertTrue(data['is_closed'])

        self.box.refresh_from_db()
        self.assertTrue(self.box.is_controlled)
        self.assertEqual(self.box.quantity, 48)
        self.assertEqual(self.box.controlled_first_sort_qty, 47)
        self.assertEqual(self.box.controlled_second_sort_qty, 1)
        self.assertEqual(self.box.status, Box.Status.COMPLETED)

    def test_lookup_includes_defect_reasons(self):
        from django.urls import reverse
        self.client.login(username="inspector_otk", password="password123")
        lookup_url = reverse('control:api_lookup')

        res = self.client.get(f"{lookup_url}?code=CONTROL:A9-777")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('defect_reasons', data['box'])
        self.assertTrue(len(data['box']['defect_reasons']) > 0)
        reasons_names = [r['name'] for r in data['box']['defect_reasons']]
        self.assertTrue(any("Mato" in name for name in reasons_names))

    def test_submit_with_defect_details_recorded_in_notes(self):
        import json
        from django.urls import reverse
        from production.models import BoxQualityInspectionLog
        self.client.login(username="inspector_otk", password="password123")
        submit_url = reverse('control:api_submit')

        # Tickets scanned
        self.t1.status = Ticket.Status.SCANNED
        self.t1.worker = self.worker1
        self.t1.save()
        self.t2.status = Ticket.Status.SCANNED
        self.t2.worker = self.worker2
        self.t2.save()

        # Submit 2-sort with defect reasons
        res = self.client.post(
            submit_url,
            data=json.dumps({
                'box_id': self.box.id,
                'mode': 'INITIAL',
                'total_qty': 50,
                'second_sort_qty': 2,
                'repair_qty': 3,
                'defect_details': [
                    {
                        'item_number': 1,
                        'operation_ids': [self.t1.id],
                        'operation_names': ["Bichish tekshiruvi"],
                        'workers': ["Zuhra Karimova"],
                        'reason_ids': [1],
                        'reason_names': ["Mato rangida dog' yoki yog' izlari"],
                        'notes': "Old cho'ntak yonida dog'"
                    },
                    {
                        'item_number': 2,
                        'operation_ids': [],
                        'operation_names': [],
                        'workers': [],
                        'reason_ids': [2],
                        'reason_names': ["Mato ignadan yoki pichoqdan teshilgan"],
                        'notes': ""
                    }
                ],
                'repair_details': [
                    {
                        'item_number': 1,
                        'operation_ids': [self.t2.id],
                        'operation_names': ["Tugma qadash"],
                        'workers': ["Fotima Karimova"],
                        'notes': "Tugma qiyshiq"
                    }
                ]
            }),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertFalse(data['is_closed']) # repair_qty=3, so not closed

        # Check inspection log
        log = BoxQualityInspectionLog.objects.filter(box=self.box).latest('created_at')
        self.assertEqual(log.first_sort_qty, 45) # 50 - 2 - 3
        self.assertEqual(log.second_sort_qty, 2)
        self.assertEqual(log.repair_qty, 3)
        self.assertIn("--- 2-SORT SABABLARI ---", log.notes)
        self.assertIn("Mato rangida dog'", log.notes)
        self.assertIn("Bichish tekshiruvi", log.notes)
        self.assertIn("Zuhra Karimova", log.notes)
        self.assertIn("Mato ignadan yoki pichoqdan teshilgan", log.notes)
        self.assertIn("--- TA'MIR OPERATSIYALARI ---", log.notes)
        self.assertIn("Tugma qadash", log.notes)
        self.assertIn("Fotima Karimova", log.notes)
        self.assertIn("Tugma qiyshiq", log.notes)

    def test_repair_return_cycle_until_closed_and_completed_mode(self):
        import json
        from django.urls import reverse
        from production.models import BoxQualityInspectionLog
        self.client.login(username="inspector_otk", password="password123")
        lookup_url = reverse('control:api_lookup')
        submit_url = reverse('control:api_submit')

        # 1. Quti ta'mirda (3 dona ta'mirda)
        self.box.quantity = 50
        self.box.controlled_first_sort_qty = 45
        self.box.controlled_second_sort_qty = 2
        self.box.controlled_repair_qty = 3
        self.box.is_controlled = False
        self.box.status = Box.Status.IN_PROGRESS
        self.box.save()

        # Lookup REPAIR_RETURN rejimini qaytaradi
        res_lookup = self.client.get(f"{lookup_url}?code=CONTROL:A9-777")
        self.assertEqual(res_lookup.status_code, 200)
        data_lookup = res_lookup.json()
        self.assertEqual(data_lookup['box']['mode'], 'REPAIR_RETURN')
        self.assertEqual(data_lookup['box']['controlled_repair_qty'], 3)

        # 2. 1-marta ta'mirdan qaytish: 3 tadan 1 ta tuzaldi, 1 ta 2-sort, 1 ta qayta ta'mir
        res_sub1 = self.client.post(
            submit_url,
            data=json.dumps({
                'box_id': self.box.id,
                'mode': 'REPAIR_RETURN',
                'second_sort_qty': 1,
                'repair_qty': 1,
                'defect_details': [{
                    'item_number': 1,
                    'operation_names': ["Tugma qadash"],
                    'reason_names': ["Mato ignadan yoki pichoqdan teshilgan"],
                    'workers': ["Fotima Karimova"],
                    'notes': "Tuzatib bo'lmadi"
                }],
                'repair_details': [{
                    'item_number': 1,
                    'operation_names': ["Bichish tekshiruvi"],
                    'workers': ["Zuhra Karimova"],
                    'notes': "Qayta tikish kerak"
                }]
            }),
            content_type='application/json'
        )
        self.assertEqual(res_sub1.status_code, 200)
        data_sub1 = res_sub1.json()
        self.assertFalse(data_sub1['is_closed'])
        self.assertEqual(data_sub1['repair_qty'], 1)

        self.box.refresh_from_db()
        self.assertFalse(self.box.is_controlled)
        self.assertEqual(self.box.controlled_first_sort_qty, 46) # 45 + 1 tuzaldi
        self.assertEqual(self.box.controlled_second_sort_qty, 3) # 2 + 1 nuqson
        self.assertEqual(self.box.controlled_repair_qty, 1)

        # Jurnal yozuvlarini tekshirish
        log1 = BoxQualityInspectionLog.objects.filter(box=self.box).latest('created_at')
        self.assertEqual(log1.action_type, BoxQualityInspectionLog.ActionType.REPAIR_RETURN)
        self.assertIn("--- TA'MIRDAN 2-SORTGA O'TGANLAR ---", log1.notes)
        self.assertIn("--- QAYTA TA'MIRGA YUBORILGANLAR ---", log1.notes)

        # 3. 2-marta ta'mirdan qaytish: oxirgi 1 dona ham tuzaldi (0 nuqson, 0 ta'mir)
        res_sub2 = self.client.post(
            submit_url,
            data=json.dumps({
                'box_id': self.box.id,
                'mode': 'REPAIR_RETURN',
                'second_sort_qty': 0,
                'repair_qty': 0,
            }),
            content_type='application/json'
        )
        self.assertEqual(res_sub2.status_code, 200)
        data_sub2 = res_sub2.json()
        self.assertTrue(data_sub2['is_closed'])
        self.assertEqual(data_sub2['repair_qty'], 0)

        self.box.refresh_from_db()
        self.assertTrue(self.box.is_controlled)
        self.assertEqual(self.box.status, Box.Status.COMPLETED)
        self.assertEqual(self.box.controlled_first_sort_qty, 47) # 46 + 1 = 47
        self.assertEqual(self.box.controlled_second_sort_qty, 3)
        self.assertEqual(self.box.controlled_repair_qty, 0)
        self.assertEqual(self.box.controlled_first_sort_qty + self.box.controlled_second_sort_qty, 50)

        # 4. Yopilgan qutini qayta skanerlaganda COMPLETED rejimida chiqadi
        res_lookup2 = self.client.get(f"{lookup_url}?code=CONTROL:A9-777")
        self.assertEqual(res_lookup2.status_code, 200)
        data_lookup2 = res_lookup2.json()
        self.assertEqual(data_lookup2['box']['mode'], 'COMPLETED')


class SewingStatisticsFeatureTest(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            username="superadmin_stat",
            password="password123",
            role=User.Role.SUPER_ADMIN
        )
        self.manager = User.objects.create_user(
            username="manager_stat",
            password="password123",
            role=User.Role.MANAGER
        )
        self.cutter = User.objects.create_user(
            username="cutter_stat",
            password="password123",
            role=User.Role.CUTTER
        )
        self.customer = Customer.objects.create(name="Stat Customer LLC")
        self.model = ProductModel.objects.create(name="Stat Model")
        self.article = Article.objects.create(
            code="STAT-ART-01",
            name="Polo Shirt Stat",
            model=self.model,
            daily_norm=200
        )
        # Operations: Op1 (Tikuv), Op2 (Dazmol)
        self.op1 = Operation.objects.create(name="Bichim Tikish", code="OP_TIK_01")
        self.op2 = Operation.objects.create(name="DAZMOL QILISH", code="OP_DAZMOL_01")
        self.ao1 = ArticleOperation.objects.create(article=self.article, operation=self.op1, sequence=1, price_per_unit=500)
        self.ao2 = ArticleOperation.objects.create(article=self.article, operation=self.op2, sequence=2, price_per_unit=300)

        # Worker
        self.worker = Worker.objects.create(first_name="Malika", last_name="Tikuvchi", worker_id="999111")

        # Order & OrderItem
        self.order = Order.objects.create(order_number="ORD-STAT-001", customer=self.customer, client_name=self.customer.name, status=Order.Status.IN_PROGRESS)
        self.item = OrderItem.objects.create(order=self.order, article=self.article, quantity=100)
        self.size_m = OrderItemSize.objects.create(order_item=self.item, size_name="M", planned_quantity=60)
        self.size_l = OrderItemSize.objects.create(order_item=self.item, size_name="L", planned_quantity=40)

        # Boxes for Size M:
        # Box 1: 30 qty, 1 ticket scanned (sewing entered, not dazmol, not controlled)
        self.box1 = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="M",
            box_number=1,
            box_code="STAT-BX-01",
            quantity=30,
            pastal_number="P-STAT-1"
        )
        self.t1_1 = Ticket.objects.create(box=self.box1, article_operation=self.ao1, quantity=30, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=2, scanned_at=timezone.now())
        self.t1_2 = Ticket.objects.create(box=self.box1, article_operation=self.ao2, quantity=30, price_per_unit=300, status=Ticket.Status.PENDING)

        # Box 2: 30 qty, 2 tickets scanned (both Op1 and Op2 Dazmol scanned -> waiting control)
        self.box2 = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="M",
            box_number=2,
            box_code="STAT-BX-02",
            quantity=30,
            pastal_number="P-STAT-1"
        )
        self.t2_1 = Ticket.objects.create(box=self.box2, article_operation=self.ao1, quantity=30, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=2, scanned_at=timezone.now())
        self.t2_2 = Ticket.objects.create(box=self.box2, article_operation=self.ao2, quantity=30, price_per_unit=300, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=3, scanned_at=timezone.now())

        # Box 3 for Size L: 40 qty, fully controlled (1-sort 38, 2-sort 2)
        self.box3 = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="L",
            box_number=3,
            box_code="STAT-BX-03",
            quantity=40,
            pastal_number="P-STAT-2",
            is_controlled=True,
            controlled_first_sort_qty=38,
            controlled_second_sort_qty=2,
            controlled_by=self.superadmin,
            controlled_at=timezone.now(),
            status=Box.Status.COMPLETED
        )
        self.t3_1 = Ticket.objects.create(box=self.box3, article_operation=self.ao1, quantity=40, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=1, scanned_at=timezone.now())
        self.t3_2 = Ticket.objects.create(box=self.box3, article_operation=self.ao2, quantity=40, price_per_unit=300, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=1, scanned_at=timezone.now())

    def test_permission_denied_for_regular_and_anonymous(self):
        # Anonymous user redirected to login
        res_anon = self.client.get(reverse('production:sewing_statistics_orders'))
        self.assertEqual(res_anon.status_code, 302)

        # Cutter redirected
        self.client.login(username="cutter_stat", password="password123")
        res_cutter = self.client.get(reverse('production:sewing_statistics_orders'))
        self.assertEqual(res_cutter.status_code, 302)

        # Manager allowed
        self.client.login(username="manager_stat", password="password123")
        res_mgr = self.client.get(reverse('production:sewing_statistics_orders'))
        self.assertEqual(res_mgr.status_code, 200)

        # Superadmin allowed
        self.client.login(username="superadmin_stat", password="password123")
        res_admin = self.client.get(reverse('production:sewing_statistics_orders'))
        self.assertEqual(res_admin.status_code, 200)

    def test_sewing_statistics_orders_view_kpis(self):
        self.client.login(username="superadmin_stat", password="password123")
        url = reverse('production:sewing_statistics_orders')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ORD-STAT-001")
        self.assertContains(res, "Stat Customer LLC")

        # Context KPI checks
        kpis = res.context['overall_kpis']
        self.assertEqual(kpis['total_orders'], 1)
        self.assertEqual(kpis['total_planned'], 100) # 60 M + 40 L
        self.assertEqual(kpis['total_boxed'], 100)   # 30 + 30 + 40
        self.assertEqual(kpis['total_entered_sewing'], 100) # all 3 boxes have >=1 scanned ticket
        self.assertEqual(kpis['total_dazmol'], 70)   # box2 (30) + box3 (40)
        self.assertEqual(kpis['total_waiting_control'], 30) # box2 is 100% scanned but not yet controlled
        self.assertEqual(kpis['total_first_sort'], 38)
        self.assertEqual(kpis['total_second_sort'], 2)

    def test_sewing_statistics_order_models_view(self):
        self.client.login(username="superadmin_stat", password="password123")
        url = reverse('production:sewing_statistics_order_models', kwargs={'order_id': self.order.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "STAT-ART-01")
        self.assertContains(res, "Polo Shirt Stat")

        models_data = res.context['models_data']
        self.assertEqual(len(models_data), 1)
        m0 = models_data[0]
        self.assertEqual(m0['planned_qty'], 100)
        self.assertEqual(m0['entered_sewing_qty'], 100)
        self.assertEqual(m0['dazmol_qty'], 70)
        self.assertEqual(m0['waiting_control_qty'], 30)
        self.assertEqual(m0['first_sort_qty'], 38)
        self.assertEqual(m0['second_sort_qty'], 2)

    def test_sewing_statistics_model_detail_view(self):
        self.client.login(username="superadmin_stat", password="password123")
        url = reverse('production:sewing_statistics_model_detail', kwargs={'order_id': self.order.id, 'order_item_id': self.item.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "STAT-ART-01")

        sizes = res.context['sizes_breakdown']
        self.assertEqual(len(sizes), 2)

        # Find Size M and Size L
        size_m_data = next(s for s in sizes if s['size_name'] == 'M')
        self.assertEqual(size_m_data['planned_qty'], 60)
        self.assertEqual(size_m_data['total_boxed_qty'], 60) # 2 boxes * 30
        self.assertEqual(size_m_data['entered_sewing_qty'], 60)
        self.assertEqual(size_m_data['dazmol_qty'], 30)      # only box2 reached dazmol
        self.assertEqual(size_m_data['waiting_control_qty'], 30) # box2 is waiting control
        self.assertEqual(size_m_data['first_sort_qty'], 0)

        size_l_data = next(s for s in sizes if s['size_name'] == 'L')
        self.assertEqual(size_l_data['planned_qty'], 40)
        self.assertEqual(size_l_data['entered_sewing_qty'], 40)
        self.assertEqual(size_l_data['dazmol_qty'], 40)
        self.assertEqual(size_l_data['waiting_control_qty'], 0)
        self.assertEqual(size_l_data['first_sort_qty'], 38)
        self.assertEqual(size_l_data['second_sort_qty'], 2)

    def test_api_sewing_statistics_size_boxes_html_and_json(self):
        self.client.login(username="superadmin_stat", password="password123")
        url = reverse('production:api_sewing_statistics_size_boxes')

        # 1. HTML partial for Size M
        res_html = self.client.get(url, {'order_item_id': self.item.id, 'size_name': 'M'})
        self.assertEqual(res_html.status_code, 200)
        self.assertContains(res_html, "STAT-BX-01")
        self.assertContains(res_html, "STAT-BX-02")
        self.assertContains(res_html, "Malika Tikuvchi")
        self.assertContains(res_html, "Bichim Tikish")
        self.assertContains(res_html, "DAZMOL QILISH")
        self.assertContains(res_html, "Kontrolda Kutmoqda") # box 2 has this badge

        # 2. JSON format for Size M
        res_json = self.client.get(url, {'order_item_id': self.item.id, 'size_name': 'M', 'format': 'json'})
        self.assertEqual(res_json.status_code, 200)
        data = res_json.json()
        self.assertEqual(data['status'], 'OK')
        self.assertEqual(data['size_name'], 'M')
        self.assertEqual(data['summary']['total_boxes'], 2)
        self.assertEqual(data['summary']['entered_sewing_boxes'], 2)
        self.assertEqual(data['summary']['passed_dazmol_boxes'], 1)
        self.assertEqual(data['summary']['waiting_control_boxes'], 1)

        # 3. HTML partial for Size L
        res_l = self.client.get(url, {'order_item_id': self.item.id, 'size_name': 'L'})
        self.assertEqual(res_l.status_code, 200)
        self.assertContains(res_l, "STAT-BX-03")
        self.assertContains(res_l, "1-sort: 38 ta")
        self.assertContains(res_l, "2-sort: 2 ta")

    def test_calculate_pipeline_balance_and_variance_reconciliation(self):
        from production.sewing_statistics_views import calculate_pipeline_balance, get_dazmol_operation_ids_for_article

        # Box 1: 30 dona, tikimga kirgan, dazmoldan o'tmagan
        # Box 2: 30 dona, dazmoldan o'tgan, 100% tikilgan -> kontrolda kutmoqda
        # Box 3: 40 dona, kontroldan o'tgan -> 38 ta 1-sort, 2 ta 2-sort (jami 40 ta yopilgan)

        # Yangi Box 4: 50 dona, dazmoldan o'tgan, lekin ticket 1 ta pending -> kontrolgacha yetib bormagan (oraliqda)
        box4 = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="XL",
            box_number=4,
            box_code="STAT-BX-04",
            quantity=50
        )
        Ticket.objects.create(box=box4, article_operation=self.ao1, quantity=50, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=10, scanned_at=timezone.now())
        Ticket.objects.create(box=box4, article_operation=self.ao2, quantity=50, price_per_unit=300, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=10, scanned_at=timezone.now())
        # Op 3: pending operatsiya (dazmol o'tgan, lekin kontrolga hali yetmagan)
        op3 = Operation.objects.create(name="Upakovka Oldi", code="OP_UPAK_01")
        ao3 = ArticleOperation.objects.create(article=self.article, operation=op3, sequence=3, price_per_unit=200)
        Ticket.objects.create(box=box4, article_operation=ao3, quantity=50, price_per_unit=200, status=Ticket.Status.PENDING)

        dazmol_ids = get_dazmol_operation_ids_for_article(self.article)
        balance = calculate_pipeline_balance([self.box1, self.box2, self.box3, box4], planned_qty=150, cut_qty=150, dazmol_ao_ids=dazmol_ids)

        # 1. Kesim: 150 ta
        self.assertEqual(balance['cut_qty'], 150)
        # 2. Tikimda: 150 ta (barcha 4 ta qutida stiker urilgan)
        self.assertEqual(balance['entered_sewing_qty'], 150)
        # 3. Dazmoldan o'tdi: Box 2 (30) + Box 3 (40) + Box 4 (50) = 120 ta
        self.assertEqual(balance['dazmol_qty'], 120)
        # 4. Kontrolgacha yetib bormagan / Oraliqda turgan: Box 4 (50 ta)
        self.assertEqual(balance['unreached_control_qty'], 50)
        self.assertEqual(balance['unreached_control_boxes'], 1)
        # 5. Controlda kutmoqda: Box 2 (30 ta)
        self.assertEqual(balance['waiting_control_qty'], 30)
        self.assertEqual(balance['waiting_control_boxes'], 1)
        # 6. Controldan o'tib yopilgan: Box 3 (1-sort: 38, 2-sort: 2)
        self.assertEqual(balance['controlled_boxes_count'], 1)
        self.assertEqual(balance['first_sort_qty'], 38)
        self.assertEqual(balance['second_sort_qty'], 2)
        self.assertEqual(balance['total_closed_qty'], 40)
        # 7. Farq (Variance): nominal 40, actual 40 -> 0
        self.assertEqual(balance['net_variance'], 0)

    def test_repair_monitoring_and_cycles_and_10_percent_warning(self):
        from production.models import BoxQualityInspectionLog
        from production.sewing_statistics_views import calculate_pipeline_balance, get_patoks_scrap_and_warning_report

        # Box 5 yaratish: 50 dona, 10 dona ta'mirga ketgan
        box5 = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="XXL",
            box_number=5,
            box_code="STAT-BX-05",
            quantity=50,
            controlled_repair_qty=10,
            controlled_first_sort_qty=35,
            controlled_second_sort_qty=5
        )
        Ticket.objects.create(box=box5, article_operation=self.ao1, quantity=50, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=10, scanned_at=timezone.now())

        # 1-marta ta'mirga borgan log
        log1 = BoxQualityInspectionLog.objects.create(
            box=box5,
            inspector=self.superadmin,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=50,
            first_sort_qty=35,
            second_sort_qty=5,
            repair_qty=10,
            notes="Nuqson: Yeng choki qiyshiq"
        )

        # 2-marta ta'mirdan qaytib, qayta ta'mirga tushgan log (Re-repair)
        log2 = BoxQualityInspectionLog.objects.create(
            box=box5,
            inspector=self.superadmin,
            action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
            inspected_qty=10,
            first_sort_qty=0,
            second_sort_qty=2,
            repair_qty=8,
            notes="Qayta ta'mir: Yoqa choki ochilib ketgan"
        )

        # Patoklar hisoboti (Oxirgi 7 kunlik)
        report = get_patoks_scrap_and_warning_report(time_filter='week', order_id=self.order.id)
        report_list = report['report_list']
        self.assertTrue(len(report_list) >= 1)

        # Patok 10 (K10) ni tekshirish
        p10 = next((p for p in report_list if p['patok_code'] == 'K10'), None)
        self.assertIsNotNone(p10)
        self.assertEqual(p10['patok_name'], "K10-Patok")
        self.assertEqual(p10['first_sort_qty'], 35)
        # second_sort_qty: 5 (birlamchi) + 2 (ta'mir qaytishidagi) = 7 ta
        self.assertEqual(p10['second_sort_qty'], 7)
        # total evaluated: 35 + 7 = 42 ta
        # brak foizi: 7 / 42 * 100 = 16.7% (>= 10.0%) -> Ogohlantirish chiqishi shart!
        self.assertGreaterEqual(p10['brak_rate_pct'], 10.0)
        self.assertTrue(p10['is_warning'])
        self.assertTrue(report['has_any_warning'])

    def test_sewing_statistics_repairs_view_and_filters(self):
        from production.models import BoxQualityInspectionLog

        # Ta'mirdagi quti yaratish
        box_rep = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="S",
            box_number=6,
            box_code="STAT-BX-REP-01",
            quantity=40,
            controlled_repair_qty=6
        )
        Ticket.objects.create(box=box_rep, article_operation=self.ao1, quantity=40, price_per_unit=500, worker=self.worker, status=Ticket.Status.SCANNED, screen_number=10, scanned_at=timezone.now())

        BoxQualityInspectionLog.objects.create(
            box=box_rep,
            inspector=self.superadmin,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=40,
            first_sort_qty=30,
            second_sort_qty=4,
            repair_qty=6,
            notes="Ta'mir operatsiyasi: Yoqa tikish"
        )

        self.client.login(username="superadmin_stat", password="password123")
        url = reverse('production:sewing_statistics_repairs')

        # 1. Bosh sahifa (active repairs)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Ta'mir Jarayoni", res.content.decode('utf-8'))
        self.assertContains(res, "STAT-BX-REP-01")
        self.assertContains(res, "Yoqa tikish")
        self.assertContains(res, "K10-Patok")

        # 2. Context KPI lari
        self.assertGreaterEqual(res.context['active_repair_boxes_count'], 1)
        self.assertGreaterEqual(res.context['active_repair_units_count'], 6)

        # 3. Status filter: resolved
        res_resolved = self.client.get(url, {'status': 'resolved'})
        self.assertEqual(res_resolved.status_code, 200)

        # 4. Search query
        res_search = self.client.get(url, {'q': 'STAT-BX-REP-01'})
        self.assertEqual(res_search.status_code, 200)
        self.assertContains(res_search, "STAT-BX-REP-01")

    def test_patok_logins_and_inline_repair_boxes_details(self):
        """Patok loginlari (patokk1..patokk13, patoku1..patoku27) va ta'mirdagi qutilar batafsil tafsiloti tekshiruvi"""
        from production.models import BoxQualityInspectionLog
        from production.services import get_patok_login, parse_patok_number
        from production.sewing_statistics_views import get_patoks_scrap_and_warning_report

        # 1. Login nomlash funksiyalari
        self.assertEqual(get_patok_login(1), "patokk1")
        self.assertEqual(get_patok_login(10), "patokk10")
        self.assertEqual(get_patok_login(13), "patokk13")
        self.assertEqual(get_patok_login(14), "patoku1")
        self.assertEqual(get_patok_login(15), "patoku2")
        self.assertEqual(get_patok_login(40), "patoku27")

        # 2. Parsing tekshiruvi
        self.assertEqual(parse_patok_number("patokk1"), 1)
        self.assertEqual(parse_patok_number("patokk10"), 10)
        self.assertEqual(parse_patok_number("patokk13"), 13)
        self.assertEqual(parse_patok_number("patoku1"), 14)
        self.assertEqual(parse_patok_number("patoku27"), 40)
        self.assertEqual(parse_patok_number("EKRAN1"), 1)
        self.assertEqual(parse_patok_number("K10"), 10)
        self.assertEqual(parse_patok_number("U1"), 14)

        # Ta'mirdagi test qutisi yaratish (K10 uchun)
        box_rep = Box.objects.create(
            order=self.order,
            article=self.article,
            razmer="S",
            box_number=106,
            box_code="STAT-BX-REP-10",
            quantity=40,
            controlled_repair_qty=6
        )
        Ticket.objects.create(
            box=box_rep,
            article_operation=self.ao1,
            quantity=40,
            price_per_unit=500,
            worker=self.worker,
            status=Ticket.Status.SCANNED,
            screen_number=10,
            scanned_at=timezone.now()
        )
        BoxQualityInspectionLog.objects.create(
            box=box_rep,
            inspector=self.superadmin,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=40,
            first_sort_qty=30,
            second_sort_qty=4,
            repair_qty=6,
            notes="Ta'mir operatsiyasi: Yoqa tikish"
        )

        # 3. Hisobotda repair_boxes va patok_login mavjudligi
        rep = get_patoks_scrap_and_warning_report(time_filter='all')
        self.assertTrue(len(rep['report_list']) > 0)
        
        # K10 patokini tekshiramiz
        k10_item = next((item for item in rep['report_list'] if item['patok_code'] == 'K10'), None)
        self.assertIsNotNone(k10_item)
        self.assertEqual(k10_item['patok_login'], "patokk10")
        self.assertGreaterEqual(len(k10_item['repair_boxes']), 1)
        
        rb = k10_item['repair_boxes'][0]
        self.assertEqual(rb['repair_qty'], 6)
        self.assertIn("Stat Model", rb['model_name'])
        self.assertGreaterEqual(rb['repair_cycles'], 1)
        self.assertIn("Yoqa tikish", rb['notes'])

        # 4. Web sahifada login va batafsil drawer ko'rinishi
        self.client.login(username="superadmin_stat", password="password123")
        res = self.client.get(reverse('production:sewing_statistics_repairs'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "patokk10")
        self.assertContains(res, "patok-drawer-K10")
        self.assertContains(res, "Batafsil")







