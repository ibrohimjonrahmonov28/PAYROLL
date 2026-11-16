"""
Taxlovchi puli (egasi talabi 2026-11-14): sanoqchi yuborgan ro'yxat → dona × shablondagi Control/Taxlov narxi,
yuborilgan kunga. Chevar stikerlari kabi: narx o'zgarsa muzlatilmaganlari qayta hisoblanadi, oy muzlatilsa qulflanadi,
kunlik yopishda muhrlanadi; maosh sahifasi, CSV, Excel tabel va kunlik tafsilotda sariq alohida.
"""
import csv
import datetime
import io
import json
from decimal import Decimal

import openpyxl
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.freeze_services import (auto_freeze_past_months, freeze_month_tickets, start_monthly_freeze_timer,
                                      unfreeze_month_tickets)
from accounts.management.commands.close_daily_payroll import perform_daily_closing
from accounts.models import DailyWorkerClosing, User, Worker
from production.excel_reports import generate_daily_excel_report, generate_month_to_date_excel_report
from production.models import Article, Box, OperationGroup, Order, PackingBatch, PackingScan


@override_settings(FREEZE_IN_BACKGROUND=False)
class TaxlovPayTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='tp_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.counter = User.objects.create_user(username='sanoqchi9', password='x', role=User.Role.COUNTER)
        self.packer = Worker.objects.create(worker_id='W-950', first_name='Aziz', last_name='Taxlovchi')
        self.group = OperationGroup.objects.create(name='TAXLOV SHABLON', taxlov_price=Decimal('2.50'))
        self.article = Article.objects.create(code='ART-TP', name='Futbolka')
        self.article.operation_group = self.group
        self.article.save()
        self.order = Order.objects.create(order_number='ORD-TP', article=self.article, packing_beta=True)
        self.box = Box.objects.create(order=self.order, article=self.article, box_number=1, quantity=50, razmer='M')

    def post(self, user, name, payload):
        self.client.force_login(user)
        return self.client.post(reverse(f'packing:{name}'), json.dumps(payload), content_type='application/json')

    def send_40(self):
        self.post(self.admin, 'api_otk_submit', {'box_id': self.box.id, 'mode': 'INITIAL', 'total_qty': 50,
                                                 'second_sort_qty': 3, 'repair_qty': 7})
        self.post(self.counter, 'api_counter_paper', {'code': f"CONTROL:{self.box.box_code}", 'worker_id': self.packer.id})
        res = self.post(self.counter, 'api_counter_send', {'worker_id': self.packer.id}).json()
        self.assertEqual(res['status'], 'OK')
        return PackingScan.objects.get(source=PackingScan.Source.CONTROL, batch__isnull=False)

    def test_send_credits_pay_and_open_or_legacy_scans_are_not_paid(self):
        scan = self.send_40()
        PackingScan.objects.create(box=self.box, source=PackingScan.Source.TAMIR, worker=self.packer, quantity=5)   # eski taxlovchi tabi
        self.assertEqual((scan.price_per_unit, scan.total_amount), (Decimal('2.50'), Decimal('100.00')))   # 40 × 2.50
        self.assertEqual(scan.credited_at, PackingBatch.objects.get().sent_at)
        self.assertEqual(self.packer.total_earned, Decimal('100.00'))          # eski skan (5 dona) pul emas

    def test_price_change_reprices_unfrozen_only(self):
        scan = self.send_40()
        old = PackingScan.objects.create(box=self.box, source=PackingScan.Source.TAMIR, worker=self.packer, quantity=4,
                                         price_per_unit=Decimal('2.50'), total_amount=Decimal('10.00'),
                                         credited_at=timezone.now(), is_frozen=True)
        self.client.force_login(self.admin)
        self.client.post(reverse('superadmin_pricing'), {'action': 'update_group_beta', 'group_id': self.group.id,
                                                         'taxlov_price': '3', 'taxlov_difficulty': '1'})
        scan.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((scan.price_per_unit, scan.total_amount), (Decimal('3.00'), Decimal('120.00')))
        self.assertEqual(old.total_amount, Decimal('10.00'))                    # muzlatilgan oy o'zgarmaydi

    def test_freeze_unfreeze_and_auto_freeze(self):
        scan = self.send_40()
        now = timezone.localtime()
        freeze_month_tickets(now.year, now.month)
        scan.refresh_from_db()
        self.assertTrue(scan.is_frozen)
        unfreeze_month_tickets(now.year, now.month)
        scan.refresh_from_db()
        self.assertFalse(scan.is_frozen)

        last_month = now.replace(day=1) - datetime.timedelta(days=3)
        PackingScan.objects.filter(pk=scan.pk).update(credited_at=last_month)
        auto_freeze_past_months()
        scan.refresh_from_db()
        self.assertTrue(scan.is_frozen)                                         # o'tgan oy — qulflandi
        closing, started = start_monthly_freeze_timer(last_month.year, last_month.month, user=self.admin)
        self.assertEqual(closing.total_gross_amount, Decimal('100.00'))         # oylik reestr summasida taxlov ham bor

    def test_frozen_scan_cannot_be_cancelled(self):
        scan = self.send_40()
        PackingScan.objects.filter(pk=scan.pk).update(is_frozen=True)
        self.client.force_login(self.counter)
        self.client.post(reverse('packing:counter_cancel'), {'scan_id': scan.id, 'code': self.box.box_code})
        scan.refresh_from_db()
        self.assertFalse(scan.is_cancelled)

    def test_daily_closing_payroll_page_breakdown_and_csv(self):
        self.send_40()
        today = timezone.localdate()
        perform_daily_closing(today)
        closing = DailyWorkerClosing.objects.get(worker=self.packer, date=today)
        self.assertEqual((closing.total_amount, closing.taxlov_amount, closing.taxlov_units, closing.total_units),
                         (Decimal('100.00'), Decimal('100.00'), 40, 0))

        self.client.force_login(self.admin)
        page = self.client.get(reverse('superadmin_payroll'), {'year': today.year, 'month': today.month})
        row = next(r for r in page.context['payroll_data'] if r['worker'] == self.packer)
        self.assertEqual((row['month_gross'], row['taxlov_amount'], row['taxlov_units'], row['lifetime_balance']),
                         (Decimal('100.00'), Decimal('100.00'), 40, Decimal('100.00')))
        self.assertEqual(page.context['grand_taxlov_month'], Decimal('100.00'))
        self.assertContains(page, 'Taxlov: 100 so')

        detail = self.client.get(reverse('superadmin_worker_daily_breakdown', args=[self.packer.id]),
                                 {'year': today.year, 'month': today.month}).json()
        self.assertEqual((detail['total_earned'], detail['rows'][0]['taxlov_units']), (100.0, 40))

        csv_text = self.client.get(reverse('superadmin_payroll_export'),
                                   {'year': today.year, 'month': today.month}).content.decode('utf-8-sig')
        header, *rows = list(csv.reader(io.StringIO(csv_text)))
        packer_row = next(r for r in rows if r[1] == 'W-950')
        self.assertEqual(packer_row[header.index(next(h for h in header if 'Taxlov' in h))], '100')

    def test_excel_tabel_and_day_sheets_show_taxlov(self):
        self.send_40()
        today = timezone.localdate()
        wb = openpyxl.load_workbook(io.BytesIO(generate_month_to_date_excel_report(today).getvalue()))
        tabel = wb.worksheets[0]
        self.assertEqual(tabel['L4'].value, "shundan Taxlov (UZS)")
        row = next(r for r in range(5, tabel.max_row + 1) if tabel.cell(r, 3).value == self.packer.full_name)
        self.assertEqual((tabel.cell(row, 6).value, tabel.cell(row, 12).value), (100.0, 100.0))   # ish haqi ichida va alohida
        day = wb[f"{today.day:02d}.{today.month:02d}"]
        texts = [c.value for r in day.iter_rows() for c in r if isinstance(c.value, str)]
        self.assertTrue(any(t.startswith('TAXLOV') for t in texts))
        self.assertIn(self.packer.full_name, texts)

        daily = openpyxl.load_workbook(io.BytesIO(generate_daily_excel_report(today).getvalue()))
        texts = [c.value for r in daily.worksheets[0].iter_rows() for c in r if isinstance(c.value, str)]
        self.assertIn(self.packer.full_name, texts)
