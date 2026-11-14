"""
OTK ta'mir cheki: ta'mir saqlanganda patok printeriga chek navbatga tushadi (operatsiya + tikkan ishchi + qutining
mavjud OTK QR kodi), pechat agenti API'si va superadmin printerlar sahifasi.
"""
import base64
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User, Worker
from production.models import (
    Article, ArticleOperation, Box, Operation, Order, PrintJob, ReceiptPrinter, Ticket,
)
from production.print_views import agent_token
from production.receipt_printing import repair_receipt_data


class RepairReceiptTest(TestCase):
    def setUp(self):
        # real patok OTK userlari kabi: assigned_screen yo'q, patok login nomidan
        self.otk = User.objects.create_user(username='patokk3', password='x', role=User.Role.CONTROL)
        self.worker = Worker.objects.create(worker_id='777001', first_name='Nodira', last_name='Aliyeva')
        article = Article.objects.create(code='TJ-01', name='Футболка')
        self.order = Order.objects.create(order_number='DM-46', article=article, total_quantity=10)
        self.box = Box.objects.create(order=self.order, article=article, box_number=7, quantity=10)
        op = Operation.objects.create(code='OP-YQ', name='YOQA TIKISH')
        ao = ArticleOperation.objects.create(article=article, operation=op, price_per_unit=Decimal('100'), sequence=1)
        Ticket.objects.filter(box=self.box).delete()
        self.ticket = Ticket.objects.create(box=self.box, article_operation=ao, quantity=10, worker=self.worker,
                                            status=Ticket.Status.SCANNED, screen_number=3)
        self.client.force_login(self.otk)

    def details(self):
        return [
            {'item_number': 1, 'operation_ids': [self.ticket.id], 'operation_names': ['soxta'], 'workers': ['soxta']},
            {'item_number': 2, 'operation_ids': [self.ticket.id], 'notes': 'chok ochilgan'},
        ]

    def submit(self, **data):
        body = {'box_id': self.box.id, 'mode': 'INITIAL', 'total_qty': 10, 'second_sort_qty': 0, 'repair_qty': 2,
                'repair_details': self.details()}
        body.update(data)
        return self.client.post(reverse('control:api_submit'), json.dumps(body), content_type='application/json')

    def test_repair_queues_receipt_with_worker_and_existing_box_qr(self):
        ReceiptPrinter.objects.create(screen_number=3, host='192.168.1.53')
        self.assertEqual(self.submit().status_code, 200)
        job = PrintJob.objects.get()
        data = base64.b64decode(job.payload)
        self.assertIn(b'\x1b@\x1c.', data[:80])                                # holat tiklanadi, xitoy rejimi o'chiq
        self.assertIn("Ta'mirga: 2 dona".encode('cp866'), data)
        self.assertIn("  YOQA TIKISH 2 ta".encode('cp866'), data)               # tikuvchi ostida operatsiya soni
        self.assertIn(f"1P0CONTROL:{self.box.box_code}".encode(), data)        # printerning o'z QR buyrug'i, mavjud kod
        self.assertNotIn(f"{self.box.box_code}\n\x1dVB".encode(), data)        # QR tagida quti raqami yo'q
        self.assertTrue(data.endswith(b'1Q0\x1dVB\x00'))                       # QR'dan keyin darhol kesiladi
        self.assertLess(len(data), 2000)                                        # rasm yo'q — printer xotirasi to'lmaydi
        self.assertTrue(data.endswith(b'\x1dVB\x00'))                          # kesish

        receipt = repair_receipt_data(self.box, 2, self.details(), screen_number=3)
        self.assertEqual(receipt['qr'], f"CONTROL:{self.box.box_code}")         # qutining mavjud QR kodi
        self.assertEqual(receipt['qty_line'], "Ta'mirga: 2 dona")
        self.assertEqual(receipt['info'], [f"Quti: {self.box.box_code} (#7)", 'Zakaz: DM-46', 'Model: TJ-01 Футболка'])
        self.assertTrue(receipt['footer'].startswith('K3 · '))
        self.worker.refresh_from_db()
        self.assertEqual(receipt['workers'], [(('Nodira Aliyeva', self.worker.user.uid), [('YOQA TIKISH', 2)])])   # bazadan, UID
        self.assertEqual(receipt['notes'], ['chok ochilgan'])
        self.assertEqual(job.status, PrintJob.Status.PENDING)

    def test_no_receipt_without_printer_or_repair(self):
        self.assertEqual(self.submit().status_code, 200)                        # printer yo'q — OTK baribir saqlanadi
        self.assertFalse(PrintJob.objects.exists())

        ReceiptPrinter.objects.create(screen_number=3, host='192.168.1.53')
        box2 = Box.objects.create(order=self.order, article=self.box.article, box_number=8, quantity=5)
        self.client.post(reverse('control:api_submit'), json.dumps({'box_id': box2.id, 'mode': 'INITIAL', 'total_qty': 5}),
                         content_type='application/json')
        self.assertFalse(PrintJob.objects.exists())                             # ta'mirsiz — chek yo'q

    def test_old_patok_login_and_personal_login(self):
        ReceiptPrinter.objects.create(screen_number=3, host='192.168.1.53')
        old_style, _ = User.objects.get_or_create(username='patok3', defaults={'role': User.Role.CONTROL})
        self.assertIsNone(old_style.assigned_screen)                           # eski login uslubi, patok yozilmagan
        self.client.force_login(old_style)
        self.submit()
        self.assertEqual(PrintJob.objects.count(), 1)

        personal = User.objects.create_user(username='otk_nodira', password='x', role=User.Role.CONTROL)
        self.client.force_login(personal)                                       # patoki noma'lum — chek yo'q
        box2 = Box.objects.create(order=self.order, article=self.box.article, box_number=9, quantity=5)
        self.client.post(reverse('control:api_submit'), json.dumps({'box_id': box2.id, 'mode': 'INITIAL', 'total_qty': 5,
                                                                    'repair_qty': 1}), content_type='application/json')
        self.assertEqual(PrintJob.objects.count(), 1)

    def test_other_patok_printer_not_used(self):
        ReceiptPrinter.objects.create(screen_number=4, host='192.168.1.54')
        self.submit()
        self.assertFalse(PrintJob.objects.exists())

    def test_repair_return_with_re_repair_prints_again(self):
        ReceiptPrinter.objects.create(screen_number=3, host='usb:XP80')
        self.submit()
        self.client.post(reverse('control:api_submit'), json.dumps({
            'box_id': self.box.id, 'mode': 'REPAIR_RETURN', 'defect_qty': 0, 're_repair_qty': 1,
            'repair_details': [{'item_number': 1, 'operation_ids': [self.ticket.id]}],
        }), content_type='application/json')
        self.assertEqual(PrintJob.objects.count(), 2)
        self.assertTrue(PrintJob.objects.latest('id').title.startswith("Qayta ta'mir"))
        self.assertEqual(repair_receipt_data(self.box, 1, [], screen_number=3, is_return=True)['qty_line'], "Qayta ta'mirga: 1 dona")

    def test_worker_operations_grouped_under_name(self):
        op2 = Operation.objects.create(code='OP-YC', name='YON CHOK')
        ao2 = ArticleOperation.objects.create(article=self.box.article, operation=op2, price_per_unit=Decimal('50'), sequence=2)
        t2 = Ticket.objects.create(box=self.box, article_operation=ao2, quantity=10, worker=self.worker,
                                   status=Ticket.Status.SCANNED, screen_number=3)
        details = [{'operation_ids': [self.ticket.id]}] * 3 + [{'operation_ids': [t2.id]}] * 2
        workers = repair_receipt_data(self.box, 5, details, screen_number=3)['workers']
        self.assertEqual([ops for _, ops in workers], [[('YOQA TIKISH', 3), ('YON CHOK', 2)]])   # bitta ism ostida


