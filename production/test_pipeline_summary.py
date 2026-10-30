from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production.models import (
    Article, ArticleOperation, Box, BoxQualityInspectionLog, CuttingBatch, CuttingBatchItem, Operation, Order,
    OrderItem, OrderItemSize, Ticket,
)
from production.sewing_pipeline_summary import ORDER_SUM_KEYS

COMPARED_KEYS = list(ORDER_SUM_KEYS) + ['cut_pct', 'dazmol_pct', 'brak_rate_pct', 'is_brak_warning']


class FactoryPipelineSummaryTest(TestCase):
    """
    "Fabrika bo'yicha jami" (/sewing-statistics/) zakaz sahifasidagi order_totals bilan aynan bir xil bo'lishi shart:
    tezkor hisob (production/sewing_pipeline_summary.py) biletlarni yuklamasdan xuddi shu qoidalarni takrorlaydi.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(username='pipe_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)
        self.worker = Worker.objects.create(worker_id='W-PIPE', first_name='Tikuv', last_name='Chi')
        self.now = timezone.now()
        self.yesterday = self.now - timedelta(days=1)
        self._box_no = 0

        # Zakaz A: kesim bandlari bor, turli holatdagi qutilar
        self.art_a = Article.objects.create(code='PIPE-A', name='Xalat')
        op_sew = Operation.objects.create(code='OP-SEW', name='TIKISH')
        op_dz = Operation.objects.create(code='OP-DZM', name='DAZMOL')
        op_old = Operation.objects.create(code='OP-OLD', name='ESKI AMAL')
        self.sew = ArticleOperation.objects.create(article=self.art_a, operation=op_sew, price_per_unit=Decimal('100'), sequence=1)
        self.dz = ArticleOperation.objects.create(article=self.art_a, operation=op_dz, price_per_unit=Decimal('50'), sequence=9)
        self.old = ArticleOperation.objects.create(article=self.art_a, operation=op_old, price_per_unit=Decimal('10'), sequence=5)
        self.order_a = Order.objects.create(order_number='PIPE-A', article=self.art_a, total_quantity=300,
                                            status=Order.Status.IN_PROGRESS)
        item_a = OrderItem.objects.create(order=self.order_a, article=self.art_a, quantity=300)
        size_m = OrderItemSize.objects.create(order_item=item_a, size_name='M', planned_quantity=150)
        size_l = OrderItemSize.objects.create(order_item=item_a, size_name='L', planned_quantity=150)
        batch_old = CuttingBatch.objects.create(order_item=item_a, batch_number=1, pastal_code='P-OLD')
        CuttingBatch.objects.filter(pk=batch_old.pk).update(created_at=self.yesterday)
        batch_new = CuttingBatch.objects.create(order_item=item_a, batch_number=2, pastal_code='P-NEW')
        ci_m = CuttingBatchItem.objects.create(batch=batch_old, order_item_size=size_m, quantity=100, real_quantity=100)
        ci_l = CuttingBatchItem.objects.create(batch=batch_new, order_item_size=size_l, quantity=80, real_quantity=80)

        b1 = self._box(self.order_a, self.art_a, ci_m, 20, 'M')                       # tikimda (dazmolgacha)
        self._t(b1, self.sew, 'SCANNED', self.yesterday)
        self._t(b1, self.dz, 'PENDING')
        b2 = self._box(self.order_a, self.art_a, ci_m, 20, 'M')                       # OTK kutmoqda: tayyor
        self._t(b2, self.sew, 'SCANNED', self.now)
        self._t(b2, self.dz, 'SCANNED', self.now)
        b3 = self._box(self.order_a, self.art_a, ci_m, 20, 'M')                       # OTK kutmoqda: chala
        self._t(b3, self.sew, 'PENDING')
        self._t(b3, self.dz, 'SCANNED', self.yesterday)
        b4 = self._box(self.order_a, self.art_a, ci_m, 22, 'M')                       # OTK yopgan (+farq)
        self._t(b4, self.sew, 'SCANNED', self.yesterday)
        self._t(b4, self.dz, 'SCANNED', self.yesterday)
        Box.objects.filter(pk=b4.pk).update(is_controlled=True, controlled_at=self.now, controlled_first_sort_qty=21,
                                            controlled_second_sort_qty=3)
        b5 = self._box(self.order_a, self.art_a, ci_l, 30, 'L')                       # ikkinchi marta ta'mirda
        self._t(b5, self.sew, 'SCANNED', self.yesterday)
        self._t(b5, self.dz, 'SCANNED', self.yesterday)
        Box.objects.filter(pk=b5.pk).update(controlled_repair_qty=4)
        for _ in range(2):
            BoxQualityInspectionLog.objects.create(box=b5, repair_qty=4)
        b6 = self._box(self.order_a, self.art_a, ci_l, 25, 'L', pastal_number='P-WAIT')  # tikim kutmoqda
        self._t(b6, self.sew, 'PENDING')
        b7 = self._box(self.order_a, self.art_a, ci_l, 25, 'L')                       # faqat nofaol amal skanerlangan
        self._t(b7, self.old, 'SCANNED', self.now)
        self._t(b7, self.sew, 'PENDING')
        ArticleOperation.objects.filter(pk=self.old.pk).update(is_active=False)
        cancelled = self._box(self.order_a, self.art_a, ci_l, 99, 'L')                # bekor qilingan — hisobga kirmaydi
        Box.objects.filter(pk=cancelled.pk).update(status=Box.Status.CANCELLED)

        # Zakaz B: kesim bandlari yo'q (kesim qutilar yaratilgan vaqtdan olinadi)
        self.art_b = Article.objects.create(code='PIPE-B', name='Tunika')
        sew_b = ArticleOperation.objects.create(article=self.art_b, operation=op_sew, price_per_unit=Decimal('100'), sequence=1)
        self.order_b = Order.objects.create(order_number='PIPE-B', article=self.art_b, total_quantity=60,
                                            status=Order.Status.IN_PROGRESS)
        OrderItem.objects.create(order=self.order_b, article=self.art_b, quantity=60)
        b9 = self._box(self.order_b, self.art_b, None, 30, 'S')
        self._t(b9, sew_b, 'SCANNED', self.now)
        b10 = self._box(self.order_b, self.art_b, None, 30, 'S')
        Box.objects.filter(pk=b10.pk).update(created_at=self.yesterday)
        self._t(b10, sew_b, 'PENDING')

    def _box(self, order, article, ci, qty, razmer, pastal_number=''):
        self._box_no += 1
        return Box.objects.create(order=order, article=article, cutting_batch_item=ci, box_number=self._box_no,
                                  quantity=qty, razmer=razmer, pastal_number=pastal_number)

    def _t(self, box, ao, status, when=None):
        return Ticket.objects.create(box=box, article_operation=ao, quantity=box.quantity, status=status,
                                     worker=self.worker if status == 'SCANNED' else None, scanned_at=when)

    def _order_page_totals(self, order, day):
        url = reverse('production:sewing_statistics_order_models', kwargs={'order_id': order.id})
        return self.client.get(url, {'date': day.isoformat()}).context['order_totals']

    def test_factory_totals_equal_order_page_totals(self):
        for day in (timezone.localdate(), timezone.localdate() - timedelta(days=1)):
            res = self.client.get(reverse('production:sewing_statistics_orders'), {'date': day.isoformat()})
            self.assertEqual(res.status_code, 200)
            factory = res.context['factory_totals']
            expected = {key: 0 for key in ORDER_SUM_KEYS}
            for order in (self.order_a, self.order_b):
                ref = self._order_page_totals(order, day)
                for key in ORDER_SUM_KEYS:
                    expected[key] += ref[key]
            for key in ORDER_SUM_KEYS:
                self.assertEqual(factory[key], expected[key], f"{day} {key}")
            self.assertEqual(factory['orders_count'], 2)

    def test_fast_summary_matches_each_order_page(self):
        from production.sewing_pipeline_summary import summarize_orders_pipeline
        from production.sewing_statistics_views import get_dazmol_operation_ids_for_article
        import datetime as dt

        tz = timezone.get_current_timezone()
        orders = list(Order.objects.prefetch_related(
            'items__article__article_operations__operation', 'items__sizes__cutting_items__batch'))
        for day in (timezone.localdate(), timezone.localdate() - timedelta(days=1)):
            start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
            end = timezone.make_aware(dt.datetime.combine(day, dt.time.max), tz)
            fast = summarize_orders_pipeline(orders, start, end, get_dazmol_operation_ids_for_article)
            for order in (self.order_a, self.order_b):
                ref = self._order_page_totals(order, day)
                for key in COMPARED_KEYS:
                    self.assertEqual(fast[order.id][key], ref[key], f"{order.order_number} {day} {key}")

    def test_key_numbers_of_the_fixture(self):
        res = self.client.get(reverse('production:sewing_statistics_orders'))
        f = res.context['factory_totals']
        self.assertEqual(f['boxes_count'], 9)                                   # bekor qilingan quti yo'q
        # A: kechagi pastal 100 + bugungi 80; B (kesimsiz): kechagi quti 30 + bugungi quti 30
        self.assertEqual((f['cut_prior'], f['cut_today'], f['cut_total']), (130, 110, 240))
        self.assertEqual(f['boxed'], 162 + 60)
        self.assertEqual((f['waiting_sewing_qty'], f['waiting_sewing_boxes'], f['waiting_pastals_count']), (55, 2, 2))
        # tayyor: A dagi b2 (20) + B dagi b9 (30, yagona operatsiyasi urilgan); chala: b3 (20)
        self.assertEqual((f['waiting_control_ready'], f['waiting_control_missing_ops']), (50, 20))
        self.assertEqual((f['in_repair'], f['re_repair_boxes']), (4, 1))
        self.assertEqual((f['first_sort_total'], f['second_sort_total'], f['control_variance']), (21, 3, 2))
        self.assertContains(res, "Fabrika bo'yicha jami")
        self.assertContains(res, f"/sewing-statistics/orders/{self.order_a.id}/?date=")


class PipelineIdentityTest(TestCase):
    """Dazmol = OTK kutmoqda + Ta'mir + 1-sort + 2-sort — model, zakaz va fabrika sahifalarida (egasi talabi)."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username='ident_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)
        worker = Worker.objects.create(worker_id='W-IDENT', first_name='Ident', last_name='Ishchi')
        now = timezone.now()
        art = Article.objects.create(code='IDENT-Z', name='Xalat Z')
        sew = ArticleOperation.objects.create(article=art, operation=Operation.objects.create(code='Z-SEW', name='TIKISH'),
                                              price_per_unit=Decimal('100'), sequence=1)
        dz = ArticleOperation.objects.create(article=art, operation=Operation.objects.create(code='Z-DZ', name='DAZMOL'),
                                             price_per_unit=Decimal('50'), sequence=9)
        self.order = Order.objects.create(order_number='IDENT-1', article=art, total_quantity=200, status=Order.Status.IN_PROGRESS)
        self.item = OrderItem.objects.create(order=self.order, article=art, quantity=200)
        size = OrderItemSize.objects.create(order_item=self.item, size_name='M', planned_quantity=200)
        batch = CuttingBatch.objects.create(order_item=self.item, batch_number=1, pastal_code='Z-1')
        ci = CuttingBatchItem.objects.create(batch=batch, order_item_size=size, quantity=87, real_quantity=87)

        def box(n, qty, tickets, **otk):
            b = Box.objects.create(order=self.order, article=art, cutting_batch_item=ci, box_number=n, quantity=qty, razmer='M')
            for ao, st in tickets:
                Ticket.objects.create(box=b, article_operation=ao, quantity=qty, status=st,
                                      worker=worker if st == 'SCANNED' else None, scanned_at=now if st == 'SCANNED' else None)
            if otk:
                Box.objects.filter(pk=b.pk).update(controlled_at=now, **otk)

        box(1, 30, [(sew, 'SCANNED'), (dz, 'SCANNED')],                     # qisman saralangan, ta'mirda
            controlled_first_sort_qty=25, controlled_second_sort_qty=1, controlled_repair_qty=4)
        box(2, 20, [(sew, 'SCANNED'), (dz, 'PENDING')],                     # dazmol stikeri urilmagan, OTK yopgan
            is_controlled=True, controlled_first_sort_qty=20)
        box(3, 15, [(sew, 'SCANNED')])                                       # DAZMOL bileti yo'q, hammasi urilgan
        box(4, 10, [(sew, 'SCANNED'), (dz, 'SCANNED')])                     # OTK kutmoqda (tayyor)
        box(5, 12, [(sew, 'SCANNED'), (dz, 'PENDING')])                     # hali tikuvda (tenglikka kirmaydi)

    def _check(self, t):
        self.assertEqual(t['dazmol_total'], 75)
        self.assertEqual((t['waiting_control'], t['in_repair'], t['first_sort_total'], t['second_sort_total']), (25, 4, 45, 1))
        self.assertEqual(t['dazmol_total'],
                         t['waiting_control'] + t['in_repair'] + t['first_sort_total'] + t['second_sort_total'])
        self.assertEqual((t['dazmol_unscanned_boxes'], t['dazmol_unscanned_qty']), (2, 35))

    def test_model_page(self):
        url = reverse('production:sewing_statistics_model_detail', kwargs={'order_id': self.order.id, 'order_item_id': self.item.id})
        res = self.client.get(url)
        self._check(res.context['model_totals'])
        self.assertContains(res, "dazmol stikeri urilmagan/yo'q")

    def test_order_page(self):
        res = self.client.get(reverse('production:sewing_statistics_order_models', kwargs={'order_id': self.order.id}))
        self._check(res.context['order_totals'])

    def test_factory_total(self):
        res = self.client.get(reverse('production:sewing_statistics_orders'))
        self._check(res.context['factory_totals'])
