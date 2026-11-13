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


class RepairReceiptTest(TestCase):
    def setUp(self):
        self.otk = User.objects.create_user(username='patokk3', password='x', role=User.Role.CONTROL, assigned_screen=3)
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

    def submit(self, **data):
        body = {'box_id': self.box.id, 'mode': 'INITIAL', 'total_qty': 10, 'second_sort_qty': 0, 'repair_qty': 2,
                'repair_details': [
                    {'item_number': 1, 'operation_ids': [self.ticket.id], 'operation_names': ['soxta'], 'workers': ['soxta']},
                    {'item_number': 2, 'operation_ids': [self.ticket.id], 'notes': 'chok ochilgan'},
                ]}
        body.update(data)
        return self.client.post(reverse('control:api_submit'), json.dumps(body), content_type='application/json')

    def test_repair_queues_receipt_with_worker_and_existing_box_qr(self):
        ReceiptPrinter.objects.create(screen_number=3, host='192.168.1.53')
        self.assertEqual(self.submit().status_code, 200)
        job = PrintJob.objects.get()
        data = base64.b64decode(job.payload)
        self.assertIn(f"CONTROL:{self.box.box_code}".encode(), data)          # qutining mavjud QR kodi
        self.assertIn("Aliyeva".encode('cp866'), data)                         # ishchi bazadan, planshet matnidan emas
        self.assertNotIn(b'soxta', data)
        self.assertIn("YOQA TIKISH".encode('cp866'), data)
        self.assertIn("chok ochilgan".encode('cp866'), data)
        self.assertIn(b'K3', data)
        self.assertTrue(data.endswith(b'\x1dVB\x00'))                          # kesish
        self.assertEqual(job.status, PrintJob.Status.PENDING)

    def test_no_receipt_without_printer_or_repair(self):
        self.assertEqual(self.submit().status_code, 200)                        # printer yo'q — OTK baribir saqlanadi
        self.assertFalse(PrintJob.objects.exists())

        ReceiptPrinter.objects.create(screen_number=3, host='192.168.1.53')
        box2 = Box.objects.create(order=self.order, article=self.box.article, box_number=8, quantity=5)
        self.client.post(reverse('control:api_submit'), json.dumps({'box_id': box2.id, 'mode': 'INITIAL', 'total_qty': 5}),
                         content_type='application/json')
        self.assertFalse(PrintJob.objects.exists())                             # ta'mirsiz — chek yo'q

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
        self.assertIn("QAYTA TA'MIR".encode('cp866'), base64.b64decode(PrintJob.objects.latest('id').payload))


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


class PrintersPageTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='pr_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)

    def test_save_printers_test_print_and_reprint(self):
        self.client.post(reverse('printers:page'), {
            'host-1': '192.168.1.51', 'active-1': '1', 'host-2': 'printer-2', 'host-3': 'usb:XP80', 'active-3': '1',
            'host-4': 'usb:bad name;rm',
        })
        self.assertEqual(list(ReceiptPrinter.objects.values_list('screen_number', 'host')),
                         [(1, '192.168.1.51'), (3, 'usb:XP80')])

        self.client.post(reverse('printers:test', args=[1]))
        job = PrintJob.objects.get()
        self.client.post(reverse('printers:reprint', args=[job.id]))
        self.assertEqual(PrintJob.objects.count(), 2)

        page = self.client.get(reverse('printers:page'))
        self.assertContains(page, agent_token())
        self.assertContains(page, '192.168.1.51')

        self.client.post(reverse('printers:page'), {'host-1': ''})            # bo'sh — printerlar o'chiriladi
        self.assertFalse(ReceiptPrinter.objects.exists())
