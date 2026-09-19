from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from .models import (
    Article, Operation, ArticleOperation, Order, OrderItem, Box, Ticket,
    Customer, ProductModel, OrderItemSize, CuttingBatch, CuttingBatchItem
)
from .services import allocate_ticket_quantities, generate_box_tickets
from accounts.models import User


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

    def test_cutting_add_batches_and_progress_tracking(self):
        # Setup order with S: 100, M: 200
        order = Order.objects.create(order_number="ORD-CUT-01", customer=self.customer, client_name=self.customer.name)
        art = Article.objects.create(code="ART-CUT-01", name="Shirt", model=self.model)
        ArticleOperation.objects.create(article=art, operation=self.op, price_per_unit=Decimal("1000"), sequence=1)
        item = OrderItem.objects.create(order=order, article=art, quantity=300)
        size_s = OrderItemSize.objects.create(order_item=item, size_name="S", planned_quantity=100)
        size_m = OrderItemSize.objects.create(order_item=item, size_name="M", planned_quantity=200)

        self.client.login(username="cutter_user", password="password123")

        # 1. Kesimchi enters Kesim 1 (S: 50, M: 100)
        add_url = reverse('cutting_add_batch', kwargs={'order_id': order.id, 'order_item_id': item.id})
        res1 = self.client.post(add_url, {
            'cutter_name': 'Ali Cutter',
            'notes': 'Part 1 started',
            f'size_qty_{size_s.id}': '50',
            f'size_qty_{size_m.id}': '100',
        }, follow=True)
        self.assertEqual(res1.status_code, 200)

        b1 = CuttingBatch.objects.get(order_item=item, batch_number=1)
        self.assertEqual(b1.name, "Kesim 1")
        self.assertEqual(b1.total_quantity, 150)
        size_s.refresh_from_db()
        size_m.refresh_from_db()
        self.assertEqual(size_s.total_cut_quantity, 50)
        self.assertEqual(size_s.cut_percentage, 50.0)
        self.assertEqual(size_m.total_cut_quantity, 100)
        self.assertEqual(size_m.cut_percentage, 50.0)
        self.assertEqual(item.overall_cut_percentage, 50.0)

        # 2. Kesimchi enters Kesim 2 (M: 100) -> M reaches 100% (200 / 200)
        res2 = self.client.post(add_url, {
            'cutter_name': 'Ali Cutter',
            'notes': 'Part 2 finished M',
            f'size_qty_{size_s.id}': '0',
            f'size_qty_{size_m.id}': '100',
        }, follow=True)
        self.assertEqual(res2.status_code, 200)

        b2 = CuttingBatch.objects.get(order_item=item, batch_number=2)
        self.assertEqual(b2.name, "Kesim 2")
        size_m.refresh_from_db()
        self.assertEqual(size_m.total_cut_quantity, 200)
        self.assertEqual(size_m.cut_percentage, 100.0)
        self.assertEqual(size_m.remaining_to_cut_quantity, 0)

        # 3. Split Kesim 2 (M: 100) into 2 boxes of 50 each
        b2_item_m = b2.items.get(order_item_size=size_m)
        split_url = reverse('cutting_split_and_create_boxes', kwargs={'batch_id': b2.id})
        res3 = self.client.post(split_url, {
            'batch_item_id': b2_item_m.id,
            'split_mode': 'equal_splits',
            'splits_count': '2'
        }, follow=True)
        self.assertEqual(res3.status_code, 200)

        b2_item_m.refresh_from_db()
        self.assertEqual(b2_item_m.boxes_created_qty, 100)
        self.assertEqual(b2_item_m.remaining_to_box, 0)

        boxes = Box.objects.filter(order=order, razmer='M')
        self.assertEqual(boxes.count(), 2)
        for b in boxes:
            self.assertEqual(b.quantity, 50)
            self.assertEqual(b.razmer, 'M')
            self.assertEqual(b.tickets.count(), 1)
            t = b.tickets.first()
            self.assertEqual(t.box.razmer, 'M')

    def test_permissions_manager_and_cutter(self):
        # Regular user cannot access manager or cutting
        user = User.objects.create_user(username="normal_user", password="password123", role=User.Role.USER)
        self.client.login(username="normal_user", password="password123")

        res = self.client.get(reverse('manager_dashboard'))
        self.assertEqual(res.status_code, 302)

        res = self.client.get(reverse('cutting_dashboard'))
        self.assertEqual(res.status_code, 302)

        # Superadmin can access both
        self.client.login(username="superadmin_test", password="password123")
        res_mgr = self.client.get(reverse('manager_dashboard'))
        self.assertEqual(res_mgr.status_code, 200)
        res_cut = self.client.get(reverse('cutting_dashboard'))
        self.assertEqual(res_cut.status_code, 200)





