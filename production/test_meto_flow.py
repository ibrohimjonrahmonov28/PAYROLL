"""
Yangi meto oqimi (beta, egasi talabi 2026-11-19): production/meto_flow.py, /meto-hub/yangi/ va OTK v2 (/control/v2/).
meto → tasnif → stiker → sklad (kg, konteyner) → Koson patogi yoki Uychi yuk xati (qaytarish bilan) → OTK v2 → sanoqchi.
"""
import datetime
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production import meto_flow
from production.container_services import create_containers
from production.meto_flow import FlowError
from production.models import (Article, ArticleOperation, Box, Container, ContainerEvent, CuttingBatch,
                               CuttingBatchItem, Operation, Order, OrderItem, OrderItemSize, PastalFlow, RepairReceipt,
                               Shipment, ShipmentPastal, Ticket)


class MetoFlowBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='mf_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.meto = User.objects.create_user(username='mf_meto', password='x', role=User.Role.METO)
        self.tasnif = User.objects.create_user(username='mf_tasnif', password='x', role=User.Role.TASNIF)
        self.sticker = User.objects.create_user(username='mf_stiker', password='x', role=User.Role.STICKER)
        self.sklad = User.objects.create_user(username='mf_sklad', password='x', role=User.Role.METO_SKLAD)
        self.uychi = User.objects.create_user(username='mf_uychi', password='x', role=User.Role.UYCHI_SKLAD,
                                              branch=User.Branch.UYCHI)
        self.otk = User.objects.create_user(username='patokk3', password='x', role=User.Role.CONTROL, assigned_screen=3)
        self.article = Article.objects.create(code='ART-MF', name='Pijama')
        for seq, name in enumerate(('YON CHOK', 'DAZMOL'), start=1):
            operation = Operation.objects.create(name=name, code=name)
            ArticleOperation.objects.create(article=self.article, operation=operation,
                                            price_per_unit=Decimal('400'), sequence=seq)
        self.order = Order.objects.create(order_number='ZAK-MF', article=self.article, total_quantity=200,
                                          meto_beta=True, meto_beta_since=timezone.now() - datetime.timedelta(hours=1))
        self.order_item = OrderItem.objects.create(order=self.order, article=self.article, quantity=200)
        self.size_m = OrderItemSize.objects.create(order_item=self.order_item, size_name='M', planned_quantity=100)
        self.size_l = OrderItemSize.objects.create(order_item=self.order_item, size_name='L', planned_quantity=100)
        self.batch = self.cut('11-1')

    # ------------------------------------------------------------------ yordamchilar

    def cut(self, code, m=50, l=40, order_item=None):
        order_item = order_item or self.order_item
        batch = CuttingBatch.objects.create(order_item=order_item, pastal_code=code,
                                            batch_number=CuttingBatch.objects.filter(order_item=order_item).count() + 1)
        CuttingBatchItem.objects.create(batch=batch, order_item_size=self.size_m, quantity=m)
        CuttingBatchItem.objects.create(batch=batch, order_item_size=self.size_l, quantity=l)
        return batch

    def items(self, batch=None):
        return list((batch or self.batch).items.order_by('order_item_size__id', 'id'))

    def counts(self, m, l, batch=None):
        first, second = self.items(batch)
        return {first.id: m, second.id: l}

    def to_sklad(self, batch=None, meto=(49, 40), tasnif=(48, 40)):
        batch = batch or self.batch
        meto_flow.take_meto(batch, self.meto)
        meto_flow.finish_meto(batch, self.counts(*meto, batch=batch), self.meto)
        meto_flow.take_tasnif(batch, self.tasnif)
        meto_flow.finish_tasnif(batch, self.counts(*tasnif, batch=batch), self.tasnif)
        return meto_flow.get_flow(batch)

    def boxes(self, batch=None):
        return list(Box.objects.filter(cutting_batch_item__batch=batch or self.batch)
                    .exclude(status=Box.Status.CANCELLED).order_by('box_number'))

    def new_containers(self, count, kind='BIG'):
        first, last = create_containers(count, kind, self.sklad)
        return [Container.objects.get(number=n) for n in range(first, last + 1)]

    def ready(self, batch=None, kg='12.5'):
        """Skladda, bo'lingan, chop etilgan, kg yozilgan, hamma quti konteynerda -> konteyner kodlari."""
        batch = batch or self.batch
        self.to_sklad(batch)
        meto_flow.split_boxes(batch, {}, self.sticker)
        Box.objects.filter(cutting_batch_item__batch=batch).update(is_printed=True)
        meto_flow.sklad_receive(batch, kg, self.sklad)
        codes = []
        boxes = self.boxes(batch)
        for box, container in zip(boxes, self.new_containers(len(boxes))):
            meto_flow.link_container(f"CONTROL:{box.box_code}", container.code, self.sklad)
            codes.append(container.code)
        return codes

    def post_json(self, user, url, payload):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(payload), content_type='application/json')


class MetoFlowStepsTest(MetoFlowBase):
    def test_new_flow_takes_unmetoed_beta_pastals_whenever_cut(self):
        # egasi 2026-11-24: beta zakazning hali meto qilinmagan hamma pastali (belgidan oldin kesilgani ham) yangi oqimda;
        # eski oqim ishlagani (eski meto tasdiqlagan yoki bot patokka bergan) eski oqimda qoladi
        untouched = self.cut('11-0')
        metoed = self.cut('11-7')
        metoed.items.update(status=CuttingBatchItem.Status.METO_CONFIRMED)
        dispatched = self.cut('11-8')
        CuttingBatch.objects.filter(id=dispatched.id).update(
            dispatch_status=CuttingBatch.DispatchStatus.DISPATCHED, assigned_screen=3)
        CuttingBatch.objects.filter(id__in=[untouched.id, metoed.id, dispatched.id]).update(
            created_at=self.order.meto_beta_since - datetime.timedelta(days=3))
        other_order = Order.objects.create(order_number='ZAK-OLD', article=self.article)
        other_item = OrderItem.objects.create(order=other_order, article=self.article, quantity=10)
        old_flow = CuttingBatch.objects.create(order_item=other_item, batch_number=1, pastal_code='11-9')   # beta emas

        self.assertEqual(set(meto_flow.v2_batches()), {self.batch, untouched})
        for batch in (untouched, metoed, dispatched, old_flow):
            batch.refresh_from_db()
            self.assertEqual(meto_flow.is_v2_batch(batch), batch == untouched, batch.pastal_code)
        self.client.force_login(self.meto)
        res = self.client.get(reverse('meto_hub_flow_meto'))           # sahifa oqim yozuvlarini o'zi yaratadi
        self.assertEqual({c['batch'].id for c in res.context['waiting']}, {self.batch.id, untouched.id})
        self.assertEqual(set(PastalFlow.objects.values_list('batch_id', flat=True)), {self.batch.id, untouched.id})

    def test_beta_toggle_since_resets_on_every_enable(self):
        order = Order.objects.create(order_number='ZAK-T', article=self.article)
        self.client.force_login(self.admin)
        url = reverse('meto_hub_flow_beta_toggle', args=[order.id])
        self.client.post(url)
        order.refresh_from_db()
        since = order.meto_beta_since
        self.assertTrue(order.meto_beta and since)
        self.client.post(url)
        self.client.post(url)
        order.refresh_from_db()
        # "hozirdan" qayta boshlanadi: o'chiq paytda kesilib eski oqimda ishlanganlari yangi oqimga tushmaydi
        self.assertTrue(order.meto_beta)
        self.assertGreater(order.meto_beta_since, since)
        self.client.force_login(self.meto)   # faqat superadmin / admin
        self.client.post(url)
        order.refresh_from_db()
        self.assertTrue(order.meto_beta)

    def test_meto_then_tasnif_through_pages(self):
        flow_url = reverse('meto_hub_flow')
        self.client.force_login(self.meto)
        page = self.client.get(reverse('meto_hub_flow_meto'))
        self.assertEqual(page.context['tab'], 'meto')
        self.assertEqual([c['batch'].id for c in page.context['waiting']], [self.batch.id])
        self.client.post(reverse('meto_hub_flow_meto_take', args=[self.batch.id]))
        first, second = self.items()
        res = self.client.post(reverse('meto_hub_flow_meto_finish', args=[self.batch.id]), {f'qty_{first.id}': '49'},
                               follow=True)
        self.assertContains(res, 'L razmeri uchun sonni kiriting')   # bitta razmer bo'sh — saqlanmaydi
        self.client.post(reverse('meto_hub_flow_meto_finish', args=[self.batch.id]),
                         {f'qty_{first.id}': '49', f'qty_{second.id}': '40'})
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.meto_done_by), (PastalFlow.Stage.TASNIF_WAIT, self.meto))
        self.assertEqual([i.flow_size.meto_qty for i in self.items()], [49, 40])

        # meto soni qulflangan: qayta tugatib bo'lmaydi; tasnifni metochi qila olmaydi
        with self.assertRaises(FlowError):
            meto_flow.finish_meto(self.batch, self.counts(10, 10), self.meto)
        self.client.post(reverse('meto_hub_flow_tasnif_take', args=[self.batch.id]))
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.TASNIF_WAIT)

        self.client.force_login(self.tasnif)
        self.assertEqual([key for key, _ in self.client.get(flow_url).context['tabs']], ['tasnif'])
        self.client.post(reverse('meto_hub_flow_tasnif_take', args=[self.batch.id]))
        self.client.post(reverse('meto_hub_flow_tasnif_finish', args=[self.batch.id]),
                         {f'qty_{first.id}': '48', f'qty_{second.id}': '40'})
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.branch, flow.tasnif_done_by), (PastalFlow.Stage.SKLAD, 'HQ', self.tasnif))
        self.assertEqual([(i.flow_size.meto_qty, i.flow_size.tasnif_qty) for i in self.items()], [(49, 48), (40, 40)])

    def test_split_uses_tasnif_count(self):
        self.to_sklad()
        self.client.force_login(self.sticker)
        first, second = self.items()
        self.client.post(reverse('meto_hub_flow_split', args=[self.batch.id]),
                         {f'boxes_{first.id}': '2', f'boxes_{second.id}': '1'})
        self.assertEqual([(b.razmer, b.quantity) for b in self.boxes()], [('M', 24), ('M', 24), ('L', 40)])
        first.refresh_from_db()
        self.assertEqual((first.real_quantity, first.status), (48, CuttingBatchItem.Status.METO_CONFIRMED))
        self.assertTrue(Ticket.objects.filter(box__in=self.boxes()).exists())
        with self.assertRaises(FlowError):   # ikkinchi marta bo'linmaydi
            meto_flow.split_boxes(self.batch, {}, self.sticker)

    def test_split_needs_article_operations(self):
        self.to_sklad()
        ArticleOperation.objects.filter(article=self.article).delete()
        with self.assertRaises(FlowError):
            meto_flow.split_boxes(self.batch, {}, self.sticker)
        self.assertFalse(self.boxes())

    def test_tasnif_count_editable_until_stickers_printed(self):
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        old = self.boxes()
        meto_flow.update_tasnif_counts(self.batch, self.counts(45, 41), self.sticker)
        for box in old:   # chop etilmagan qutilar bekor — kodi o'zgarmaydi, o'chirilmaydi
            box.refresh_from_db()
            self.assertEqual(box.status, Box.Status.CANCELLED)
            self.assertFalse(box.tickets.exclude(status=Ticket.Status.CANCELLED).exists())
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        self.assertEqual([b.quantity for b in self.boxes()], [45, 41])
        self.assertTrue(set(b.box_code for b in self.boxes()).isdisjoint(b.box_code for b in old))

        Box.objects.filter(id=self.boxes()[0].id).update(is_printed=True)
        with self.assertRaises(FlowError):
            meto_flow.update_tasnif_counts(self.batch, self.counts(40, 40), self.sticker)
        self.assertEqual([i.flow_size.tasnif_qty for i in self.items()], [45, 41])

    def test_sklad_receive_needs_valid_kg(self):
        self.to_sklad()
        for bad in ('', 'abc', '0', '-2', 'NaN', 'Infinity', '1e9'):
            with self.assertRaises(FlowError):
                meto_flow.sklad_receive(self.batch, bad, self.sklad)
        meto_flow.sklad_receive(self.batch, '12,5', self.sklad)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.actual_weight_kg, Decimal('12.5'))
        self.assertEqual(meto_flow.get_flow(self.batch).sklad_received_by, self.sklad)


