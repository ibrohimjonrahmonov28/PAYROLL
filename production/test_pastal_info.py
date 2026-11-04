"""
Pastal bo'yicha qidirish (Qutilar statistikasi): production/pastal_info.py va /statistics/?pastal_info=...
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production.models import (
    Article, ArticleOperation, Box, BoxQualityInspectionLog, CuttingBatch, CuttingBatchItem, Customer,
    Operation, Order, OrderItem, OrderItemSize, Ticket,
)
from production.pastal_info import build_pastal_info, find_pastal_batches
from production.services import auto_generate_boxes_for_batch_item


class PastalInfoTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='pi_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.master = User.objects.create_user(
            username='pi_master', password='x', role=User.Role.MASTER, first_name='Ali', last_name='Master'
        )
        self.customer = Customer.objects.create(name="Test Mijoz", code="TM")
        self.article = Article.objects.create(code="ART-PI", name="Futbolka")
        sew = Operation.objects.create(name="YON CHOK", code="YON CHOK")
        dz = Operation.objects.create(name="DAZMOL", code="DAZMOL")
        ArticleOperation.objects.create(article=self.article, operation=sew, price_per_unit=Decimal('500'), sequence=1)
        ArticleOperation.objects.create(article=self.article, operation=dz, price_per_unit=Decimal('300'), sequence=2)
        self.order = Order.objects.create(order_number="ZAK-PI", customer=self.customer, article=self.article, total_quantity=100)
        order_item = OrderItem.objects.create(order=self.order, article=self.article, quantity=100)
        size = OrderItemSize.objects.create(order_item=order_item, size_name="M", planned_quantity=100)

        now = timezone.now()
        h = lambda hours: now - datetime.timedelta(hours=hours)
        self.batch = CuttingBatch.objects.create(
            order_item=order_item, batch_number=1, pastal_code="10-777", cutter_name="Bichuvchi Vali", created_by=self.admin
        )
        item = CuttingBatchItem.objects.create(
            batch=self.batch, order_item_size=size, quantity=100, real_quantity=98,
            status=CuttingBatchItem.Status.METO_CONFIRMED, meto_worker_name="Metochi Olim", meto_completed_at=h(5.5),
        )
        self.boxes = auto_generate_boxes_for_batch_item(item, split_count=2)   # 49 + 49

        # Bot K3 ga jo'natgan, K3 planshetida qabul qilingan; stikerlar chop etilgan
        CuttingBatch.objects.filter(id=self.batch.id).update(
            created_at=h(6), dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED, assigned_branch='HQ', assigned_screen=3,
            dispatched_at=h(4), dispatched_by="Bot Dispatcher", accepted_at=h(3), accepted_by=self.master,
        )
        Box.objects.filter(cutting_batch_item__batch=self.batch).update(
            created_at=h(5), assigned_screen=3, is_received_by_patok=True, is_printed=True, printed_at=h(4.5)
        )

        # Birinchi qutining barcha stikerlari (dazmol ham) K3 da urilgan; OTK: 47 1-sort, 1 2-sort, 1 ta'mir
        worker = Worker.objects.create(first_name="Hasan", last_name="Umarov")
        box1 = self.boxes[0]
        Ticket.objects.filter(box=box1).update(status=Ticket.Status.SCANNED, worker=worker, screen_number=3, scanned_at=h(2))
        Box.objects.filter(id=box1.id).update(
            controlled_at=h(1), controlled_first_sort_qty=47, controlled_second_sort_qty=1, controlled_repair_qty=1,
        )
        BoxQualityInspectionLog.objects.create(
            box=box1, inspector=self.admin, inspected_qty=49, first_sort_qty=47, second_sort_qty=1, repair_qty=1
        )
        self.batch.refresh_from_db()

    def _second_batch_same_code(self):
        shorts = Article.objects.create(code="ART-PI2", name="Shorti")
        order_item = OrderItem.objects.create(order=self.order, article=shorts, quantity=50)
        return CuttingBatch.objects.create(order_item=order_item, batch_number=1, pastal_code="10-777")

    def test_build_pastal_info_full_card(self):
        info = build_pastal_info(self.batch)

        self.assertEqual(info['pastal_code'], '10-777')
        self.assertEqual(info['order']['number'], 'ZAK-PI')
        self.assertEqual(info['article']['code'], 'ART-PI')
        self.assertEqual(info['cutter_name'], 'Bichuvchi Vali')
        self.assertEqual(
            {k: info['totals'][k] for k in ('cut', 'meto', 'boxed', 'boxes', 'printed_boxes', 'cancelled_boxes')},
            {'cut': 100, 'meto': 98, 'boxed': 98, 'boxes': 2, 'printed_boxes': 2, 'cancelled_boxes': 0},
        )
        self.assertEqual(info['sizes'][0]['meto_qty'], 98)
        self.assertEqual(info['sizes'][0]['meto_worker'], 'Metochi Olim')

        sewing = info['sewing']
        self.assertEqual((sewing['tickets_total'], sewing['tickets_scanned'], sewing['progress_pct']), (4, 2, 50))
        self.assertEqual(sewing['workers'], 1)
        self.assertEqual(sewing['earned_display'], '39 200')   # 49 x 500 + 49 x 300
        self.assertEqual(sewing['by_patok'], [{'code': 'K3', 'tickets': 2, 'pct': 100}])
        self.assertEqual(info['dazmol']['qty'], 49)
        self.assertEqual(info['dazmol']['by_patok'], [{'code': 'K3', 'qty': 49}])

        otk = info['otk']
        self.assertEqual(
            (otk['controlled_boxes'], otk['closed_boxes'], otk['first_sort'], otk['second_sort'], otk['repair_now'], otk['inspections']),
            (1, 0, 47, 1, 1, 1),
        )
        dispatch = info['dispatch']
        self.assertEqual((dispatch['patok_code'], dispatch['dispatched_by'], dispatch['accepted_by'], dispatch['received_boxes']),
                         ('K3', 'Bot Dispatcher', 'Ali Master', 2))
        self.assertEqual(info['state_label'], "OTK jarayonida")
        self.assertEqual(
            [e['title'] for e in info['timeline']],
            ["Kesim kiritildi", "Meto tasdiqladi", "Qutilar yaratildi", "Stikerlar chop etildi", "Patokka jo'natildi",
             "Patok qabul qildi", "Birinchi stiker urildi", "Birinchi dazmol", "Birinchi OTK tekshiruvi"],
        )

    def test_find_by_code_passport_qr_box_code_and_suggestions(self):
        self.assertEqual(find_pastal_batches('10-777'), ([self.batch], []))
        self.assertEqual(find_pastal_batches(f'PASTAL:{self.batch.id}'), ([self.batch], []))
        self.assertEqual(find_pastal_batches(self.boxes[1].box_code), ([self.batch], []))
        self.assertEqual(find_pastal_batches('ZZZ-404'), ([], []))

        other = self._second_batch_same_code()
        found, _ = find_pastal_batches('10-777')
        self.assertEqual({b.id for b in found}, {self.batch.id, other.id})
        found, suggestions = find_pastal_batches('777')   # "-777" bilan tugaydigan ikkala pastal — tanlatiladi
        self.assertEqual(found, [])
        self.assertEqual({b.id for b in suggestions}, {self.batch.id, other.id})

    def test_statistics_page_shows_card_and_only_this_pastal_boxes(self):
        other_item = CuttingBatchItem.objects.create(
            batch=CuttingBatch.objects.create(order_item=self.batch.order_item, batch_number=2, pastal_code="10-888"),
            order_item_size=self.batch.items.first().order_item_size, quantity=20, status=CuttingBatchItem.Status.METO_CONFIRMED,
        )
        auto_generate_boxes_for_batch_item(other_item, split_count=1)

        self.client.force_login(self.admin)
        res = self.client.get(reverse('production:statistics_pipeline'), {'pastal_info': '10-777'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([c['batch_id'] for c in res.context['pastal_info_cards']], [self.batch.id])
        self.assertEqual({item['box'].id for item in res.context['boxes_data']}, {b.id for b in self.boxes})
        self.assertContains(res, "Pastal bo'yicha qidirish")
        self.assertContains(res, "Pastal pasporti")
        self.assertContains(res, "Birinchi OTK tekshiruvi")

        res = self.client.get(reverse('production:statistics_pipeline'), {'pastal_info': 'ZZZ-404'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['pastal_info_cards'], [])
        self.assertContains(res, "bo'yicha pastal topilmadi")