class PrintAgentApiTest(TestCase):
    def setUp(self):
        self.printer = ReceiptPrinter.objects.create(screen_number=1, host='192.168.1.51')
        self.job = PrintJob.objects.create(printer=self.printer, title='Sinov', payload=base64.b64encode(b'x').decode())
        self.headers = {'HTTP_X_AGENT_TOKEN': agent_token()}

    def result(self, ok, **extra):
        return self.client.post(reverse('printers:agent_result', args=[self.job.id]),
                                json.dumps(dict(ok=ok, **extra)), content_type='application/json', **self.headers)

    def test_token_required(self):
        self.assertEqual(self.client.get(reverse('printers:agent_jobs')).status_code, 403)
        self.assertEqual(self.client.get(reverse('printers:agent_jobs'), HTTP_X_AGENT_TOKEN='bad').status_code, 403)

    def test_job_is_handed_out_once_and_marked_printed(self):
        jobs = self.client.get(reverse('printers:agent_jobs'), **self.headers).json()['jobs']
        self.assertEqual([(j['host'], j['port'], j['data']) for j in jobs], [('192.168.1.51', 9100, 'eA==')])
        self.assertEqual(self.client.get(reverse('printers:agent_jobs'), **self.headers).json()['jobs'], [])
        self.result(True)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, PrintJob.Status.PRINTED)

    def test_failed_job_retries_then_stops(self):
        for attempt in range(5):
            self.assertEqual(len(self.client.get(reverse('printers:agent_jobs'), **self.headers).json()['jobs']), 1)
            self.result(False, error='timed out')
        self.job.refresh_from_db()
        self.assertEqual((self.job.status, self.job.attempts), (PrintJob.Status.FAILED, 5))
        self.assertIn('timed out', self.job.error)