class ContainerLinkTest(MetoFlowBase):
    def setUp(self):
        super().setUp()
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        self.box_m, self.box_l = self.boxes()
        self.c1, self.c2, self.c3 = self.new_containers(3)

    def test_link_by_any_sticker_one_box_per_container(self):
        ticket = self.box_l.tickets.first()
        meto_flow.link_container(f"CONTROL:{self.box_m.box_code}", self.c1.code, self.sklad)
        meto_flow.link_container(ticket.stiker_code, f"kont-{self.c2.number}", self.sklad)
        self.c1.refresh_from_db()
        self.assertEqual((self.c1.box, self.c1.status), (self.box_m, Container.Status.FILLED))
        self.assertEqual(Container.objects.get(box=self.box_l), self.c2)
        self.assertTrue(ContainerEvent.objects.filter(container=self.c1, action=ContainerEvent.Action.LINKED,
                                                      box=self.box_m, user=self.sklad).exists())
        with self.assertRaises(FlowError):   # quti allaqachon konteynerda
            meto_flow.link_container(self.box_m.box_code, self.c3.code, self.sklad)
        with self.assertRaises(FlowError):   # konteyner bo'sh emas
            meto_flow.link_container(self.box_m.box_code, self.c2.code, self.sklad)

    def test_link_refuses_wrong_container_or_box(self):
        small = self.new_containers(1, 'SMALL')[0]
        uychi_busy = self.new_containers(1)[0]
        Container.objects.filter(id=uychi_busy.id).update(branch='UYCHI', status=Container.Status.IN_TRANSIT)
        old_order = Order.objects.create(order_number='ZAK-OLD', article=self.article)
        old_box = Box.objects.create(order=old_order, article=self.article, box_number=1, quantity=10, razmer='M')
        for box_code, container_code in ((self.box_m.box_code, small.code), (self.box_m.box_code, uychi_busy.code),
                                         (self.box_m.box_code, 'KONT-999999'), (old_box.box_code, self.c1.code),
                                         ('YOQ-123', self.c1.code)):
            with self.assertRaises(FlowError, msg=(box_code, container_code)):
                meto_flow.link_container(box_code, container_code, self.sklad)
        self.assertFalse(Container.objects.filter(box__isnull=False).exists())

    def test_empty_container_back_from_uychi_links_at_koson(self):
        # Uychida bo'shagan konteyner darhol mashinada Kosonga qaytadi (egasi 2026-11-21) — Kosonda uriladi
        returned = self.new_containers(1)[0]
        Container.objects.filter(id=returned.id).update(branch='UYCHI')
        meto_flow.link_container(self.box_m.box_code, returned.code, self.sklad)
        returned.refresh_from_db()
        self.assertEqual((returned.branch, returned.status, returned.box), ('HQ', Container.Status.FILLED, self.box_m))
        self.assertTrue(ContainerEvent.objects.filter(container=returned, action=ContainerEvent.Action.RECEIVED,
                                                      branch='HQ', note__contains="bo'sh qaytdi").exists())

    def test_lost_label_unlink_by_sticker_then_relink(self):
        meto_flow.link_container(self.box_m.box_code, self.c1.code, self.sklad)
        meto_flow.unlink(f"CONTROL:{self.box_m.box_code}", self.sklad)   # konteyner QR yo'qolgan — stiker uriladi
        self.c1.refresh_from_db()
        self.assertEqual((self.c1.box, self.c1.status), (None, Container.Status.EMPTY))
        self.assertTrue(ContainerEvent.objects.filter(container=self.c1, action=ContainerEvent.Action.UNLINKED).exists())
        meto_flow.link_container(self.box_m.box_code, self.c2.code, self.sklad)
        self.assertEqual(Container.objects.get(box=self.box_m), self.c2)

    def test_scanner_api_two_steps(self):
        self.client.force_login(self.sklad)
        resolved = self.client.get(reverse('meto_hub_flow_api_resolve'), {'code': f"CONTROL:{self.box_m.box_code}"}).json()
        self.assertEqual((resolved['kind'], resolved['box'], resolved['container']), ('box', self.box_m.box_code, None))
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_link'), {'box': self.box_m.box_code, 'container': self.c1.code})
        self.assertEqual(res.json()['status'], 'OK')
        resolved = self.client.get(reverse('meto_hub_flow_api_resolve'), {'code': self.c1.code}).json()
        self.assertEqual((resolved['kind'], resolved['box'], resolved['state']), ('container', self.box_m.box_code, 'Skladda'))
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_link'), {'box': self.box_l.box_code, 'container': self.c1.code})
        self.assertEqual(res.status_code, 400)
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_unlink'), {'code': self.c1.code})
        self.assertEqual(res.json()['status'], 'OK')
        # Uychi skladi Koson pastalini biriktira olmaydi
        res = self.post_json(self.uychi, reverse('meto_hub_flow_api_link'), {'box': self.box_m.box_code, 'container': self.c1.code})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Container.objects.filter(box__isnull=False).exists())

    def resolve(self, code):
        self.client.force_login(self.sklad)
        return self.client.get(reverse('meto_hub_flow_api_resolve'), {'code': code})

    def test_resolve_container_first_with_tablet_dash(self):
        # Planshet: avval konteyner (qo'lda yozilgan, klaviatura uzun chiziqcha qo'ygan), keyin stiker
        data = self.resolve(f"KONT–{self.c1.number:06d}").json()
        self.assertEqual((data['kind'], data['code'], data['problem']), ('container', self.c1.code, None))
        data = self.resolve(f"CONTROL:{self.box_m.box_code}").json()
        self.assertEqual((data['kind'], data['problem']), ('box', None))
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_link'), {'box': data['box'], 'container': self.c1.code})
        self.assertEqual(res.json()['status'], 'OK')
        self.assertIn('allaqachon', self.resolve(self.c1.code).json()['problem'])
        self.assertIn('allaqachon', self.resolve(self.box_m.box_code).json()['problem'])

    def test_resolve_explains_missing_container_and_cyrillic(self):
        res = self.resolve('KONT-000050')
        self.assertEqual(res.status_code, 400)
        self.assertIn("KONT-000050 konteyneri tizimda yo'q", res.json()['message'])
        self.assertIn(self.c3.code, res.json()['message'])   # oxirgi yaratilgani
        self.assertIn('kirill', self.resolve('ЛЩТЕ-000050').json()['message'])
        self.assertIn('«YOQ-123»', self.resolve('YOQ-123').json()['message'])

    def test_resolve_flags_unlinkable_container_or_box(self):
        small = self.new_containers(1, 'SMALL')[0]
        self.assertIn('kichik', self.resolve(small.code).json()['problem'])
        Container.objects.filter(id=self.c2.id).update(status=Container.Status.IN_TRANSIT)
        self.assertIn("bo'sh emas", self.resolve(self.c2.code).json()['problem'])
        old_order = Order.objects.create(order_number='ZAK-OLD2', article=self.article)
        old_box = Box.objects.create(order=old_order, article=self.article, box_number=1, quantity=10, razmer='M')
        self.assertIn('yangi meto oqimida emas', self.resolve(old_box.box_code).json()['problem'])
        Box.objects.filter(id=self.box_l.id).update(status=Box.Status.CANCELLED)
        self.assertIn('bekor qilingan', self.resolve(self.box_l.box_code).json()['problem'])
        PastalFlow.objects.filter(batch=self.batch).update(stage=PastalFlow.Stage.TASNIF_WORK)
        self.assertIn('skladda emas', self.resolve(self.box_m.box_code).json()['problem'])


class DispatchTest(MetoFlowBase):
    def test_give_to_koson_patok_needs_every_container(self):
        codes = self.ready()
        with self.assertRaises(FlowError):
            meto_flow.give_to_patok(self.batch, 3, codes[:1], self.sklad)
        with self.assertRaises(FlowError):   # Uychi patogi Koson skladidan berilmaydi
            meto_flow.give_to_patok(self.batch, 16, codes, self.sklad)
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.SKLAD)

        meto_flow.give_to_patok(self.batch, 3, [c.lower() for c in codes], self.sklad)
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.dispatch_status, self.batch.assigned_branch, self.batch.assigned_screen),
                         (CuttingBatch.DispatchStatus.DISPATCHED, 'HQ', 3))   # master planshetida qabul qiladi
        self.assertEqual({b.assigned_screen for b in self.boxes()}, {3})
        self.assertEqual(set(Container.objects.values_list('status', flat=True)), {Container.Status.AT_PATOK})
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.AT_PATOK)

    def test_unlinked_box_or_missing_kg_stops_pastal(self):
        codes = self.ready()
        meto_flow.unlink(codes[0], self.sklad)
        with self.assertRaises(FlowError):
            meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        other = self.cut('11-2')
        self.to_sklad(other)
        meto_flow.split_boxes(other, {}, self.sticker)
        Box.objects.filter(cutting_batch_item__batch=other).update(is_printed=True)
        with self.assertRaises(FlowError) as error:
            meto_flow.give_to_patok(other, 3, [], self.sklad)
        self.assertIn('kg', str(error.exception))

    def test_give_api_and_check_list(self):
        codes = self.ready()
        self.client.force_login(self.sklad)
        check = self.client.get(reverse('meto_hub_flow_api_check'), {'batches': str(self.batch.id)}).json()
        self.assertEqual([r['container'] for r in check['batches'][0]['rows']], codes)
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_give'),
                             {'batch_id': self.batch.id, 'screen': 3, 'scanned': codes[:1]})
        self.assertEqual(res.status_code, 400)
        self.assertIn('skaner qilinmagan', res.json()['message'])
        res = self.post_json(self.uychi, reverse('meto_hub_flow_api_give'),
                             {'batch_id': self.batch.id, 'screen': 3, 'scanned': codes})
        self.assertEqual(res.status_code, 400)   # Uychi skladi Koson patogiga bermaydi
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_give'),
                             {'batch_id': self.batch.id, 'screen': 3, 'scanned': codes})
        self.assertEqual(res.json()['status'], 'OK')

    def test_master_reject_returns_pastal_to_sklad(self):
        codes = self.ready()
        meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        CuttingBatch.objects.filter(id=self.batch.id).update(
            dispatch_status=CuttingBatch.DispatchStatus.WAITING, assigned_screen=None, rejection_reason='Noto\'g\'ri model')
        meto_flow.sync_rejected_flows()
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.SKLAD)
        self.assertEqual(set(Container.objects.values_list('status', flat=True)), {Container.Status.FILLED})
        self.assertEqual(ContainerEvent.objects.filter(action=ContainerEvent.Action.RETURNED).count(), len(codes))

    def test_container_state_patok_ichida_after_first_sticker_scan(self):
        codes = self.ready()
        meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        container = Container.objects.get(box=self.boxes()[0])
        self.assertEqual(meto_flow.container_state(container), 'Patok oldida')
        Ticket.objects.filter(id=self.boxes()[0].tickets.first().id).update(status=Ticket.Status.SCANNED)
        self.assertEqual(meto_flow.container_state(container), 'Patok ichida')


class UychiShipmentTest(MetoFlowBase):
    def setUp(self):
        super().setUp()
        self.codes = self.ready()
        self.shipment = meto_flow.create_shipment([self.batch], self.codes, self.sklad, vehicle='01a123bc', driver='Ali')
        self.line = self.shipment.lines.get()

    def test_shipment_moves_containers(self):
        self.assertEqual((self.shipment.number, self.shipment.vehicle_number), ('YX-000001', '01A123BC'))
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.IN_TRANSIT)
        self.assertEqual(set(Container.objects.values_list('status', flat=True)), {Container.Status.IN_TRANSIT})
        with self.assertRaises(FlowError):   # yo'ldagi pastal patokka berilmaydi
            meto_flow.give_to_patok(self.batch, 3, self.codes, self.sklad)

    def test_uychi_accepts_only_complete_then_gives_to_its_patok(self):
        with self.assertRaises(FlowError):
            meto_flow.receive_line(self.line, self.codes[:1], self.uychi)
        line, missing = meto_flow.receive_line(self.line, self.codes, self.uychi)
        self.assertEqual((line.status, missing), (ShipmentPastal.Status.ACCEPTED, []))
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.Status.CLOSED)
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.branch), (PastalFlow.Stage.SKLAD, 'UYCHI'))
        self.assertEqual(set(Container.objects.values_list('status', 'branch')), {(Container.Status.FILLED, 'UYCHI')})

        with self.assertRaises(FlowError):   # Koson patogi emas
            meto_flow.give_to_patok(self.batch, 3, self.codes, self.uychi)
        meto_flow.give_to_patok(self.batch, 16, self.codes, self.uychi)   # U3
        self.batch.refresh_from_db()
        self.assertEqual((self.batch.dispatch_status, self.batch.assigned_branch, self.batch.accepted_by),
                         (CuttingBatch.DispatchStatus.ACCEPTED, 'UYCHI', self.uychi))   # berish = qabul
        self.assertEqual({b.assigned_screen for b in self.boxes()}, {16})

    def test_uychi_returns_incomplete_and_koson_takes_it_back(self):
        back = meto_flow.return_line(self.line, self.uychi, 'Bitta konteyner yo\'q')
        self.line.refresh_from_db()
        self.assertEqual(self.line.status, ShipmentPastal.Status.RETURNED)
        self.assertEqual((back.source_branch, back.destination_branch, back.status), ('UYCHI', 'HQ', Shipment.Status.IN_TRANSIT))
        back_line = back.lines.get()
        self.assertEqual(back_line.batch, self.batch)
        with self.assertRaises(FlowError):   # qaytarishni qaytarib bo'lmaydi
            meto_flow.return_line(back_line, self.uychi)

        line, missing = meto_flow.receive_line(back_line, self.codes[1:], self.sklad)   # Koson har holda qabul qiladi
        self.assertEqual(missing, self.codes[:1])
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.branch), (PastalFlow.Stage.SKLAD, 'HQ'))
        self.assertEqual(set(Container.objects.values_list('status', 'branch')), {(Container.Status.FILLED, 'HQ')})
        self.assertTrue(ContainerEvent.objects.filter(container__number=Container.objects.get(box=self.boxes()[0]).number,
                                                      note__contains='skaner qilinmadi').exists())

    def test_receive_and_return_api_permissions(self):
        url = reverse('meto_hub_flow_api_receive', args=[self.line.id])
        self.assertEqual(self.post_json(self.sklad, url, {'scanned': self.codes}).status_code, 400)   # Uychi qabul qiladi
        self.assertEqual(self.post_json(self.uychi, url, {'scanned': self.codes}).json()['status'], 'OK')
        printed = self.client.get(reverse('meto_hub_shipment_print', args=[self.shipment.id]))
        self.assertContains(printed, 'YX-000001')
        self.assertContains(printed, '11-1')


