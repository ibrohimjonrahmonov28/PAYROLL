from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from accounts.models import Worker, User
from production.models import Article, Operation, ArticleOperation, Order, Box, Ticket
from screens.views import get_screen_data


class MasterWebTerminalTest(TestCase):
    def setUp(self):
        self.client = Client()

        # Master User
        self.master = User.objects.create_user(
            username="terminal_master",
            password="password123",
            first_name="Anvar",
            last_name="Karimov",
            role=User.Role.MASTER
        )

        # Worker User
        self.worker_user = User.objects.create_user(
            username="worker_shahlo",
            first_name="Shahlo",
            last_name="Alimova",
            role=User.Role.USER
        )

        # Worker
        self.worker = Worker.objects.create(
            worker_id="W-050",
            first_name="Shahlo",
            last_name="Alimova",
            user=self.worker_user,
            is_active=True
        )

        # Production models
        self.article = Article.objects.create(code="MOD-101", name="Yozgi Bluzka")
        self.op1 = Operation.objects.create(code="OP-10", name="Yeng tikish")
        self.art_op1 = ArticleOperation.objects.create(
            article=self.article,
            operation=self.op1,
            price_per_unit=Decimal("1500.00"),
            sequence=1
        )
        self.order = Order.objects.create(
            order_number="ORD-TERM-01",
            article=self.article,
            total_quantity=200
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
            price_per_unit=Decimal("1500.00"),
            status=Ticket.Status.PENDING
        )
        self.ticket2 = Ticket.objects.create(
            box=self.box,
            article_operation=self.art_op1,
            quantity=50,
            split_index=2,
            total_splits=2,
            price_per_unit=Decimal("1500.00"),
            status=Ticket.Status.PENDING
        )

    def test_terminal_home_view(self):
        res = self.client.get(reverse('production:terminal_home'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "MASTER SKANERLASH TERMINALI")
        self.assertContains(res, "Zebra DS22")

    def test_worker_identification_api(self):
        # 1. Identify by 6-digit UID
        res_uid = self.client.post(reverse('production:terminal_identify_worker'), {'code': self.worker_user.uid})
        self.assertEqual(res_uid.status_code, 200)
        data_uid = res_uid.json()
        self.assertEqual(data_uid['status'], 'OK')
        self.assertEqual(data_uid['worker']['id'], self.worker.id)
        self.assertEqual(data_uid['worker']['worker_id'], 'W-050')

        # 2. Identify by USER:<UID>
        res_qr = self.client.post(reverse('production:terminal_identify_worker'), {'code': f"USER:{self.worker_user.uid}"})
        self.assertEqual(res_qr.status_code, 200)
        self.assertEqual(res_qr.json()['status'], 'OK')

        # 3. Identify by WORKER:<worker_id>
        res_wid = self.client.post(reverse('production:terminal_identify_worker'), {'code': f"WORKER:{self.worker.worker_id}"})
        self.assertEqual(res_wid.status_code, 200)
        self.assertEqual(res_wid.json()['status'], 'OK')

        # 4. Multiple matches when searching ambiguous name
        Worker.objects.create(
            worker_id="W-051",
            first_name="Shahlo",
            last_name="Qosimova",
            is_active=True
        )
        res_mult = self.client.post(reverse('production:terminal_identify_worker'), {'code': 'Shahlo'})
        self.assertEqual(res_mult.status_code, 200)
        data_mult = res_mult.json()
        self.assertEqual(data_mult['status'], 'MULTIPLE_MATCHES')
        self.assertEqual(len(data_mult['candidates']), 2)

        # 5. Non-existent worker
        res_none = self.client.post(reverse('production:terminal_identify_worker'), {'code': 'NOT_EXISTING'})
        self.assertEqual(res_none.status_code, 200)
        self.assertEqual(res_none.json()['status'], 'NOT_FOUND')

    def test_scan_ticket_duplicate_prevention_and_finalize(self):
        self.client.force_login(self.master)

        # 1. Attempt scanning ticket without selecting worker
        res_no_w = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': self.ticket1.ticket_code})
        self.assertEqual(res_no_w.status_code, 200)
        self.assertEqual(res_no_w.json()['status'], 'NO_WORKER')

        # 2. Identify worker
        self.client.post(reverse('production:terminal_identify_worker'), {'code': self.worker.worker_id})

        # 3. Scan ticket 1 (with TICKET: prefix as Zebra DS22 outputs)
        res_scan1 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': f"TICKET:{self.ticket1.ticket_code}"})
        self.assertEqual(res_scan1.status_code, 200)
        data1 = res_scan1.json()
        self.assertEqual(data1['status'], 'OK')
        self.assertEqual(data1['summary']['count'], 1)
        self.assertEqual(data1['summary']['units'], 50)
        self.assertEqual(data1['summary']['amount'], 75000.0) # 50 * 1500

        # 3b. Test mangled scanner input with "TICK. T:" and spaces in place of Cyrillic
        hash_code = self.ticket2.ticket_code.split('-')[-1]
        mangled_code = f"TICK. T:TK- -1-{self.box.box_code}-{hash_code}"
        # Suffix matching should successfully resolve to ticket2!

        # 4. Duplicate scan in same session -> MUST BE REJECTED
        res_dup = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': self.ticket1.ticket_code})
        self.assertEqual(res_dup.status_code, 200)
        self.assertEqual(res_dup.json()['status'], 'DUPLICATE_IN_SESSION')

        # 5. Scan ticket 2 with mangled scanner input
        res_scan2 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': mangled_code})
        self.assertEqual(res_scan2.status_code, 200)
        data2 = res_scan2.json()
        self.assertEqual(data2['status'], 'OK')
        self.assertEqual(data2['summary']['count'], 2)
        self.assertEqual(data2['summary']['units'], 100)
        self.assertEqual(data2['summary']['amount'], 150000.0) # 100 * 1500

        # 6. Test removing ticket 2 from batch
        res_rem = self.client.post(reverse('production:terminal_remove_ticket'), {'ticket_id': self.ticket2.id})
        self.assertEqual(res_rem.status_code, 200)
        data_rem = res_rem.json()
        self.assertEqual(data_rem['summary']['count'], 1)
        self.assertEqual(data_rem['summary']['units'], 50)

        # Scan ticket 2 back
        self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': self.ticket2.ticket_code})

        # 7. Finalize and route to Screen 2
        res_fin = self.client.post(reverse('production:terminal_finalize'), {'screen_number': 2})
        self.assertEqual(res_fin.status_code, 200)
        data_fin = res_fin.json()
        self.assertEqual(data_fin['status'], 'OK')
        self.assertEqual(data_fin['summary']['screen_number'], 2)
        self.assertEqual(data_fin['summary']['tickets_count'], 2)
        self.assertEqual(data_fin['summary']['total_units'], 100)
        self.assertEqual(data_fin['summary']['total_amount'], 150000.0)

        # Verify tickets in DB
        self.ticket1.refresh_from_db()
        self.ticket2.refresh_from_db()
        self.assertEqual(self.ticket1.status, Ticket.Status.SCANNED)
        self.assertEqual(self.ticket1.worker, self.worker)
        self.assertEqual(self.ticket1.screen_number, 2)
        self.assertEqual(self.ticket1.scanned_by, self.master)

        # 8. Attempt scanning already scanned ticket from a new session -> MUST BE REJECTED
        new_client = Client()
        new_client.post(reverse('production:terminal_identify_worker'), {'code': self.worker.worker_id})
        res_already = new_client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': self.ticket1.ticket_code})
        self.assertEqual(res_already.status_code, 200)
        self.assertEqual(res_already.json()['status'], 'ALREADY_SCANNED')

        # 9. Verify Screen 2 has updated in real-time
        screen2_data = get_screen_data(2)
        self.assertEqual(screen2_data['active_workers_count'], 1)
        self.assertEqual(screen2_data['workers'][0]['worker_id'], 'W-050')
        self.assertEqual(screen2_data['grand_total_earnings'], 150000)
        self.assertEqual(screen2_data['grand_total_units'], 100)

    def test_box_lookup_and_worker_balance_api(self):
        # 1. Box lookup
        res_box = self.client.get(reverse('production:terminal_box_lookup'), {'identifier': f"BOX:{self.box.box_code}"})
        self.assertEqual(res_box.status_code, 200)
        data_box = res_box.json()
        self.assertEqual(data_box['status'], 'OK')
        self.assertEqual(data_box['box']['box_code'], self.box.box_code)
        self.assertEqual(data_box['box']['total_tickets'], 2)

        # 2. Worker balance lookup
        res_bal = self.client.get(reverse('production:terminal_worker_balance'), {'code': self.worker.worker_id})
        self.assertEqual(res_bal.status_code, 200)
        data_bal = res_bal.json()
        self.assertEqual(data_bal['status'], 'OK')
        self.assertEqual(data_bal['worker']['worker_id'], 'W-050')

    def test_master_cannot_access_other_modules(self):
        self.client.force_login(self.master)

        # Restricted pages:
        restricted_urls = [
            '/',
            reverse('production:order_list'),
            reverse('production:articles_catalog'),
            reverse('superadmin_dashboard'),
            reverse('accounts:worker_list'),
            '/screens/1/',
        ]

        for url in restricted_urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 302, f"Expected 302 redirect for {url}")
            self.assertIn('/terminal/', res.url, f"Expected redirect to terminal for {url}")

    def test_fast_ticket_lookup_variants(self):
        self.client.force_login(self.master)
        self.client.post(reverse('production:terminal_identify_worker'), {'code': self.worker.worker_id})

        # Set a known stiker_code on ticket1
        self.ticket1.stiker_code = "ABCDEFGH"
        self.ticket1.save()

        # 1. Lookup by 8-character stiker_code with # prefix
        res1 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': '#ABCDEFGH'})
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()['status'], 'OK')
        self.assertEqual(res1.json()['scanned_ticket']['id'], self.ticket1.id)

        # Clear session for next lookup
        self.client.post(reverse('production:terminal_remove_ticket'), {'ticket_id': self.ticket1.id})

        # 2. Lookup by raw 8-character stiker_code (no prefix)
        res2 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': 'abcdefgh'})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()['status'], 'OK')
        self.assertEqual(res2.json()['scanned_ticket']['id'], self.ticket1.id)

        self.client.post(reverse('production:terminal_remove_ticket'), {'ticket_id': self.ticket1.id})

        # 3. Lookup by ticket ID
        res3 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': f"#{self.ticket1.id}"})
        self.assertEqual(res3.status_code, 200)
        self.assertEqual(res3.json()['status'], 'OK')
        self.assertEqual(res3.json()['scanned_ticket']['id'], self.ticket1.id)

        self.client.post(reverse('production:terminal_remove_ticket'), {'ticket_id': self.ticket1.id})

        # 4. Lookup by hash
        hash_part = self.ticket1.ticket_code.split('-')[-1]
        res4 = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': f"-{hash_part}"})
        self.assertEqual(res4.status_code, 200)
        self.assertEqual(res4.json()['status'], 'OK')
        self.assertEqual(res4.json()['scanned_ticket']['id'], self.ticket1.id)

        self.client.post(reverse('production:terminal_remove_ticket'), {'ticket_id': self.ticket1.id})

        # 5. Lookup box code -> should return IS_BOX_CODE warning
        res_box = self.client.post(reverse('production:terminal_scan_ticket'), {'ticket_code': f"BOX:{self.box.box_code}"})
        self.assertEqual(res_box.status_code, 200)
        self.assertEqual(res_box.json()['status'], 'IS_BOX_CODE')