class TabletPrintApiTest(TestCase):
    """Planshetdagi Terry Jar Print ilovasi: ?patok= bilan faqat o'z patokining "tablet" printeri cheklari."""
    def setUp(self):
        self.headers = {'HTTP_X_AGENT_TOKEN': agent_token()}
        self.k13 = ReceiptPrinter.objects.create(screen_number=13, host='tablet')
        self.k2 = ReceiptPrinter.objects.create(screen_number=2, host='tablet')
        self.lan = ReceiptPrinter.objects.create(screen_number=1, host='192.168.1.51')
        for printer in (self.k13, self.k2, self.lan):
            PrintJob.objects.create(printer=printer, title=f"#{printer.screen_number}", payload='eA==')

    def jobs(self, **params):
        return self.client.get(reverse('printers:agent_jobs'), params, **self.headers).json()

    def test_tablet_gets_only_its_patok_and_is_marked_seen(self):
        self.assertEqual([j['title'] for j in self.jobs(patok='K13')['jobs']], ['#13'])
        self.k13.refresh_from_db()
        self.assertIsNotNone(self.k13.last_seen_at)
        self.assertEqual([j['title'] for j in self.jobs(patok='2')['jobs']], ['#2'])

    def test_computer_agent_skips_tablet_printers(self):
        self.assertEqual([j['title'] for j in self.jobs()['jobs']], ['#1'])

    def test_patok_without_tablet_printer(self):
        self.assertEqual(self.jobs(patok='K1')['status'], 'NO_PRINTER')        # K1 — LAN printer, planshet emas
        self.assertEqual(self.jobs(patok='abc')['status'], 'NO_PRINTER')


class PrintersPageTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='pr_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)

    def test_save_printers_test_print_and_reprint(self):
        self.client.post(reverse('printers:page'), {
            'host-1': '192.168.1.51', 'active-1': '1', 'host-2': 'printer-2', 'host-3': 'usb:XP80', 'active-3': '1',
            'host-5': 'Tablet', 'active-5': '1',
            'host-4': 'usb:bad name;rm',
        })
        self.assertEqual(list(ReceiptPrinter.objects.values_list('screen_number', 'host')),
                         [(1, '192.168.1.51'), (3, 'usb:XP80'), (5, 'tablet')])

        self.client.post(reverse('printers:test', args=[1]))
        job = PrintJob.objects.get()
        self.client.post(reverse('printers:reprint', args=[job.id]))
        self.assertEqual(PrintJob.objects.count(), 2)

        page = self.client.get(reverse('printers:page'))
        self.assertContains(page, agent_token())
        self.assertContains(page, '192.168.1.51')

        self.client.post(reverse('printers:page'), {'host-1': ''})            # bo'sh — printerlar o'chiriladi
        self.assertFalse(ReceiptPrinter.objects.exists())