class OtkV2Test(MetoFlowBase):
    def setUp(self):
        super().setUp()
        codes = self.ready()
        meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        self.box = self.boxes()[0]                                  # M, 48 dona
        self.big = Container.objects.get(box=self.box)
        self.small, self.small2 = self.new_containers(2, 'SMALL')
        self.client.force_login(self.otk)

    def submit(self, **payload):
        return self.client.post(reverse('control:v2_api_submit'), json.dumps(dict(box_id=self.box.id, **payload)),
                                content_type='application/json')

    def test_page_and_lookup_show_containers(self):
        self.assertContains(self.client.get(reverse('control:v2_home')), 'OTK v2')
        data = self.client.get(reverse('control:v2_api_lookup'), {'code': f"CONTROL:{self.box.box_code}"}).json()
        self.assertEqual((data['box']['container_flow'], data['box']['big_container']), (True, self.big.code))

    def test_repair_needs_an_empty_small_container(self):
        for code in ('', self.big.code, 'KONT-999999'):
            res = self.submit(mode='INITIAL', total_qty=48, repair_qty=3, repair_container=code)
            self.assertEqual((res.status_code, res.json()['status']), (400, 'CONTAINER'), code)
        self.assertFalse(RepairReceipt.objects.exists())
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_controlled or self.box.controlled_repair_qty)

        res = self.submit(mode='INITIAL', total_qty=48, repair_qty=3, repair_container=self.small.code)
        self.assertEqual(res.json()['status'], 'OK', res.content)
        receipt = RepairReceipt.objects.get(box=self.box)
        self.small.refresh_from_db()
        self.assertEqual((self.small.status, self.small.repair_receipt), (Container.Status.IN_REPAIR, receipt))
        self.big.refresh_from_db()
        self.assertEqual((self.big.status, self.big.box), (Container.Status.AT_PATOK, self.box))   # 1-sort bor

    def test_no_first_sort_releases_big_container_at_once(self):
        res = self.submit(mode='INITIAL', total_qty=48, second_sort_qty=48)
        self.assertEqual(res.json()['status'], 'OK', res.content)
        self.big.refresh_from_db()
        self.assertEqual((self.big.status, self.big.box), (Container.Status.EMPTY, None))
        self.assertTrue(ContainerEvent.objects.filter(container=self.big, action=ContainerEvent.Action.RELEASED).exists())

    def test_re_repair_keeps_the_same_small_container(self):
        self.submit(mode='INITIAL', total_qty=48, repair_qty=3, repair_container=self.small.code)
        res = self.submit(mode='REPAIR_RETURN', defect_qty=0, re_repair_qty=1)
        self.assertEqual(res.json()['status'], 'OK', res.content)
        second = RepairReceipt.objects.get(box=self.box, cycle=2)
        self.small.refresh_from_db()
        self.assertEqual((self.small.status, self.small.repair_receipt), (Container.Status.IN_REPAIR, second))
        self.submit(mode='REPAIR_RETURN', defect_qty=0, re_repair_qty=0)   # oxirgisi tuzaldi — sanoqchini kutadi
        self.small.refresh_from_db()
        self.assertEqual((self.small.status, self.small.repair_receipt), (Container.Status.AT_PATOK, second))
        self.small2.refresh_from_db()
        self.assertEqual(self.small2.status, Container.Status.EMPTY)

    def test_repair_without_fixed_items_releases_small_container(self):
        self.submit(mode='INITIAL', total_qty=48, repair_qty=2, repair_container=self.small.code)
        self.submit(mode='REPAIR_RETURN', defect_qty=2, re_repair_qty=0)
        self.small.refresh_from_db()
        self.assertEqual((self.small.status, self.small.repair_receipt), (Container.Status.EMPTY, None))

    def test_old_box_works_like_control(self):
        old_order = Order.objects.create(order_number='ZAK-OLD', article=self.article)
        old_box = Box.objects.create(order=old_order, article=self.article, box_number=1, quantity=20, razmer='M',
                                     assigned_screen=3)
        res = self.client.post(reverse('control:v2_api_submit'),
                               json.dumps({'box_id': old_box.id, 'mode': 'INITIAL', 'total_qty': 20, 'repair_qty': 2}),
                               content_type='application/json')
        self.assertEqual(res.json()['status'], 'OK', res.content)
        self.assertTrue(RepairReceipt.objects.filter(box=old_box).exists())
        self.assertEqual(Container.objects.filter(status=Container.Status.IN_REPAIR).count(), 0)

    def test_counter_scan_releases_containers(self):
        from production.packing_services import take_paper
        Order.objects.filter(id=self.order.id).update(packing_beta=True)
        counter = User.objects.create_user(username='mf_sanoqchi', password='x', role=User.Role.COUNTER)
        packer = Worker.objects.create(worker_id='W-950', first_name='Taxlovchi', last_name='Bir')
        self.submit(mode='INITIAL', total_qty=48, repair_qty=3, repair_container=self.small.code)
        scan, error = take_paper(f"CONTROL:{self.box.box_code}", packer, counter)
        self.assertIsNone(error)
        self.big.refresh_from_db()
        self.assertEqual((self.big.status, self.big.box), (Container.Status.EMPTY, None))
        self.small.refresh_from_db()
        self.assertEqual(self.small.status, Container.Status.IN_REPAIR)   # ta'mir cheki hali qaytmagan

        self.submit(mode='REPAIR_RETURN', defect_qty=0, re_repair_qty=0)
        scan, error = take_paper(f"TAMIR1:{self.box.box_code}", packer, counter)
        self.assertIsNone(error)
        self.small.refresh_from_db()
        self.assertEqual((self.small.status, self.small.repair_receipt), (Container.Status.EMPTY, None))


class FlowAccessTest(MetoFlowBase):
    def test_role_tabs(self):
        expected = {
            self.meto: ['meto', 'sklad', 'yukxat'], self.tasnif: ['tasnif'], self.sticker: ['stiker'],
            self.sklad: ['sklad', 'yukxat'], self.uychi: ['uychi', 'yukxat'],
            self.admin: ['meto', 'tasnif', 'stiker', 'sklad', 'uychi', 'yukxat', 'zakazlar'],
        }
        for user, tabs in expected.items():
            self.client.force_login(user)
            hub = self.client.get(reverse('meto_hub_flow'))
            self.assertEqual([key for key, _ in hub.context['tabs']], tabs, user)
            for t in tabs:
                self.assertContains(hub, reverse(f'meto_hub_flow_{t}'))
        self.client.force_login(self.tasnif)
        self.assertRedirects(
            self.client.get(reverse('meto_hub_flow_sklad')),
            reverse('meto_hub_flow'),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get(reverse('meto_hub_flow'), {'tab': 'sklad'}),
            reverse('meto_hub_flow'),
            fetch_redirect_response=False,
        )

    def test_new_roles_stay_inside_new_flow(self):
        for user in (self.tasnif, self.sklad, self.uychi):
            self.client.force_login(user)
            for path in ('/meto-hub/', reverse('production:order_list'), '/meto/'):
                self.assertRedirects(self.client.get(path), reverse('meto_hub_flow'), fetch_redirect_response=False)
        self.client.force_login(self.sklad)
        self.assertEqual(self.client.get(reverse('meto_hub_containers')).status_code, 200)
        self.client.force_login(self.uychi)
        self.assertNotContains(self.client.get(reverse('meto_hub_containers')), reverse('meto_hub_container_create'))
        self.client.force_login(self.tasnif)
        self.assertRedirects(self.client.get(reverse('meto_hub_containers')), reverse('meto_hub_flow'),
                             fetch_redirect_response=False)

    def test_branch_admin_reaches_uychi_tab(self):
        branch_admin = User.objects.create_user(username='mf_filial', password='x', role=User.Role.BRANCH_ADMIN,
                                                branch=User.Branch.UYCHI)
        self.client.force_login(branch_admin)
        hub = self.client.get(reverse('meto_hub_flow'))
        self.assertEqual([key for key, _ in hub.context['tabs']], ['uychi', 'yukxat'])
        res = self.client.get(reverse('meto_hub_flow_uychi'))
        self.assertEqual(res.context['tab'], 'uychi')
        self.assertRedirects(self.client.get('/meto-hub/'), reverse('superadmin_payroll'), fetch_redirect_response=False)

    def test_login_redirects_new_roles(self):
        self.client.logout()
        res = self.client.post(reverse('root_login'), {'username': 'mf_tasnif', 'password': 'x'})
        self.assertRedirects(res, reverse('meto_hub_flow'), fetch_redirect_response=False)

    def test_other_roles_cannot_open_flow(self):
        master = User.objects.create_user(username='mf_master', password='x', role=User.Role.MASTER)
        self.client.force_login(master)
        self.assertNotEqual(self.client.get(reverse('meto_hub_flow')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('meto_hub_flow_meto')).status_code, 200)
        self.assertEqual(self.post_json(master, reverse('meto_hub_flow_api_link'), {}).status_code, 403)

    def test_every_tab_renders_with_data(self):
        self.ready()
        second = self.cut('11-2')
        third = self.cut('11-3')
        meto_flow.take_meto(third, self.meto)
        codes = [c.code for c in Container.objects.filter(box__cutting_batch_item__batch=self.batch)]
        meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        self.client.force_login(self.admin)
        for tab in ('meto', 'tasnif', 'stiker', 'sklad', 'uychi', 'yukxat', 'zakazlar'):
            res = self.client.get(reverse(f'meto_hub_flow_{tab}'))
            self.assertEqual((res.status_code, res.context['tab']), (200, tab))
        res = self.client.get(reverse('meto_hub_flow_meto'))
        self.assertEqual([c['batch'].id for c in res.context['waiting']], [second.id])
        self.assertEqual([c['batch'].id for c in res.context['working']], [third.id])
        res = self.client.get(reverse('meto_hub_flow_sklad'))
        self.assertEqual([c['patok_code'] for c in res.context['patok_rows']], ['K3'])
        self.assertContains(res, 'Patok oldida')
        detail = self.client.get(reverse('meto_hub_container_detail', args=[Container.objects.get(box=self.boxes()[0]).number]))
        self.assertContains(detail, self.boxes()[0].box_code)


class TayyorlovMetoUpgradeTest(MetoFlowBase):
    def test_tayyorlov_url_and_legacy_redirects(self):
        self.assertEqual(reverse('meto_hub_flow'), '/tayyorlov/')
        self.assertEqual(reverse('meto_hub_flow_meto'), '/tayyorlov/meto/')
        self.assertEqual(reverse('meto_hub_flow_tasnif'), '/tayyorlov/tasnif/')
        self.assertEqual(reverse('meto_hub_containers'), '/tayyorlov/konteynerlar/')
        self.client.force_login(self.admin)
        hub = self.client.get('/tayyorlov/')
        self.assertEqual([c['key'] for c in hub.context['bosh_cards']], ['meto', 'tasnif', 'stiker', 'sklad'])
        self.assertEqual([c['key'] for c in hub.context['filial_cards']], ['uychi', 'yukxat'])
        self.assertEqual([c['key'] for c in hub.context['utility_cards']], ['konteynerlar', 'zakazlar'])
        self.assertContains(hub, 'href="/tayyorlov/meto/" target="_blank"')
        self.assertContains(hub, 'href="/tayyorlov/tasnif/" target="_blank"')
        self.assertContains(hub, 'href="/tayyorlov/konteynerlar/" target="_blank"')

        # Alohida bo'lim sahifasida qolgan bo'limlarga havola bo'lmasligi shart
        meto_page = self.client.get('/tayyorlov/meto/')
        for forbidden in ('/tayyorlov/tasnif/', '/tayyorlov/stiker/', '/tayyorlov/sklad/', '/tayyorlov/uychi/',
                          '/tayyorlov/yukxat/', '/tayyorlov/zakazlar/', '/tayyorlov/konteynerlar/', '/meto-hub/'):
            self.assertNotContains(meto_page, f'href="{forbidden}"')
        cont_page = self.client.get('/tayyorlov/konteynerlar/')
        for forbidden in ('/tayyorlov/meto/', '/tayyorlov/tasnif/', '/meto-hub/'):
            self.assertNotContains(cont_page, f'href="{forbidden}"')

        self.assertRedirects(
            self.client.get('/tayyorlov/meto'),
            '/tayyorlov/meto/',
            status_code=301,
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get('/tayyorlov/?tab=meto&q=11-1'),
            '/tayyorlov/meto/?q=11-1',
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get('/meto-hub/yangi/?tab=meto&q=11-1'),
            '/tayyorlov/?tab=meto&q=11-1',
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            self.client.get('/meto-hub/konteynerlar/?tur=BIG'),
            '/tayyorlov/konteynerlar/?tur=BIG',
            fetch_redirect_response=False,
        )
        self.client.force_login(self.tasnif)
        self.assertNotContains(self.client.get('/tayyorlov/'), '/tayyorlov/konteynerlar/')
        self.assertRedirects(
            self.client.get('/tayyorlov/konteynerlar/'),
            '/tayyorlov/',
            fetch_redirect_response=False,
        )

    def test_meto_cancel_note_and_typo_guard(self):
        self.client.force_login(self.meto)
        take_res = self.client.post(reverse('meto_hub_flow_meto_take', args=[self.batch.id]))
        self.assertRedirects(take_res, '/tayyorlov/meto/', fetch_redirect_response=False)
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.METO_WORK)
        with self.assertRaises(FlowError):   # allaqachon qabul qilingan
            meto_flow.take_meto(self.batch, self.admin)

        self.client.post(reverse('meto_hub_flow_meto_cancel', args=[self.batch.id]))
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.meto_taken_by), (PastalFlow.Stage.METO_WAIT, None))

        self.client.post(reverse('meto_hub_flow_meto_take', args=[self.batch.id]))
        first, second = self.items()
        with self.assertRaises(FlowError):   # 50 o'rniga 5000 yozishdan himoya
            meto_flow.finish_meto(self.batch, {first.id: 5000, second.id: 40}, self.meto)

        self.client.post(
            reverse('meto_hub_flow_meto_finish', args=[self.batch.id]),
            {f'qty_{first.id}': '48', f'qty_{second.id}': '40', 'meto_note': '2 qavat yetmadi'},
        )
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual((flow.stage, flow.meto_note), (PastalFlow.Stage.TASNIF_WAIT, '2 qavat yetmadi'))

    def test_meto_search_and_pagination_for_thousands(self):
        for idx in range(2, 36):
            self.cut(f'P-{idx:03d}')
        self.client.force_login(self.meto)
        page1 = self.client.get(reverse('meto_hub_flow_meto'))
        self.assertEqual(page1.context['kpi']['wait_count'], 35)
        self.assertEqual(len(page1.context['waiting']), 30)
        page2 = self.client.get(reverse('meto_hub_flow_meto'), {'pw': 2})
        self.assertEqual(len(page2.context['waiting']), 5)
        filtered = self.client.get(reverse('meto_hub_flow_meto'), {'q': 'P-015'})
        self.assertEqual([c['batch'].pastal_code for c in filtered.context['waiting']], ['P-015'])

    def test_old_meto_blocks_taken_tayyorlov_batches_and_split_handles_zero_and_recount(self):
        self.client.force_login(self.meto)
        first, second = self.items()
        meto_flow.take_meto(self.batch, self.meto)          # yangi metoda olindi — endi faqat yangi oqimniki
        self.client.post(reverse('meto_confirm_item', args=[first.id]), {'real_quantity': '50'})
        first.refresh_from_db()
        self.assertEqual(first.status, CuttingBatchItem.Status.CUT_ENTERED)
        self.assertFalse(Box.objects.filter(cutting_batch_item=first).exists())
        meto_flow.cancel_meto(self.batch, self.meto)        # to_sklad o'zi qaytadan oladi

        self.to_sklad(tasnif=(45, 0))
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        second.refresh_from_db()
        self.assertEqual((second.real_quantity, second.status), (0, CuttingBatchItem.Status.STICKERS_PRINTED))

        meto_flow.update_tasnif_counts(self.batch, self.counts(30, 10), self.sticker)
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        first.refresh_from_db()
        self.assertEqual(first.boxes_created_qty, 30)


