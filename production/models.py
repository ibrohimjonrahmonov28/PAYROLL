import io
import uuid
import secrets
import string
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
    daily_norm = models.PositiveIntegerField(default=1500, verbose_name="Kunlik norma (ball / shartli dona)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Model / Artikul"
        verbose_name_plural = "Modellar / Artikullar"
        ordering = ['code']

    @property
    def total_unit_rate(self):
        return sum(ao.price_per_unit for ao in self.article_operations.all())

    def __str__(self):
        return f"{self.code} - {self.name}"


class Operation(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="Operatsiya kodi")
    name = models.CharField(max_length=200, verbose_name="Operatsiya nomi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    default_difficulty = models.FloatField(default=1.0, verbose_name="Standart qiyinlik koeffitsienti")

    class Meta:
        verbose_name = "Operatsiya"
        verbose_name_plural = "Operatsiyalar katalogi"
        ordering = ['code']

    @property
    def default_difficulty_display(self):
        if self.default_difficulty is None:
            return "1"
        try:
            val = float(self.default_difficulty)
            if val.is_integer():
                return str(int(val))
            return f"{val:g}"
        except Exception:
            return str(self.default_difficulty)

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
    difficulty = models.FloatField(default=1.0, verbose_name="Qiyinlik darajasi / koeffitsienti")

    class Meta:
        verbose_name = "Model operatsiyasi va narxi"
        verbose_name_plural = "Model operatsiyalari va narxlari"
        unique_together = ('article', 'operation')
        ordering = ['sequence', 'id']

    @property
    def difficulty_display(self):
        if self.difficulty is None:
            return "1"
        try:
            val = float(self.difficulty)
            if val.is_integer():
                return str(int(val))
            return f"{val:g}"
        except Exception:
            return str(self.difficulty)

    def __str__(self):
        return f"{self.article.code} -> {self.operation.name} ({self.price_per_unit:,.0f} UZS, Qiyinlik: {self.difficulty_display})"


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Qoralama'
        IN_PROGRESS = 'IN_PROGRESS', 'Ishlab chiqarishda'
        COMPLETED = 'COMPLETED', 'Yakunlandi'
        CANCELLED = 'CANCELLED', 'Bekor qilindi'

    order_number = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Buyurtma raqami")
    article = models.ForeignKey(Article, on_delete=models.PROTECT, null=True, blank=True, related_name='orders', verbose_name="Asosiy Model (Artikul)")
    total_quantity = models.PositiveIntegerField(default=0, verbose_name="Jami reja miqdori (dona)")
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
        if hasattr(self, 'annotated_total_boxes_qty') and self.annotated_total_boxes_qty is not None:
            return self.annotated_total_boxes_qty
        return sum(b.quantity for b in self.boxes.all())

    @property
    def completed_tickets_count(self):
        if hasattr(self, 'annotated_completed_tickets'):
            return self.annotated_completed_tickets
        return Ticket.objects.filter(box__order=self, status=Ticket.Status.SCANNED).count()

    @property
    def total_tickets_count(self):
        if hasattr(self, 'annotated_total_tickets'):
            return self.annotated_total_tickets
        return Ticket.objects.filter(box__order=self).count()

    @property
    def progress_percentage(self):
        total = self.total_tickets_count
        if total == 0:
            return 0
        return int((self.completed_tickets_count / total) * 100)

    @property
    def models_count(self):
        count = self.items.count()
        return count if count > 0 else (1 if self.article else 0)

    @property
    def all_models_quantity(self):
        items_sum = self.items.aggregate(s=models.Sum('quantity'))['s']
        if items_sum:
            return items_sum
        return self.total_quantity

    @property
    def total_order_cost(self):
        """Zakaz ichidagi barcha modellar sdelshina fondi summasi"""
        return sum(item.total_cost for item in self.items.all())

    @property
    def average_unit_cost(self):
        total_qty = self.all_models_quantity
        if total_qty and total_qty > 0:
            return Decimal(str(self.total_order_cost)) / Decimal(str(total_qty))
        return Decimal('0.00')

    @property
    def total_operations_count(self):
        return sum(item.article.article_operations.count() for item in self.items.all())

    def __str__(self):
        return f"{self.order_number} ({self.client_name or 'Buyurtma'}) - {self.all_models_quantity} dona"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items', verbose_name="Zakaz")
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name='order_items', verbose_name="Model (Artikul)")
    quantity = models.PositiveIntegerField(verbose_name="Soni (dona)")
    norm = models.PositiveIntegerField(default=1000, verbose_name="Model normasi (ball)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zakaz Modeli"
        verbose_name_plural = "Zakaz Modellari"
        unique_together = ('order', 'article')

    def save(self, *args, **kwargs):
        if not self.norm and self.article and getattr(self.article, 'daily_norm', None):
            self.norm = self.article.daily_norm
        super().save(*args, **kwargs)

    @property
    def unit_total_rate(self):
        """Bitta dona kiyim uchun barcha operatsiyalar summasi (UZS)"""
        return sum(ao.price_per_unit for ao in self.article.article_operations.all())

    @property
    def total_cost(self):
        """Ushbu model butun partiyasi uchun jami sdelshina fondi (UZS)"""
        return self.quantity * self.unit_total_rate

    @property
    def share_of_order_quantity(self):
        total_qty = self.order.all_models_quantity
        if total_qty and total_qty > 0:
            return round((self.quantity / total_qty) * 100, 1)
        return 0

    @property
    def share_of_order_cost(self):
        total_cost = self.order.total_order_cost
        if total_cost and total_cost > 0:
            return round((self.total_cost / total_cost) * 100, 1)
        return 0

    def get_operations_with_totals(self):
        """Har bir operatsiyani partiya summasi va ulushi bilan qaytarish"""
        ops = self.article.article_operations.select_related('operation').order_by('sequence', 'id')
        unit_rate = self.unit_total_rate
        result = []
        for op in ops:
            total_line_cost = op.price_per_unit * self.quantity
            share_pct = round((op.price_per_unit / unit_rate * 100), 1) if unit_rate > 0 else 0
            t_scanned = Ticket.objects.filter(box__order=self.order, article_operation=op, status=Ticket.Status.SCANNED).count()
            t_total = Ticket.objects.filter(box__order=self.order, article_operation=op).count()
            t_progress = int((t_scanned / t_total * 100)) if t_total > 0 else 0
            
            result.append({
                'ao': op,
                'total_line_cost': total_line_cost,
                'share_pct': share_pct,
                'scanned_count': t_scanned,
                'total_count': t_total,
                'progress': t_progress
            })
        return result

    def __str__(self):
        return f"{self.order.order_number} -> {self.article.code} ({self.quantity} dona)"


def generate_unique_box_code():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(8))
        if not Box.objects.filter(box_code=code).exists():
            return code


class Box(models.Model):
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'Yaratildi'
        IN_PROGRESS = 'IN_PROGRESS', 'Jarayonda'
        COMPLETED = 'COMPLETED', 'Bajarildi'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='boxes', verbose_name="Buyurtma")
    article = models.ForeignKey(
        Article, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True, 
        related_name='boxes', 
        verbose_name="Model (Artikul)"
    )
    box_code = models.CharField(
        max_length=8, 
        unique=True, 
        default=generate_unique_box_code,
        db_index=True, 
        verbose_name="Quti Unikal ID (8 talik)"
    )
    box_number = models.PositiveIntegerField(db_index=True, verbose_name="Quti raqami")
    quantity = models.PositiveIntegerField(verbose_name="Qutidagi donalar soni")
    razmer = models.CharField(
        max_length=50, 
        blank=True, 
        null=True, 
        verbose_name="Razmer (O'lcham)"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True, verbose_name="Holati")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Quti (Bundle)"
        verbose_name_plural = "Qutilar (Bundles)"
        unique_together = ('order', 'box_number')
        ordering = ['order', 'box_number']

    def save(self, *args, **kwargs):
        if not self.box_code:
            self.box_code = generate_unique_box_code()
        super().save(*args, **kwargs)

    @property
    def target_article(self):
        if self.article:
            return self.article
        if self.order and self.order.article:
            return self.order.article
        if self.order and self.order.items.exists():
            return self.order.items.first().article
        return None

    @property
    def tickets_count(self):
        return self.tickets.count()

    @property
    def scanned_tickets_count(self):
        return self.tickets.filter(status=Ticket.Status.SCANNED).count()

    @property
    def progress_percentage(self):
        total = self.tickets_count
        if total == 0:
            return 0
        return int((self.scanned_tickets_count / total) * 100)

    @property
    def is_fully_scanned(self):
        tickets = self.tickets.all()
        if not tickets.exists():
            return False
        return not tickets.filter(status=Ticket.Status.PENDING).exists()

    def __str__(self):
        art_code = self.target_article.code if self.target_article else "N/A"
        return f"{self.order.order_number} - Quti #{self.box_number} [{self.box_code}] ({self.quantity} dona)"


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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True, verbose_name="Holati")
    
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
        db_index=True,
        verbose_name="Monitor ekrani (1-10)"
    )
    scanned_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Skanerlangan vaqt")
    qr_code_image = models.ImageField(upload_to='qr_codes/tickets/', blank=True, null=True)

    class Meta:
        verbose_name = "Operatsiya QR Bilti"
        verbose_name_plural = "Operatsiya QR Biletlari"
        ordering = ['box', 'article_operation__sequence', 'split_index']
        indexes = [
            models.Index(fields=['status', 'scanned_at'], name='tkt_status_scanned_idx'),
            models.Index(fields=['screen_number', 'status', 'scanned_at'], name='tkt_screen_stat_idx'),
            models.Index(fields=['worker', 'status', 'scanned_at'], name='tkt_worker_stat_idx'),
            models.Index(fields=['box', 'status'], name='tkt_box_stat_idx'),
        ]

    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
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
            random_part = uuid.uuid4().hex[:6].upper()
            self.ticket_code = f"TK-{self.box.order.order_number}-{self.box.box_number}-{self.box.box_code}-{random_part}"
        if not self.price_per_unit:
            self.price_per_unit = self.article_operation.price_per_unit
        self.total_amount = Decimal(self.quantity) * self.price_per_unit
        
        if not self.qr_code_image:
            self.generate_qr_code()
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_code} ({self.article_operation.operation.name}: {self.quantity} dona)"
