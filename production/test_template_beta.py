"""
Narx shablonidagi beta belgisi (egasi talabi 2026-11-14): sariq "OTK Control/Taxlov" qatori (narx, qiyinlik) va
faqat belgi yoqilgandan keyin yaratilgan qutilar yangi tartibda ishlaydi. Stikerlar o'zgarmaydi.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from production.models import Article, Box, Operation, OperationGroup, OperationGroupItem, Order, Ticket
from production.packing_services import beta_group
from production.services import generate_box_tickets


class TemplateBetaTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='tb_admin', password='x', role=User.Role.SUPER_ADMIN)
        self.client.force_login(self.admin)
        self.group = OperationGroup.objects.create(name='FUTBOLKA BETA')
        OperationGroupItem.objects.create(group=self.group, operation=Operation.objects.create(code='OP-Y', name='YOQA'),
                                          price_per_unit=Decimal('300'), sequence=1)
        self.article = Article.objects.create(code='TB-1', name='Futbolka')
        self.article.operation_group = self.group
        self.article.save()
        self.order = Order.objects.create(order_number='TB-ORD', article=self.article, total_quantity=100)

    def box(self, number):
        box = Box.objects.create(order=self.order, article=self.article, box_number=number, quantity=50)
        generate_box_tickets(box)
        return box

    def set_beta(self, on, price='1', difficulty='1.5'):
        data = {'action': 'update_group_beta', 'group_id': self.group.id, 'taxlov_price': price, 'taxlov_difficulty': difficulty}
        if on:
            data['is_beta'] = '1'
        self.client.post(reverse('superadmin_pricing'), data)
        self.group.refresh_from_db()

    def test_only_boxes_created_after_beta_and_stickers_unchanged(self):
        old_box = self.box(1)
        old_tickets = list(old_box.tickets.values_list('ticket_code', flat=True))
        self.set_beta(True)
        self.assertTrue(self.group.is_beta)
        self.assertEqual((self.group.taxlov_price, self.group.taxlov_difficulty), (Decimal('1'), 1.5))

        new_box = self.box(2)
        self.assertIsNone(beta_group(old_box))                                 # belgidan oldingi quti — eski tartib
        self.assertEqual(beta_group(new_box), self.group)
        # Control/Taxlov artikul operatsiyasi emas: yangi stiker yaratilmaydi, eskilari o'zgarmaydi
        self.assertEqual(self.article.article_operations.count(), 1)
        self.assertEqual(new_box.tickets.count(), 1)
        self.assertEqual(list(old_box.tickets.values_list('ticket_code', flat=True)), old_tickets)

    def test_beta_since_kept_on_reenable_and_off_stops_new_flow(self):
        self.set_beta(True)
        first_since = self.group.beta_since
        box = self.box(1)
        self.set_beta(False)
        self.assertIsNone(beta_group(box))
        Box.objects.filter(pk=box.pk).update(created_at=timezone.now() + datetime.timedelta(seconds=1))
        self.set_beta(True)
        self.assertEqual(self.group.beta_since, first_since)
        box.refresh_from_db()
        self.assertEqual(beta_group(box), self.group)

    def test_validation_and_page(self):
        self.set_beta(True, price='-5')
        self.assertFalse(self.group.is_beta)                                    # manfiy narx — saqlanmaydi
        self.set_beta(True, price='2,5')
        self.assertEqual(self.group.taxlov_price, Decimal('2.5'))
        page = self.client.get(reverse('superadmin_pricing'))
        self.assertContains(page, 'BETA · OTK Control/Taxlov')
        self.assertContains(page, "BETA · Control/Taxlov 2.50 so'm")
