"""
Sanoqchi 1-sortni uradi (egasi talabi 2026-11-14): avval taxlovchi badge'i, keyin uning qog'ozlari — CONTROL stikeri
(qog'ozda 50, bazada OTK 1-sorti 40) va TAMIRn cheklari, har biri bir marta, tartib ahamiyatsiz; "Saqlash va yuborish"
bilan upakovkaning 1-sort balansiga. Qutining qog'ozlari va ta'mir tarixi.
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production.models import Article, Box, BoxQualityInspectionLog, OperationGroup, Order, PackingBatch, PackingScan
from production.packing_services import is_beta_box, paper_status


class CounterPackingTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='cp_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.counter = User.objects.create_user(username='sanoqchi7', password='x', role=User.Role.COUNTER)
        self.counter2 = User.objects.create_user(username='sanoqchi8', password='x', role=User.Role.COUNTER)
        self.packing = User.objects.create_user(username='upakovka7', password='x', role=User.Role.PACKING)
        self.packer = Worker.objects.create(worker_id='W-901', first_name='Aziz', last_name="G'oyipov")
        self.packer2 = Worker.objects.create(worker_id='W-902', first_name='Malika', last_name='Karimova')
        self.article = Article.objects.create(code='ART-CP', name='Futbolka')
        self.order = Order.objects.create(order_number='ORD-CP', article=self.article, packing_beta=True)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=50,
                                      razmer='M', assigned_screen=3)
        self.code = self.box.box_code

    def post(self, user, name, payload):
        self.client.force_login(user)
        return self.client.post(reverse(f'packing:{name}'), json.dumps(payload), content_type='application/json')

    def otk(self, **payload):
        res = self.post(self.admin, 'api_otk_submit', dict(box_id=self.box.id, **payload))
        self.assertEqual(res.status_code, 200, res.content)

    def initial_and_return(self, re_repair=2):
        # A11-1 misoli: 50 ta → 40 ta 1-sort, 7 ta ta'mir, 3 ta 2-sort; ta'mirdan 5 tasi 1-sort, 2 tasi yana ta'mir
        self.otk(mode='INITIAL', total_qty=50, second_sort_qty=3, repair_qty=7)
        self.otk(mode='REPAIR_RETURN', defect_qty=0, re_repair_qty=re_repair)

    def paper(self, code, worker=None, user=None):
        return self.post(user or self.counter, 'api_counter_paper', {'code': code, 'worker_id': (worker or self.packer).id}).json()

    def waiting_total(self):
        self.client.force_login(self.admin)
        return self.client.get(reverse('packing:hub'), {'tab': 'upakovka'}).context['waiting_total']

    def test_counter_scans_real_counts_in_any_order_and_sends(self):
        self.initial_and_return()
        identified = self.post(self.counter, 'api_counter_packer', {'code': 'WORKER:W-901'}).json()
        self.assertEqual(identified['worker']['name'], "Aziz G'oyipov")

        first = self.paper(f"TAMIR1:{self.code}")                               # avval ta'mir cheki — muammo yo'q
        self.assertEqual((first['status'], first['line']['quantity'], first['line']['paper']), ('OK', 5, "1-ta'mir"))
        second = self.paper(f"CONTROL:{self.code}")
        self.assertEqual(second['line']['quantity'], 40)                         # stikerda 50, bazada 40
        self.assertEqual(second['batch']['total'], 45)
        self.assertEqual(self.paper(f"CONTROL:{self.code}")['status'], 'NOTHING')   # bir marta
        self.assertEqual(self.paper(f"CONTROL:{self.code}", user=self.counter2)['status'], 'NOTHING')  # boshqa planshetda ham
        self.assertEqual(self.paper(f"TAMIR2:{self.code}")['status'], 'NOTHING')     # hali ta'mirda

        self.assertEqual(self.waiting_total(), 0)                               # yuborilmaguncha upakovkada yo'q
        sent = self.post(self.counter, 'api_counter_send', {'worker_id': self.packer.id}).json()
        self.assertEqual(sent['status'], 'OK')
        self.assertEqual(sent['worker']['today'], 45)
        self.assertEqual(self.waiting_total(), 45)
        batch = PackingBatch.objects.get()
        self.assertEqual((batch.status, batch.worker, batch.counter), (PackingBatch.Status.SENT, self.packer, self.counter))
        self.assertEqual(self.post(self.counter, 'api_counter_send', {'worker_id': self.packer.id}).json()['status'], 'EMPTY')

    def test_remove_line_frees_paper_and_open_list_resumes(self):
        self.initial_and_return()
        line = self.paper(f"CONTROL:{self.code}")['line']
        resumed = self.post(self.counter, 'api_counter_packer', {'code': 'W-901'}).json()
        self.assertEqual(resumed['batch']['total'], 40)                          # badge qayta urilsa ro'yxat davom etadi
        removed = self.post(self.counter, 'api_counter_remove', {'scan_id': line['id']}).json()
        self.assertEqual(removed['batch']['total'], 0)
        self.assertEqual(self.paper(f"CONTROL:{self.code}", worker=self.packer2)['line']['quantity'], 40)

    def test_paper_status(self):
        self.initial_and_return(re_repair=2)
        status = paper_status(self.box)
        self.assertEqual(status['repair_cycles'], 2)
        self.assertEqual([(p['label'], p['state'], p['waiting']) for p in status['papers']],
                         [('CONTROL', 'waiting', 40), ("1-ta'mir", 'waiting', 5), ("2-ta'mir", 'in_repair', 0)])

        error = self.paper(f"TAMIR2:{self.code}")                              # xato bo'lsa ham qutining qog'ozlari chiqadi
        self.assertEqual(error['box']['repair_cycles'], 2)
        self.paper(f"CONTROL:{self.code}")
        done = paper_status(self.box)['papers'][0]
        self.assertEqual((done['state'], done['scans'][0]['sent']), ('done', False))   # ochiq ro'yxatda — urilgan
        self.client.force_login(self.counter)
        page = self.client.get(reverse('packing:hub'), {'tab': 'sanoqchi'})
        self.assertNotIn('unscanned', page.context)                             # "Hali urilmagan qog'ozlar" olib tashlangan
        self.assertNotContains(page, "Hali urilmagan qog'ozlar")

    def test_template_beta_box_without_order_flag(self):
        group = OperationGroup.objects.create(name='BETA SHABLON', is_beta=True, taxlov_price=Decimal('1'),
                                              beta_since=timezone.now())
        article = Article.objects.create(code='ART-TB', name='Shim')
        article.operation_group = group
        article.save()
        order = Order.objects.create(order_number='ORD-TB', article=article)          # zakazda belgi yo'q
        new_box = Box.objects.create(order=order, article=article, box_number=1, quantity=20, razmer='L')
        self.otk_box(new_box, total_qty=20)
        self.assertEqual(self.paper(f"CONTROL:{new_box.box_code}")['line']['quantity'], 20)

        old_box = Box.objects.create(order=order, article=article, box_number=2, quantity=20, razmer='L')
        Box.objects.filter(pk=old_box.pk).update(created_at=group.beta_since - timezone.timedelta(days=1))
        self.assertEqual(self.paper(f"CONTROL:{old_box.box_code}")['status'], 'NOT_BETA')   # belgidan oldingi quti

    def otk_box(self, box, **payload):
        res = self.post(self.admin, 'api_otk_submit', dict(box_id=box.id, mode='INITIAL', **payload))
        self.assertEqual(res.status_code, 200, res.content)

    def test_counter_api_roles(self):
        self.initial_and_return()
        self.assertEqual(self.post(self.packing, 'api_counter_paper', {'code': self.code, 'worker_id': self.packer.id}).status_code, 403)
        self.assertEqual(self.post(self.counter, 'api_counter_packer', {'code': 'W-404'}).json()['status'], 'NOT_FOUND')
        self.assertEqual(self.post(self.counter, 'api_counter_paper', {'code': self.code, 'worker_id': 0}).json()['status'], 'NO_WORKER')
        self.assertFalse(PackingScan.objects.exists())


class OrderBetaStartsTodayTest(TestCase):
    """Zakazga beta belgisi qo'yilsa — faqat shu vaqtdan keyin OTK dan o'tgan qutilar (egasi talabi 2026-11-16)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username='bt_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.counter = User.objects.create_user(username='sanoqchi_bt', password='x', role=User.Role.COUNTER)
        self.packer = Worker.objects.create(worker_id='W-960', first_name='Bek', last_name='Taxlov')
        self.article = Article.objects.create(code='ART-BT', name='Futbolka')
        self.order = Order.objects.create(order_number='ORD-BT', article=self.article)          # hali beta emas
        self.old_box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=30,
                                          razmer='M', assigned_screen=2)
        self.new_box = Box.objects.create(order=self.order, article=self.article, box_number=2, quantity=20,
                                          razmer='M', assigned_screen=2)

    def otk(self, box):
        self.client.force_login(self.admin)
        return self.client.post(reverse('control:api_submit'), json.dumps(
            {'box_id': box.id, 'mode': 'INITIAL', 'total_qty': box.quantity, 'second_sort_qty': 2}),
            content_type='application/json')

    def test_only_boxes_inspected_after_beta_enabled(self):
        self.otk(self.old_box)                                                  # belgidan OLDIN OTK
        BoxQualityInspectionLog.objects.filter(box=self.old_box).update(
            created_at=timezone.now() - timezone.timedelta(minutes=5))
        self.client.force_login(self.admin)
        self.client.post(reverse('packing:beta_toggle'), {'order_id': self.order.id})
        self.order.refresh_from_db()
        self.assertTrue(self.order.packing_beta and self.order.packing_beta_since)
        self.otk(self.new_box)                                                  # belgidan KEYIN OTK

        self.assertFalse(is_beta_box(self.old_box))
        self.assertTrue(is_beta_box(self.new_box))
        self.client.force_login(self.counter)
        page = self.client.get(reverse('packing:hub'), {'tab': 'sanoqchi'})
        self.assertEqual([(p['code'], p['qty']) for p in page.context['patoks']], [('K2', 2)])   # faqat yangi qutining 2-sorti
        old = self.client.post(reverse('packing:api_counter_paper'), json.dumps(
            {'code': f"CONTROL:{self.old_box.box_code}", 'worker_id': self.packer.id}), content_type='application/json')
        self.assertEqual(old.json()['status'], 'NOT_BETA')

        self.client.force_login(self.admin)
        self.client.post(reverse('packing:beta_toggle'), {'order_id': self.order.id})          # o'chirildi
        self.order.refresh_from_db()
        self.assertEqual((self.order.packing_beta, self.order.packing_beta_since), (False, None))
        self.assertFalse(is_beta_box(Box.objects.get(pk=self.new_box.pk)))
