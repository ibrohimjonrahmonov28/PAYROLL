import io
import uuid
from decimal import Decimal
import qrcode
from django.db import models
from django.core.files.base import ContentFile
from django.utils import timezone
from accounts.models import Worker, User


class Article(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="Artikul kodi (Model)")
    name = models.CharField(max_length=200, verbose_name="Model nomi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Model / Artikul"
        verbose_name_plural = "Modellar / Artikullar"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class Operation(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="Operatsiya kodi")
    name = models.CharField(max_length=200, verbose_name="Operatsiya nomi")
    description = models.TextField(blank=True, verbose_name="Tavsif")

    class Meta:
        verbose_name = "Operatsiya"
        verbose_name_plural = "Operatsiyalar katalogi"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class ArticleOperation(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name='article_operations')
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name='operation_articles')
    price_per_unit = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        verbose_name="Dona narxi (UZS)"
    )
    sequence = models.PositiveIntegerField(default=1, verbose_name="Ketma-ketlik tartibi")

    class Meta:
        verbose_name = "Model operatsiyasi va narxi"
        verbose_name_plural = "Model operatsiyalari va narxlari"
        unique_together = ('article', 'operation')
        ordering = ['sequence', 'id']

    def __str__(self):
        return f"{self.article.code} -> {self.operation.name} ({self.price_per_unit:,.0f} UZS)"


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Qoralama'
        IN_PROGRESS = 'IN_PROGRESS', 'Ishlab chiqarishda'
        COMPLETED = 'COMPLETED', 'Yakunlandi'
        CANCELLED = 'CANCELLED', 'Bekor qilindi'

    order_number = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Buyurtma raqami")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name='orders', verbose_name="Model (Artikul)")
    total_quantity = models.PositiveIntegerField(verbose_name="Jami reja miqdori (dona)")
    client_name = models.CharField(max_length=200, blank=True, verbose_name="Buyurtmachi")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, verbose_name="Holati")
    deadline = models.DateField(null=True, blank=True, verbose_name="Muddat")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Buyurtma"
        verbose_name_plural = "Buyurtmalar"
        ordering = ['-created_at']

    @property
    def total_boxes_quantity(self):
        return sum(b.quantity for b in self.boxes.all())

    @property
    def completed_tickets_count(self):
        return Ticket.objects.filter(box__order=self, status=Ticket.Status.SCANNED).count()

    @property
    def total_tickets_count(self):
        return Ticket.objects.filter(box__order=self).count()

    @property
    def progress_percentage(self):
        total = self.total_tickets_count
        if total == 0:
            return 0
        return int((self.completed_tickets_count / total) * 100)

    def __str__(self):
        return f"{self.order_number} ({self.article.code}) - {self.total_quantity} dona"


class Box(models.Model):
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'Yaratildi'
        IN_PROGRESS = 'IN_PROGRESS', 'Jarayonda'
        COMPLETED = 'COMPLETED', 'Bajarildi'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='boxes', verbose_name="Buyurtma")
    box_number = models.PositiveIntegerField(verbose_name="Quti raqami")
    quantity = models.PositiveIntegerField(verbose_name="Qutidagi donalar soni")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, verbose_name="Holati")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Quti (Bundle)"
        verbose_name_plural = "Qutilar (Bundles)"
        unique_together = ('order', 'box_number')
        ordering = ['order', 'box_number']

    @property
    def is_fully_scanned(self):
        tickets = self.tickets.all()
        if not tickets.exists():
            return False
        return not tickets.filter(status=Ticket.Status.PENDING).exists()

    def __str__(self):
        return f"{self.order.order_number} - Quti #{self.box_number} ({self.quantity} dona)"


class Ticket(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Kutilmoqda'
        SCANNED = 'SCANNED', 'Bajarildi (Skanerlangan)'

    ticket_code = models.CharField(
        max_length=64, 
        unique=True, 
        db_index=True, 
        verbose_name="Bilet kodi"
    )
    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name='tickets', verbose_name="Quti")
    article_operation = models.ForeignKey(
        ArticleOperation, 
        on_delete=models.PROTECT, 
        related_name='tickets',
        verbose_name="Operatsiya"
    )
    quantity = models.PositiveIntegerField(verbose_name="Ajratilgan dona soni")
    split_index = models.PositiveIntegerField(default=1, verbose_name="Bo'lak indeksi")
    total_splits = models.PositiveIntegerField(default=1, verbose_name="Jami bo'laklar")
    price_per_unit = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Dona narxi")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, verbose_name="Jami summa (UZS)")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name="Holati")
    
    # Skanerlash natijalari
    worker = models.ForeignKey(
        Worker, 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='tickets',
        verbose_name="Tikuvchi"
    )
    scanned_by = models.ForeignKey(
        User, 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='scanned_tickets',
        verbose_name="Master"
    )
    screen_number = models.PositiveSmallIntegerField(
        null=True, 
        blank=True, 
        verbose_name="Monitor ekrani (1-10)"
    )
    scanned_at = models.DateTimeField(null=True, blank=True, verbose_name="Skanerlangan vaqt")
    qr_code_image = models.ImageField(upload_to='qr_codes/tickets/', blank=True, null=True)

    class Meta:
        verbose_name = "Operatsiya QR Bilti"
        verbose_name_plural = "Operatsiya QR Biletlari"
        ordering = ['box', 'article_operation__sequence', 'split_index']

    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        # Bilet QR kodi formati: TICKET:<ticket_code>
        qr.add_data(f"TICKET:{self.ticket_code}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        filename = f"ticket_{self.ticket_code}.png"
        self.qr_code_image.save(filename, ContentFile(buffer.getvalue()), save=False)

    def save(self, *args, **kwargs):
        if not self.ticket_code:
            # Unikal qisqa xavfsiz kod: TKT-ORD-BOX-OP-RANDOM
            random_part = uuid.uuid4().hex[:8].upper()
            self.ticket_code = f"TK-{self.box.order.order_number}-{self.box.box_number}-{self.article_operation.operation.code}-{random_part}"
        if not self.price_per_unit:
            self.price_per_unit = self.article_operation.price_per_unit
        self.total_amount = Decimal(self.quantity) * self.price_per_unit
        
        if not self.qr_code_image:
            self.generate_qr_code()
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_code} ({self.article_operation.operation.name}: {self.quantity} dona)"
