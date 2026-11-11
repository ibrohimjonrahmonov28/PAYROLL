"""
Managerlar NEW (/managers-new/): zakaz, artikul, mato qismlari, ruxsatlar, eski sahifalardan yashirish va Excel.
"""
import io
import shutil
import tempfile
from decimal import Decimal

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import User
from production.models import Article, Order, OrderChangeLog, OrderItem

MEDIA_TMP = tempfile.mkdtemp()


def image_upload(name='model.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (300, 400), (90, 120, 200)).save(buffer, 'PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class ManagersNewTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_superuser(username='mn_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.manager = User.objects.create_user(username='abdulaziz', password='x', role=User.Role.MANAGER, first_name='Abdulaziz')
        self.manager2 = User.objects.create_user(username='manager2', password='x', role=User.Role.MANAGER)
        self.client.force_login(self.manager)

    def create_order(self, number='DM-46'):
        self.client.post(reverse('managers_new:order_create'), {
            'order_number': number, 'client_name': 'ДМ', 'order_date': '2026-11-07',
            'export_dates': '15.01.2027 / 19.02.2027',
        }, follow=True)   # follow: "saqlandi" xabari shu yerda ko'rsatilib tugaydi
        return Order.objects.get(order_number=number)

    def article_data(self, code='TJ-MS-SS27-01', **extra):
        data = {
            'article_code': code, 'model_name': 'Шорты мужские', 'ship_date': '2027-02-19', 'prosjyot': '480',
            'size_labels': 'S (46), M (48), L (50)', 'size-0': '84', 'size-1': '168', 'size-2': '168',
            'fabric_row': ['n1', 'n2'],
            'fabric-n1-name': '2 ип', 'fabric-n1-color': 'Бежевый 15-1308', 'fabric-n1-composition': '80% хлопок',
            'fabric-n1-width_cm': '185', 'fabric-n1-density_gsm': '220', 'fabric-n1-kg_per_piece': '0,255',
            'fabric-n1-prosjyot_percent': '87,65', 'fabric-n1-raw_percent': '10', 'fabric-n1-export_date': '2027-02-19',
            'fabric-n2-name': 'Рибана, ТУБ', 'fabric-n2-color': 'Чёрный 19-4305', 'fabric-n2-kg_per_piece': '0.01',
            'fabric-n2-prosjyot_percent': '89.72', 'fabric-n2-raw_percent': '10',
            'model_image': image_upload(),
        }
        data.update(extra)
        return data

    def add_article(self, order, **extra):
        return self.client.post(reverse('managers_new:article_create', args=[order.id]), self.article_data(**extra))

    def test_manager_creates_order_with_article_sizes_and_fabrics(self):
        order = self.create_order()
        self.assertTrue(order.is_new_flow)
        self.assertEqual((order.manager, order.status), (self.manager, Order.Status.DRAFT))

        res = self.add_article(order)
        self.assertEqual(res.status_code, 302)
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.article.code, 'TJ-MS-SS27-01 · DM-46')   # har zakazda yangi artikul, kod takrorlanmas
        self.assertEqual((item.article_code, item.quantity, item.prosjyot), ('TJ-MS-SS27-01', 420, 480))
        self.assertEqual({s.size_name: s.planned_quantity for s in item.sizes.all()}, {'S (46)': 84, 'M (48)': 168, 'L (50)': 168})
        self.assertEqual(item.size_list, ['S (46)', 'M (48)', 'L (50)'])
        self.assertTrue(item.article.image.name.split('/')[-1].startswith('model_'))

        main, rib = item.fabrics.order_by('id')
        self.assertEqual((main.color, rib.color), ('Бежевый 15-1308', 'Чёрный 19-4305'))   # rang har detalda alohida
        self.assertEqual(main.finished_kg, Decimal('122.4'))                                # 0,255 kg × 480 Просчет
        self.assertEqual(main.raw_kg, Decimal('134.64'))                                    # + 10 %
        self.assertEqual(main.prosjyot_percent, Decimal('87.65'))
        order.refresh_from_db()
        self.assertEqual(order.total_quantity, 420)
        self.assertEqual(OrderChangeLog.objects.filter(order=order).count(), 2)

        page = self.client.get(reverse('managers_new:order_detail', args=[order.id]))
        self.assertContains(page, 'TJ-MS-SS27-01')
        self.assertContains(page, '122.4')

    def test_article_validation(self):
        order = self.create_order()
        self.add_article(order)
        self.add_article(order)                                     # shu zakazda takroriy kod
        self.add_article(order, article_code='TJ-02', prosjyot='100')  # Просчет zakaz sonidan kam
        self.add_article(order, article_code='TJ-03', model_image='')  # model rasmi majburiy
        self.add_article(order, article_code='TJ-04', fabric_row=[])   # mato qismi majburiy
        self.add_article(order, article_code='TJ-05', size_labels='')  # razmer majburiy
        self.assertEqual(order.items.count(), 1)

    def test_manager_sees_only_own_orders(self):
        order = self.create_order()
        self.client.force_login(self.manager2)
        self.assertEqual(self.client.get(reverse('managers_new:order_detail', args=[order.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('managers_new:order_list')), 'DM-46')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('managers_new:order_list')), 'DM-46')

    def test_cutting_status_locks_editing_for_manager(self):
        order = self.create_order()
        self.add_article(order)
        self.client.post(reverse('managers_new:order_status', args=[order.id]), {'status': 'IN_CUTTING'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_CUTTING)

        self.add_article(order, article_code='TJ-02')
        self.assertEqual(order.items.count(), 1)
        self.client.post(reverse('managers_new:order_status', args=[order.id]), {'status': 'ARCHIVED'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_CUTTING)    # arxivni faqat superadmin

        self.client.force_login(self.admin)
        self.add_article(order, article_code='TJ-02')
        self.assertEqual(order.items.count(), 2)

    def test_confirm_needs_article_and_superadmin_reassigns(self):
        order = self.create_order()
        self.client.post(reverse('managers_new:order_status', args=[order.id]), {'status': 'CONFIRMED'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DRAFT)

        self.client.post(reverse('managers_new:order_reassign', args=[order.id]), {'manager_id': self.manager2.id})
        order.refresh_from_db()
        self.assertEqual(order.manager, self.manager)               # menejer o'zi o'tkaza olmaydi
        self.client.force_login(self.admin)
        self.client.post(reverse('managers_new:order_reassign', args=[order.id]), {'manager_id': self.manager2.id})
        order.refresh_from_db()
        self.assertEqual(order.manager, self.manager2)
        self.assertTrue(order.change_logs.filter(message__startswith='Menejer:').exists())

    def test_order_number_change_updates_article_codes(self):
        order = self.create_order()
        self.add_article(order)
        self.client.post(reverse('managers_new:order_edit', args=[order.id]),
                         {'client_name': 'ДМ', 'order_date': '2026-11-07', 'export_dates': '', 'order_number': 'DM-47'})
        self.assertTrue(Article.objects.filter(code='TJ-MS-SS27-01 · DM-47').exists())

    def test_articles_have_own_sizes(self):
        order = self.create_order()
        self.add_article(order)
        self.add_article(order, article_code='TJ-KIDS-01', prosjyot='', size_labels='110, 116',
                         **{'size-0': '50', 'size-1': '60', 'size-2': ''})
        kids = order.items.get(article_code='TJ-KIDS-01')
        self.assertEqual((kids.size_list, kids.quantity), (['110', '116'], 110))
        self.assertEqual({s.size_name: s.planned_quantity for s in kids.sizes.all()}, {'110': 50, '116': 60})

        # tahrirda razmer olib tashlansa, uning qatori ham o'chadi
        self.client.post(reverse('managers_new:article_edit', args=[order.id, kids.id]),
                         self.article_data(code='TJ-KIDS-01', prosjyot='', size_labels='116, 122',
                                           **{'size-0': '60', 'size-1': '40', 'size-2': ''}))
        kids.refresh_from_db()
        self.assertEqual((kids.size_list, kids.quantity), (['116', '122'], 100))
        self.assertEqual(set(kids.sizes.values_list('size_name', flat=True)), {'116', '122'})

        page = self.client.get(reverse('managers_new:order_detail', args=[order.id]))
        self.assertContains(page, '>122<')
        res = self.client.get(reverse('managers_new:export_order_list', args=[order.id]))
        ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
        kids_row = next(r for r in range(4, ws.max_row + 1) if ws.cell(r, 2).value == 'TJ-KIDS-01')
        self.assertEqual([ws.cell(kids_row, c).value for c in (6, 7, 8)], ['116', '122', 'Всего'])
        self.assertEqual(ws.cell(kids_row + 1, 8).value, 100)

    def test_new_flow_orders_hidden_from_old_pages_and_manager_restricted(self):
        Order.objects.create(order_number='OLD-1', client_name='X5')
        self.create_order()
        self.assertRedirects(self.client.get(reverse('production:order_list')), reverse('managers_new:order_list'),
                             fetch_redirect_response=False)
        self.client.force_login(self.admin)
        for url in (reverse('production:order_list'), reverse('manager_dashboard'), reverse('superadmin_orders_list')):
            page = self.client.get(url)
            self.assertContains(page, 'OLD-1')
            self.assertNotContains(page, 'DM-46')

    def test_excel_exports(self):
        order = self.create_order()
        self.add_article(order)
        res = self.client.get(reverse('managers_new:export_fabric_order', args=[order.id]))
        self.assertEqual(res.status_code, 200)
        ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
        self.assertEqual(ws['C2'].value, 'DM-46')
        self.assertEqual((ws['B5'].value, ws['H5'].value, ws['L5'].value, ws['O5'].value), ('TJ-MS-SS27-01', '2 ип', 122.4, 420))
        self.assertAlmostEqual(ws['R5'].value, 0.8765)
        self.assertEqual(ws['R5'].number_format, '0.00%')

        res = self.client.get(reverse('managers_new:export_order_list', args=[order.id]))
        ws = openpyxl.load_workbook(io.BytesIO(res.content)).active
        self.assertEqual((ws['B2'].value, ws['B4'].value, ws['F4'].value), ('DM-46', 'TJ-MS-SS27-01', 'S (46)'))
        self.assertEqual((ws['F5'].value, ws['I5'].value), (84, 420))

    def test_delete_draft_order_removes_its_articles(self):
        order = self.create_order()
        self.add_article(order)
        self.client.post(reverse('managers_new:order_delete', args=[order.id]))
        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertFalse(Article.objects.filter(code__endswith='· DM-46').exists())
