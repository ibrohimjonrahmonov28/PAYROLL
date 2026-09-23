import json
from decimal import Decimal
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import User, Worker, WorkerPayout, DailyWorkerClosing
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket, OrderItem, ProductModel


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
        self.order_item = OrderItem.objects.create(
            order=self.order,
            article=self.article,
            quantity=100
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

    def test_superadmin_create_order_with_model_and_checked_operations(self):
        self.client.force_login(self.superadmin)
        post_data = {
            'order_number': 'ORD-NEW-2026',
            'client_name': 'Katta Tikuvchilik MCHJ',
            'model_name': 'Klassik Polo Futbolka',
            'model_code': 'POLO-NEW',
            'quantity': '1000',
            'selected_operations': [self.operation.id],
            f'price_{self.operation.id}': '1250.00',
        }
        res = self.client.post(reverse('superadmin_orders_list'), post_data)
        self.assertEqual(res.status_code, 302)

        order = Order.objects.get(order_number='ORD-NEW-2026')
        self.assertEqual(order.client_name, 'Katta Tikuvchilik MCHJ')
        self.assertEqual(order.items.count(), 1)

        item = order.items.first()
        self.assertEqual(item.article.code, 'POLO-NEW')
        self.assertEqual(item.quantity, 1000)

        # Check operation was linked with price 1250
        art_op = item.article.article_operations.get(operation=self.operation)
        self.assertEqual(art_op.price_per_unit, Decimal("1250.00"))
        self.assertEqual(item.unit_total_rate, Decimal("1250.00"))
        self.assertEqual(item.total_cost, Decimal("1250000.00"))

    def test_superadmin_order_detail_view(self):
        self.client.force_login(self.superadmin)
        res = self.client.get(reverse('superadmin_order_detail', args=[self.order.id]))
        self.assertEqual(res.status_code, 200)
        self.assertIn("ORD-TEST-SA", res.content.decode('utf-8'))
        self.assertIn("Test Model", res.content.decode('utf-8'))

    def test_superadmin_order_add_second_model(self):
        self.client.force_login(self.superadmin)
        add_data = {
            'model_name': 'Ikkinchi Model',
            'model_code': 'MOD-2',
            'quantity': '500',
            'selected_operations': [self.operation.id],
            f'price_{self.operation.id}': '900.00'
        }
        res = self.client.post(reverse('superadmin_order_add_model', args=[self.order.id]), add_data)
        self.assertEqual(res.status_code, 302)

        self.assertEqual(self.order.items.count(), 2)
        mod2_item = self.order.items.get(article__code='MOD-2')
        self.assertEqual(mod2_item.quantity, 500)
        self.assertEqual(mod2_item.unit_total_rate, Decimal("900.00"))

    def test_superadmin_order_edit_and_model_management(self):
        self.client.force_login(self.superadmin)
        
        # 1. Edit order client & status
        edit_order_data = {
            'client_name': 'Yangilangan Mijoz MCHJ',
            'deadline': '2026-10-15',
            'status': 'IN_PROGRESS'
        }
        res1 = self.client.post(reverse('superadmin_order_edit', args=[self.order.id]), edit_order_data)
        self.assertEqual(res1.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.client_name, 'Yangilangan Mijoz MCHJ')

        # 2. Edit model quantity & name
        edit_model_data = {
            'model_name': 'Super Model Yangilandi',
            'quantity': '250'
        }
        res2 = self.client.post(
            reverse('superadmin_order_model_edit', args=[self.order.id, self.order_item.id]),
            edit_model_data
        )
        self.assertEqual(res2.status_code, 302)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 250)
        self.assertEqual(self.order_item.article.name, 'Super Model Yangilandi')

        # 3. Update operation price
        update_op_data = {
            'article_operation_id': self.art_op.id,
            'price_per_unit': '1400.00',
            'sequence': '1'
        }
        res3 = self.client.post(
            reverse('superadmin_order_model_update_operation', args=[self.order.id, self.order_item.id]),
            update_op_data
        )
        self.assertEqual(res3.status_code, 302)
        self.art_op.refresh_from_db()
        self.assertEqual(self.art_op.price_per_unit, Decimal("1400.00"))

        # 4. Check KPI calculations
        # Total cost = 250 * 1400 = 350,000 UZS
        self.assertEqual(self.order.total_order_cost, Decimal("350000.00"))
        self.assertEqual(self.order.average_unit_cost, Decimal("1400.00"))

        # 5. Add new operation to model
        new_op = Operation.objects.create(code="OP-EXTRA", name="Extra Tikish")
        add_op_data = {
            'operation_id': new_op.id,
            'price_per_unit': '600.00',
            'sequence': '2'
        }
        res4 = self.client.post(
            reverse('superadmin_order_model_add_operation', args=[self.order.id, self.order_item.id]),
            add_op_data
        )
        self.assertEqual(res4.status_code, 302)
        self.assertEqual(self.order_item.article.article_operations.count(), 2)

        # 6. Delete operation from model
        new_ao = ArticleOperation.objects.get(article=self.order_item.article, operation=new_op)
        res5 = self.client.post(
            reverse('superadmin_order_model_delete_operation', args=[self.order.id, self.order_item.id, new_ao.id])
        )
        self.assertEqual(res5.status_code, 302)
        self.assertEqual(self.order_item.article.article_operations.count(), 1)

    def test_user_uid_qr_and_management(self):
        self.client.force_login(self.superadmin)

        # 1. Automatic 6-digit UID and QR code on user creation
        new_user = User.objects.create_user(
            username="test_worker_user",
            first_name="Jasur",
            last_name="Bekzodov",
            password="pass12345Password!",
            role=User.Role.MASTER
        )
        self.assertIsNotNone(new_user.uid)
        self.assertEqual(len(new_user.uid), 6)
        self.assertTrue(new_user.uid.isdigit())
        self.assertTrue(bool(new_user.qr_code))

        # 2. Create user via SuperAdmin UI POST
        create_res = self.client.post(reverse('superadmin_users'), {
            'action': 'create_user',
            'username': 'noviy_master',
            'first_name': 'Botir',
            'last_name': 'Qodirov',
            'password': 'StrongPass123!',
            'role': User.Role.MASTER,
            'phone_number': '+998901112233'
        })
        self.assertEqual(create_res.status_code, 302)
        created_user = User.objects.get(username='noviy_master')
        self.assertEqual(created_user.first_name, 'Botir')
        self.assertEqual(created_user.last_name, 'Qodirov')
        self.assertEqual(len(created_user.uid), 6)
        self.assertTrue(bool(created_user.qr_code))

        # 3. Change role via SuperAdmin UI
        role_res = self.client.post(reverse('superadmin_users'), {
            'action': 'change_role',
            'user_id': created_user.id,
            'role': User.Role.ADMIN
        })
        self.assertEqual(role_res.status_code, 302)
        created_user.refresh_from_db()
        self.assertEqual(created_user.role, User.Role.ADMIN)
        self.assertTrue(created_user.is_staff)

        # 4. Delete user via SuperAdmin UI
        del_res = self.client.post(reverse('superadmin_users'), {
            'action': 'delete_user',
            'user_id': created_user.id
        })
        self.assertEqual(del_res.status_code, 302)
        self.assertFalse(User.objects.filter(username='noviy_master').exists())

    def test_worker_auto_creates_oddiy_user(self):
        # When a new worker is created, an associated 'Oddiy User' (USER role) must be auto-created
        worker = Worker.objects.create(
            worker_id="W-888",
            first_name="Madina",
            last_name="Alimova",
            phone_number="+998901234567"
        )
        self.assertIsNotNone(worker.user)
        self.assertEqual(worker.user.role, User.Role.USER)
        self.assertEqual(worker.user.first_name, "Madina")
        self.assertEqual(worker.user.last_name, "Alimova")
        self.assertEqual(len(worker.user.uid), 6)
        self.assertTrue(bool(worker.user.qr_code))
        self.assertTrue(worker.user.is_regular_user())
        self.assertFalse(worker.user.is_staff)

    def test_daily_closing_command_and_model(self):
        from django.core.management import call_command
        today = timezone.localdate()
        # Run command for today
        call_command('close_daily_payroll', f'--date={today.strftime("%Y-%m-%d")}')

        closing = DailyWorkerClosing.objects.filter(worker=self.worker, date=today).first()
        self.assertIsNotNone(closing)
        self.assertEqual(closing.total_units, 100)
        self.assertEqual(closing.total_amount, Decimal("100000.00"))
        self.assertEqual(closing.ticket_count, 1)

        # Idempotency check: running again updates the record without creating duplicate
        call_command('close_daily_payroll', f'--date={today.strftime("%Y-%m-%d")}')
        self.assertEqual(DailyWorkerClosing.objects.filter(worker=self.worker, date=today).count(), 1)

    def test_tv_screen_daily_isolation(self):
        from screens.views import get_screen_data
        import datetime

        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        # Create a ticket scanned yesterday
        yesterday_dt = timezone.now() - datetime.timedelta(days=1)
        Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op,
            quantity=50,
            price_per_unit=Decimal("1000.00"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            screen_number=2,
            scanned_at=yesterday_dt
        )

        # Screen 2 on today should show 0 units and 0 earnings because the scan was yesterday
        today_data = get_screen_data(2, today)
        self.assertEqual(today_data['grand_total_units'], 0)
        self.assertEqual(today_data['grand_total_earnings'], 0)
        self.assertEqual(len(today_data['workers']), 0)

        # Screen 2 for yesterday should show 50 units and 50,000 UZS
        yesterday_data = get_screen_data(2, yesterday)
        self.assertEqual(yesterday_data['grand_total_units'], 50)
        self.assertEqual(yesterday_data['grand_total_earnings'], 50000)
        self.assertEqual(len(yesterday_data['workers']), 1)

    def test_monthly_payroll_calculation_and_daily_breakdown(self):
        self.client.force_login(self.superadmin)
        today = timezone.localdate()

        # Add advance payout for worker in current month
        WorkerPayout.objects.create(
            worker=self.worker,
            amount=Decimal("30000.00"),
            payout_type=WorkerPayout.PayoutType.ADVANCE,
            payout_date=today,
            created_by=self.superadmin
        )

        # Get payroll page for current month
        res = self.client.get(reverse('superadmin_payroll'), {'year': today.year, 'month': today.month})
        self.assertEqual(res.status_code, 200)

        # Test daily breakdown AJAX endpoint
        breakdown_url = reverse('superadmin_worker_daily_breakdown', kwargs={'worker_id': self.worker.id})
        b_res = self.client.get(breakdown_url, {'year': today.year, 'month': today.month})
        self.assertEqual(b_res.status_code, 200)
        data = b_res.json()
        self.assertEqual(data['worker_id'], self.worker.worker_id)
        self.assertEqual(data['total_units'], 100)
        self.assertEqual(data['total_earned'], 100000.0)
        self.assertEqual(data['total_advance'], 30000.0)
        self.assertEqual(data['net_payable'], 70000.0)

    def test_search_scalability_payroll_and_orders(self):
        self.client.force_login(self.superadmin)

        # 1. Payroll search by Worker ID
        res_pay_wid = self.client.get(reverse('superadmin_payroll'), {'q': self.worker.worker_id})
        self.assertEqual(res_pay_wid.status_code, 200)
        self.assertEqual(len(res_pay_wid.context['payroll_data']), 1)

        # Payroll search with non-matching term
        res_pay_none = self.client.get(reverse('superadmin_payroll'), {'q': 'NONEXISTENT'})
        self.assertEqual(res_pay_none.status_code, 200)
        self.assertEqual(len(res_pay_none.context['payroll_data']), 0)

        # 2. SuperAdmin Orders search by order_number
        res_ord_num = self.client.get(reverse('superadmin_orders_list'), {'q': self.order.order_number})
        self.assertEqual(res_ord_num.status_code, 200)
        self.assertIn(self.order, res_ord_num.context['orders'])

        # SuperAdmin Orders search by box_code
        res_ord_box = self.client.get(reverse('superadmin_orders_list'), {'q': self.box.box_code})
        self.assertEqual(res_ord_box.status_code, 200)
        self.assertIn(self.order, res_ord_box.context['orders'])

        # 3. Production Orders search by box_code
        res_prod_box = self.client.get(reverse('production:order_list'), {'q': self.box.box_code})
        self.assertEqual(res_prod_box.status_code, 200)
        self.assertIn(self.order, res_prod_box.context['orders'])

    def test_master_restricted_to_terminal_only(self):
        self.client.force_login(self.master)

        # 1. Master can access terminal
        res_term = self.client.get(reverse('production:terminal_home'))
        self.assertEqual(res_term.status_code, 200)

        # 2. Master accessing / (dashboard) is redirected to terminal
        res_root = self.client.get('/')
        self.assertEqual(res_root.status_code, 302)
        self.assertIn('/terminal/', res_root.url)

        # 3. Master accessing /orders/ is redirected to terminal
        res_ord = self.client.get(reverse('production:order_list'))
        self.assertEqual(res_ord.status_code, 302)
        self.assertIn('/terminal/', res_ord.url)

        # 4. Master accessing /superadmin/ is redirected to terminal
        res_sa = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(res_sa.status_code, 302)
        self.assertIn('/terminal/', res_sa.url)

        # 5. Master making AJAX request to non-terminal returns 403 Forbidden
        res_ajax = self.client.get(reverse('production:order_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_ajax.status_code, 403)
        self.assertEqual(res_ajax.json()['status'], 'FORBIDDEN')

        # 6. Superadmin is NOT restricted
        self.client.force_login(self.superadmin)
        res_sa_ok = self.client.get(reverse('superadmin_dashboard'))
        self.assertEqual(res_sa_ok.status_code, 200)
        res_sa_ord = self.client.get(reverse('production:order_list'))
        self.assertEqual(res_sa_ord.status_code, 200)
        res_sa_term = self.client.get(reverse('production:terminal_home'))
        self.assertEqual(res_sa_term.status_code, 200)

    def test_master_login_and_logout(self):
        # 1. Login with master credentials
        res_login = self.client.post(reverse('accounts:login'), {
            'username': 'master_test',
            'password': 'testpassword123'
        })
        self.assertEqual(res_login.status_code, 302)
        self.assertIn('/terminal/', res_login.url)

        # 2. Logout
        res_logout = self.client.get(reverse('accounts:logout'))
        self.assertEqual(res_logout.status_code, 302)
        self.assertIn('/login/', res_logout.url)

    def test_delete_model_from_order_does_not_resurrect(self):
        self.client.force_login(self.superadmin)
        art = Article.objects.create(code="ART-DEL", name="Model to Delete")
        ord_del = Order.objects.create(order_number="ORD-DEL-MOD", article=art, total_quantity=50)
        item = OrderItem.objects.create(order=ord_del, article=art, quantity=50)

        # Delete model from order
        res = self.client.post(reverse('superadmin_order_model_delete', kwargs={'order_id': ord_del.id, 'item_id': item.id}))
        self.assertEqual(res.status_code, 302)

        ord_del.refresh_from_db()
        self.assertEqual(ord_del.items.count(), 0)
        self.assertIsNone(ord_del.article)
        self.assertEqual(ord_del.total_quantity, 0)

        # Visiting order detail page must NOT resurrect the deleted model
        res_detail = self.client.get(reverse('superadmin_order_detail', kwargs={'order_id': ord_del.id}))
        self.assertEqual(res_detail.status_code, 200)
        self.assertEqual(ord_del.items.count(), 0)
        self.assertContains(res_detail, "Ushbu zakazda hali modellar mavjud emas")

    def test_delete_entire_order(self):
        self.client.force_login(self.superadmin)
        ord_del = Order.objects.create(order_number="ORD-TOTAL-DELETE", total_quantity=50)
        res = self.client.post(reverse('superadmin_order_delete', kwargs={'order_id': ord_del.id}))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Order.objects.filter(id=ord_del.id).exists())

    def test_add_model_with_operations_and_prices(self):
        self.client.force_login(self.superadmin)
        ord_obj = Order.objects.create(order_number="ORD-ADD-MODEL-TEST", total_quantity=0)
        op1 = Operation.objects.create(code="OP-NEW-1", name="Yeng tikish")
        op2 = Operation.objects.create(code="OP-NEW-2", name="Yoqa tikish")

        data = {
            'model_name': 'Futbolka Test',
            'model_code': 'FUT-TEST',
            'quantity': '250',
            'selected_operations': [str(op1.id), str(op2.id)],
            f'price_{op1.id}': '1200',
            f'price_{op2.id}': '1800',
        }
        res = self.client.post(reverse('superadmin_order_add_model', kwargs={'order_id': ord_obj.id}), data)
        self.assertEqual(res.status_code, 302)

        ord_obj.refresh_from_db()
        self.assertEqual(ord_obj.items.count(), 1)
        item = ord_obj.items.first()
        self.assertEqual(item.article.code, 'FUT-TEST')
        self.assertEqual(item.quantity, 250)

        # Verify operations & per-unit prices
        ao1 = ArticleOperation.objects.get(article=item.article, operation=op1)
        self.assertEqual(ao1.price_per_unit, Decimal('1200.00'))
        ao2 = ArticleOperation.objects.get(article=item.article, operation=op2)
        self.assertEqual(ao2.price_per_unit, Decimal('1800.00'))

    def test_custom_integer_operation_price_like_39(self):
        self.client.force_login(self.superadmin)
        ord_obj = Order.objects.create(order_number="ORD-PRICE-39", total_quantity=10)
        art = Article.objects.create(code="ART-39", name="Model 39")
        item = OrderItem.objects.create(order=ord_obj, article=art, quantity=10)
        op = Operation.objects.create(code="OP-39", name="Maxsus operatsiya")
        ao = ArticleOperation.objects.create(article=art, operation=op, price_per_unit=Decimal("10.00"), sequence=1)

        # Update price to 39
        res = self.client.post(
            reverse('superadmin_order_model_update_operation', kwargs={'order_id': ord_obj.id, 'item_id': item.id}),
            {
                'article_operation_id': ao.id,
                'price_per_unit': '39',
                'sequence': '1'
            }
        )
        self.assertEqual(res.status_code, 302)
        ao.refresh_from_db()
        self.assertEqual(ao.price_per_unit, Decimal('39.00'))

    def test_superadmin_user_badge_print(self):
        self.client.force_login(self.superadmin)
        test_user = User.objects.create(
            username='birka_user',
            first_name='Jasur',
            last_name='Nazarov',
            role=User.Role.USER
        )
        url = reverse('superadmin_user_badge', kwargs={'user_id': test_user.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Jasur')
        self.assertContains(res, 'Nazarov')
        self.assertContains(res, f'UID: {test_user.uid}')
        self.assertContains(res, f'USER:{test_user.uid}')
        self.assertContains(res, 'TIKUVCHILIK FABRIKASI')
        self.assertContains(res, 'a4-sheet-container')
        self.assertContains(res, 'cut-box')
        self.assertContains(res, 'scissor-guide')
        self.assertContains(res, 'setCopies')

    def test_superadmin_users_print_badges_batch(self):
        self.client.force_login(self.superadmin)
        u1 = User.objects.create(username='u_test_1', first_name='Azamat', last_name='Qodirov', role=User.Role.USER)
        u2 = User.objects.create(username='u_test_2', first_name='Zafar', last_name='Bekov', role=User.Role.MASTER)

        # 1. Barcha birkalar
        res_all = self.client.get(reverse('superadmin_users_print_badges'))
        self.assertEqual(res_all.status_code, 200)
        self.assertContains(res_all, 'Azamat')
        self.assertContains(res_all, 'Zafar')
        self.assertContains(res_all, f'UID: {u1.uid}')
        self.assertContains(res_all, f'UID: {u2.uid}')

        # 2. Rol bo'yicha filtr (faqat USER)
        res_user = self.client.get(reverse('superadmin_users_print_badges') + '?role=USER')
        self.assertEqual(res_user.status_code, 200)
        self.assertContains(res_user, 'Azamat')
        self.assertNotContains(res_user, 'Zafar')

        # 3. Qidiruv bo'yicha filtr
        res_q = self.client.get(reverse('superadmin_users_print_badges') + '?q=Azamat')
        self.assertEqual(res_q.status_code, 200)
        self.assertContains(res_q, 'Azamat')
        self.assertNotContains(res_q, 'Zafar')

        # 4. PDF yuklab olish testi
        res_pdf = self.client.get(reverse('superadmin_users_download_badges_pdf'))
        self.assertEqual(res_pdf.status_code, 200)
        self.assertEqual(res_pdf['Content-Type'], 'application/pdf')

    def test_operation_difficulty_and_box_stickers(self):
        self.client.force_login(self.superadmin)

        # 1. Check Operation difficulty display
        op1 = Operation.objects.create(code='OP-DIFF-1', name='Tugma qadash', default_difficulty=3.5)
        op2 = Operation.objects.create(code='OP-DIFF-2', name='Yoqa tikish', default_difficulty=1.0)
        self.assertEqual(op1.default_difficulty_display, '3.5')
        self.assertEqual(op2.default_difficulty_display, '1')

        # 2. Add model with custom difficulty to order
        data = {
            'model_code': 'DIFF-MOD-1',
            'model_name': 'Ko\'ylak Diff',
            'quantity': '200',
            'selected_operations': [str(op1.id)],
            f'price_{op1.id}': '1500',
            f'diff_{op1.id}': '3.5',
        }
        res = self.client.post(reverse('superadmin_order_add_model', kwargs={'order_id': self.order.id}), data)
        self.assertEqual(res.status_code, 302)

        new_art = Article.objects.get(code='DIFF-MOD-1')
        ao = ArticleOperation.objects.get(article=new_art, operation=op1)
        self.assertEqual(ao.difficulty, 3.5)
        self.assertEqual(ao.difficulty_display, '3.5')
        self.assertEqual(ao.price_per_unit, Decimal('1500.00'))

        # 3. Update operation difficulty via superadmin_order_model_update_operation
        item = OrderItem.objects.get(order=self.order, article=new_art)
        update_data = {
            'article_operation_id': ao.id,
            'price_per_unit': '1800',
            'sequence': '2',
            'difficulty': '4.2',
        }
        res_update = self.client.post(
            reverse('superadmin_order_model_update_operation', kwargs={'order_id': self.order.id, 'item_id': item.id}),
            update_data
        )
        self.assertEqual(res_update.status_code, 302)
        ao.refresh_from_db()
        self.assertEqual(ao.difficulty, 4.2)
        self.assertEqual(ao.difficulty_display, '4.2')
        self.assertEqual(ao.price_per_unit, Decimal('1800.00'))

        # 4. Box sticker print displays difficulty
        box_diff = Box.objects.create(order=self.order, article=new_art, box_number=2, quantity=50)
        ticket_diff = Ticket.objects.create(
            box=box_diff,
            article_operation=ao,
            quantity=50,
            split_index=1,
            total_splits=1,
            price_per_unit=Decimal('1800.00')
        )
        res_print = self.client.get(reverse('production:box_print_stickers', kwargs={'box_id': box_diff.id}))
        self.assertContains(res_print, 'QIYINLIK:')
        self.assertContains(res_print, '4.2')

    def test_update_article_daily_norm(self):
        self.client.force_login(self.superadmin)
        res = self.client.post(reverse('superadmin_pricing'), {
            'action': 'update_article_norm',
            'article_id': self.article.id,
            'daily_norm': '2500'
        })
        self.assertEqual(res.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.daily_norm, 2500)

    def test_worker_norm_and_difficulty_kpi_dashboard(self):
        self.client.force_login(self.superadmin)
        self.ticket.delete()
        self.article.daily_norm = 1000
        self.article.save()

        # Operatsiya 1: qiyinlik 4.0, 100 dona -> 400 ball
        self.art_op.difficulty = 4.0
        self.art_op.save()

        Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op,
            worker=self.worker,
            quantity=100,
            status=Ticket.Status.SCANNED,
            scanned_at=timezone.now(),
            total_amount=Decimal('50000.00'),
            screen_number=1
        )

        # Operatsiya 2: qiyinlik 0.5, 200 dona -> 100 ball
        op2 = Operation.objects.create(code='OP-NORM-2', name='Op 0.5')
        art_op2 = ArticleOperation.objects.create(
            article=self.article,
            operation=op2,
            price_per_unit=Decimal('200.00'),
            difficulty=0.5,
            sequence=2
        )
        Ticket.objects.create(
            box=self.box,
            article_operation=art_op2,
            worker=self.worker,
            quantity=200,
            status=Ticket.Status.SCANNED,
            scanned_at=timezone.now(),
            total_amount=Decimal('40000.00'),
            screen_number=1
        )

        # Jami ball: 400 + 100 = 500 ball. Norma: 1000 ball. Foiz: 50.0%
        res = self.client.get(reverse('superadmin_dashboard') + '?period=today')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Tikuvchilarning Norma Bajarilishi')
        self.assertContains(res, '50.0%')
        self.assertContains(res, '500.0')

    def test_box_stickers_100x60_pdf_download(self):
        self.client.force_login(self.superadmin)
        # Ensure a ticket exists for self.box
        Ticket.objects.get_or_create(
            box=self.box,
            article_operation=self.art_op,
            defaults={'quantity': 50, 'status': Ticket.Status.PENDING}
        )
        url = reverse('production:box_download_stickers_pdf', kwargs={'box_id': self.box.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(len(res.content) > 0)
        self.assertIn('.pdf', res['Content-Disposition'])


    def test_order_stickers_100x60_pdf_download(self):
        self.client.force_login(self.superadmin)
        Ticket.objects.get_or_create(
            box=self.box,
            article_operation=self.art_op,
            defaults={'quantity': 50, 'status': Ticket.Status.PENDING}
        )
        url = reverse('production:order_download_all_stickers_pdf', kwargs={'order_id': self.order.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'application/pdf')
        self.assertTrue(len(res.content) > 0)
        self.assertIn(self.order.order_number, res['Content-Disposition'])
        self.assertIn('BARCHA_STIKERLAR_65x45.pdf', res['Content-Disposition'])


class WorkerPerformanceHistoryAndBonusTest(TestCase):
    def setUp(self):
        from production.models import ProductModel, Operation, Article, ArticleOperation, Order, Box, Ticket
        self.superadmin = User.objects.create_superuser(username="admin_perf", password="password123", role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.superadmin)

        self.worker = Worker.objects.create(worker_id="W-PERF-01", first_name="Dilfuza", last_name="Rahimova")

        # Model A: norm = 10,000
        self.model_a = ProductModel.objects.create(code="PM-A", name="Model A (Katta Partiya)", daily_norm=10000)
        self.art_a = Article.objects.create(code="ART-A", name="Artikul A", model=self.model_a)
        self.op_a = Operation.objects.create(code="OP-A", name="Operatsiya A")
        self.ao_a = ArticleOperation.objects.create(article=self.art_a, operation=self.op_a, price_per_unit=Decimal("100"), difficulty=1.0)

        # Model B: norm = 500
        self.model_b = ProductModel.objects.create(code="PM-B", name="Model B (Kichik Partiya)", daily_norm=500)
        self.art_b = Article.objects.create(code="ART-B", name="Artikul B", model=self.model_b)
        self.op_b = Operation.objects.create(code="OP-B", name="Operatsiya B")
        self.ao_b = ArticleOperation.objects.create(article=self.art_b, operation=self.op_b, price_per_unit=Decimal("200"), difficulty=1.0)

        self.order = Order.objects.create(order_number="ORD-PERF-01", article=self.art_a, total_quantity=10000)
        self.box_a = Box.objects.create(order=self.order, article=self.art_a, box_number=1, quantity=1000)
        self.box_b = Box.objects.create(order=self.order, article=self.art_b, box_number=2, quantity=500)

    def test_multi_model_carryover_percentage_formula(self):
        # User formula example:
        # 1000 units on 10,000 norm model = 10%
        # 400 units on 500 norm model = 80%
        # Total = 10% + 80% = 90%
        now = timezone.now()
        Ticket.objects.create(
            box=self.box_a,
            article_operation=self.ao_a,
            quantity=1000,
            price_per_unit=Decimal("100"),
            total_amount=Decimal("100000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )
        Ticket.objects.create(
            box=self.box_b,
            article_operation=self.ao_b,
            quantity=400,
            price_per_unit=Decimal("200"),
            total_amount=Decimal("80000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )

        url = reverse('superadmin_worker_history', kwargs={'worker_id': self.worker.id}) + '?range=today'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # Check percentage is 90.0%
        self.assertContains(res, "90.0%")
        # Bonus should be 0 (since 90.0% is not > 100.0%)
        self.assertContains(res, "—")
        self.assertNotContains(res, "+30 000 UZS")

    def test_bonus_disabled_by_default(self):
        # By default DAILY_BONUS_AMOUNT is 0 (canceled for now)
        now = timezone.now()
        Ticket.objects.create(
            box=self.box_b,
            article_operation=self.ao_b,
            quantity=550, # 110%
            price_per_unit=Decimal("200"),
            total_amount=Decimal("110000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )
        url = reverse('superadmin_worker_history', kwargs={'worker_id': self.worker.id}) + '?range=today'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "110.0%")
        # Bonus should be 0 / not awarded because DAILY_BONUS_AMOUNT is 0
        self.assertNotContains(res, "+30 000 UZS")
        self.assertContains(res, "0 <small class=\"text-xs text-slate-500 font-normal\">UZS (Bekor qilingan)</small>")

    @override_settings(DAILY_BONUS_AMOUNT=30000)
    def test_bonus_strictly_above_100_percent(self):
        now = timezone.now()
        # Exactly 100% on Model B (500 units on 500 norm):
        t1 = Ticket.objects.create(
            box=self.box_b,
            article_operation=self.ao_b,
            quantity=500,
            price_per_unit=Decimal("200"),
            total_amount=Decimal("100000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )
        url = reverse('superadmin_worker_history', kwargs={'worker_id': self.worker.id}) + '?range=today'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "100.0%")
        # User rule: 100% is NOT enough for bonus: "KUNLIK UMUMIY 100 FOIZDAN OSHSA 100 BOLSA HISOB EMAS AVTOMATIK YONIDA + 30000 BONUS QOSHISH KERAK"
        self.assertNotContains(res, "+30 000 UZS")

        # Now add 50 units on Model B (10% more -> total 110.0%):
        Ticket.objects.create(
            box=self.box_b,
            article_operation=self.ao_b,
            quantity=50,
            price_per_unit=Decimal("200"),
            total_amount=Decimal("10000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )
        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, 200)
        self.assertContains(res2, "110.0%")
        # Strictly > 100%: bonus +30 000 UZS awarded!
        self.assertContains(res2, "+30 000 UZS")

    def test_multiple_articles_linked_to_one_model_daily_norm_isolation(self):
        from datetime import timedelta
        from screens.views import get_screen_data

        # 1. Create Model with daily_norm = 1500
        polo_model = ProductModel.objects.create(code="PM-POLO", name="Polo Futbolka", daily_norm=1500)
        # 4 articles belonging to the same model
        art1 = Article.objects.create(code="POLO-BLK", name="Polo Qora", model=polo_model)
        art2 = Article.objects.create(code="POLO-WHT", name="Polo Oq", model=polo_model)
        art3 = Article.objects.create(code="POLO-BLU", name="Polo Ko'k", model=polo_model)
        art4 = Article.objects.create(code="POLO-RED", name="Polo Qizil", model=polo_model)

        # Another model with daily_norm = 1000
        shorts_model = ProductModel.objects.create(code="PM-SHORTS", name="Shortik", daily_norm=1000)
        art_shorts = Article.objects.create(code="SHORTS-01", name="Shortik Qora", model=shorts_model)

        op = Operation.objects.create(code="OP-SEW", name="Tikish")
        ao1 = ArticleOperation.objects.create(article=art1, operation=op, price_per_unit=Decimal("100"), difficulty=1.0)
        ao2 = ArticleOperation.objects.create(article=art2, operation=op, price_per_unit=Decimal("100"), difficulty=1.0)
        ao3 = ArticleOperation.objects.create(article=art3, operation=op, price_per_unit=Decimal("100"), difficulty=1.0)
        ao4 = ArticleOperation.objects.create(article=art4, operation=op, price_per_unit=Decimal("100"), difficulty=1.0)
        ao_shorts = ArticleOperation.objects.create(article=art_shorts, operation=op, price_per_unit=Decimal("150"), difficulty=1.0)

        order = Order.objects.create(order_number="ORD-MULTI-120K", article=art1, total_quantity=120000)
        b1 = Box.objects.create(order=order, article=art1, box_number=101, quantity=400)
        b2 = Box.objects.create(order=order, article=art2, box_number=102, quantity=400)
        b3 = Box.objects.create(order=order, article=art3, box_number=103, quantity=400)
        b4 = Box.objects.create(order=order, article=art4, box_number=104, quantity=300)
        b_shorts = Box.objects.create(order=order, article=art_shorts, box_number=105, quantity=500)

        now = timezone.now()
        yesterday = now - timedelta(days=1)

        # Day 1 (yesterday): 3 articles of POLO sewn: 400 + 400 + 400 = 1200 points. Norm = 1500. Day 1 = 80.0%
        Ticket.objects.create(box=b1, article_operation=ao1, quantity=400, price_per_unit=Decimal("100"), total_amount=Decimal("40000"), status=Ticket.Status.SCANNED, worker=self.worker, scanned_at=yesterday)
        Ticket.objects.create(box=b2, article_operation=ao2, quantity=400, price_per_unit=Decimal("100"), total_amount=Decimal("40000"), status=Ticket.Status.SCANNED, worker=self.worker, scanned_at=yesterday)
        Ticket.objects.create(box=b3, article_operation=ao3, quantity=400, price_per_unit=Decimal("100"), total_amount=Decimal("40000"), status=Ticket.Status.SCANNED, worker=self.worker, scanned_at=yesterday)

        # Day 2 (today): 4th article of POLO sewn: 300 points (20.0%). And SHORTS: 500 points (50.0%). Day 2 = 70.0%
        Ticket.objects.create(box=b4, article_operation=ao4, quantity=300, price_per_unit=Decimal("100"), total_amount=Decimal("30000"), status=Ticket.Status.SCANNED, worker=self.worker, scanned_at=now, screen_number=1)
        Ticket.objects.create(box=b_shorts, article_operation=ao_shorts, quantity=500, price_per_unit=Decimal("150"), total_amount=Decimal("75000"), status=Ticket.Status.SCANNED, worker=self.worker, scanned_at=now, screen_number=1)

        # 2. Check superadmin_worker_history
        url = reverse('superadmin_worker_history', kwargs={'worker_id': self.worker.id}) + '?range=7days'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # Yesterday shows 80.0%
        self.assertContains(res, "80.0%")
        # Today shows 70.0%
        self.assertContains(res, "70.0%")

        # 3. Check TV Screen data for screen 1 (today)
        screen_data = get_screen_data(screen_number=1, target_date=timezone.localdate())
        worker_screen = next(w for w in screen_data['workers'] if w['worker_id'] == self.worker.worker_id)
        self.assertEqual(worker_screen['total_percentage'], 70.0)
        self.assertEqual(worker_screen['total_points'], 800)
        self.assertEqual(len(worker_screen['models']), 2)
        polo_screen_m = next(m for m in worker_screen['models'] if m['model_code'] == 'PM-POLO')
        self.assertEqual(polo_screen_m['ratio_str'], '300/1500')
        self.assertEqual(polo_screen_m['percentage'], 20.0)

        shorts_screen_m = next(m for m in worker_screen['models'] if m['model_code'] == 'PM-SHORTS')
        self.assertEqual(shorts_screen_m['ratio_str'], '500/1000')
        self.assertEqual(shorts_screen_m['percentage'], 50.0)

        # 4. Check superadmin_dashboard
        dash_url = reverse('superadmin_dashboard') + '?period=today'
        dash_res = self.client.get(dash_url)
        self.assertEqual(dash_res.status_code, 200)
        self.assertContains(dash_res, "70.0%")


class WorkerTicketsByDateApiTest(TestCase):
    def setUp(self):
        from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket
        self.superadmin = User.objects.create_superuser(username="admin_api", password="password123", role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.superadmin)
        self.worker = Worker.objects.create(worker_id="W-API-01", first_name="Feruza", last_name="M")

        self.art = Article.objects.create(code="ART-API", name="API Model")
        self.op = Operation.objects.create(code="OP-API", name="Yoqa tikish")
        self.ao = ArticleOperation.objects.create(article=self.art, operation=self.op, price_per_unit=Decimal("500"))
        self.order = Order.objects.create(order_number="ORD-API", article=self.art, total_quantity=100)
        self.box = Box.objects.create(order=self.order, box_number=1, quantity=50)

    def test_api_worker_tickets_by_date_success(self):
        today = timezone.localdate()
        t = Ticket.objects.create(
            box=self.box,
            article_operation=self.ao,
            quantity=20,
            price_per_unit=Decimal("500"),
            total_amount=Decimal("10000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=timezone.now()
        )
        url = reverse('api_worker_tickets_by_date', kwargs={'worker_id': self.worker.id}) + f"?date={today.strftime('%Y-%m-%d')}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['tickets'][0]['stiker_id'], t.stiker_id)
        self.assertEqual(data['tickets'][0]['quantity'], 20)
        self.assertIn('model_name', data['tickets'][0])


class SuperAdminPayrollBulkPayTest(TestCase):
    def setUp(self):
        from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket
        self.superadmin = User.objects.create_superuser(username="admin_bulk", password="password123", role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.superadmin)

        self.worker1 = Worker.objects.create(worker_id="W-BULK-1", first_name="Ali", last_name="Valiyev")
        self.worker2 = Worker.objects.create(worker_id="W-BULK-2", first_name="Vali", last_name="Aliyev")

        self.art = Article.objects.create(code="ART-BULK", name="Bulk Model")
        self.op = Operation.objects.create(code="OP-BULK", name="Tikuv")
        self.ao = ArticleOperation.objects.create(article=self.art, operation=self.op, price_per_unit=Decimal("1000"))
        self.order = Order.objects.create(order_number="ORD-BULK", article=self.art, total_quantity=200)
        self.box = Box.objects.create(order=self.order, box_number=1, quantity=100)

        # Worker 1 has 50,000 UZS earned
        Ticket.objects.create(
            box=self.box,
            article_operation=self.ao,
            quantity=50,
            price_per_unit=Decimal("1000"),
            total_amount=Decimal("50000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker1,
            scanned_at=timezone.now()
        )
        # Worker 2 has 30,000 UZS earned
        Ticket.objects.create(
            box=self.box,
            article_operation=self.ao,
            quantity=30,
            price_per_unit=Decimal("1000"),
            total_amount=Decimal("30000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker2,
            scanned_at=timezone.now()
        )

    def test_bulk_payout_pays_selected_workers(self):
        today = timezone.localdate()
        url = reverse('superadmin_payroll_bulk_pay')
        res = self.client.post(url, {
            'selected_worker_ids': [self.worker1.id, self.worker2.id],
            'selected_year': today.year,
            'selected_month': today.month,
        })
        self.assertEqual(res.status_code, 302)

        # Check WorkerPayout records created
        p1 = WorkerPayout.objects.filter(worker=self.worker1, payout_type=WorkerPayout.PayoutType.SALARY).first()
        self.assertIsNotNone(p1)
        self.assertEqual(p1.amount, Decimal("50000.00"))

        p2 = WorkerPayout.objects.filter(worker=self.worker2, payout_type=WorkerPayout.PayoutType.SALARY).first()
        self.assertIsNotNone(p2)
        self.assertEqual(p2.amount, Decimal("30000.00"))


class WorkerHistoryAndPayrollStickerFeaturesTest(TestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            username='admin_test_stickers',
            password='password123',
            email='admin_stickers@test.com',
            role=User.Role.SUPER_ADMIN
        )
        self.client.login(username='admin_test_stickers', password='password123')

        self.worker = Worker.objects.create(
            worker_id="W-STK-01",
            first_name="Zilola",
            last_name="Karimova",
            is_active=True
        )

        self.model = ProductModel.objects.create(code="PM-STK-T", name="Futbolka Test", daily_norm=1000)
        self.article = Article.objects.create(code="ART-STK-01", name="Futbolka Oq", model=self.model)

        self.op_meto = Operation.objects.create(code="OP-METO", name="Meto")
        self.op_dazmol = Operation.objects.create(code="OP-DAZMOL", name="Dazmol")

        self.ao_meto = ArticleOperation.objects.create(
            article=self.article, operation=self.op_meto, price_per_unit=Decimal("200"), difficulty=1.0
        )
        self.ao_dazmol = ArticleOperation.objects.create(
            article=self.article, operation=self.op_dazmol, price_per_unit=Decimal("150"), difficulty=1.0
        )

        self.order = Order.objects.create(order_number="ORD-STK-001", article=self.article, total_quantity=5000)
        self.box1 = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=400)
        self.box2 = Box.objects.create(order=self.order, article=self.article, box_number=2, quantity=100)

        self.today = timezone.localdate()
        now = timezone.now()

        # Ticket 1: Meto 400 ta in box 1
        self.t1 = Ticket.objects.create(
            box=self.box1,
            article_operation=self.ao_meto,
            quantity=400,
            price_per_unit=Decimal("200"),
            total_amount=Decimal("80000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )
        # Ticket 2: Dazmol 100 ta in box 2
        self.t2 = Ticket.objects.create(
            box=self.box2,
            article_operation=self.ao_dazmol,
            quantity=100,
            price_per_unit=Decimal("150"),
            total_amount=Decimal("15000"),
            status=Ticket.Status.SCANNED,
            worker=self.worker,
            scanned_at=now
        )

    def test_superadmin_worker_history_operations_and_stickers(self):
        url = reverse('superadmin_worker_history', kwargs={'worker_id': self.worker.id}) + '?range=today'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        daily = res.context['daily_history']
        self.assertEqual(len(daily), 1)
        day0 = daily[0]

        # Check operations summary & list
        self.assertIn("Meto: 400 ta", day0['operations_summary'])
        self.assertIn("Dazmol: 100 ta", day0['operations_summary'])
        self.assertEqual(day0['boxes_count'], 2)
        self.assertEqual(day0['ticket_count'], 2)
        self.assertIsNotNone(day0['compact_stickers'])
        self.assertEqual(len(day0['ticket_ids_list']), 2)

        # Check HTML renders operations & sticker button
        self.assertContains(res, "Meto:")
        self.assertContains(res, "400 ta")
        self.assertContains(res, "Dazmol:")
        self.assertContains(res, "100 ta")
        self.assertContains(res, "2 ta quti")
        self.assertContains(res, "Stikerlar (2)")

    def test_api_worker_tickets_by_date(self):
        url = reverse('api_worker_tickets_by_date', kwargs={'worker_id': self.worker.id}) + f"?date={self.today.strftime('%Y-%m-%d')}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertTrue(data['success'])
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['boxes_count'], 2)
        self.assertIn("Meto: 400 ta", data['operations_summary'])
        self.assertEqual(len(data['ticket_ids']), 2)
        self.assertIn(self.t1.stiker_id, data['ticket_ids'])

    def test_superadmin_worker_daily_breakdown_json(self):
        url = reverse('superadmin_worker_daily_breakdown', kwargs={'worker_id': self.worker.id}) + f"?year={self.today.year}&month={self.today.month}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data['worker_pk'], self.worker.id)
        self.assertEqual(len(data['rows']), 1)
        r0 = data['rows'][0]
        self.assertEqual(r0['ticket_count'], 2)
        self.assertEqual(r0['boxes_count'], 2)
        self.assertIn("Meto: 400 ta", r0['operations_summary'])
        self.assertIn("Dazmol: 100 ta", r0['operations_summary'])
        self.assertEqual(len(r0['ticket_ids']), 2)

    def test_superadmin_payroll_boxes_and_tickets(self):
        url = reverse('superadmin_payroll') + f"?year={self.today.year}&month={self.today.month}&q={self.worker.worker_id}"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

        payroll_data = res.context['payroll_data']
        self.assertEqual(len(payroll_data), 1)
        w_data = payroll_data[0]
        self.assertEqual(w_data['total_tickets'], 2)
        self.assertEqual(w_data['total_boxes'], 2)
        self.assertContains(res, "2 stiker • 2 quti")


class SuperadminUserEditFeaturesTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username='main_superadmin',
            password='Password123!',
            role=User.Role.SUPER_ADMIN
        )
        self.client.force_login(self.superadmin)

        # Xodim apostrofli ism bilan
        self.user1 = User.objects.create_user(
            username='worker_otkir',
            password='worker123',
            first_name="O'tkir",
            last_name="Po'latov",
            role=User.Role.USER,
            phone_number="+998901112233"
        )
        self.worker1 = Worker.objects.create(
            user=self.user1,
            worker_id="W-901",
            first_name="O'tkir",
            last_name="Po'latov",
            phone_number="+998901112233"
        )

    def test_users_page_renders_with_apostrophe_names_safely(self):
        """Apostrofli ismlar (O'tkir, Po'latov) xatosiz HTML data-* atributlarida chiqishi kerak"""
        url = reverse('superadmin_users')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        # HTML da data-first-name va data-last-name mavjudligini tekshirish
        self.assertContains(res, 'data-first-name="O&#x27;tkir"')
        self.assertContains(res, 'openEditUserModalFromBtn(this)')

    def test_update_user_name_with_apostrophe_and_worker_sync(self):
        """Ismni apostrof bilan o'zgartirish va u avtomatik Worker profiliga ham o'tishi kerak"""
        url = reverse('superadmin_users')
        data = {
            'action': 'update_user',
            'user_id': self.user1.id,
            'first_name': "G'ayrat",
            'last_name': "Yo'ldoshev",
            'username': "worker_otkir",
            'role': User.Role.USER,
            'phone_number': "+998909998877",
            'email': "gayrat@example.com"
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.user1.refresh_from_db()
        self.assertEqual(self.user1.first_name, "G'ayrat")
        self.assertEqual(self.user1.last_name, "Yo'ldoshev")
        self.assertEqual(self.user1.phone_number, "+998909998877")

        # Worker modeli ham sinxronlashgan bo'lishi kerak
        self.worker1.refresh_from_db()
        self.assertEqual(self.worker1.first_name, "G'ayrat")
        self.assertEqual(self.worker1.last_name, "Yo'ldoshev")
        self.assertEqual(self.worker1.phone_number, "+998909998877")
        self.assertEqual(self.worker1.full_name, "G'ayrat Yo'ldoshev")

    def test_update_user_without_last_name(self):
        """Faqat ismi bo'lgan (familiyasi bo'sh) xodimni saqlash muvaffaqiyatli bo'lishi kerak"""
        url = reverse('superadmin_users')
        data = {
            'action': 'update_user',
            'user_id': self.user1.id,
            'first_name': "Ziyoda",
            'last_name': "",
            'role': User.Role.USER,
            'phone_number': "+998901112233"
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.user1.refresh_from_db()
        self.assertEqual(self.user1.first_name, "Ziyoda")
        self.assertEqual(self.user1.last_name, "")

    def test_duplicate_telegram_id_does_not_crash(self):
        """Takroriy Telegram ID kiritilganda server 500 bermasligi, ogohlantirish berishi kerak"""
        self.user2 = User.objects.create_user(
            username='master_user',
            password='password',
            role=User.Role.MASTER,
            telegram_user_id=123456789
        )
        url = reverse('superadmin_users')
        data = {
            'action': 'update_user',
            'user_id': self.user1.id,
            'first_name': "Test",
            'last_name': "User",
            'role': User.Role.USER,
            'telegram_user_id': "123456789"  # user2 ga tegishli
        }
        res = self.client.post(url, data, follow=True)
        self.assertEqual(res.status_code, 200)

        self.user1.refresh_from_db()
        # user1 ga bu ID biriktirilmasligi kerak (chunki u user2 da bor)
        self.assertNotEqual(self.user1.telegram_user_id, 123456789)


class ControlRoleAndQualityControlTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.superadmin = User.objects.create_superuser(
            username="super_ctrl",
            password="password123",
            role=User.Role.SUPER_ADMIN
        )
        self.controller, _ = User.objects.get_or_create(
            username="patok1",
            defaults={
                'role': User.Role.CONTROL,
                'first_name': "Patok 1",
                'last_name': "Kontrolchi",
            }
        )
        self.controller.set_password("password123")
        self.controller.role = User.Role.CONTROL
        self.controller.save()
        self.order = Order.objects.create(order_number="ORD-QC-01", total_quantity=50)
        self.article = Article.objects.create(code="DC-0309", name="Erkaklar Futbolkasi")
        self.box = Box.objects.create(
            order=self.order,
            article=self.article,
            box_number=1,
            quantity=50,
            razmer="XL",
            pastal_number="P-101"
        )

    def test_control_role_access_and_restriction(self):
        # 1. Kontrolchi /control/ sahifasiga kira oladi
        self.client.force_login(self.controller)
        res = self.client.get(reverse('control:home'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "SIFAT NAZORATI")
        self.assertContains(res, "patok1")

        # 2. Kontrolchi /orders/ ga kirishga harakat qilsa, /control/ ga yo'naltiriladi
        res_orders = self.client.get(reverse('production:order_list'))
        self.assertEqual(res_orders.status_code, 302)
        self.assertIn('/control/', res_orders.url)

        # 3. Kontrolchi AJAX orqali boshqa joyga so'rov bersa 403 oladi
        res_ajax = self.client.get(reverse('production:order_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res_ajax.status_code, 403)
        self.assertEqual(res_ajax.json()['status'], 'FORBIDDEN')

        # 4. Superadmin ham /control/ ga kira oladi
        self.client.force_login(self.superadmin)
        res_sa = self.client.get(reverse('control:home'))
        self.assertEqual(res_sa.status_code, 200)

    def test_control_box_lookup_api(self):
        self.client.force_login(self.controller)

        # 1. CONTROL:BOX_CODE formatida qidirish
        res = self.client.get(reverse('control:api_lookup'), {'code': f'CONTROL:{self.box.box_code}'})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertEqual(data['box']['box_code'], self.box.box_code)
        self.assertEqual(data['box']['article_code'], 'DC-0309')
        self.assertEqual(data['box']['quantity'], 50)
        self.assertEqual(data['box']['mode'], 'INITIAL')

        # 2. Shunchaki box_code orqali qidirish
        res2 = self.client.get(reverse('control:api_lookup'), {'code': self.box.box_code.lower()})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()['box']['id'], self.box.id)

    def test_control_inspection_initial_and_repair_cycle(self):
        self.client.force_login(self.controller)

        # 1. Birlamchi tekshiruv: 50 tadan 10 tasi 2-sort, 3 tasi ta'mir -> 1-sort = 37 ta bo'lishi kerak
        payload = {
            'box_id': self.box.id,
            'mode': 'INITIAL',
            'total_qty': 50,
            'second_sort_qty': 10,
            'repair_qty': 3
        }
        res = self.client.post(
            reverse('control:api_submit'),
            json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertEqual(data['box']['first_sort'], 37)
        self.assertEqual(data['box']['second_sort'], 10)
        self.assertEqual(data['box']['repair'], 3)
        self.assertFalse(data['is_closed'])
        self.assertIn("ta'mirga", data['message'])

        self.box.refresh_from_db()
        self.assertFalse(self.box.is_controlled)  # Ta'mirga ketgani uchun hali yopilmagan
        self.assertEqual(self.box.status, Box.Status.IN_PROGRESS)
        self.assertEqual(self.box.controlled_first_sort_qty, 37)
        self.assertEqual(self.box.controlled_second_sort_qty, 10)
        self.assertEqual(self.box.controlled_repair_qty, 3)

        # 2. Quti qayta qidirilganda REPAIR_RETURN rejimida chiqishi kerak (chunki repair_qty = 3)
        res_lookup = self.client.get(reverse('control:api_lookup'), {'code': self.box.box_code})
        self.assertEqual(res_lookup.json()['box']['mode'], 'REPAIR_RETURN')
        self.assertEqual(res_lookup.json()['box']['controlled_repair_qty'], 3)

        # 3. Ta'mirdan qaytish: 3 tadan 1 tasi tuzalmas brak, 0 tasi qayta ta'mir -> 2 tasi 1-sortga qo'shiladi!
        repair_payload = {
            'box_id': self.box.id,
            'mode': 'REPAIR_RETURN',
            'defect_qty': 1,
            're_repair_qty': 0
        }
        res_rep = self.client.post(
            reverse('control:api_submit'),
            json.dumps(repair_payload),
            content_type='application/json'
        )
        self.assertEqual(res_rep.status_code, 200)
        self.assertTrue(res_rep.json()['is_closed'])
        self.assertIn("yopildi", res_rep.json()['message'])

        self.box.refresh_from_db()
        # 1-sort: 37 + 2 = 39 ta!
        self.assertEqual(self.box.controlled_first_sort_qty, 39)
        self.assertEqual(self.box.controlled_second_sort_qty, 10)
        self.assertEqual(self.box.controlled_defect_qty, 1)
        self.assertEqual(self.box.controlled_repair_qty, 0)
        self.assertTrue(self.box.is_controlled)  # Endi quti to'liq yopildi
        self.assertEqual(self.box.status, Box.Status.COMPLETED)
        # Jami: 39 (1-sort) + 10 (2-sort) + 1 (brak) = 50 ta!

    def test_mandatory_control_sticker_printed(self):
        # Quti stikerlari chop etilganda oxirida CONTROL stikeri chiqishi kerak
        self.client.force_login(self.superadmin)
        url = reverse('production:box_print_stickers', kwargs={'box_id': self.box.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "CONTROL")
        self.assertContains(res, "SIFAT NAZORATI")
        self.assertContains(res, "CONTROL STIKER")
        self.assertContains(res, f"CONTROL:{self.box.box_code}")

    def test_patok_users_generation_and_command(self):
        from django.core.management import call_command
        from io import StringIO

        # 1. 0011 migratsiyasi orqali patok1 dan patok40 gacha yaratilganini tekshirish
        self.assertTrue(User.objects.filter(username="patok1", role=User.Role.CONTROL).exists())
        self.assertTrue(User.objects.filter(username="patok40", role=User.Role.CONTROL).exists())

        # 2. Management command create_patok_users ni sinash
        out = StringIO()
        call_command('create_patok_users', count=10, reset_passwords=True, stdout=out)
        self.assertIn("Jami: 10 ta patok", out.getvalue())

        # 3. SuperAdmin panelidagi generate_patok_users action ini sinash
        self.client.force_login(self.superadmin)
        res = self.client.post(reverse('superadmin_users'), {
            'action': 'generate_patok_users',
            'count': 15,
            'reset_passwords': '1'
        })
        self.assertEqual(res.status_code, 302)
        self.assertIn("?role=CONTROL", res.url)
        self.assertEqual(User.objects.filter(username="patok15", role=User.Role.CONTROL).count(), 1)

    def test_unique_boxes_and_piece_aggregations_metric(self):
        """
        Bitta quti bir necha marta (masalan, ta'mirga borib qaytganda) tekshirilsa ham,
        bugungi hisoblagichda unikal qutilar soni 1 ta deb ko'rsatilishi kerak.
        Shuningdek, 1-sort, 2-sort va brak donalari to'g'ri agregatsiya qilinishi kerak.
        """
        self.client.force_login(self.controller)

        # 1-quti: Birlamchi tekshiruv (50 dona: 37 ta 1-sort, 10 ta 2-sort, 3 ta ta'mir)
        payload1 = {
            'box_id': self.box.id,
            'mode': 'INITIAL',
            'total_qty': 50,
            'second_sort_qty': 10,
            'repair_qty': 3
        }
        res1 = self.client.post(reverse('control:api_submit'), json.dumps(payload1), content_type='application/json')
        self.assertEqual(res1.status_code, 200)

        # 1-quti: Ta'mirdan qaytish (3 ta ta'mirdan 2 tasi tuzaldi -> 1-sort, 1 tasi brak)
        payload2 = {
            'box_id': self.box.id,
            'mode': 'REPAIR_RETURN',
            'defect_qty': 1,
            're_repair_qty': 0
        }
        res2 = self.client.post(reverse('control:api_submit'), json.dumps(payload2), content_type='application/json')
        self.assertEqual(res2.status_code, 200)

        # Loglar soni 2 ta bo'lishi kerak, lekin UNIKAL qutilar soni aniq 1 ta bo'lishi shart!
        res_recent = self.client.get(reverse('control:api_recent'))
        self.assertEqual(res_recent.status_code, 200)
        data = res_recent.json()
        self.assertEqual(data['status'], 'OK')
        self.assertEqual(len(data['logs']), 2)  # 2 ta tekshiruv amali bo'lgan
        self.assertEqual(data['today_boxes_count'], 1)  # FAQAT 1 TA UNIKAL QUTI!
        self.assertEqual(data['today_first_sort'], 39)  # 37 + 2 = 39 dona
        self.assertEqual(data['today_second_sort'], 10)  # 10 dona
        self.assertEqual(data['today_defects'], 1)  # 1 dona brak

        # Sahifa HTML'ida ham bugungi unikal qutilar va donalar soni to'g'ri chiqishini tekshirish
        res_page = self.client.get(reverse('control:home'))
        self.assertEqual(res_page.status_code, 200)
        self.assertEqual(res_page.context['today_boxes_count'], 1)
        self.assertEqual(res_page.context['today_first_sort'], 39)
        self.assertEqual(res_page.context['today_second_sort'], 10)
        self.assertEqual(res_page.context['today_defects'], 1)

        # Endi 2-qutini kiritamiz (30 dona, barchasi 1-sort)
        box2 = Box.objects.create(
            order=self.order,
            article=self.article,
            box_number=2,
            quantity=30,
            razmer="L",
            pastal_number="P-102"
        )
        payload3 = {
            'box_id': box2.id,
            'mode': 'INITIAL',
            'total_qty': 30,
            'second_sort_qty': 0,
            'repair_qty': 0
        }
        res3 = self.client.post(reverse('control:api_submit'), json.dumps(payload3), content_type='application/json')
        self.assertEqual(res3.status_code, 200)

        # Endi unikal qutilar soni 2 ta, 1-sort esa 39 + 30 = 69 bo'lishi kerak
        res_recent2 = self.client.get(reverse('control:api_recent'))
        data2 = res_recent2.json()
        self.assertEqual(data2['today_boxes_count'], 2)  # 2 ta unikal quti!
        self.assertEqual(data2['today_first_sort'], 69)  # 39 + 30 = 69
        self.assertEqual(data2['today_second_sort'], 10)
        self.assertEqual(data2['today_defects'], 1)













