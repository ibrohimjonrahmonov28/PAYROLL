"""
Taxlovchilar TV ekranlari (egasi talabi 2026-11-14, 6-bosqich): sex ekranlarida alohida bo'lim, 3 ta TV. Har patok bitta
ekranga biriktiriladi; taxlovchi bugungi oxirgi yuborilgan qutisi patogining ekranida, kunlik butun ishi bilan.
Ball = dona × shablondagi Control/Taxlov qiyinligi, norma 1000; faqat sanoqchi yuborgan (pul yozilgan) skanlar.
"""
import datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production.models import (Article, ArticleOperation, Box, Operation, OperationGroup, Order, PackerScreen,
                               PackingScan, Ticket)
from production.packer_screens import packer_screens_data

DAY = datetime.date(2026, 11, 16)


def at(day, hour):
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)))


class PackerScreensTest(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(username='ps_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.aziz = Worker.objects.create(worker_id='W-971', first_name='Aziz', last_name='Taxlov')
        self.malika = Worker.objects.create(worker_id='W-972', first_name='Malika', last_name='Taxlov')
        group = OperationGroup.objects.create(name='TAXLOV SHABLON', taxlov_price=Decimal('2.00'), taxlov_difficulty=1.5)
        self.article = Article.objects.create(code='ART-PS', name='Futbolka', operation_group=group)
        self.order = Order.objects.create(order_number='ORD-PS', article=self.article, packing_beta=True)
        self.boxes = {patok: Box.objects.create(order=self.order, article=self.article, box_number=patok, quantity=500,
                                                razmer='M', assigned_screen=patok) for patok in (1, 2, 5, 9)}
        for number, patoks in ((1, '1,2'), (2, '5'), (3, '')):
            PackerScreen.objects.update_or_create(number=number, defaults={'name': '', 'patoks': patoks})

    def scan(self, worker, patok, qty, when, **extra):
        return PackingScan.objects.create(box=self.boxes[patok], source=PackingScan.Source.CONTROL, worker=worker,
                                          quantity=qty, price_per_unit=Decimal('2.00'), total_amount=Decimal(qty * 2),
                                          credited_at=when, **extra)

    def test_worker_on_screen_of_latest_patok_with_whole_day(self):
        self.scan(self.aziz, 1, 300, at(DAY, 9))
        self.scan(self.aziz, 9, 50, at(DAY, 10))          # K9 hech qaysi ekranda emas — ishi hisobda, egalikda emas
        self.scan(self.aziz, 5, 100, at(DAY, 11))         # oxirgisi K5 → 2-ekran
        self.scan(self.malika, 2, 200, at(DAY, 9))
        data = packer_screens_data(DAY)

        self.assertEqual([w['full_name'] for w in data[1]['workers']], ['Malika Taxlov'])
        aziz = data[2]['workers'][0]
        self.assertEqual((aziz['total_units'], aziz['total_points'], aziz['total_percentage'], aziz['ratio_str']),
                         (450, 675, 67.5, '675/1000'))    # 450 dona × 1.5 qiyinlik
        self.assertEqual(aziz['patoks'], [{'code': 'K1', 'units': 300}, {'code': 'K5', 'units': 100},
                                          {'code': 'K9', 'units': 50}])
        self.assertEqual((aziz['total_earnings_formatted'], data[2]['units_total'], data[2]['davomat']), ('900', 450, 1))
        self.assertEqual((data[1]['patok_codes'], data[1]['avg_percentage'], data[1]['title']), (['K1', 'K2'], 30.0, 'TAXLOV-1'))
        self.assertEqual((data[3]['workers'], data[3]['patok_codes']), ([], []))

    def test_only_sent_today_scans_and_month_earnings(self):
        self.scan(self.malika, 1, 100, at(DAY, 9))
        self.scan(self.malika, 1, 70, None)                                  # ochiq ro'yxat — hali yuborilmagan
        self.scan(self.malika, 1, 30, at(DAY, 10), is_cancelled=True)       # bekor qilingan
        self.scan(self.malika, 1, 400, at(DAY - datetime.timedelta(days=3), 9))   # shu oy, boshqa kun
        self.scan(self.malika, 1, 900, at(datetime.date(2026, 10, 31), 9))        # o'tgan oy
        operation = Operation.objects.create(name='YON CHOK', code='YON CHOK')
        article_op = ArticleOperation.objects.create(article=self.article, operation=operation,
                                                     price_per_unit=Decimal('100'), sequence=1)
        Ticket.objects.create(box=self.boxes[1], article_operation=article_op, quantity=10, price_per_unit=Decimal('100'),
                              status=Ticket.Status.SCANNED, worker=self.malika, screen_number=1, scanned_at=at(DAY, 8))

        malika = packer_screens_data(DAY)[1]['workers'][0]
        self.assertEqual((malika['total_units'], malika['total_earnings_formatted']), (100, '200'))
        self.assertEqual(malika['month_earnings_formatted'], '2 000')        # (100 + 400) × 2 taxlov + 10 × 100 stiker
        self.assertEqual(packer_screens_data(DAY + datetime.timedelta(days=1))[1]['workers'], [])

    def test_tv_page_api_and_overview_section(self):
        self.scan(self.aziz, 5, 1200, at(DAY, 9))
        page = self.client.get(reverse('screens:packer_view', args=[2]), {'date': DAY.isoformat()})
        self.assertContains(page, 'TAXLOV-2')
        self.assertContains(page, 'Aziz Taxlov')
        self.assertContains(page, 'K5:')
        self.assertNotContains(page, 'id="stat-patoks"')
        self.assertNotContains(page, 'Bugungi Ish Haqi')
        self.assertNotContains(page, 'Oylik Maosh')
        api = self.client.get(reverse('screens:packer_api', args=[2]), {'date': DAY.isoformat()}).json()
        self.assertEqual((api['workers'][0]['total_percentage'], api['workers'][0]['is_above_norm'], api['units_total']),
                         (180.0, True, 1200))
        self.assertEqual(self.client.get(reverse('screens:packer_view', args=[9])).status_code, 404)

        overview = self.client.get(reverse('screens:overview'), {'date': DAY.isoformat()})
        self.assertContains(overview, 'TAXLOVCHILAR EKRANLARI')
        self.assertContains(overview, reverse('screens:packer_view', args=[2]))
        self.assertContains(overview, 'K1-PATOK')                            # chevarlar kartochkalari joyida
        self.assertNotContains(overview, 'Patoklarni biriktirish')           # sozlash faqat superadminga
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('screens:overview')), 'Patoklarni biriktirish')

    def test_settings_superadmin_only_and_one_screen_per_patok(self):
        url = reverse('screens:packer_settings')
        self.assertTrue(self.client.get(url)['Location'].startswith('/login/'))
        branch = User.objects.create_user(username='filial_ps', password='x', role=User.Role.BRANCH_ADMIN, branch='HQ')
        self.client.force_login(branch)
        self.assertEqual(self.client.post(url, {'tv-1': '3'})['Location'], reverse('screens:overview'))
        self.assertEqual(PackerScreen.objects.get(number=1).patoks, '1,2')

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), 'K13')
        self.client.post(url, {'name-1': 'QADOQ-A', 'tv-1': '3', 'tv-2': '1', 'tv-5': '2', 'tv-14': '2', 'tv-7': '9'})
        screens = {s.number: s for s in PackerScreen.objects.all()}
        self.assertEqual((screens[1].title, screens[1].patoks), ('QADOQ-A', '2'))
        self.assertEqual((screens[2].patoks, screens[3].patoks), ('5,14', '1'))   # K1 3-ekranga ko'chdi; 9-ekran yo'q

    def test_sanoqchi_selected_patok_assigns_tv_screen(self):
        # Qutida patok yo'q (yoki boshqa patok) bo'lsa ham sanoqchi qo'ygan patok bo'yicha TV ekraniga chiqadi
        no_patok_box = Box.objects.create(order=self.order, article=self.article, box_number=99, quantity=100, razmer='L')
        PackingScan.objects.create(box=no_patok_box, source=PackingScan.Source.CONTROL, worker=self.aziz,
                                   screen_number=5, quantity=100, price_per_unit=Decimal('2.00'),
                                   total_amount=Decimal('200.00'), credited_at=at(DAY, 12))
        PackingScan.objects.create(box=self.boxes[1], source=PackingScan.Source.CONTROL, worker=self.malika,
                                   screen_number=2, quantity=50, price_per_unit=Decimal('2.00'),
                                   total_amount=Decimal('100.00'), credited_at=at(DAY, 12))
        data = packer_screens_data(DAY)
        self.assertEqual([w['full_name'] for w in data[2]['workers']], ['Aziz Taxlov'])
        self.assertEqual(data[2]['workers'][0]['patoks'], [{'code': 'K5', 'units': 100}])
        self.assertEqual(data[1]['workers'][0]['patoks'], [{'code': 'K2', 'units': 50}])