class MetoTwoShiftsTest(MetoFlowBase):
    def test_shift_boundary_calculations(self):
        from unittest.mock import patch
        import zoneinfo
        from production.shifts import get_shift_for_timestamp, get_shift_info, get_work_day_shifts, TASHKENT_TZ

        # 1-smena: 08:00 - 18:00
        s1_dt = datetime.datetime(2026, 11, 20, 10, 30, 0, tzinfo=TASHKENT_TZ)
        s1_info = get_shift_info(s1_dt)
        self.assertEqual(s1_info['shift_num'], 1)
        self.assertEqual(s1_info['shift_label'], '1-smena')
        self.assertEqual(s1_info['work_date'], datetime.date(2026, 11, 20))
        self.assertEqual(s1_info['shift_start'], datetime.datetime(2026, 11, 20, 8, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(s1_info['shift_end'], datetime.datetime(2026, 11, 20, 18, 0, tzinfo=TASHKENT_TZ))

        # 2-smena (kechqurun): 18:00 - 23:59
        s2_eve_dt = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=TASHKENT_TZ)
        s2_eve_info = get_shift_info(s2_eve_dt)
        self.assertEqual(s2_eve_info['shift_num'], 2)
        self.assertEqual(s2_eve_info['shift_label'], '2-smena')
        self.assertEqual(s2_eve_info['work_date'], datetime.date(2026, 11, 20))
        self.assertEqual(s2_eve_info['shift_start'], datetime.datetime(2026, 11, 20, 18, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(s2_eve_info['shift_end'], datetime.datetime(2026, 11, 21, 8, 0, tzinfo=TASHKENT_TZ))

        # 2-smena (yarim tundan keyin, masalan 21-kuni 03:00): 19/20-kunning tungi smenasiga tegishli!
        s2_morn_dt = datetime.datetime(2026, 11, 21, 3, 30, 0, tzinfo=TASHKENT_TZ)
        s2_morn_info = get_shift_info(s2_morn_dt)
        self.assertEqual(s2_morn_info['shift_num'], 2)
        self.assertEqual(s2_morn_info['shift_label'], '2-smena')
        self.assertEqual(s2_morn_info['work_date'], datetime.date(2026, 11, 20)) # 20-kunning ish kuni!
        self.assertEqual(s2_morn_info['shift_start'], datetime.datetime(2026, 11, 20, 18, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(s2_morn_info['shift_end'], datetime.datetime(2026, 11, 21, 8, 0, tzinfo=TASHKENT_TZ))

        # 21-kuni ertalab 08:00 dan yangi 1-smena boshlanadi
        s1_next_dt = datetime.datetime(2026, 11, 21, 8, 0, 0, tzinfo=TASHKENT_TZ)
        s1_next_info = get_shift_info(s1_next_dt)
        self.assertEqual(s1_next_info['shift_num'], 1)
        self.assertEqual(s1_next_info['work_date'], datetime.date(2026, 11, 21))

        # get_work_day_shifts
        day_shifts = get_work_day_shifts(datetime.date(2026, 11, 20))
        self.assertEqual(day_shifts['shift1']['start'], datetime.datetime(2026, 11, 20, 8, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(day_shifts['shift1']['end'], datetime.datetime(2026, 11, 20, 18, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(day_shifts['shift2']['start'], datetime.datetime(2026, 11, 20, 18, 0, tzinfo=TASHKENT_TZ))
        self.assertEqual(day_shifts['shift2']['end'], datetime.datetime(2026, 11, 21, 8, 0, tzinfo=TASHKENT_TZ))

    def test_meto_kpi_and_history_by_shifts(self):
        from unittest.mock import patch
        from production.shifts import TASHKENT_TZ
        from production.models import PastalFlowSize

        # 3 ta pastal tayyorlaymiz:
        # P1: 2026-11-20 10:00 da (1-smena: 50 + 40 = 90 dona)
        # P2: 2026-11-20 20:00 da (2-smena kechqurun: 48 + 40 = 88 dona)
        # P3: 2026-11-21 04:00 da (2-smena yarim tundan keyin: 50 + 40 = 90 dona -> 20-kunning 2-smenasiga kiradi!)
        b1 = self.cut('P-S1')
        b2 = self.cut('P-S2-EVE')
        b3 = self.cut('P-S2-NIGHT')

        f1 = meto_flow.take_meto(b1, self.meto)
        meto_flow.finish_meto(b1, self.counts(50, 40, batch=b1), self.meto)
        f1.meto_done_at = datetime.datetime(2026, 11, 20, 10, 0, 0, tzinfo=TASHKENT_TZ)
        f1.save(update_fields=['meto_done_at'])

        f2 = meto_flow.take_meto(b2, self.meto)
        meto_flow.finish_meto(b2, self.counts(48, 40, batch=b2), self.meto)
        f2.meto_done_at = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=TASHKENT_TZ)
        f2.save(update_fields=['meto_done_at'])

        f3 = meto_flow.take_meto(b3, self.meto)
        meto_flow.finish_meto(b3, self.counts(50, 40, batch=b3), self.meto)
        f3.meto_done_at = datetime.datetime(2026, 11, 21, 4, 0, 0, tzinfo=TASHKENT_TZ)
        f3.save(update_fields=['meto_done_at'])

        self.client.force_login(self.meto)

        # 1) Hozirgi vaqt 2026-11-20 14:00 (1-smena payti) bo'lsa:
        with patch('django.utils.timezone.localtime', return_value=datetime.datetime(2026, 11, 20, 14, 0, 0, tzinfo=TASHKENT_TZ)):
            res = self.client.get(reverse('meto_hub_flow_meto'))
            self.assertEqual(res.status_code, 200)
            kpi = res.context['kpi']
            # Joriy smena = 1-smena: faqat P1 (1 ta pastal, 90 dona)
            self.assertEqual(kpi['shift_done_count'], 1)
            self.assertEqual(kpi['shift_done_qty'], 90)
            self.assertEqual(kpi['today_done_count'], 1)
            # Kun jami (P1 + P2 + P3 = 3 ta pastal, 90 + 88 + 90 = 268 dona)
            self.assertEqual(kpi['day_done_count'], 3)
            self.assertEqual(kpi['day_done_qty'], 268)
            # Smena kesimida
            self.assertEqual(kpi['s1_done_count'], 1)
            self.assertEqual(kpi['s1_done_qty'], 90)
            self.assertEqual(kpi['s2_done_count'], 2)
            self.assertEqual(kpi['s2_done_qty'], 178)

            # HTML tekshiruvi: faol smena, ko'rsatkichlar va smena tanlagich
            content = res.content.decode('utf-8')
            self.assertIn("Faol: 1-smena (kunduzgi)", content)
            self.assertIn("1-smena: <b class=\"text-slate-200\">1</b> p", content)
            self.assertIn("2-smena: <b class=\"text-slate-200\">2</b> p", content)
            self.assertIn("Kun jami: <b class=\"text-white\">3</b> p", content)
            self.assertIn('name="smena"', content)

        # 2) Hozirgi vaqt 2026-11-21 02:00 (2-smena tungi vaqt) bo'lsa:
        with patch('django.utils.timezone.localtime', return_value=datetime.datetime(2026, 11, 21, 2, 0, 0, tzinfo=TASHKENT_TZ)):
            res = self.client.get(reverse('meto_hub_flow_meto'))
            kpi = res.context['kpi']
            # Joriy smena = 2-smena (20-kunning tungi smenasi): P2 va P3 (2 ta pastal, 178 dona)
            self.assertEqual(kpi['shift_done_count'], 2)
            self.assertEqual(kpi['shift_done_qty'], 178)
            self.assertEqual(kpi['today_done_count'], 2)
            self.assertIn("Faol: 2-smena (tungi)", res.content.decode('utf-8'))

        # 3) Filtrlar tekshiruvi (smena=current, s1, s2, day)
        with patch('django.utils.timezone.localtime', return_value=datetime.datetime(2026, 11, 20, 14, 0, 0, tzinfo=TASHKENT_TZ)):
            # Faqat 1-smena
            res_s1 = self.client.get(reverse('meto_hub_flow_meto'), {'smena': 's1'})
            done_codes_s1 = [c['batch'].pastal_code for c in res_s1.context['done_cards']]
            self.assertEqual(done_codes_s1, ['P-S1'])

            # Faqat 2-smena
            res_s2 = self.client.get(reverse('meto_hub_flow_meto'), {'smena': 's2'})
            done_codes_s2 = [c['batch'].pastal_code for c in res_s2.context['done_cards']]
            self.assertEqual(sorted(done_codes_s2), ['P-S2-EVE', 'P-S2-NIGHT'])

            # Joriy smena (14:00 da joriy smena = 1-smena)
            res_cur = self.client.get(reverse('meto_hub_flow_meto'), {'smena': 'current'})
            done_codes_cur = [c['batch'].pastal_code for c in res_cur.context['done_cards']]
            self.assertEqual(done_codes_cur, ['P-S1'])

            # Bugungi 24 soatlik ish kuni (hammasi)
            res_day = self.client.get(reverse('meto_hub_flow_meto'), {'smena': 'day'})
            done_codes_day = [c['batch'].pastal_code for c in res_day.context['done_cards']]
            self.assertEqual(sorted(done_codes_day), ['P-S1', 'P-S2-EVE', 'P-S2-NIGHT'])

    def test_meto_ui_steppers_chips_and_holat_visibility(self):
        b = self.cut('P-UI-1')
        meto_flow.take_meto(b, self.meto)
        self.client.force_login(self.meto)

        # Faol holatda: qidiruv, avto-yangilash, stepperlar, tezkor izohlar va tarix jadvali mavjud
        res_faol = self.client.get(reverse('meto_hub_flow_meto'))
        html_faol = res_faol.content.decode('utf-8')
        self.assertIn('id="meto-search-input"', html_faol)
        self.assertIn('id="btn-auto-refresh"', html_faol)
        self.assertIn('btn-step-minus', html_faol)
        self.assertIn('btn-step-plus', html_faol)
        self.assertIn('onfocus="this.select()"', html_faol)
        self.assertIn('meto-note-chips', html_faol)
        self.assertIn('Tugatilgan pastallar tarixi', html_faol)

        # Faqat kutmoqda holatida: tugatilganlar tarixi yashiriladi
        res_wait = self.client.get(reverse('meto_hub_flow_meto'), {'holat': 'wait'})
        html_wait = res_wait.content.decode('utf-8')
        self.assertNotIn('Tugatilgan pastallar tarixi', html_wait)

        # Faqat meto bo'lmoqda holatida: tugatilganlar tarixi yashiriladi
        res_work = self.client.get(reverse('meto_hub_flow_meto'), {'holat': 'work'})
        html_work = res_work.content.decode('utf-8')
        self.assertNotIn('Tugatilgan pastallar tarixi', html_work)

        # Tugatilganlar tarixi tanlanganda: tarix jadvali ko'rinadi
        res_done = self.client.get(reverse('meto_hub_flow_meto'), {'holat': 'done'})
        html_done = res_done.content.decode('utf-8')
        self.assertIn('Tugatilgan pastallar tarixi', html_done)

    def test_tasnif_lifecycle_take_cancel_finish(self):
        b = self.cut('T-LIFE-1', m=50, l=40)
        # Metoni yakunlaymiz -> pastal TASNIF_WAIT holatiga o'tadi
        meto_flow.take_meto(b, self.meto)
        meto_flow.finish_meto(b, self.counts(49, 39, batch=b), self.meto)
        flow = meto_flow.get_flow(b)
        self.assertEqual(flow.stage, PastalFlow.Stage.TASNIF_WAIT)

        # 1) Qabul qilish (take_tasnif)
        flow = meto_flow.take_tasnif(b, self.tasnif)
        self.assertEqual(flow.stage, PastalFlow.Stage.TASNIF_WORK)
        self.assertEqual(flow.tasnif_taken_by, self.tasnif)
        self.assertIsNotNone(flow.tasnif_taken_at)

        # 2) Bekor qilish (cancel_tasnif) -> yana TASNIF_WAIT ga qaytadi
        flow = meto_flow.cancel_tasnif(b, self.tasnif)
        self.assertEqual(flow.stage, PastalFlow.Stage.TASNIF_WAIT)
        self.assertIsNone(flow.tasnif_taken_by)
        self.assertIsNone(flow.tasnif_taken_at)

        # 3) Qayta qabul qilish va xato (haddan tashqari katta son kiritilganda)
        meto_flow.take_tasnif(b, self.tasnif)
        with self.assertRaises(FlowError):
            meto_flow.finish_tasnif(b, self.counts(999999, 40, batch=b), self.tasnif)

        # 4) Aniq sonlar bilan tugatish -> SKLAD bosqichiga o'tadi (Meto ombori)
        flow = meto_flow.finish_tasnif(b, self.counts(48, 40, batch=b), self.tasnif)
        self.assertEqual(flow.stage, PastalFlow.Stage.SKLAD)
        self.assertEqual(flow.tasnif_done_by, self.tasnif)
        self.assertIsNotNone(flow.tasnif_done_at)

        # Razmerlar tekshiruvi: meto vs tasnif
        from production.models import PastalFlowSize
        sizes = {s.item.order_item_size.size_name: s for s in PastalFlowSize.objects.filter(item__batch=b)}
        self.assertEqual(sizes['M'].meto_qty, 49)
        self.assertEqual(sizes['M'].tasnif_qty, 48)
        self.assertEqual(sizes['L'].meto_qty, 39)
        self.assertEqual(sizes['L'].tasnif_qty, 40)

    def test_tasnif_two_shifts(self):
        from unittest.mock import patch
        from production.shifts import TASHKENT_TZ

        b1 = self.cut('T-S1', m=50, l=40)
        b2 = self.cut('T-S2-EVE', m=50, l=40)
        b3 = self.cut('T-S2-NIGHT', m=50, l=40)

        for b in (b1, b2, b3):
            meto_flow.take_meto(b, self.meto)
            meto_flow.finish_meto(b, self.counts(50, 40, batch=b), self.meto)

        f1 = meto_flow.take_tasnif(b1, self.tasnif)
        meto_flow.finish_tasnif(b1, self.counts(50, 40, batch=b1), self.tasnif)
        f1.tasnif_done_at = datetime.datetime(2026, 11, 20, 10, 0, 0, tzinfo=TASHKENT_TZ)
        f1.save(update_fields=['tasnif_done_at'])

        f2 = meto_flow.take_tasnif(b2, self.tasnif)
        meto_flow.finish_tasnif(b2, self.counts(48, 40, batch=b2), self.tasnif)
        f2.tasnif_done_at = datetime.datetime(2026, 11, 20, 20, 0, 0, tzinfo=TASHKENT_TZ)
        f2.save(update_fields=['tasnif_done_at'])

        f3 = meto_flow.take_tasnif(b3, self.tasnif)
        meto_flow.finish_tasnif(b3, self.counts(50, 40, batch=b3), self.tasnif)
        f3.tasnif_done_at = datetime.datetime(2026, 11, 21, 4, 0, 0, tzinfo=TASHKENT_TZ)
        f3.save(update_fields=['tasnif_done_at'])

        self.client.force_login(self.tasnif)

        # 1) Hozirgi vaqt 2026-11-20 14:00 (1-smena payti) bo'lsa:
        with patch('django.utils.timezone.localtime', return_value=datetime.datetime(2026, 11, 20, 14, 0, 0, tzinfo=TASHKENT_TZ)):
            res = self.client.get(reverse('meto_hub_flow_tasnif'))
            self.assertEqual(res.status_code, 200)
            kpi = res.context['kpi']
            # Joriy smena = 1-smena: b1 (1 ta pastal, 90 dona)
            self.assertEqual(kpi['shift_done_count'], 1)
            self.assertEqual(kpi['shift_done_qty'], 90)
            # Kun jami (b1 + b2 + b3 = 3 ta pastal, 90 + 88 + 90 = 268 dona)
            self.assertEqual(kpi['day_done_count'], 3)
            self.assertEqual(kpi['day_done_qty'], 268)
            # Smena kesimida
            self.assertEqual(kpi['s1_done_count'], 1)
            self.assertEqual(kpi['s1_done_qty'], 90)
            self.assertEqual(kpi['s2_done_count'], 2)
            self.assertEqual(kpi['s2_done_qty'], 178)

            content = res.content.decode('utf-8')
            self.assertIn("Faol: 1-smena (kunduzgi)", content)
            self.assertIn("1-smena: <b class=\"text-slate-200\">1</b> p", content)
            self.assertIn("2-smena: <b class=\"text-slate-200\">2</b> p", content)
            self.assertIn("Kun jami: <b class=\"text-white\">3</b> p", content)

        # 2) Smena filtrlari
        with patch('django.utils.timezone.localtime', return_value=datetime.datetime(2026, 11, 20, 14, 0, 0, tzinfo=TASHKENT_TZ)):
            # Faqat 1-smena
            res_s1 = self.client.get(reverse('meto_hub_flow_tasnif'), {'smena': 's1'})
            done_codes_s1 = [c['batch'].pastal_code for c in res_s1.context['done_cards']]
            self.assertEqual(done_codes_s1, ['T-S1'])

            # Faqat 2-smena
            res_s2 = self.client.get(reverse('meto_hub_flow_tasnif'), {'smena': 's2'})
            done_codes_s2 = [c['batch'].pastal_code for c in res_s2.context['done_cards']]
            self.assertEqual(sorted(done_codes_s2), ['T-S2-EVE', 'T-S2-NIGHT'])

    def test_tasnif_ui_elements_and_two_columns(self):
        # 1 ta kutmoqda, 1 ta ishda
        b_wait = self.cut('T-WAIT', m=50, l=40)
        meto_flow.take_meto(b_wait, self.meto)
        meto_flow.finish_meto(b_wait, self.counts(50, 40, batch=b_wait), self.meto)

        b_work = self.cut('T-WORK', m=50, l=40)
        meto_flow.take_meto(b_work, self.meto)
        meto_flow.finish_meto(b_work, self.counts(49, 39, batch=b_work), self.meto)
        meto_flow.take_tasnif(b_work, self.tasnif)

        self.client.force_login(self.tasnif)
        res = self.client.get(reverse('meto_hub_flow_tasnif'))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode('utf-8')

        # 2 tomonli ekran: chapda Qabul qilish / Qaytarish, o'ngda Pastalni tugatish + butun ekran oynasi
        self.assertIn("Tasnif kutmoqda", html)
        self.assertIn("Tasnif bo'lmoqda", html)
        self.assertIn("Qabul qilish", html)
        self.assertIn("Qaytarish", html)
        self.assertIn("Qabulni bekor qilish", html)
        self.assertIn("Pastalni tugatish", html)
        self.assertIn('id="tasnif-finish-modal"', html)
        self.assertIn('id="tfm-btn-save"', html)
        self.assertIn('id="tfm-btn-cancel"', html)
        self.assertIn('id="tasnif-return-modal"', html)
        self.assertIn('id="tasnif-search-input"', html)
        # Egasi talabi: Tasnifda tugatilganlar tarixi kerak emas
        self.assertNotIn('Tugatilgan pastallar tarixi', html)

        # POST orqali bekor qilish (qabul qilingan pastal kutmoqda ga qaytadi)
        cancel_url = reverse('meto_hub_flow_tasnif_cancel', args=[b_work.id])
        res_cancel = self.client.post(cancel_url)
        self.assertEqual(res_cancel.status_code, 302)
        flow_work = meto_flow.get_flow(b_work)
        self.assertEqual(flow_work.stage, PastalFlow.Stage.TASNIF_WAIT)

        # POST orqali qabul qilish (kutmoqda -> ishda)
        take_url = reverse('meto_hub_flow_tasnif_take', args=[b_wait.id])
        res_take = self.client.post(take_url)
        self.assertEqual(res_take.status_code, 302)
        flow_wait = meto_flow.get_flow(b_wait)
        self.assertEqual(flow_wait.stage, PastalFlow.Stage.TASNIF_WORK)

        # POST orqali tugatish -> tasnif soni pastal ma'lumotlariga yoziladi va SKLAD / Stikerga o'tadi
        item_m, item_l = self.items(b_wait)
        finish_url = reverse('meto_hub_flow_tasnif_finish', args=[b_wait.id])
        res_finish = self.client.post(finish_url, {
            f'qty_{item_m.id}': 49,
            f'qty_{item_l.id}': 40,
        })
        self.assertEqual(res_finish.status_code, 302)
        flow_wait.refresh_from_db()
        self.assertEqual(flow_wait.stage, PastalFlow.Stage.SKLAD)
        item_m.refresh_from_db()
        item_l.refresh_from_db()
        self.assertEqual((item_m.real_quantity, item_l.real_quantity), (49, 40))

    def test_tasnif_return_to_meto_and_resubmit(self):
        b = self.cut('T-RET-1', m=50, l=40)
        meto_flow.take_meto(b, self.meto)
        meto_flow.finish_meto(b, self.counts(48, 39, batch=b), self.meto)
        self.assertEqual(meto_flow.get_flow(b).stage, PastalFlow.Stage.TASNIF_WAIT)

        # Tasnif masteri pastalni sabab bilan Metoga qaytaradi
        self.client.force_login(self.tasnif)
        res = self.client.post(
            reverse('meto_hub_flow_tasnif_return', args=[b.id]),
            {'reason': 'Nomerovka xato'},
        )
        self.assertEqual(res.status_code, 302)
        flow = meto_flow.get_flow(b)
        self.assertEqual(flow.stage, PastalFlow.Stage.METO_WAIT)
        self.assertIsNone(flow.meto_done_at)
        self.assertEqual(flow.tasnif_return_reason, 'Nomerovka xato')
        self.assertEqual(flow.tasnif_returned_by, self.tasnif)
        self.assertIsNotNone(flow.tasnif_returned_at)

        # Meto sahifasida «Tasnifdan qaytarildi» va sababi ko'rinadi
        self.client.force_login(self.meto)
        meto_page = self.client.get(reverse('meto_hub_flow_meto'))
        self.assertContains(meto_page, 'Tasnifdan qaytarildi')
        self.assertContains(meto_page, 'Nomerovka xato')

        # Meto qayta qabul qilib tugatganda qaytarish belgilari tozalanib, yana Tasnifga boradi
        meto_flow.take_meto(b, self.meto)
        meto_flow.finish_meto(b, self.counts(50, 40, batch=b), self.meto)
        flow.refresh_from_db()
        self.assertEqual(flow.stage, PastalFlow.Stage.TASNIF_WAIT)
        self.assertEqual(flow.tasnif_return_reason, '')
        self.assertIsNone(flow.tasnif_returned_at)

    def test_tayyorlov_stiker_1to1_flow_from_tasnif_split_edit_and_print(self):
        from production.pastal_info import build_pastal_info

        # 1) Meto → Tasnif: tasnif soni (48, 40) pastal ma'lumotlariga yoziladi
        self.to_sklad(meto=(49, 40), tasnif=(48, 40))
        first, second = self.items()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.real_quantity, second.real_quantity), (48, 40))

        # Pastal ma'lumotlarida (pastal_info) ham tasnif soni ko'rinadi
        pinfo = build_pastal_info(self.batch)
        self.assertEqual(pinfo['totals']['tasnif'], 88)
        self.assertEqual([s['tasnif_qty'] for s in pinfo['sizes']], [48, 40])
        self.assertIn("Tasnif tugatildi", [e['title'] for e in pinfo['timeline']])

        # 2) Stikerchi /tayyorlov/stiker/ ga kiradi (1 ga 1 nusxa — buyurtma avtomatik ro'yxatda!)
        self.client.force_login(self.sticker)
        dash = self.client.get(reverse('meto_hub_flow_stiker'))
        self.assertEqual(dash.status_code, 200)
        self.assertEqual(dash.context['total_unsplit_pastals'], 1)
        self.assertEqual(dash.context['unprinted_orders_count'], 1)
        self.assertEqual([row['order'].id for row in dash.context['orders_data']], [self.order.id])
        self.assertContains(dash, self.order.order_number)
        self.assertContains(dash, '88 dona')
        # Boshqa Tayyorlov bo'limlariga link yo'q
        for forbidden in ('/tayyorlov/meto/', '/tayyorlov/tasnif/', '/tayyorlov/sklad/', '/stickers/'):
            self.assertNotContains(dash, f'href="{forbidden}"')

        # 3) Buyurtma qutilari sahifasi (/tayyorlov/stiker/orders/<id>/)
        order_url = reverse('tayyorlov_stiker_order_boxes', args=[self.order.id])
        order_page = self.client.get(order_url)
        self.assertEqual(order_page.status_code, 200)
        self.assertEqual(order_page.context['unsplit_pastals_count'], 1)
        self.assertContains(order_page, 'PC: 11-1')
        self.assertContains(order_page, "Qutilarga bo'lish")
        for forbidden in ('/tayyorlov/meto/', '/tayyorlov/tasnif/', '/tayyorlov/sklad/', '/stickers/'):
            self.assertNotContains(order_page, f'href="{forbidden}"')

        # 4) Lazy-loading partial: hali bo'linmagan -> Tasnif soni (48, 40) bilan bo'lish oynasi
        partial_url = reverse('tayyorlov_stiker_batch_boxes', args=[self.order.id, self.batch.id])
        partial_before = self.client.get(partial_url)
        self.assertEqual(partial_before.status_code, 200)
        self.assertTrue(partial_before.context['can_split'])
        self.assertContains(partial_before, f'name="qty_{first.id}" value="48"')
        self.assertContains(partial_before, f'name="qty_{second.id}" value="40"')
        self.assertContains(partial_before, "Qutilarga bo'lish va stikerlarni yaratish")

        # 5) Stikerchi xato tufayli M razmerni 48 -> 47 ga qo'lda o'zgartirib 2 ta qutiga bo'ladi
        split_res = self.client.post(
            reverse('meto_hub_flow_split', args=[self.batch.id]),
            {
                f'qty_{first.id}': '47',
                f'qty_{second.id}': '40',
                f'boxes_{first.id}': '2',
                f'boxes_{second.id}': '1',
                'next': f"{order_url}?open_batch={self.batch.id}",
            },
        )
        self.assertRedirects(split_res, f"{order_url}?open_batch={self.batch.id}", fetch_redirect_response=False)
        self.assertEqual([(b.razmer, b.quantity) for b in self.boxes()], [('M', 24), ('M', 23), ('L', 40)])
        first.refresh_from_db()
        self.assertEqual((first.flow_size.tasnif_qty, first.real_quantity), (47, 47))

        # 6) Partial qayta yuklanganda qutilar jadvali va chop etilguncha o'zgartirish bloki ko'rinadi
        partial_after = self.client.get(partial_url)
        self.assertFalse(partial_after.context['can_split'])
        self.assertTrue(partial_after.context['can_edit_tasnif'])
        self.assertEqual(partial_after.context['total_boxes_count'], 3)
        self.assertContains(partial_after, "Tasnif sonini o'zgartirish va qutilarni qayta bo'lish")

        # 7) Bitta qutini AJAX orqali chop etildi belgilash -> endi tasnif sonini o'zgartirish qulflanadi
        b0 = self.boxes()[0]
        mark_box_res = self.client.post(
            reverse('tayyorlov_stiker_mark_box_printed', args=[b0.id]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(mark_box_res.status_code, 200)
        self.assertEqual(mark_box_res.json()['status'], 'ok')
        b0.refresh_from_db()
        self.assertTrue(b0.is_printed)

        partial_locked = self.client.get(partial_url)
        self.assertFalse(partial_locked.context['can_edit_tasnif'])

        # 8) Butun pastalni AJAX orqali chop etildi belgilash -> buyurtma Tikuvga Berilganlar ga o'tadi
        mark_batch_res = self.client.post(
            reverse('tayyorlov_stiker_mark_batch_printed', args=[self.order.id, self.batch.id]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(mark_batch_res.status_code, 200)
        self.assertEqual(mark_batch_res.json()['status'], 'ok')
        self.assertEqual(mark_batch_res.json()['count'], 2)

        dash_done = self.client.get(reverse('meto_hub_flow_stiker'), {'status': 'printed'})
        self.assertEqual([row['order'].id for row in dash_done.context['orders_data']], [self.order.id])
        dash_unprinted = self.client.get(reverse('meto_hub_flow_stiker'), {'status': 'unprinted'})
        self.assertEqual(dash_unprinted.context['orders_data'], [])

        # 9) Eski /stickers/ bo'limiga tegilmagan — o'z holicha ishlaydi
        old_stickers = self.client.get(reverse('sticker_dashboard'))
        self.assertEqual(old_stickers.status_code, 200)


class MetoTabletTest(MetoFlowBase):
    """Meto Planshet (Tablet) interfeysi va uning 3 ta asosiy funksiyasi testlari."""

    def test_tablet_access_and_views(self):
        # 1) Anonim foydalanuvchi loginga yo'naltiriladi
        resp = self.client.get(reverse('meto_hub_tablet'))
        self.assertEqual(resp.status_code, 302)

        # 2) Sklad xodimi planshetga kiradi
        self.client.force_login(self.sklad)
        res = self.client.get(reverse('meto_hub_tablet'))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "METO PLANSHET")
        self.assertContains(res, "1. Biriktirish")
        self.assertContains(res, "2. Meto Sklad")
        self.assertContains(res, "3. Yuk Xati")
        self.assertContains(res, "4. Patokka Berish")

        # 3) /tayyorlov/planshet/ alias ham 200 beradi
        res_alias = self.client.get(reverse('meto_hub_planshet'))
        self.assertEqual(res_alias.status_code, 200)

        # 4) State API JSON qaytaradi
        api_res = self.client.get(reverse('meto_hub_tablet_state'))
        self.assertEqual(api_res.status_code, 200)
        data = api_res.json()
        self.assertEqual(data['status'], 'OK')
        self.assertIn('empty_containers', data)
        self.assertIn('sklad_cards', data)

    def test_tablet_operations_link_and_unlink(self):
        self.client.force_login(self.sklad)
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {self.items()[0].id: {'boxes': 1}, self.items()[1].id: {'boxes': 1}}, self.sticker)
        box = self.boxes()[0]
        c = Container.objects.create(number=901, branch='HQ')

        # 1) Skaner orqali qutini aniqlash (resolve)
        resolve_res = self.client.get(f"{reverse('meto_hub_flow_api_resolve')}?code={box.box_code}")
        self.assertEqual(resolve_res.status_code, 200)
        self.assertEqual(resolve_res.json()['kind'], 'box')
        self.assertEqual(resolve_res.json()['box'], box.box_code)

        # 2) Quti va konteynerni biriktirish (link)
        link_res = self.client.post(
            reverse('meto_hub_flow_api_link'),
            data={'box': box.box_code, 'container': c.code},
            content_type='application/json'
        )
        self.assertEqual(link_res.status_code, 200)
        self.assertEqual(link_res.json()['status'], 'OK')
        c.refresh_from_db()
        self.assertEqual(c.box_id, box.id)
        self.assertEqual(c.status, Container.Status.FILLED)

        # 3) Bekor qilish (unlink)
        unlink_res = self.client.post(
            reverse('meto_hub_flow_api_unlink'),
            data={'code': c.code},
            content_type='application/json'
        )
        self.assertEqual(unlink_res.status_code, 200)
        self.assertEqual(unlink_res.json()['status'], 'OK')
        c.refresh_from_db()
        self.assertIsNone(c.box_id)
        self.assertEqual(c.status, Container.Status.EMPTY)

    def test_tablet_operations_patok_dispatch(self):
        self.client.force_login(self.sklad)
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {self.items()[0].id: {'boxes': 1}, self.items()[1].id: {'boxes': 1}}, self.sticker)
        boxes = self.boxes()
        for b in boxes:
            b.is_printed = True
            b.save(update_fields=['is_printed'])
        c1 = Container.objects.create(number=902, branch='HQ', box=boxes[0], status=Container.Status.FILLED)
        c2 = Container.objects.create(number=903, branch='HQ', box=boxes[1], status=Container.Status.FILLED)

        # Sklad qabul (kg)
        flow = meto_flow.get_flow(self.batch)
        flow.sklad_received_at = timezone.now()
        flow.sklad_received_by = self.sklad
        flow.save()
        self.batch.actual_weight_kg = Decimal('38.5')
        self.batch.save(update_fields=['actual_weight_kg'])

        # 1) api_check
        check_res = self.client.get(f"{reverse('meto_hub_flow_api_check')}?batches={self.batch.id}")
        self.assertEqual(check_res.status_code, 200)
        self.assertEqual(len(check_res.json()['batches'][0]['rows']), 2)

        # 2) Patokka berish (api_give)
        give_res = self.client.post(
            reverse('meto_hub_flow_api_give'),
            data={'batch_id': self.batch.id, 'screen': 3, 'scanned': [c1.code, c2.code]},
            content_type='application/json'
        )
        self.assertEqual(give_res.status_code, 200)
        self.assertEqual(give_res.json()['status'], 'OK')
        flow.refresh_from_db()
        self.assertEqual(flow.stage, PastalFlow.Stage.AT_PATOK)
        c1.refresh_from_db()
        self.assertEqual(c1.status, Container.Status.AT_PATOK)

    def test_tablet_uychi_branch_no_link_mode(self):
        # 1) Uychi filialida quti bilan konteyner biriktirish rejimi ko'rsatilmaydi
        self.client.force_login(self.uychi)
        res = self.client.get(f"{reverse('meto_hub_tablet')}?branch=UYCHI")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Uychi Filiali")
        self.assertContains(res, "1. Yuk Qabuli")
        self.assertContains(res, "2. Saqlash")
        self.assertContains(res, "3. Patokka Berish")
        self.assertNotContains(res, "1. Biriktirish")
        self.assertNotContains(res, 'id="mode-link"')
        self.assertContains(res, 'id="mode-storage"')

        # 2) Uychi screens U1..U6 mavjud
        self.assertContains(res, "U1")
        self.assertContains(res, "U6")

    def test_pending_verification_full_cycle(self):
        # Tayyorlov master selects pastal -> Request verification -> Meto tablet checks & dispatches -> Master prints -> Uychi receives
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {self.items()[0].id: {'boxes': 1}, self.items()[1].id: {'boxes': 1}}, self.sticker)
        boxes = self.boxes()
        for b in boxes:
            b.is_printed = True
            b.save(update_fields=['is_printed'])
        c1 = Container.objects.create(number=910, branch='HQ', box=boxes[0], status=Container.Status.FILLED)
        c2 = Container.objects.create(number=911, branch='HQ', box=boxes[1], status=Container.Status.FILLED)

        flow = self.batch.flow
        flow.stage = PastalFlow.Stage.SKLAD
        flow.branch = 'HQ'
        flow.sklad_received_at = timezone.now()
        flow.sklad_received_by = self.sklad
        flow.save()
        self.batch.actual_weight_kg = Decimal('38.5')
        self.batch.save(update_fields=['actual_weight_kg'])

        # 1. Tayyorlov bo'limidan tekshirishga yuborish
        self.client.force_login(self.sklad)
        req_res = self.client.post(
            reverse('meto_hub_flow_api_shipment_request_verification'),
            data={'batch_ids': [self.batch.id]},
            content_type='application/json'
        )
        self.assertEqual(req_res.status_code, 200)
        shipment_id = req_res.json()['shipment_id']
        shipment = Shipment.objects.get(id=shipment_id)
        self.assertEqual(shipment.status, Shipment.Status.PENDING)
        self.assertEqual(shipment.lines.first().status, ShipmentPastal.Status.PENDING)

        # Tayyorlov sklad sahifasida tekshiruv kutilayotgani ko'rinadi
        sklad_page = self.client.get(reverse('meto_hub_flow_sklad'))
        self.assertContains(sklad_page, shipment.number)
        self.assertContains(sklad_page, "Meto Planshetida tekshiruv kutayotgan")

        # Meto planshetida pending_shipments mavjud
        self.client.force_login(self.meto)
        tablet_page = self.client.get(reverse('meto_hub_tablet'))
        self.assertContains(tablet_page, shipment.number)
        self.assertContains(tablet_page, "Tekshirishni Boshlash")

        # 2. Meto planshetida konteynerlar tekshirilib jo'natiladi (dispatch)
        disp_res = self.client.post(
            reverse('meto_hub_flow_api_shipment_dispatch', args=[shipment.id]),
            data={'scanned': [c1.code, c2.code], 'driver': 'Ali', 'vehicle': '01A123BC'},
            content_type='application/json'
        )
        self.assertEqual(disp_res.status_code, 200)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, Shipment.Status.IN_TRANSIT)
        self.assertEqual(shipment.driver_name, 'Ali')

        # 3. Tayyorlov masterida chop etish tugmasi ochiladi
        self.client.force_login(self.sklad)
        sklad_page2 = self.client.get(reverse('meto_hub_flow_sklad'))
        self.assertContains(sklad_page2, "Chop etishga tayyor")
        self.assertContains(sklad_page2, reverse('meto_hub_shipment_print', args=[shipment.id]))

        # 4. Uychi planshetida kelgan yuk ko'rinadi va qabul qilinadi
        self.client.force_login(self.uychi)
        uychi_tablet = self.client.get(f"{reverse('meto_hub_tablet')}?branch=UYCHI")
        self.assertContains(uychi_tablet, shipment.number)
        self.assertContains(uychi_tablet, "Tekshiruvga Olish")

        # Uychida barcha konteynerlar skanerlanib balansga saqlanadi
        rec_res = self.client.post(
            reverse('meto_hub_flow_api_shipment_receive_all', args=[shipment.id]),
            data={'scanned': [c1.code, c2.code]},
            content_type='application/json'
        )
        self.assertEqual(rec_res.status_code, 200)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, Shipment.Status.CLOSED)

        flow.refresh_from_db()
        self.assertEqual(flow.branch, 'UYCHI')
        self.assertEqual(flow.stage, PastalFlow.Stage.SKLAD)

        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual(c1.branch, 'UYCHI')
        self.assertEqual(c2.branch, 'UYCHI')

    def test_cancel_verification_flow(self):
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {self.items()[0].id: {'boxes': 1}, self.items()[1].id: {'boxes': 1}}, self.sticker)
        boxes = self.boxes()
        for b in boxes:
            b.is_printed = True
            b.save(update_fields=['is_printed'])
        c1 = Container.objects.create(number=920, branch='HQ', box=boxes[0], status=Container.Status.FILLED)
        c2 = Container.objects.create(number=921, branch='HQ', box=boxes[1], status=Container.Status.FILLED)

        flow = self.batch.flow
        flow.stage = PastalFlow.Stage.SKLAD
        flow.branch = 'HQ'
        flow.sklad_received_at = timezone.now()
        flow.sklad_received_by = self.sklad
        flow.save()
        self.batch.actual_weight_kg = Decimal('38.5')
        self.batch.save(update_fields=['actual_weight_kg'])

        # Tekshirishga yuborish
        shipment = meto_flow.request_verification([self.batch], self.sklad)
        self.assertEqual(shipment.status, Shipment.Status.PENDING)

        # Bekor qilish
        self.client.force_login(self.sklad)
        cancel_res = self.client.post(
            reverse('meto_hub_flow_api_shipment_cancel_verification', args=[shipment.id]),
            content_type='application/json'
        )
        self.assertEqual(cancel_res.status_code, 200)
        self.assertFalse(Shipment.objects.filter(id=shipment.id).exists())
        self.assertFalse(ShipmentPastal.objects.filter(batch=self.batch).exists())

    def test_tablet_tasnif_kg_pending_and_packet_storage(self):
        # 1. Pastal tasnifdan chiqadi
        self.to_sklad()
        flow = self.batch.flow
        self.assertEqual(flow.stage, PastalFlow.Stage.SKLAD)
        self.assertEqual(flow.branch, 'HQ')
        self.assertIsNone(flow.sklad_received_at)
        self.assertIsNone(self.batch.actual_weight_kg)

        # 2. Meto Planshet sahifasi va state API da kg kiritiladiganlar ro'yxatida ko'rinadi
        self.client.force_login(self.sklad)
        state_res = self.client.get(reverse('meto_hub_tablet_state') + '?branch=HQ')
        self.assertEqual(state_res.status_code, 200)
        state_data = state_res.json()
        kg_pending = state_data['kg_pending_cards']
        self.assertTrue(any(c['batch_id'] == self.batch.id for c in kg_pending))

        # Pastal hali qutiga solinmagan (paketda)
        card = next(c for c in kg_pending if c['batch_id'] == self.batch.id)
        self.assertEqual(card['boxes_count'], 0)
        self.assertEqual(card['packaging_status'], 'packet')
        self.assertEqual(card['packaging_label'], 'Paketda (Qutisiz)')

        # 3. Planshet orqali kg kiritiladi va skladga qabul qilinadi
        receive_res = self.client.post(
            reverse('meto_hub_tablet_kg_receive'),
            data=json.dumps({'batch_id': self.batch.id, 'kg': '24.8'}),
            content_type='application/json'
        )
        self.assertEqual(receive_res.status_code, 200)
        receive_data = receive_res.json()
        self.assertEqual(receive_data['status'], 'OK')

        # 4. Endi pastal kg kiritiladiganlardan chiqib, Meto Skladga o'tdi
        self.batch.refresh_from_db()
        flow.refresh_from_db()
        self.assertEqual(self.batch.actual_weight_kg, Decimal('24.80'))
        self.assertIsNotNone(flow.sklad_received_at)

        state_res2 = self.client.get(reverse('meto_hub_tablet_state') + '?branch=HQ')
        state_data2 = state_res2.json()
        self.assertFalse(any(c['batch_id'] == self.batch.id for c in state_data2['kg_pending_cards']))
        stored = state_data2['stored_cards']
        self.assertTrue(any(c['batch_id'] == self.batch.id for c in stored))
        stored_card = next(c for c in stored if c['batch_id'] == self.batch.id)
        self.assertEqual(stored_card['packaging_status'], 'packet')

        # 5. Qutilar bo'linganda stiker kutilmoqda holatiga o'tadi
        meto_flow.split_boxes(self.batch, {self.items()[0].id: {'boxes': 1}, self.items()[1].id: {'boxes': 1}}, self.sticker)
        state_res3 = self.client.get(reverse('meto_hub_tablet_state') + '?branch=HQ')
        stored_card3 = next(c for c in state_res3.json()['stored_cards'] if c['batch_id'] == self.batch.id)
        self.assertEqual(stored_card3['packaging_status'], 'stiker_pending')

        # 6. Stiker chop etilgach, konteyner kutilmoqda holatiga o'tadi
        boxes = self.boxes()
        for b in boxes:
            b.is_printed = True
            b.save(update_fields=['is_printed'])
        state_res4 = self.client.get(reverse('meto_hub_tablet_state') + '?branch=HQ')
        stored_card4 = next(c for c in state_res4.json()['stored_cards'] if c['batch_id'] == self.batch.id)
        self.assertEqual(stored_card4['packaging_status'], 'container_pending')

        # 7. Konteyner biriktirilgach to'liq tayyor bo'ladi
        Container.objects.create(number=930, branch='HQ', box=boxes[0], status=Container.Status.FILLED)
        Container.objects.create(number=931, branch='HQ', box=boxes[1], status=Container.Status.FILLED)
        state_res5 = self.client.get(reverse('meto_hub_tablet_state') + '?branch=HQ')
        stored_card5 = next(c for c in state_res5.json()['stored_cards'] if c['batch_id'] == self.batch.id)
        self.assertEqual(stored_card5['packaging_status'], 'ready')
        self.assertTrue(stored_card5['ready'])









class TayyorlovReviewFixesTest(MetoFlowBase):
    """/tayyorlov/ tekshiruvidagi xatolar tuzatilgani (2026-11-21)."""

    def test_pending_shipment_blocks_patok_and_dispatch_rechecks(self):
        codes = self.ready()
        shipment = meto_flow.request_verification([self.batch], self.sklad)
        with self.assertRaises(FlowError) as error:
            meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        self.assertIn(shipment.number, str(error.exception))
        self.client.force_login(self.sklad)
        card = next(c for c in self.client.get(reverse('meto_hub_tablet_state')).json()['stored_cards']
                    if c['batch_id'] == self.batch.id)
        self.assertEqual((card['ready'], card['pending_verification']), (False, shipment.number))
        # tekshiruv paytida pastal skladdan chiqib ketgan bo'lsa — yuk xati jo'natilmaydi
        PastalFlow.objects.filter(batch=self.batch).update(stage=PastalFlow.Stage.AT_PATOK)
        with self.assertRaises(FlowError):
            meto_flow.dispatch_shipment(shipment, codes, self.sklad)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, Shipment.Status.PENDING)

    def test_direct_ship_failure_leaves_no_pending_shipment(self):
        codes = self.ready()
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_ship'),
                             {'batch_ids': [self.batch.id], 'scanned': codes[:1]})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Shipment.objects.exists())
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_ship'), {'batch_ids': [self.batch.id], 'scanned': codes})
        self.assertEqual(res.json()['status'], 'OK')

    def test_kg_only_by_sklad_and_shipment_permissions(self):
        self.to_sklad()
        for user in (self.tasnif, self.sticker):
            res = self.post_json(user, reverse('meto_hub_tablet_kg_receive'), {'batch_id': self.batch.id, 'kg': '9'})
            self.assertEqual(res.status_code, 403)
        res = self.post_json(self.sklad, reverse('meto_hub_tablet_kg_receive'), {'batch_id': 'abc', 'kg': '9'})
        self.assertEqual(res.status_code, 400)
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.actual_weight_kg)

        other = self.cut('11-7')
        codes = self.ready(other)
        shipment = meto_flow.request_verification([other], self.sklad)
        url = reverse('meto_hub_flow_api_shipment_dispatch', args=[shipment.id])
        self.assertEqual(self.post_json(self.uychi, url, {'scanned': codes}).status_code, 403)
        self.assertEqual(self.post_json(self.sklad, url, {'scanned': codes}).json()['status'], 'OK')
        back = meto_flow.return_line(shipment.lines.get(), self.uychi, 'kam')
        url = reverse('meto_hub_flow_api_shipment_receive_all', args=[back.id])
        self.assertEqual(self.post_json(self.uychi, url, {'scanned': codes}).status_code, 400)   # Kosonga qaytayotgan yuk
        self.assertEqual(self.post_json(self.sklad, url, {'scanned': codes}).json()['status'], 'OK')
        self.assertEqual(meto_flow.get_flow(other).branch, 'HQ')

    def test_uychi_sklad_with_default_branch_gets_uychi_tablet(self):
        user = User.objects.create_user(username='mf_uychi_def', password='x', role=User.Role.UYCHI_SKLAD)
        self.assertEqual(user.branch, 'HQ')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('meto_hub_tablet')).context['branch'], 'UYCHI')

    def test_beta_off_keeps_started_pastals_and_on_takes_only_unmetoed_ones(self):
        self.to_sklad()
        url = reverse('meto_hub_flow_beta_toggle', args=[self.order.id])
        self.client.force_login(self.admin)
        self.client.post(url)                                        # o'chirildi
        self.order.refresh_from_db()
        self.assertFalse(self.order.meto_beta)
        res = self.client.post(reverse('meto_hub_flow_split', args=[self.batch.id]))
        self.assertEqual(res.status_code, 302)                       # 404 emas — ishi shu oqimda tugaydi
        self.assertTrue(self.boxes())
        off_batch = self.cut('11-5')                                 # o'chiq paytda kesildi va eski meto tasdiqladi
        off_batch.items.update(status=CuttingBatchItem.Status.METO_CONFIRMED)
        off_untouched = self.cut('11-6')                             # o'chiq paytda kesildi, hali meto qilinmagan
        self.client.post(url)                                        # qayta yoqildi
        off_batch, off_untouched = (CuttingBatch.objects.get(id=b.id) for b in (off_batch, off_untouched))  # yangi holat
        self.assertFalse(meto_flow.is_v2_batch(off_batch))
        self.assertTrue(meto_flow.is_v2_batch(off_untouched))        # egasi 2026-11-24: meto qilinmagani yangi oqimga
        meto_flow.ensure_flows()
        self.assertFalse(PastalFlow.objects.filter(batch=off_batch).exists())
        self.assertTrue(PastalFlow.objects.filter(batch=off_untouched).exists())
        self.assertTrue(meto_flow.is_v2_batch(self.batch))

    def test_give_api_bad_input_is_400(self):
        codes = self.ready()
        url = reverse('meto_hub_flow_api_give')
        self.assertEqual(self.post_json(self.sklad, url, {'batch_id': self.batch.id, 'screen': 'K3', 'scanned': codes}).status_code, 400)
        self.assertEqual(self.post_json(self.sklad, url, {'batch_id': 'x', 'screen': 3}).status_code, 400)
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_shipment_request_verification'), {'batch_ids': ['a', None]})
        self.assertEqual(res.status_code, 400)

    def test_container_states_bulk_equals_single(self):
        codes = self.ready()
        meto_flow.give_to_patok(self.batch, 3, codes, self.sklad)
        first = self.boxes()[0]
        Ticket.objects.filter(id=first.tickets.first().id).update(status=Ticket.Status.SCANNED)
        expected = {}
        for container in Container.objects.filter(box__cutting_batch_item__batch=self.batch).order_by('number'):
            state = meto_flow.container_state(container)
            expected[state] = expected.get(state, 0) + 1
        self.assertEqual(meto_flow.container_states_by_batch([self.batch.id]), {self.batch.id: list(expected.items())})

    def test_tablet_and_sklad_queries_do_not_grow_per_patok_pastal(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.admin)

        def count(url):
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx.captured_queries)

        counts = []
        for n in range(1, 5):
            batch = self.batch if n == 1 else self.cut(f'11-{n + 20}')
            meto_flow.give_to_patok(batch, 3, self.ready(batch), self.sklad)
            if n in (1, 4):
                counts.append((count(reverse('meto_hub_tablet')), count(reverse('meto_hub_flow_sklad'))))
        (tablet_1, sklad_1), (tablet_4, sklad_4) = counts
        self.assertLessEqual(tablet_4 - tablet_1, 3, counts)
        self.assertLessEqual(sklad_4 - sklad_1, 3, counts)

    def test_tablet_json_escapes_script_tags(self):
        Article.objects.filter(id=self.article.id).update(name='Pijama </script><b>')
        self.to_sklad()
        self.client.force_login(self.sklad)
        page = self.client.get(reverse('meto_hub_tablet')).content.decode()
        self.assertNotIn('Pijama </script>', page)
        self.assertIn('Pijama \\u003C/script\\u003E', page)


