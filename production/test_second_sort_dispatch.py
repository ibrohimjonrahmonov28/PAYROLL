"""
2-sort balansidan jo'natish (1 razmer tezkor va har xil zakaz/modellarni yig'ib jo'natish),
dinamik jo'natish turlari va oylik aylanma hisoboti testlari (2026-11-18).
"""
from datetime import datetime
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from production.models import (Article, Box, BoxQualityInspectionLog, Order, SecondSortBatch, SecondSortDispatch,
                               SecondSortDispatchItem, SecondSortDispatchType)
from production.packing_services import second_sort_balance_data, second_sort_monthly_report


class SecondSortDispatchTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='ssd_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.master = User.objects.create_user(username='ssd_master', password='x', role=User.Role.PACKING_MASTER)
        self.packing = User.objects.create_user(username='ssd_packing', password='x', role=User.Role.PACKING)
        self.counter = User.objects.create_user(username='ssd_counter', password='x', role=User.Role.COUNTER)

        self.art1 = Article.objects.create(code='MOD-01', name='Xudi Qora')
        self.art2 = Article.objects.create(code='MOD-02', name='Shim Kulrang')
        self.ord1 = Order.objects.create(order_number='ORD-201', client_name='Brand A', article=self.art1, packing_beta=True)
        self.ord2 = Order.objects.create(order_number='ORD-202', client_name='Brand B', article=self.art2, packing_beta=True)

        self.b1_m = Box.objects.create(order=self.ord1, article=self.art1, box_number=1, quantity=20, razmer='M', assigned_screen=1)
        self.b1_l = Box.objects.create(order=self.ord1, article=self.art1, box_number=2, quantity=20, razmer='L', assigned_screen=1)
        self.b2_xl = Box.objects.create(order=self.ord2, article=self.art2, box_number=1, quantity=30, razmer='XL', assigned_screen=2)

        self.hub = reverse('packing:hub')

    def _accept_second_sort(self, box, qty, accepted_at=None):
        basket = SecondSortBatch.objects.create(
            counter=self.counter,
            status=SecondSortBatch.Status.ACCEPTED,
            sent_at=accepted_at or timezone.now(),
            accepted_at=accepted_at or timezone.now(),
            accepted_by=self.master,
        )
        BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=self.admin,
            inspected_qty=qty,
            second_sort_qty=qty,
            second_sort_batch=basket,
        )
        return basket

    def _post_dispatch(self, user, payload):
        self.client.force_login(user)
        return self.client.post(
            reverse('packing:api_second_sort_dispatch'),
            json.dumps(payload),
            content_type='application/json',
        )

    def test_seed_migration_created_default_types(self):
        names = list(SecondSortDispatchType.objects.order_by('sort_order').values_list('name', flat=True))
        self.assertEqual(names, ['Sotuvga', 'Zakaz uchun', 'Qayta omborga'])

    def test_single_size_partial_and_multi_order_dispatch_and_cancel(self):
        self._accept_second_sort(self.b1_m, 12)
        self._accept_second_sort(self.b1_l, 8)
        self._accept_second_sort(self.b2_xl, 10)

        t_sotuv = SecondSortDispatchType.objects.get(name='Sotuvga')
        t_zakaz = SecondSortDispatchType.objects.get(name='Zakaz uchun')

        # 1) Bitta razmerdan qisman jo'natish: MOD-01 (M) 12 dan 5 tasi -> qoldiq 7 (jami 25)
        res1 = self._post_dispatch(self.master, {
            'dispatch_type_id': t_sotuv.id,
            'note': 'Bozor #1',
            'items': [{'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 5}],
        })
        self.assertEqual(res1.status_code, 200, res1.content)
        d1 = SecondSortDispatch.objects.get(code=res1.json()['code'])
        self.assertEqual((d1.dispatch_type, d1.note, d1.total_qty), (t_sotuv, 'Bozor #1', 5))

        bal = second_sort_balance_data()
        self.assertEqual(bal['total'], 25)
        self.assertEqual(bal['balance_map'][(self.ord1.id, self.art1.id, 'M')], 7)

        # 2) Har xil zakazdagi har xil ishlarni yig'ib jo'natish (dublikat qatorlar bilan ham jamlaydi)
        res2 = self._post_dispatch(self.master, {
            'dispatch_type_id': t_zakaz.id,
            'note': 'Yigma yuk',
            'items': [
                {'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 4},
                {'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 3},
                {'order_id': self.ord2.id, 'article_id': self.art2.id, 'size': 'XL', 'quantity': 10},
            ],
        })
        self.assertEqual(res2.status_code, 200, res2.content)
        bal = second_sort_balance_data()
        self.assertEqual(bal['total'], 8)
        # MOD-02 hammasi tugagan -> kartochka ro'yxatdan yo'qoladi, faqat MOD-01 (L: 8) qoladi
        self.assertEqual(len(bal['cards']), 1)
        self.assertEqual(dict(bal['cards'][0]['sizes']), {'L': 8})

        # Balansdan ko'p so'ralsa rad etiladi va hech narsa ayrilmaydi
        over = self._post_dispatch(self.master, {
            'dispatch_type_id': t_sotuv.id,
            'items': [{'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'L', 'quantity': 9}],
        })
        self.assertEqual(over.status_code, 400)
        self.assertEqual(second_sort_balance_data()['total'], 8)

        # Upakovka sahifasi ham yangilangan qoldiqni ko'rsatadi
        self.client.force_login(self.master)
        page = self.client.get(self.hub, {'tab': 'upakovka', 'sec': 'balance2'})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context['second_sort_total'], 8)

        # 1-jo'natmani bekor qilish -> 5 dona M qaytib 8+5=13 bo'ladi
        cancel_res = self.client.post(reverse('packing:second_sort_dispatch_cancel'), {
            'dispatch_id': d1.id,
            'reason': 'Xato kiritilgan',
        })
        self.assertEqual(cancel_res.status_code, 302)
        d1.refresh_from_db()
        self.assertTrue(d1.is_cancelled)
        self.assertEqual(d1.cancel_reason, 'Xato kiritilgan')
        self.assertEqual(second_sort_balance_data()['total'], 13)

    def test_role_permissions_for_dispatch_types_and_report(self):
        self._accept_second_sort(self.b1_m, 10)
        t_sotuv = SecondSortDispatchType.objects.get(name='Sotuvga')

        # Oddiy PACKING va COUNTER jo'nata olmaydi ham, tur qo'sha olmaydi ham
        for user in (self.packing, self.counter):
            res = self._post_dispatch(user, {
                'dispatch_type_id': t_sotuv.id,
                'items': [{'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 2}],
            })
            self.assertEqual(res.status_code, 403)
            self.client.force_login(user)
            self.client.post(reverse('packing:second_sort_type_create'), {'name': 'Yangi'})
            self.assertFalse(SecondSortDispatchType.objects.filter(name='Yangi').exists())

        # Lekin PACKING hisobotni ko'rishi va CSV yuklab olishi mumkin
        self.client.force_login(self.packing)
        csv_res = self.client.get(reverse('packing:second_sort_report_csv'))
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn('ORD-201', csv_res.content.decode('utf-8'))

    def test_dynamic_dispatch_types_add_rename_toggle(self):
        self._accept_second_sort(self.b1_m, 10)
        self.client.force_login(self.master)

        # Yangi tur qo'shish
        self.client.post(reverse('packing:second_sort_type_create'), {
            'name': 'Brak omboriga',
            'color': 'rose',
            'description': 'Qayta sotilmaydi',
        })
        custom = SecondSortDispatchType.objects.get(name='Brak omboriga')
        self.assertTrue(custom.is_active)

        # Bir xil nomni (katta-kichik harfda ham) qo'shib bo'lmaydi
        self.client.post(reverse('packing:second_sort_type_create'), {'name': 'brak omboriga'})
        self.assertEqual(SecondSortDispatchType.objects.filter(name__iexact='Brak omboriga').count(), 1)

        # Yangi tur bilan jo'natish
        res = self._post_dispatch(self.master, {
            'dispatch_type_id': custom.id,
            'items': [{'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 3}],
        })
        self.assertEqual(res.status_code, 200)

        # Nofaol qilish — yangi jo'natishda ishlatib bo'lmaydi, lekin hisobotda tarixi saqlanadi
        self.client.post(reverse('packing:second_sort_type_update'), {'type_id': custom.id, 'action': 'toggle'})
        custom.refresh_from_db()
        self.assertFalse(custom.is_active)

        blocked = self._post_dispatch(self.master, {
            'dispatch_type_id': custom.id,
            'items': [{'order_id': self.ord1.id, 'article_id': self.art1.id, 'size': 'M', 'quantity': 1}],
        })
        self.assertEqual(blocked.status_code, 400)

        report = second_sort_monthly_report()
        self.assertIn(custom, report['report_types'])
        self.assertEqual(report['totals']['month_out'], 3)

    def test_monthly_report_opening_in_out_closing_and_csv_protection(self):
        tz = timezone.get_current_timezone()
        oct_10 = timezone.make_aware(datetime(2026, 10, 10, 14, 0), tz)
        oct_25 = timezone.make_aware(datetime(2026, 10, 25, 15, 0), tz)
        nov_05 = timezone.make_aware(datetime(2026, 11, 5, 11, 0), tz)
        nov_12 = timezone.make_aware(datetime(2026, 11, 12, 16, 0), tz)

        # Oktyabrda 20 ta qabul, 5 tasi Sotuvga ketgan -> Oktyabr oxirida 15 qoldiq
        self._accept_second_sort(self.b1_m, 20, accepted_at=oct_10)
        t_sotuv = SecondSortDispatchType.objects.get(name='Sotuvga')
        t_ombor = SecondSortDispatchType.objects.get(name='Qayta omborga')

        d_oct = SecondSortDispatch.objects.create(
            dispatch_type=t_sotuv, dispatched_by=self.master, dispatched_at=oct_25, note='Oktyabr savdo',
        )
        SecondSortDispatchItem.objects.create(dispatch=d_oct, order=self.ord1, article=self.art1, razmer='M', quantity=5)

        # Noyabrda yana 8 ta L qabul, 4 ta M Zakazga/Omborga ketgan
        self._accept_second_sort(self.b1_l, 8, accepted_at=nov_05)
        d_nov = SecondSortDispatch.objects.create(
            dispatch_type=t_ombor, dispatched_by=self.master, dispatched_at=nov_12, note='=FORMULA_INJECT',
        )
        SecondSortDispatchItem.objects.create(dispatch=d_nov, order=self.ord1, article=self.art1, razmer='M', quantity=4)

        oct_rep = second_sort_monthly_report(month_str='2026-10')
        self.assertEqual((oct_rep['totals']['opening'], oct_rep['totals']['month_in'],
                          oct_rep['totals']['month_out'], oct_rep['totals']['closing']), (0, 20, 5, 15))

        nov_rep = second_sort_monthly_report(month_str='2026-11')
        self.assertEqual((nov_rep['totals']['opening'], nov_rep['totals']['month_in'],
                          nov_rep['totals']['month_out'], nov_rep['totals']['closing']), (15, 8, 4, 19))
        self.assertEqual(nov_rep['rows'][0]['closing_sizes'], [{'size': 'M', 'qty': 11}, {'size': 'L', 'qty': 8}])

        # CSV export formuladan himoyalangan bo'lishi kerak
        self.client.force_login(self.master)
        csv_res = self.client.get(reverse('packing:second_sort_report_csv'), {'month': '2026-11'})
        text = csv_res.content.decode('utf-8-sig')
        self.assertIn("'=FORMULA_INJECT", text)
        self.assertIn('15,8,', text)

