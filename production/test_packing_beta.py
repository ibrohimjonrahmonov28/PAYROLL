"""
Beta (2026-11-12): bitta /beta/ sahifasi — OTK ta'mir cheki ("TAMIR:<box_code>"), taxlovchi CONTROL/TAMIR qismlari,
sanoqchi 2-sorti, rollar bo'yicha tablar. Muzlatilgan /control/ sahifasi o'zgarmaydi.
"""
import json

from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Worker
from production.models import (Article, Box, BoxQualityInspectionLog, Order, PackingScan,
                               RepairReceipt, SecondSortBatch)


class PackingBetaTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='pk_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.otk = User.objects.create_user(username='patokk1', password='x', role=User.Role.CONTROL, assigned_screen=1)
        self.packing = User.objects.create_user(username='upakovka1', password='x', role=User.Role.PACKING)
        self.counter = User.objects.create_user(username='sanoqchi1', password='x', role=User.Role.COUNTER)
        self.packer = Worker.objects.create(worker_id='W-900', first_name='Taxlovchi', last_name='Bir')
        self.article = Article.objects.create(code='ART-PK', name='Futbolka')
        self.order = Order.objects.create(order_number='ORD-PK', article=self.article, packing_beta=True)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=50,
                                      razmer='M', assigned_screen=1)
        self.hub = reverse('packing:hub')

    def post_json(self, user, url, payload):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(payload), content_type='application/json')

    def otk_submit(self, box, **payload):
        res = self.post_json(self.admin, reverse('packing:api_otk_submit'), dict(box_id=box.id, **payload))
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def initial(self, box=None):
        return self.otk_submit(box or self.box, mode='INITIAL', total_qty=50, second_sort_qty=3, repair_qty=7,
                               repair_details=[{'item_number': 1, 'operation_names': ['Yoqa'], 'workers': ['Malika (W-1)']}])

    def repair_return(self, defect=0, re_repair=0):
        return self.otk_submit(self.box, mode='REPAIR_RETURN', defect_qty=defect, re_repair_qty=re_repair)

    def scan(self, code):
        return self.post_json(self.packing, reverse('packing:api_scan'), {'code': code, 'worker_id': self.packer.id}).json()

    def test_otk_tab_prints_repair_receipt(self):
        data = self.initial()
        receipt = RepairReceipt.objects.get(box=self.box)
        self.assertEqual((receipt.quantity, receipt.qr_text), (7, f"TAMIR:{self.box.box_code}"))
        self.assertEqual(receipt.details[0]['workers'], ['Malika (W-1)'])
        self.assertEqual(data['receipt_url'], reverse('packing:repair_receipt', args=[receipt.id]) + '?print=1')

        page = self.client.get(data['receipt_url'])
        self.assertContains(page, f"TAMIR:{self.box.box_code}")
        self.assertContains(page, 'window.print()')
        self.assertEqual([r.id for r in self.client.get(self.hub, {'tab': 'otk'}).context['receipts']], [receipt.id])

    def test_otk_tab_takes_only_beta_boxes(self):
        old = Order.objects.create(order_number='ORD-OLD', article=self.article)
        old_box = Box.objects.create(order=old, article=self.article, box_number=1, quantity=50, razmer='M')
        self.client.force_login(self.admin)
        lookup = self.client.get(reverse('packing:api_otk_lookup'), {'code': f"CONTROL:{old_box.box_code}"})
        self.assertEqual(lookup.json()['status'], 'NOT_BETA')
        res = self.post_json(self.admin, reverse('packing:api_otk_submit'),
                             {'box_id': old_box.id, 'mode': 'INITIAL', 'total_qty': 50})
        self.assertEqual(res.json()['status'], 'NOT_BETA')
        old_box.refresh_from_db()
        self.assertIsNone(old_box.controlled_at)

    def test_control_page_unchanged_but_beta_box_still_gets_receipt(self):
        res = self.post_json(self.otk, reverse('control:api_submit'),
                             {'box_id': self.box.id, 'mode': 'INITIAL', 'total_qty': 50, 'repair_qty': 2})
        self.assertNotIn('receipt_url', res.json())
        self.assertEqual(RepairReceipt.objects.get(box=self.box).quantity, 2)
        self.assertNotContains(self.client.get(reverse('control:home')), 'receipt_url')

    def test_tamir_qr_opens_repair_return(self):
        self.initial()
        self.client.force_login(self.admin)
        res = self.client.get(reverse('packing:api_otk_lookup'), {'code': f"TAMIR:{self.box.box_code}"})
        self.assertEqual(res.json()['box']['mode'], 'REPAIR_RETURN')

    def test_each_paper_gets_only_its_own_portion(self):
        # Egasi misoli: 40 ta 1-sort hali urilmagan, ta'mirdan 4 tasi 1-sort bo'ldi (3 tasi qayta ta'mirga)
        self.initial()
        self.repair_return(re_repair=3)
        identified = self.post_json(self.packing, reverse('packing:api_identify'), {'code': 'WORKER:W-900'}).json()
        self.assertEqual(identified['worker']['id'], self.packer.id)

        self.assertEqual(self.scan(f"TAMIR:{self.box.box_code}")['scan']['quantity'], 4)
        self.assertEqual(self.scan(f"CONTROL:{self.box.box_code}")['scan']['quantity'], 40)
        self.assertEqual(self.scan(f"CONTROL+{self.box.box_code}")['status'], 'NOTHING')   # qayta urish hech narsa qo'shmaydi
        self.assertEqual(self.scan(f"TAMIR:{self.box.box_code}")['status'], 'NOTHING')

        self.repair_return(re_repair=0)   # qolgan 3 tasi tuzaldi, o'sha chek qaytdi
        self.assertEqual(self.scan(f"TAMIR:{self.box.box_code}")['scan']['quantity'], 3)
        self.assertEqual(self.client.get(self.hub, {'tab': 'upakovka'}).context['waiting_total'], 47)

    def test_typed_box_code_counts_as_control_sticker(self):
        # Taxlovchi quti kodini qo'lda yozadi (stiker ustidagi yozuv) — CONTROL stikeri bilan bir xil
        self.initial()
        self.assertEqual(self.scan(self.box.box_code.lower())['scan']['quantity'], 40)
        self.assertEqual(self.scan(f"CONTROL:{self.box.box_code}")['status'], 'NOTHING')
        self.assertEqual(self.scan(f"BOX:{self.box.box_code}")['status'], 'NOTHING')
        self.assertEqual(self.scan('TK-12345678')['status'], 'BAD_CODE')
        self.assertEqual(self.scan('ZZ-404')['status'], 'NOT_FOUND')
        self.client.force_login(self.counter)
        listed = self.client.get(self.hub, {'tab': 'sanoqchi', 'cancel': self.box.box_code}).context['cancel_scans']
        self.assertEqual(len(listed), 1)

    def test_scan_errors(self):
        self.initial()
        no_worker = self.post_json(self.packing, reverse('packing:api_scan'), {'code': f"CONTROL:{self.box.box_code}"})
        self.assertEqual(no_worker.json()['status'], 'NO_WORKER')
        self.assertEqual(self.scan('TICKET:TK-1')['status'], 'BAD_CODE')
        self.order.packing_beta = False
        self.order.save(update_fields=['packing_beta'])
        self.assertEqual(self.scan(f"CONTROL:{self.box.box_code}")['status'], 'NOT_BETA')

    def test_counter_counts_second_sort_and_cancels_scans(self):
        self.initial()                              # 3 ta 2-sort
        self.repair_return(defect=1, re_repair=0)   # ta'mirdan 1 ta brak (2-sort)
        self.client.force_login(self.counter)
        page = self.client.get(self.hub, {'tab': 'sanoqchi'})
        self.assertEqual([(p['code'], p['qty']) for p in page.context['patoks']], [('K1', 4)])
        self.assertEqual(self.client.get(self.hub, {'tab': 'sanoqchi', 'patok': 1}).context['cards'][0]['total'], 4)

        data = {'screen': 1, 'order_id': self.order.id, 'article_id': self.article.id}
        self.client.post(reverse('packing:counter_accept'), dict(data, counted='3'))   # OTK soniga teng emas
        self.assertFalse(SecondSortBatch.objects.exists())
        res = self.client.post(reverse('packing:counter_accept'), dict(data, counted='4'))
        self.assertRedirects(res, f"{self.hub}?tab=sanoqchi&patok=1", fetch_redirect_response=False)
        basket = SecondSortBatch.objects.get(counter=self.counter)
        self.assertEqual(basket.status, SecondSortBatch.Status.BASKET)
        self.assertEqual(self.client.get(self.hub, {'tab': 'sanoqchi'}).context['patoks'], [])

        self.client.post(reverse('packing:counter_send'))
        basket.refresh_from_db()
        self.assertEqual(basket.status, SecondSortBatch.Status.SENT)
        self.client.force_login(self.packing)
        self.assertEqual(self.client.get(self.hub, {'tab': 'upakovka'}).context['second_sort_total'], 4)

        # xato urilgan taxlovchi skani sanoqchida bekor qilinadi va qog'oz qayta urilishi mumkin
        self.scan(f"CONTROL:{self.box.box_code}")
        scan = PackingScan.objects.get(source='CONTROL')
        code = f"CONTROL:{self.box.box_code}"
        self.client.force_login(self.counter)
        listed = self.client.get(self.hub, {'tab': 'sanoqchi', 'cancel': code}).context['cancel_scans']
        self.assertEqual([s.id for s in listed], [scan.id])
        self.client.post(reverse('packing:counter_cancel'), {'scan_id': scan.id, 'code': code})
        scan.refresh_from_db()
        self.assertTrue(scan.is_cancelled)
        self.assertEqual(self.scan(code)['scan']['quantity'], 40)

    def test_tabs_follow_roles(self):
        self.client.force_login(self.packing)
        page = self.client.get(self.hub, {'tab': 'otk'})
        self.assertEqual([key for key, _ in page.context['tabs']], ['taxlovchi', 'upakovka'])
        self.assertEqual(page.context['tab'], 'taxlovchi')
        self.assertRedirects(self.client.get(reverse('production:order_list')), self.hub, fetch_redirect_response=False)
        res = self.post_json(self.packing, reverse('packing:api_otk_submit'), {'box_id': self.box.id, 'mode': 'INITIAL'})
        self.assertEqual(res.status_code, 403)

        self.client.force_login(self.counter)
        self.assertEqual([key for key, _ in self.client.get(self.hub).context['tabs']], ['sanoqchi'])
        self.assertEqual(self.post_json(self.counter, reverse('packing:api_scan'), {'code': 'CONTROL:X'}).status_code, 403)
        self.client.force_login(self.counter)
        self.client.post(reverse('packing:beta_toggle'), {'order_id': self.order.id})
        self.order.refresh_from_db()
        self.assertTrue(self.order.packing_beta)   # sanoqchi o'zgartira olmaydi

        self.client.force_login(self.admin)
        tabs = [key for key, _ in self.client.get(self.hub).context['tabs']]
        self.assertEqual(tabs, ['otk', 'taxlovchi', 'sanoqchi', 'upakovka', 'zakazlar'])
        self.client.post(reverse('packing:beta_toggle'), {'order_id': self.order.id})
        self.order.refresh_from_db()
        self.assertFalse(self.order.packing_beta)

    def test_all_tabs_render_for_superadmin(self):
        self.initial()
        self.repair_return(defect=1)
        self.scan(f"CONTROL:{self.box.box_code}")
        self.client.force_login(self.admin)
        for params in ({'tab': 'otk'}, {'tab': 'taxlovchi'}, {'tab': 'upakovka'}, {'tab': 'zakazlar', 'q': 'ORD-PK'},
                       {'tab': 'sanoqchi', 'patok': 1, 'cancel': f"CONTROL:{self.box.box_code}"},
                       {'tab': 'sanoqchi', 'cancel': 'TICKET:1'}):
            self.assertEqual(self.client.get(self.hub, params).status_code, 200, params)
        self.assertContains(self.client.get(self.hub, {'tab': 'zakazlar', 'q': 'ORD-PK'}), 'ORD-PK')

    def test_login_redirects_to_beta_page(self):
        for user in (self.packing, self.counter):
            self.client.logout()
            res = self.client.post(reverse('root_login'), {'username': user.username, 'password': 'x'})
            self.assertRedirects(res, self.hub, fetch_redirect_response=False)

    def test_otk_lookup_returns_patok_and_existing_receipt(self):
        initial = self.initial()
        self.assertEqual(initial['receipt_meta']['patok_code'], 'K1')
        self.client.force_login(self.admin)
        res = self.client.get(reverse('packing:api_otk_lookup'), {'code': f"TAMIR:{self.box.box_code}"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['box']['patok_code'], 'K1')
        self.assertEqual(data['existing_receipt']['quantity'], 7)
        self.assertEqual(data['existing_receipt']['remaining'], 7)
        self.assertIn('/beta/tamir-cheki/', data['existing_receipt']['receipt_url'])

    def test_counter_basket_remove_and_box_breakdown(self):
        self.initial()
        self.client.force_login(self.counter)
        patok_page = self.client.get(self.hub, {'tab': 'sanoqchi', 'patok': 1})
        card = patok_page.context['cards'][0]
        self.assertEqual(card['box_count'], 1)
        self.assertEqual(card['boxes'][0]['box_code'], self.box.box_code)
        self.assertEqual(card['boxes'][0]['qty'], 3)

        self.client.post(reverse('packing:counter_accept'), {
            'screen': 1, 'order_id': self.order.id, 'article_id': self.article.id, 'counted': '3',
        })
        self.assertEqual(self.client.get(self.hub, {'tab': 'sanoqchi'}).context['basket_total'], 3)

        res = self.client.post(reverse('packing:counter_remove'), {
            'order_id': self.order.id, 'article_id': self.article.id, 'patok': 1,
        })
        self.assertRedirects(res, f"{self.hub}?tab=sanoqchi&patok=1", fetch_redirect_response=False)
        after = self.client.get(self.hub, {'tab': 'sanoqchi'})
        self.assertEqual(after.context['basket_total'], 0)
        self.assertEqual([(p['code'], p['qty']) for p in after.context['patoks']], [('K1', 3)])

    def test_zakazlar_filter_modes(self):
        Order.objects.create(order_number='ORD-NONBETA', article=self.article, packing_beta=False)
        self.client.force_login(self.admin)
        beta_page = self.client.get(self.hub, {'tab': 'zakazlar', 'filter': 'beta'})
        self.assertEqual([o.order_number for o in beta_page.context['orders']], ['ORD-PK'])
        off_page = self.client.get(self.hub, {'tab': 'zakazlar', 'filter': 'off'})
        self.assertEqual([o.order_number for o in off_page.context['orders']], ['ORD-NONBETA'])

    def test_upakovka_ignores_non_beta_second_sort(self):
        non_beta = Order.objects.create(order_number='ORD-OLD', article=self.article, packing_beta=False)
        old_box = Box.objects.create(order=non_beta, article=self.article, box_number=1, quantity=20, razmer='L')
        sent = SecondSortBatch.objects.create(counter=self.counter, status=SecondSortBatch.Status.SENT)
        BoxQualityInspectionLog.objects.create(box=old_box, second_sort_qty=5, second_sort_batch=sent)
        self.client.force_login(self.packing)
        self.assertEqual(self.client.get(self.hub, {'tab': 'upakovka'}).context['second_sort_total'], 0)