class BoshSkladListTest(MetoFlowBase):
    """/tayyorlov/sklad/ — "Bosh sklad" (egasi 2026-11-21): ixcham ro'yxat, holat filtrlari, qidiruv, jonli yangilanish."""

    def setUp(self):
        super().setUp()
        self.tayyor = self.cut('SK-TAYYOR')
        self.tayyor_codes = self.ready(self.tayyor)
        self.stiker = self.cut('SK-STIKER')            # bo'lingan, chop etilmagan, kg yo'q, konteynersiz
        self.to_sklad(self.stiker)
        meto_flow.split_boxes(self.stiker, {}, self.sticker)
        self.kont = self.cut('SK-KONT')                # chop etilgan, kg bor, konteynersiz
        self.to_sklad(self.kont)
        meto_flow.split_boxes(self.kont, {}, self.sticker)
        Box.objects.filter(cutting_batch_item__batch=self.kont).update(is_printed=True)
        meto_flow.sklad_receive(self.kont, '9.5', self.sklad)
        self.paket = self.cut('SK-PAKET')              # tasnifdan chiqqan, hali qutilarga bo'linmagan
        self.to_sklad(self.paket)
        self.tekshir = self.cut('SK-TEKSHIR')          # tayyor va planshetga tekshiruvga yuborilgan
        self.ready(self.tekshir)
        meto_flow.request_verification([self.tekshir], self.sklad)
        self.client.force_login(self.sklad)

    def partial(self, url_name='meto_hub_flow_sklad', **params):
        res = self.client.get(reverse(url_name), {'partial': '1', **params})
        self.assertEqual(res.status_code, 200)
        return res.json()

    def shown(self, data):
        html = data['sections']['list']
        return {code for code in ('SK-TAYYOR', 'SK-STIKER', 'SK-KONT', 'SK-PAKET', 'SK-TEKSHIR') if code in html}

    def test_page_is_bosh_sklad_list(self):
        res = self.client.get(reverse('meto_hub_flow_sklad'))
        self.assertContains(res, 'Bosh skladdagi pastallar')
        self.assertContains(res, '<title>Bosh sklad')
        self.assertContains(res, 'id="sklad-list"')
        self.assertContains(res, f"givePatok({self.tayyor.id}")
        self.assertContains(res, f"sendOneToUychi({self.tayyor.id}")
        self.assertNotContains(res, f"givePatok({self.kont.id}")   # tayyor emas — tugma o'chiq
        self.assertEqual(res.context['tab_label'], 'Bosh sklad')

    def test_filter_counts_and_lists(self):
        data = self.partial()
        self.assertEqual(data['counts'], {'all': 5, 'kg': 2, 'stiker': 2, 'kont': 2, 'tayyor': 1, 'tekshiruv': 1})
        expected = {'kg': {'SK-STIKER', 'SK-PAKET'}, 'stiker': {'SK-STIKER', 'SK-PAKET'},
                    'kont': {'SK-STIKER', 'SK-KONT'}, 'tayyor': {'SK-TAYYOR'}, 'tekshiruv': {'SK-TEKSHIR'}}
        for key, codes in expected.items():
            self.assertEqual(self.shown(self.partial(f=key)), codes, key)
        self.assertEqual(len(self.shown(self.partial(f='nomalum'))), 5)   # noma'lum filtr = hammasi

    def test_search_by_pastal_container_or_box(self):
        self.assertEqual(self.shown(self.partial(q='sk-kont')), {'SK-KONT'})
        self.assertEqual(self.shown(self.partial(q=self.tayyor_codes[0])), {'SK-TAYYOR'})
        box = self.boxes(self.stiker)[0]
        self.assertEqual(self.shown(self.partial(q=box.box_code.lower())), {'SK-STIKER'})
        self.assertEqual(self.shown(self.partial(q=f"CONTROL:{box.box_code}")), {'SK-STIKER'})
        empty = self.partial(q='YOQ-PASTAL')
        self.assertEqual((self.shown(empty), empty['counts']['all']), (set(), 0))
        self.assertIn("pastal topilmadi", empty['sections']['list'])

    def test_list_refreshes_after_linking(self):
        # foydalanuvchi: "konteynerga qutini biriktirdim ammo sahifa o'zgarmasdan turibdi"
        before = self.partial()
        containers = self.new_containers(len(self.boxes(self.kont)))
        for box, container in zip(self.boxes(self.kont), containers):
            res = self.post_json(self.sklad, reverse('meto_hub_flow_api_link'), {'box': box.box_code, 'container': container.code})
            self.assertEqual(res.json()['status'], 'OK')
        after = self.partial()
        self.assertEqual((before['counts']['kont'], after['counts']['kont']), (2, 1))
        self.assertEqual((before['counts']['tayyor'], after['counts']['tayyor']), (1, 2))
        self.assertIn(containers[0].code, after['sections']['list'])
        self.assertIn(f"sendOneToUychi({self.kont.id}", after['sections']['list'])

    def test_kg_receive_and_fix_by_ajax(self):
        url = reverse('meto_hub_flow_sklad_receive', args=[self.paket.id])
        res = self.client.post(url, {'kg': '7,25'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(res.json()['status'], 'OK')
        self.assertEqual(self.partial()['counts']['kg'], 1)
        res = self.client.post(url, {'kg': '7.5'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')   # tuzatish
        self.paket.refresh_from_db()
        self.assertEqual((res.json()['status'], self.paket.actual_weight_kg), ('OK', Decimal('7.5')))
        self.assertEqual(self.client.post(url, {'kg': '0'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400)

    def test_uychi_list_has_no_kg_filter_and_access_is_checked(self):
        self.client.force_login(self.uychi)
        data = self.partial('meto_hub_flow_uychi')
        self.assertNotIn('kg', data['counts'])
        self.assertEqual(data['counts']['all'], 0)
        page = self.client.get(reverse('meto_hub_flow_uychi'))
        self.assertContains(page, 'Uychi skladidagi pastallar')
        self.client.force_login(self.tasnif)
        res = self.client.get(reverse('meto_hub_flow_sklad'), {'partial': '1'})
        self.assertEqual(res.status_code, 302)


class MetoTasnifKioskTest(MetoFlowBase):
    """Meto va Tasnif umumiy QR-birka plansheti (/tayyorlov/meto-tasnif/)."""

    def setUp(self):
        super().setUp()
        self.meto2 = User.objects.create_user(
            username='mf_meto_2', password='x', role=User.Role.METO,
            first_name='Dilshod', last_name='Karimov',
        )
        self.tasnif2 = User.objects.create_user(
            username='mf_tasnif_2', password='x', role=User.Role.TASNIF,
            first_name='Malika', last_name='Rustamova',
        )
        self.kiosk_url = reverse('meto_hub_flow_kiosk')
        self.scan_url = reverse('meto_hub_flow_kiosk_scan')
        self.ping_url = reverse('meto_hub_flow_kiosk_ping')
        self.exit_url = reverse('meto_hub_flow_kiosk_exit')

    def _scan(self, code):
        return self.client.post(self.scan_url, json.dumps({'code': code}), content_type='application/json')

    def test_login_free_standby_and_anonymous_full_cycle(self):
        # Login/parolsiz istalgan qurilmadan kutish ekrani to'g'ridan-to'g'ri ochiladi
        self.client.logout()
        res = self.client.get(self.kiosk_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Birkangizni skanerlang')
        self.assertContains(res, 'btn-kiosk-fullscreen')
        self.assertIsNone(res.context['active_master'])

        # Oddiy kompyuter sahifalari va desktop POST esa baribir login talab qiladi
        self.assertEqual(self.client.get(reverse('meto_hub_flow_meto')).status_code, 302)
        self.assertIn('/login/', self.client.get(reverse('meto_hub_flow_meto')).url)
        desktop_post = self.client.post(reverse('meto_hub_flow_meto_take', args=[self.batch.id]))
        self.assertEqual(desktop_post.status_code, 302)
        self.assertIn('/login/', desktop_post.url)

        # Birka skanerlanmasdan kiosk POST ham rad etiladi
        unauth_kiosk = self.client.post(
            reverse('meto_hub_flow_meto_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(unauth_kiosk.status_code, 401)

        # Loginsiz brauzerda Meto birkasini skanerlab to'liq ish bajara oladi
        self.assertEqual(self._scan(f"USER:{self.meto2.uid}").status_code, 200)
        res = self.client.post(
            reverse('meto_hub_flow_meto_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
        )
        self.assertRedirects(res, self.kiosk_url)
        self.assertEqual(meto_flow.get_flow(self.batch).meto_taken_by, self.meto2)

        # Chiqish -> yana loginsiz kutish ekrani
        res = self.client.post(self.exit_url)
        self.assertRedirects(res, self.kiosk_url)
        self.assertIsNone(self.client.get(self.kiosk_url).context['active_master'])

    def test_scan_routes_by_role_and_blocks_other_roles_and_cyrillic(self):
        self.client.logout()

        # Kirill harf transliteratsiya qilinmaydi — ogohlantirish qaytadi
        res = self._scan('ЮЗЕР:123456')
        self.assertEqual(res.status_code, 400)
        self.assertIn('kirill', res.json()['message'].lower())

        # Boshqa rollar (stikerchi, sklad, superadmin) rad etiladi — faqat Meto va Tasnif
        for other in (self.sticker, self.sklad, self.admin, self.otk):
            res = self._scan(f"USER:{other.uid}")
            self.assertEqual(res.status_code, 403, other.role)
            self.assertIn('faqat Meto va Tasnif', res.json()['message'])

        # Nofaol master rad etiladi
        inactive = User.objects.create_user(username='mf_meto_off', password='x', role=User.Role.METO, is_active=False)
        res = self._scan(f"USER:{inactive.uid}")
        self.assertEqual(res.status_code, 403)

        # Meto birkasi (USER+ prefiksi bilan ham) -> Meto oynasi
        res = self._scan(f"USER+{self.meto2.uid}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['master']['tab'], 'meto')
        page = self.client.get(self.kiosk_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Dilshod Karimov')
        self.assertContains(page, 'METO MASTERI')
        self.assertContains(page, 'Meto kutmoqda')
        self.assertNotContains(page, 'Tasnif kutmoqda')

        # Tasnif birkasi (plain 6 xonali UID bilan ham) -> darhol Tasnif oynasiga o'tadi
        res = self._scan(self.tasnif2.uid)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['master']['tab'], 'tasnif')
        page = self.client.get(self.kiosk_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Malika Rustamova')
        self.assertContains(page, 'TASNIF MASTERI')
        self.assertContains(page, 'Tasnif kutmoqda')
        self.assertNotContains(page, 'Meto kutmoqda')

    def test_cross_role_actions_attribute_exact_scanned_master(self):
        """Planshet bitta akkauntda (masalan mf_meto) kirgan bo'lsa ham,
        skanerlangan Meto va Tasnif masterlarining haqiqiy ismi yoziladi."""
        self.client.force_login(self.meto)

        # 1) Dilshod (meto2) skanerlaydi → qabul va tugatish Dilshod nomiga yoziladi
        self.assertEqual(self._scan(f"USER:{self.meto2.uid}").status_code, 200)
        res = self.client.post(
            reverse('meto_hub_flow_meto_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
        )
        self.assertRedirects(res, self.kiosk_url)
        flow = meto_flow.get_flow(self.batch)
        self.assertEqual(flow.meto_taken_by, self.meto2)

        # Meto masteri Tasnif amalini bajarolmaydi
        res_bad = self.client.post(
            reverse('meto_hub_flow_tasnif_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(res_bad.status_code, 403)

        first, second = self.items()
        res = self.client.post(
            reverse('meto_hub_flow_meto_finish', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url, f'qty_{first.id}': '49', f'qty_{second.id}': '40', 'meto_note': 'OK'},
        )
        self.assertRedirects(res, self.kiosk_url)
        flow.refresh_from_db()
        self.assertEqual(flow.stage, PastalFlow.Stage.TASNIF_WAIT)
        self.assertEqual(flow.meto_done_by, self.meto2)

        # 2) Chiqish -> kutish ekraniga qaytadi, planshetning o'zi tizimdan chiqmaydi
        res = self.client.post(self.exit_url)
        self.assertRedirects(res, self.kiosk_url)
        standby = self.client.get(self.kiosk_url)
        self.assertContains(standby, 'Birkangizni skanerlang')
        self.assertTrue(standby.wsgi_request.user.is_authenticated)

        # Chiqishdan keyin kiosk POST rad etiladi (planshet akkaunti nomidan yozilmaydi!)
        res_unauth = self.client.post(
            reverse('meto_hub_flow_tasnif_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(res_unauth.status_code, 401)

        # 3) Malika (tasnif2) o'sha planshetda (host=mf_meto) skanerlaydi → Tasnif amallari Malika nomiga yoziladi
        self.assertEqual(self._scan(f"USER:{self.tasnif2.uid}").status_code, 200)
        res = self.client.post(
            reverse('meto_hub_flow_tasnif_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
        )
        self.assertRedirects(res, self.kiosk_url)
        flow.refresh_from_db()
        self.assertEqual(flow.tasnif_taken_by, self.tasnif2)

        # Tasnif masteri Meto amalini bajarolmaydi
        res_bad2 = self.client.post(
            reverse('meto_hub_flow_meto_take', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(res_bad2.status_code, 403)

        # Tasnif tugatish -> Malika nomiga saqlanadi
        res = self.client.post(
            reverse('meto_hub_flow_tasnif_finish', args=[self.batch.id]),
            {'kiosk': '1', 'next': self.kiosk_url, f'qty_{first.id}': '48', f'qty_{second.id}': '40'},
        )
        self.assertRedirects(res, self.kiosk_url)
        flow.refresh_from_db()
        self.assertEqual(flow.stage, PastalFlow.Stage.SKLAD)
        self.assertEqual(flow.tasnif_done_by, self.tasnif2)

    def test_server_idle_expiry_and_ping_and_desktop_isolation(self):
        from production import meto_flow_views as mfv

        self.client.force_login(self.admin)
        self.assertEqual(self._scan(f"USER:{self.meto2.uid}").status_code, 200)

        # Ping faol sessiyani tirik saqlaydi
        self.assertEqual(self.client.post(self.ping_url).status_code, 200)

        # Kompyuterdagi oddiy /tayyorlov/meto/ da kiosk sessiyasi ta'sir qilmaydi (admin nomiga yoziladi)
        res = self.client.post(
            reverse('meto_hub_flow_meto_take', args=[self.batch.id]),
            {'next': reverse('meto_hub_flow_meto')},
        )
        self.assertRedirects(res, reverse('meto_hub_flow_meto'))
        self.assertEqual(meto_flow.get_flow(self.batch).meto_taken_by, self.admin)

        # Server zaxira muddati o'tsa kutish ekraniga qaytadi
        sess = self.client.session
        sess[mfv.MT_KIOSK_SESSION_AT_KEY] = 1
        sess.save()
        res = self.client.get(self.kiosk_url)
        self.assertIsNone(res.context['active_master'])
        self.assertContains(res, 'Birkangizni skanerlang')
        self.assertEqual(self.client.post(self.ping_url).status_code, 401)

    def test_meto_and_tasnif_badges_render_role_labels(self):
        self.client.force_login(self.admin)
        res_meto = self.client.get(reverse('superadmin_user_badge', args=[self.meto2.id]))
        self.assertEqual(res_meto.status_code, 200)
        self.assertContains(res_meto, 'METO MASTERI')

        res_tasnif = self.client.get(reverse('superadmin_user_badge', args=[self.tasnif2.id]))
        self.assertEqual(res_tasnif.status_code, 200)
        self.assertContains(res_tasnif, 'TASNIF MASTERI')

    def test_superadmin_users_has_meto_and_tasnif_roles(self):
        self.client.force_login(self.admin)
        url = reverse('superadmin_users')
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'value="METO"')
        self.assertContains(page, 'value="TASNIF"')
        self.assertContains(page, 'Meto masteri')
        self.assertContains(page, 'Tasnif masteri')

        # Yangi Meto masteri yaratish va filtr orqali ko'rish
        res = self.client.post(url, {
            'action': 'create_user',
            'first_name': 'Sardor',
            'last_name': 'Metochi',
            'password': 'secret',
            'role': User.Role.METO,
            'branch': User.Branch.HQ,
        })
        self.assertEqual(res.status_code, 302)
        created = User.objects.get(first_name='Sardor', last_name='Metochi')
        self.assertEqual(created.role, User.Role.METO)
        self.assertTrue(bool(created.uid))

        filtered = self.client.get(url, {'role': User.Role.METO})
        self.assertContains(filtered, 'Sardor')
        self.assertContains(filtered, 'Meto masteri')
        self.assertNotContains(filtered, 'Malika')




class SharedMetoWaitTest(MetoFlowBase):
    """Egasi 2026-11-24: meto kutayotgan pastal eski /meto/ da ham, yangi /tayyorlov/meto/ da ham ko'rinadi — qaysi tomon
    birinchi olsa, pastal o'shaniki (ikkinchisidan yo'qoladi)."""

    def old_confirm(self, item, qty='48'):
        self.client.force_login(self.meto)
        return self.client.post(reverse('meto_confirm_item', args=[item.id]), {'real_quantity': qty})

    def old_row(self):
        page = self.client.get(reverse('meto_order_detail', args=[self.order.id]))
        return page, next(b for it in page.context['items_data'] for b in it['batches'] if b['id'] == self.batch.id)

    def test_waiting_pastal_is_visible_on_both_sides(self):
        self.client.force_login(self.meto)
        new_page = self.client.get(reverse('meto_hub_flow_meto'))
        self.assertEqual([c['batch'].id for c in new_page.context['waiting']], [self.batch.id])
        page, row = self.old_row()
        self.assertEqual((row['is_v2'], row['is_shared']), (False, True))
        self.assertContains(page, "Tayyorlov'da ham kutmoqda")
        dashboard = self.client.get(reverse('meto_dashboard'))
        order_row = next(r for r in dashboard.context['orders_data'] if r['order'].id == self.order.id)
        self.assertEqual((order_row['pending_items_count'], order_row['v2_batches_count']), (2, 0))

    def test_old_meto_first_takes_it_out_of_the_new_flow(self):
        meto_flow.ensure_flows()
        first, second = self.items()
        self.old_confirm(first)
        first.refresh_from_db()
        self.assertEqual(first.status, CuttingBatchItem.Status.METO_CONFIRMED)
        self.assertTrue(Box.objects.filter(cutting_batch_item=first).exists())
        self.assertFalse(PastalFlow.objects.filter(batch=self.batch).exists())     # yangi oqimdagi kutish yozuvi o'chdi
        self.assertFalse(meto_flow.is_v2_batch(self.batch))
        res = self.client.post(reverse('meto_hub_flow_meto_take', args=[self.batch.id]))   # eskirgan ro'yxatdan bosildi
        self.assertEqual(res.status_code, 302)                                       # 404 emas — tushunarli xabar
        self.assertFalse(PastalFlow.objects.filter(batch=self.batch).exists())
        with self.assertRaises(FlowError):
            meto_flow.take_meto(self.batch, self.meto)
        self.assertEqual(self.client.get(reverse('meto_hub_flow_meto')).context['kpi']['wait_count'], 0)
        self.old_confirm(second, '40')                                               # eski metoda davom etadi
        second.refresh_from_db()
        self.assertEqual(second.status, CuttingBatchItem.Status.METO_CONFIRMED)

    def test_leftover_wait_record_is_swept_when_old_side_took_it(self):
        meto_flow.ensure_flows()
        CuttingBatchItem.objects.filter(batch=self.batch).update(status=CuttingBatchItem.Status.METO_CONFIRMED)
        self.assertEqual(meto_flow.release_to_old_flow(), 1)
        self.assertFalse(PastalFlow.objects.filter(batch=self.batch).exists())

    def test_new_meto_first_blocks_the_old_meto(self):
        meto_flow.take_meto(self.batch, self.meto)
        first, _second = self.items()
        self.old_confirm(first)
        first.refresh_from_db()
        self.assertEqual(first.status, CuttingBatchItem.Status.CUT_ENTERED)
        self.assertFalse(Box.objects.filter(cutting_batch_item=first).exists())
        page, row = self.old_row()
        self.assertEqual((row['is_v2'], row['is_shared']), (True, False))
        # tasnif metoga qaytargani ham faqat yangi oqimniki
        meto_flow.finish_meto(self.batch, self.counts(49, 40), self.meto)
        meto_flow.return_tasnif(self.batch, self.tasnif, 'qayta sanang')
        self.assertEqual(meto_flow.get_flow(self.batch).stage, PastalFlow.Stage.METO_WAIT)
        self.assertTrue(meto_flow.new_flow_owns(self.batch))
        self.old_confirm(first)
        first.refresh_from_db()
        self.assertEqual(first.status, CuttingBatchItem.Status.CUT_ENTERED)
        meto_flow.ensure_flows()
        self.assertTrue(PastalFlow.objects.filter(batch=self.batch).exists())      # supurilmaydi


class CyrillicOrderTicketScanTest(MetoFlowBase):
    """Zakaz nomida kirill harflari (0410(Шорты)): US klaviaturadagi skaner ularni yoza olmaydi — bilet QR'i
    TICKET:TK-0410()-<quti №>-<quti kodi>-<xesh> bo'lib keladi. Planshet terminal kabi oxiri bo'yicha topadi (2026-11-24)."""

    def setUp(self):
        super().setUp()
        Order.objects.filter(id=self.order.id).update(order_number='0410(Шорты)')
        self.order.refresh_from_db()
        self.to_sklad()
        meto_flow.split_boxes(self.batch, {}, self.sticker)
        self.box = self.boxes()[0]
        self.ticket = self.box.tickets.exclude(status=Ticket.Status.CANCELLED).first()
        self.assertIn('Шорты', self.ticket.ticket_code)
        self.scanned = 'TICKET:' + ''.join(ch for ch in self.ticket.ticket_code if ord(ch) < 128)   # skaner yozgani

    def test_ticket_without_cyrillic_part_resolves_to_its_box(self):
        self.assertEqual(meto_flow.resolve_box(f"TICKET:{self.ticket.ticket_code}"), self.box)   # to'liq QR
        self.assertEqual(meto_flow.resolve_box(self.scanned), self.box)                         # kirillsiz
        tail = self.scanned[self.scanned.index(')'):]                                            # bo'linib qolgan oxiri
        self.assertEqual(meto_flow.resolve_box(tail), self.box)
        self.assertIsNone(meto_flow.resolve_box('TICKET:TK-0410('))                              # boshi — topilmaydi

    def test_tablet_links_box_scanned_without_cyrillic(self):
        self.client.force_login(self.sklad)
        data = self.client.get(reverse('meto_hub_flow_api_resolve'), {'code': self.scanned}).json()
        self.assertEqual((data['kind'], data['box'], data['problem']), ('box', self.box.box_code, None))
        container = self.new_containers(1)[0]
        res = self.post_json(self.sklad, reverse('meto_hub_flow_api_link'), {'box': self.scanned, 'container': container.code})
        self.assertEqual(res.json()['status'], 'OK')
        self.assertEqual(Container.objects.get(box=self.box), container)
