from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from production.models import Order, Article, ProductModel

User = get_user_model()

class NormaCanvasThumbnailTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='test_admin',
            password='testpassword123',
            role=User.Role.SUPER_ADMIN
        )
        self.client.force_login(self.user)

        # Create model & article with image
        self.pmodel = ProductModel.objects.create(
            code='MOD-TEST',
            name='Test Model',
            daily_norm=1000
        )
        fake_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        self.image_file = SimpleUploadedFile("test_garment.png", fake_png, content_type="image/png")
        self.art_with_img = Article.objects.create(
            code='ART-WITH-IMG',
            name='Model with Photo',
            model=self.pmodel,
            image=self.image_file
        )
        self.art_no_img = Article.objects.create(
            code='ART-NO-IMG',
            name='Model without Photo',
            model=self.pmodel
        )
        self.order = Order.objects.create(
            order_number='ZAK-CANVAS-01',
            status=Order.Status.IN_PROGRESS,
            article=self.art_with_img
        )

    def test_norma_canvas_renders_with_thumbnail_support(self):
        response = self.client.get('/norma/canvas/')
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')
        # Check modal presence
        self.assertIn('imagePreviewModal', content)
        self.assertIn('openImagePreviewModal', content)
        self.assertIn('closeImagePreviewModal', content)

        # Check serialization in orders_data_json
        orders_json = response.context['orders_data_json']
        self.assertIn('"image_url":', orders_json)
        self.assertIn('test_garment', orders_json)

        # Check v9 storage key and w-72 card width
        self.assertIn('norma_canvas_table_positions_v9', content)
        self.assertIn('w-72', content)
