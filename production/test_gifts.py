"""
Patok taqdirlashlari: /gifts/boshqaruv/ (superadmin), /gifts/<patok>/ (TV) va TV ekrandagi sovg'a belgisi.
"""
import datetime
import io
import os
import shutil
import tempfile
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User, Worker
from production.gift_views import worker_candidates
from production.models import Article, ArticleOperation, Box, Operation, Order, PatokAward, Ticket

MEDIA_TMP = tempfile.mkdtemp()


def image_upload(name, size, mode='RGB'):
    buffer = io.BytesIO()
    color = (200, 30, 30, 128) if mode == 'RGBA' else (200, 30, 30)
    Image.new(mode, size, color).save(buffer, 'PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class PatokAwardTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser(username='gift_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.manager = User.objects.create_user(username='gift_manager', password='x', role=User.Role.MANAGER)
        self.tv_k1 = User.objects.create_user(username='ekrank1', password='x', role=User.Role.SCREEN, assigned_screen=1)
        self.oct = datetime.date(2026, 10, 1)
        self.nov = datetime.date(2026, 11, 1)

    def post_award(self, **extra):
        data = {
            'action': 'create', 'patok': 'k1', 'month': '2026-11',
            'full_name': 'Munojot Ikromova', 'nomination': 'Oyning eng yaxshi tikuvchisi', 'reason': 'Normani 125% bajardi',
        }
        data.update(extra)
        return self.client.post(reverse('gifts:manage'), data)

    def test_superadmin_creates_updates_and_deletes_award(self):
        self.client.force_login(self.admin)
        res = self.post_award(photo=image_upload('xodim.png', (3000, 2000), 'RGBA'), gift_photo=image_upload('sovga.png', (800, 800)))
        self.assertEqual(res.status_code, 302)
        award = PatokAward.objects.get()
        self.assertEqual((award.screen_number, award.month, award.full_name), (1, self.nov, 'Munojot Ikromova'))
        self.assertEqual(award.created_by, self.admin)
        with Image.open(award.photo.path) as img:          # telefon rasmi TV uchun kichraytiriladi
            self.assertEqual((img.format, max(img.size)), ('JPEG', 1600))
        with Image.open(award.gift_photo.path) as img:
            self.assertEqual(img.size, (800, 800))

        gift_path = award.gift_photo.path
        self.client.post(reverse('gifts:manage'), {
            'action': 'update', 'award_id': award.id, 'patok': 'k1', 'month': '2026-11',
            'full_name': 'Munojot Ikromova', 'nomination': 'Eng sifatli ish', 'remove_gift_photo': '1',
        })
        award.refresh_from_db()
        self.assertEqual(award.nomination, 'Eng sifatli ish')
        self.assertFalse(award.gift_photo)
        self.assertFalse(os.path.exists(gift_path))
        self.assertTrue(award.photo)

        photo_path = award.photo.path
        self.client.post(reverse('gifts:manage'), {'action': 'delete', 'award_id': award.id, 'patok': 'k1', 'month': '2026-11'})
        self.assertFalse(PatokAward.objects.exists())
        self.assertFalse(os.path.exists(photo_path))

    def test_required_fields_and_non_image_are_rejected(self):
        self.client.force_login(self.admin)
        self.post_award(nomination='')
        self.post_award(photo=SimpleUploadedFile('rasm.png', b'rasm emas', content_type='image/png'))
        self.assertFalse(PatokAward.objects.exists())

    def test_manage_page_is_superadmin_only(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('gifts:manage')).status_code, 302)
        self.client.force_login(self.admin)
        res = self.client.get(reverse('gifts:manage'), {'patok': 'k1', 'month': '2026-11'})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Patok Taqdirlashlari")
        self.assertContains(res, "Noyabr 2026")

    def test_worker_candidates_sorted_by_average_norm_percent(self):
        article = Article.objects.create(code="ART-GIFT", name="Futbolka", daily_norm=100)
        op = Operation.objects.create(name="YON CHOK", code="YON CHOK")
        ao = ArticleOperation.objects.create(article=article, operation=op, price_per_unit=Decimal('10'), sequence=1)
        order = Order.objects.create(order_number="ORD-GIFT", article=article, total_quantity=500)
        box = Box.objects.create(order=order, box_number=1, quantity=500, article=article)
        anvar = Worker.objects.create(worker_id="W-501", first_name="Anvar", last_name="Karimov")
        barno = Worker.objects.create(worker_id="W-502", first_name="Barno", last_name="Aliyeva")
        begona = Worker.objects.create(worker_id="W-503", first_name="Begona", last_name="Patokdan")
        tz = timezone.get_current_timezone()

        def scan(worker, day, qty, screen=1):
            Ticket.objects.create(box=box, article_operation=ao, quantity=qty, price_per_unit=Decimal('10'),
                                  status=Ticket.Status.SCANNED, worker=worker, screen_number=screen,
                                  scanned_at=timezone.make_aware(datetime.datetime(2026, 11, day, 10), tz))

        scan(anvar, 3, 100)          # 100%
        scan(anvar, 4, 50)           # 50%  -> o'rtacha 75%
        scan(barno, 3, 90)           # 90%
        scan(begona, 3, 300, screen=2)   # boshqa patok — ro'yxatga kirmaydi
        candidates = worker_candidates(1, self.nov)
        self.assertEqual([(c['full_name'], c['avg_pct'], c['days']) for c in candidates],
                         [('Barno Aliyeva', 90.0, 1), ('Anvar Karimov', 75.0, 2)])

    def test_tv_shows_latest_month_with_navigation(self):
        PatokAward.objects.create(screen_number=1, month=self.oct, full_name="Oktyabr G'olibi", nomination="Eng sifatli ish")
        PatokAward.objects.create(screen_number=1, month=self.nov, full_name="Munojot Ikromova", nomination="Oyning eng yaxshi tikuvchisi")
        PatokAward.objects.create(screen_number=1, month=self.nov, full_name="Dilnoza Karimova", nomination="Eng sifatli ish")

        res = self.client.get(reverse('gifts:tv', args=['k1']))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['month_label'], "Noyabr 2026")
        self.assertEqual([a.full_name for a in res.context['awards']], ["Munojot Ikromova", "Dilnoza Karimova"])
        self.assertEqual((res.context['prev_month'], res.context['next_month']), ('2026-10', ''))
        self.assertContains(res, "Har 15 soniyada almashadi")
        self.assertContains(res, 'href="/screens/1/"')

        res = self.client.get(reverse('gifts:tv', args=['K1']), {'month': '2026-10'})
        self.assertEqual([a.full_name for a in res.context['awards']], ["Oktyabr G'olibi"])
        self.assertEqual(res.context['next_month'], '2026-11')
        self.assertEqual(self.client.get('/gifts/1/').status_code, 200)

        empty = self.client.get(reverse('gifts:tv', args=['k2']))
        self.assertContains(empty, "hali taqdirlash kiritilmagan")
        self.assertEqual(self.client.get('/gifts/k99/').status_code, 404)

    def test_version_changes_when_award_added(self):
        url = reverse('gifts:version', args=['k1'])
        before = self.client.get(url).json()['version']
        PatokAward.objects.create(screen_number=1, month=self.nov, full_name="Yangi", nomination="Eng sifatli ish")
        self.assertNotEqual(self.client.get(url).json()['version'], before)

    def test_tv_account_sees_only_its_own_patok(self):
        self.client.force_login(self.tv_k1)
        self.assertEqual(self.client.get('/gifts/k1/').status_code, 200)
        self.assertEqual(self.client.get('/gifts/api/k1/version/').status_code, 200)
        self.assertRedirects(self.client.get('/gifts/k2/'), '/screens/1/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/gifts/api/k2/version/').status_code, 403)

    def test_monitor_has_gift_button_with_latest_month_count(self):
        PatokAward.objects.create(screen_number=1, month=self.oct, full_name="Oktyabr G'olibi", nomination="Eng sifatli ish")
        PatokAward.objects.create(screen_number=1, month=self.nov, full_name="A", nomination="N")
        PatokAward.objects.create(screen_number=1, month=self.nov, full_name="B", nomination="N")
        res = self.client.get(reverse('screens:screen_view', args=[1]))
        self.assertEqual((res.context['gift_url'], res.context['gift_count']), ('/gifts/k1/', 2))
        self.assertContains(res, 'id="btn-gifts"')
        self.assertEqual(self.client.get(reverse('screens:screen_view', args=[2])).context['gift_count'], 0)
