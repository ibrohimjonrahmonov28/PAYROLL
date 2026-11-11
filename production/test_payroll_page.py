"""
/superadmin/payroll/ yengil bo'lishi (2026-11-11: 998 xodim bitta sahifada 7,4 MB edi): bo'sh xodimlar standart holatda
yashirin, 100 tadan sahifalar, ID yoki ism-familiya (bir nechta so'z) bo'yicha qidiruv barcha sahifalar bo'yicha.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, Worker
from production.models import Article, ArticleOperation, Box, Operation, Order, Ticket


class PayrollPageTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="payroll_page_admin", password="x", role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)
        article = Article.objects.create(code="ART-PP", name="Payroll")
        art_op = ArticleOperation.objects.create(article=article, operation=Operation.objects.create(code="OP-PP", name="Tikish"),
                                                 price_per_unit=Decimal("100"), sequence=1)
        box = Box.objects.create(order=Order.objects.create(order_number="ORD-PP", article=article, total_quantity=50),
                                 article=article, box_number=1, quantity=50)
        self.active = []
        for i in range(5):
            worker = Worker.objects.create(worker_id=f"W-PP-{i}", first_name=f"Ishchi{i}", last_name="Faol")
            Ticket.objects.create(box=box, article_operation=art_op, quantity=1, price_per_unit=Decimal("100"),
                                  total_amount=Decimal("100"), status=Ticket.Status.SCANNED, worker=worker,
                                  scanned_at=timezone.now())
            self.active.append(worker)
        self.empty = Worker.objects.create(worker_id="W-PP-EMPTY", first_name="Malika", last_name="Karimova")

    def ids(self, response):
        return [row['worker'].id for row in response.context['payroll_data']]

    def test_empty_workers_hidden_by_default(self):
        res = self.client.get(reverse('superadmin_payroll'))
        self.assertNotIn(self.empty.id, self.ids(res))
        self.assertEqual((res.context['visible_count'], res.context['hidden_empty_count']), (5, 1))
        self.assertContains(res, "1 ta bo'sh xodimni ko'rsatish")
        self.assertIn(self.empty.id, self.ids(self.client.get(reverse('superadmin_payroll'), {'all': '1'})))

    def test_search_by_id_or_full_name_shows_even_empty_worker(self):
        for query in ("W-PP-EMPTY", "Malika Karimova", "karimova mal"):
            res = self.client.get(reverse('superadmin_payroll'), {'q': query})
            self.assertEqual(self.ids(res), [self.empty.id], query)
        self.assertEqual(self.ids(self.client.get(reverse('superadmin_payroll'), {'q': 'Malika Faol'})), [])

    def test_pagination_keeps_filters_and_numbering(self):
        with patch('accounts.superadmin_views.PAYROLL_PAGE_SIZE', 2):
            res = self.client.get(reverse('superadmin_payroll'), {'branch': 'ALL', 'page': 2})
            self.assertEqual(res.context['page_obj'].paginator.num_pages, 3)
            self.assertEqual(len(self.ids(res)), 2)
            self.assertEqual(res.context['row_offset'], 2)
            self.assertContains(res, 'href="?branch=ALL&amp;page=3"')
            self.assertEqual(self.client.get(reverse('superadmin_payroll'), {'page': 99}).context['page_obj'].number, 3)
