from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from production.models import Order, Article, ProductModel, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem, Box

User = get_user_model()

class SewingStatisticsOrderModelsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='test_admin_order',
            password='testpassword123',
            role=User.Role.SUPER_ADMIN
        )
        self.client.force_login(self.user)

        self.pmodel = ProductModel.objects.create(
            code='MOD-SS',
            name='Sewing Test Model',
            daily_norm=500
        )
        self.article = Article.objects.create(
            code='ART-SS-1',
            name='Sewing Article 1',
            model=self.pmodel
        )
        self.order = Order.objects.create(
            order_number='ZAK-TEST-99',
            status=Order.Status.IN_PROGRESS,
            article=self.article
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            article=self.article,
            quantity=100
        )
        self.order_size = OrderItemSize.objects.create(
            order_item=self.order_item,
            size_name='M',
            planned_quantity=100
        )
        self.batch = CuttingBatch.objects.create(
            order_item=self.order_item,
            batch_number=1,
            pastal_code='PST-01'
        )
        self.batch_item = CuttingBatchItem.objects.create(
            batch=self.batch,
            order_item_size=self.order_size,
            quantity=50
        )
        self.box = Box.objects.create(
            order=self.order,
            article=self.article,
            cutting_batch_item=self.batch_item,
            box_number=1,
            quantity=25,
            razmer='M',
            pastal_number='PST-01',
            status=Box.Status.CREATED
        )

    def test_order_models_view_with_date_and_pastals(self):
        url = f'/sewing-statistics/orders/{self.order.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Context assertions
        self.assertIn('order_totals', response.context)
        self.assertIn('selected_date_str', response.context)
        self.assertIn('waiting_pastals', response.context['order_totals'])
        self.assertIn('real_wip_sewing', response.context['order_totals'])
        self.assertIn('waiting_control_ready', response.context['order_totals'])
        self.assertIn('waiting_control_missing_ops', response.context['order_totals'])

        content = response.content.decode('utf-8')
        # Check modal presence
        self.assertIn('modal-waiting-pastals', content)
        self.assertIn('openWaitingPastalsModal', content)
        # Check pipeline cards
        self.assertIn('3. Tikim Kutmoqda', content)
        self.assertIn('4. Hozir Patokda', content)
        self.assertIn('6. OTK Kutmoqda', content)
        # Check pastal code in modal
        self.assertIn('PST-01', content)

    def test_order_models_view_with_custom_date_param(self):
        url = f'/sewing-statistics/orders/{self.order.id}/?date=2026-10-15'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_date_str'], '2026-10-15')
        self.assertEqual(response.context['prev_date_str'], '2026-10-14')
        self.assertEqual(response.context['next_date_str'], '2026-10-16')
        self.assertFalse(response.context['is_today'])
