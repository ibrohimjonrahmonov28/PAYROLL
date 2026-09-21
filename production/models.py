import io
import base64
import uuid
import secrets
import string
from decimal import Decimal
import qrcode
from django.db import models
from django.core.files.base import ContentFile
from django.utils import timezone
from accounts.models import Worker, User


class Customer(models.Model):
    name = models.CharField(max_length=200, unique=True, verbose_name="Zakazchi (Mijoz) nomi")
    code = models.CharField(max_length=50, blank=True, verbose_name="Kodi / Qisqartmasi")
    phone_number = models.CharField(max_length=50, blank=True, verbose_name="Telefon raqami")
    address = models.CharField(max_length=255, blank=True, verbose_name="Manzili")
    description = models.TextField(blank=True, verbose_name="Tavsif / Izoh")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zakazchi (Mijoz)"
        verbose_name_plural = "Zakazchilar (Mijozlar)"
        ordering = ['name']

    def __str__(self):
        return self.name


class ProductModel(models.Model):
    name = models.CharField(max_length=200, verbose_name="Model nomi")
    code = models.CharField(max_length=50, unique=True, verbose_name="Model kodi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    daily_norm = models.PositiveIntegerField(default=1000, verbose_name="Kunlik norma (ball / dona)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Model"
        verbose_name_plural = "Modellar"
        ordering = ['code']

    @property
    def total_unit_rate(self):
        return sum(mo.price_per_unit for mo in self.model_operations.all())

    def sync_operations_to_articles(self):
        """Modeldagi barcha operatsiyalarni unga tegishli barcha artikullarga sinxronlash"""
        for art in self.articles.all():
            art.sync_operations_from_model()

    def __str__(self):
        return f"{self.code} - {self.name}"


class Article(models.Model):
    model = models.ForeignKey(
        ProductModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles',
        verbose_name="Tegishli Model"
    )
    operation_group = models.ForeignKey(
        'OperationGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles',
        verbose_name="Operatsiyalar Guruhi (Shablon)"
    )
    code = models.CharField(max_length=50, unique=True, verbose_name="Artikul kodi")
    name = models.CharField(max_length=200, verbose_name="Artikul nomi / Rangi")
    image = models.ImageField(upload_to='articles/%Y/%m/', null=True, blank=True, verbose_name="Model rasmi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    daily_norm = models.PositiveIntegerField(default=1000, verbose_name="Kunlik norma (ball / shartli dona)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Artikul"
        verbose_name_plural = "Artikullar"
        ordering = ['code']

    @property
    def total_unit_rate(self):
        return sum(ao.price_per_unit for ao in self.article_operations.all())

    def sync_operations_from_group(self):
        """Guruhga tegishli barcha operatsiyalarni ushbu artikulga biriktirish / yangilash"""
        if not self.operation_group:
            return
        for item in self.operation_group.items.select_related('operation').all():
            ao = self.article_operations.filter(operation=item.operation).first()
            if ao:
                ao.price_per_unit = item.price_per_unit
                ao.sequence = item.sequence
                ao.difficulty = item.difficulty
                ao.save()
            else:
                ArticleOperation.objects.create(
                    article=self,
                    operation=item.operation,
                    price_per_unit=item.price_per_unit,
                    sequence=item.sequence,
                    difficulty=item.difficulty
                )

    def sync_operations_from_model(self):
        """Modelga tegishli barcha operatsiyalarni ushbu artikulga nusxalash"""
        if not self.model:
            return
        for m_op in self.model.model_operations.all():
            ArticleOperation.objects.update_or_create(
                article=self,
                operation=m_op.operation,
                defaults={
                    'price_per_unit': m_op.price_per_unit,
                    'sequence': m_op.sequence,
                    'difficulty': m_op.difficulty,
                }
            )

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if self.model and (not self.daily_norm or self.daily_norm == 1000):
            if self.model.daily_norm:
                self.daily_norm = self.model.daily_norm
        super().save(*args, **kwargs)
        if is_new and self.model:
            self.sync_operations_from_model()
        if self.operation_group:
            self.sync_operations_from_group()

    def __str__(self):
        if self.model:
            return f"[{self.model.name}] {self.code} - {self.name}"
        return f"{self.code} - {self.name}"


class Operation(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="Operatsiya kodi")
    name = models.CharField(max_length=200, verbose_name="Operatsiya nomi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    default_difficulty = models.FloatField(default=1.0, verbose_name="Standart qiyinlik koeffitsienti")
    order_number = models.PositiveIntegerField(default=1, verbose_name="Standart tartib raqami")

    class Meta:
        verbose_name = "Operatsiya"
        verbose_name_plural = "Operatsiyalar katalogi"
        ordering = ['order_number', 'code']

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


class ProductModelOperation(models.Model):
    model = models.ForeignKey(ProductModel, on_delete=models.CASCADE, related_name='model_operations', verbose_name="Model")
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name='operation_models', verbose_name="Operatsiya")
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
        unique_together = ('model', 'operation')
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
        return f"{self.model.code} -> {self.operation.name} ({self.price_per_unit:,.0f} UZS, Qiyinlik: {self.difficulty_display})"


class OperationGroup(models.Model):
    name = models.CharField(max_length=200, unique=True, verbose_name="Guruh nomi / Shablon nomi")
    description = models.TextField(blank=True, verbose_name="Tavsif")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Operatsiyalar guruhi"
        verbose_name_plural = "Operatsiyalar guruhlari"
        ordering = ['name']

    @property
    def total_unit_rate(self):
        return sum(item.price_per_unit for item in self.items.all())

    def sync_to_articles(self):
        """Ushbu guruhga ulangan barcha artikullarga operatsiyalarni sinxronlash"""
        for art in self.articles.all():
            art.sync_operations_from_group()

    def __str__(self):
        return self.name


class OperationGroupItem(models.Model):
    group = models.ForeignKey(OperationGroup, on_delete=models.CASCADE, related_name='items', verbose_name="Guruh")
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE, related_name='group_items', verbose_name="Operatsiya")
    price_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Dona narxi (UZS)"
    )
    sequence = models.PositiveIntegerField(default=1, verbose_name="Ketma-ketlik tartibi")
    difficulty = models.FloatField(default=1.0, verbose_name="Qiyinlik darajasi / koeffitsienti")

    class Meta:
        verbose_name = "Guruh operatsiyasi va narxi"
        verbose_name_plural = "Guruh operatsiyalari va narxlari"
        unique_together = ('group', 'operation')
        ordering = ['sequence', 'id']

    @property
    def difficulty_display(self):
        if self.difficulty is None:
            return "1"
        try:
            val = float(self.difficulty)
            return str(int(val)) if val.is_integer() else f"{val:g}"
        except Exception:
            return str(self.difficulty)

    def save(self, *args, **kwargs):
        is_existing = self.pk is not None
        super().save(*args, **kwargs)
        # Guruhdagi operatsiya narxi o'zgarsa, ushbu guruhga ulangan barcha artikullarda
        # tegishli ArticleOperation narxi yangilanadi va barcha biletlar qayta hisoblanadi
        for art in self.group.articles.all():
            ao = ArticleOperation.objects.filter(article=art, operation=self.operation).first()
            if ao:
                ao.price_per_unit = self.price_per_unit
                ao.sequence = self.sequence
                ao.difficulty = self.difficulty
                ao.save()
            else:
                ArticleOperation.objects.create(
                    article=art,
                    operation=self.operation,
                    price_per_unit=self.price_per_unit,
                    sequence=self.sequence,
                    difficulty=self.difficulty
                )

    def __str__(self):
        return f"{self.group.name} -> {self.operation.name} ({self.price_per_unit:,.0f} UZS)"


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
        verbose_name = "Artikul operatsiyasi va narxi"
        verbose_name_plural = "Artikul operatsiyalari va narxlari"
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

    def sync_price_to_tickets(self):
        """
        Operatsiya narxi o'zgarganda ushbu operatsiyaning barcha biletlari
        (shu jumladan o'tgan kunlarda skanerlangan biletlar) dona narxi
        va umumiy summasini avtomatik yangilash.
        """
        from django.db.models import F
        return self.tickets.update(
            price_per_unit=self.price_per_unit,
            total_amount=F('quantity') * self.price_per_unit
        )

    # Eskiroq kodlar bilan moslik uchun alias:
    sync_price_to_unfrozen_tickets = sync_price_to_tickets

    def save(self, *args, **kwargs):
        is_existing = self.pk is not None
        super().save(*args, **kwargs)
        if is_existing:
            self.sync_price_to_tickets()

    def __str__(self):
        return f"{self.article.code} -> {self.operation.name} ({self.price_per_unit:,.0f} UZS, Qiyinlik: {self.difficulty_display})"


class Order(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Tayyorlanmoqda'
        IN_PROGRESS = 'IN_PROGRESS', 'Jarayonda'
        COMPLETED = 'COMPLETED', 'Tugatildi'
        ARCHIVED = 'ARCHIVED', 'Arxivlandi'
        CANCELLED = 'CANCELLED', 'Bekor qilindi'

    order_number = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Buyurtma raqami")
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        verbose_name="Zakazchi (Mijoz)"
    )
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

    def save(self, *args, **kwargs):
        if self.customer and not self.client_name:
            self.client_name = self.customer.name
        elif self.client_name and not self.customer:
            cust = Customer.objects.filter(name__iexact=self.client_name.strip()).first()
            if cust:
                self.customer = cust
        super().save(*args, **kwargs)

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

    @property
    def total_planned_quantity(self):
        if self.sizes.exists():
            return sum(s.planned_quantity for s in self.sizes.all())
        return self.quantity

    @property
    def total_cut_quantity(self):
        if self.sizes.exists():
            return sum(s.total_cut_quantity for s in self.sizes.all())
        return sum(b.total_quantity for b in self.cutting_batches.all())

    @property
    def overall_cut_percentage(self):
        planned = self.total_planned_quantity
        if planned > 0:
            return round((self.total_cut_quantity / planned) * 100, 1)
        return 0.0

    @property
    def remaining_to_cut_quantity(self):
        return max(0, self.total_planned_quantity - self.total_cut_quantity)

    def __str__(self):
        return f"{self.order.order_number} -> {self.article.code} ({self.quantity} dona)"


class OrderItemSize(models.Model):
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name='sizes',
        verbose_name="Zakaz Modeli"
    )
    size_name = models.CharField(max_length=50, verbose_name="Razmer (O'lcham)", help_text="Masalan: S, M, L, XL, 38, 40...")
    planned_quantity = models.PositiveIntegerField(default=0, verbose_name="Zakaz Reja Soni")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Model Razmeri"
        verbose_name_plural = "Model Razmerlari"
        unique_together = ('order_item', 'size_name')
        ordering = ['id']

    @property
    def total_cut_quantity(self):
        """Barcha kesim partiyalarida ushbu razmerdan kesilgan jami dona"""
        return sum(item.quantity for item in self.cutting_items.all())

    @property
    def cut_percentage(self):
        """Kesilish foizi (Rejaga nisbatan)"""
        if self.planned_quantity and self.planned_quantity > 0:
            return round((self.total_cut_quantity / self.planned_quantity) * 100, 1)
        return 0.0

    @property
    def total_real_quantity(self):
        """Meto tasdiqlagan haqiqiy donalar soni"""
        return sum(
            it.real_quantity for it in self.cutting_items.filter(
                real_quantity__isnull=False
            )
        )

    @property
    def remaining_to_cut_quantity(self):
        """Hali kesilishi kerak bo'lgan qoldiq reja"""
        return max(0, self.planned_quantity - self.total_cut_quantity)

    @property
    def excess_cut_quantity(self):
        """Rejadan ortiqcha kesilgan dona"""
        return max(0, self.total_cut_quantity - self.planned_quantity)

    @property
    def boxes_created_qty(self):
        """Ushbu razmer bo'yicha yaratilgan qutilardagi jami dona"""
        return sum(b.quantity for b in self.order_item.order.boxes.filter(
            article=self.order_item.article,
            razmer__iexact=self.size_name
        ))

    @property
    def remaining_to_box_qty(self):
        """Hali quti qilinmagan kesim qoldig'i"""
        base_qty = self.total_real_quantity if self.total_real_quantity > 0 else self.total_cut_quantity
        return max(0, base_qty - self.boxes_created_qty)

    def __str__(self):
        return f"{self.order_item.article.code} [{self.size_name}]: {self.planned_quantity} ta (Kesildi: {self.total_cut_quantity})"


class CuttingBatch(models.Model):
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name='cutting_batches',
        verbose_name="Zakaz Modeli"
    )
    batch_number = models.PositiveIntegerField(verbose_name="Kesim Raqami")
    name = models.CharField(max_length=100, blank=True, verbose_name="Kesim Nomi")
    cutter_name = models.CharField(max_length=100, blank=True, verbose_name="Bichuvchi")
    pastal_code = models.CharField(max_length=100, blank=True, verbose_name="Pastal Kodi")
    partiya_number = models.CharField(max_length=100, blank=True, verbose_name="Partiya Raqami")
    fabric_weight_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Mato Og'irligi (kg)")
    fabric_batch_code = models.CharField(max_length=100, blank=True, verbose_name="Mato Partiyasi / Rulon")
    notes = models.TextField(blank=True, verbose_name="Izoh")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Kesilgan vaqti")
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_cutting_batches',
        verbose_name="Kirituvchi"
    )

    class Meta:
        verbose_name = "Kesim Partiyasi"
        verbose_name_plural = "Kesim Partiyalari"
        unique_together = ('order_item', 'batch_number')
        ordering = ['batch_number']

    def save(self, *args, **kwargs):
        if not self.batch_number:
            max_b = CuttingBatch.objects.filter(order_item=self.order_item).aggregate(m=models.Max('batch_number'))['m'] or 0
            self.batch_number = max_b + 1
        if not self.name:
            self.name = f"Kesim {self.batch_number}"
        super().save(*args, **kwargs)

    @property
    def total_quantity(self):
        return sum(it.quantity for it in self.items.all())

    @property
    def total_real_quantity(self):
        return sum(it.effective_quantity for it in self.items.all())

    @property
    def is_all_meto_confirmed(self):
        return self.items.exists() and all(it.status == CuttingBatchItem.Status.METO_CONFIRMED for it in self.items.all())

    def __str__(self):
        return f"{self.order_item.order.order_number} -> {self.order_item.article.code} | {self.name} ({self.total_quantity} dona)"


class CuttingBatchItem(models.Model):
    class Status(models.TextChoices):
        CUT_ENTERED = 'CUT_ENTERED', 'Kesim kiritildi (Meto kutilmoqda)'
        METO_CONFIRMED = 'METO_CONFIRMED', 'Meto tasdiqladi (Stikerlar yaratildi)'
        STICKERS_PRINTED = 'STICKERS_PRINTED', 'Stikerlar chop etildi (Tikuvga berildi)'

    batch = models.ForeignKey(
        CuttingBatch,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name="Kesim Partiyasi"
    )
    order_item_size = models.ForeignKey(
        OrderItemSize,
        on_delete=models.CASCADE,
        related_name='cutting_items',
        verbose_name="Razmer"
    )
    quantity = models.PositiveIntegerField(default=0, verbose_name="Kesilgan Soni (Kesimchi)")
    
    # Meto (Nomerovka) ma'lumotlari
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.CUT_ENTERED,
        db_index=True,
        verbose_name="Holati"
    )
    real_quantity = models.PositiveIntegerField(null=True, blank=True, verbose_name="Aniq Son (Meto)")
    meto_number_start = models.CharField(max_length=50, blank=True, verbose_name="Meto Boshlanishi")
    meto_number_end = models.CharField(max_length=50, blank=True, verbose_name="Meto Tugashi")
    meto_worker_name = models.CharField(max_length=100, blank=True, verbose_name="Metochi")
    meto_notes = models.TextField(blank=True, verbose_name="Meto Izohi (Braklar)")
    meto_completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Meto Tugatilgan Vaqt")
    meto_completed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='completed_meto_items',
        verbose_name="Metochi"
    )

    boxes_created_qty = models.PositiveIntegerField(default=0, verbose_name="Quti Qilingan Soni")

    class Meta:
        verbose_name = "Kesim Razmer Bandi"
        verbose_name_plural = "Kesim Razmer Bandlari"
        unique_together = ('batch', 'order_item_size')

    @property
    def effective_quantity(self):
        return self.real_quantity if self.real_quantity is not None else self.quantity

    @property
    def can_edit_cut(self):
        """Meto tasdiqlamaguncha kesimchi sonni tahrirlashi mumkin"""
        return self.status == self.Status.CUT_ENTERED and self.real_quantity is None

    @property
    def remaining_to_box(self):
        return max(0, self.effective_quantity - self.boxes_created_qty)

    def __str__(self):
        return f"{self.batch.name} - {self.order_item_size.size_name}: Kesim {self.quantity} ta (Meto: {self.effective_quantity})"


def generate_unique_box_code():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(8))
        if not Box.objects.filter(box_code=code).exists():
            return code


def generate_unique_stiker_code():
    """Yangi stikerlar uchun 8 xonali unikal harf va raqamlardan iborat kod"""
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(alphabet) for _ in range(8))
        if not Ticket.objects.filter(stiker_code=code).exists() and not Box.objects.filter(box_code=code).exists():
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
    cutting_batch_item = models.ForeignKey(
        CuttingBatchItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='boxes',
        verbose_name="Kesim Partiyasi Bandi"
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
    pastal_number = models.CharField(
        max_length=50,
        blank=True,
        default='',
        verbose_name="Pastal raqami (Pastal Code)"
    )
    meto_range = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Meto Raqamlari"
    )
    is_printed = models.BooleanField(default=False, verbose_name="Chop etilgan")
    printed_at = models.DateTimeField(null=True, blank=True, verbose_name="Chop etilgan vaqt")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True, verbose_name="Holati")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Quti (Bundle)"
        verbose_name_plural = "Qutilar (Bundles)"
        unique_together = ('order', 'box_number')
        ordering = ['order', 'box_number']

    @property
    def pastal_code(self):
        return self.pastal_number

    def save(self, *args, **kwargs):
        if not self.box_code:
            self.box_code = generate_unique_box_code()
        if not self.pastal_number and self.cutting_batch_item and self.cutting_batch_item.batch:
            self.pastal_number = self.cutting_batch_item.batch.pastal_code or str(self.cutting_batch_item.batch.batch_number)
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

    def update_status_from_tickets(self):
        total = self.tickets.count()
        if total == 0:
            return self.status
        scanned = self.tickets.filter(status=Ticket.Status.SCANNED).count()
        if scanned == total:
            new_status = self.Status.COMPLETED
        elif scanned > 0:
            new_status = self.Status.IN_PROGRESS
        else:
            new_status = self.Status.CREATED
        if self.status != new_status:
            self.status = new_status
            self.save(update_fields=['status'])
        return self.status

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
    stiker_code = models.CharField(
        max_length=8, 
        unique=True, 
        null=True, 
        blank=True, 
        db_index=True, 
        verbose_name="Stiker Unikal ID (8 talik)"
    )
    qr_code_image = models.ImageField(upload_to='qr_codes/tickets/%Y/%m/', blank=True, null=True)

    # Oylikni muzlatish (Salary Freeze)
    is_frozen = models.BooleanField(
        default=False, 
        db_index=True, 
        verbose_name="Muzlatilgan (Qulflangan)"
    )
    frozen_at = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name="Muzlatilgan vaqt"
    )
    frozen_payout = models.ForeignKey(
        'accounts.WorkerPayout', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='frozen_tickets',
        verbose_name="Bog'langan oylik to'lovi"
    )

    class Meta:
        verbose_name = "Operatsiya QR Bilti"
        verbose_name_plural = "Operatsiya QR Biletlari"
        ordering = ['box', 'article_operation__sequence', 'split_index']
        indexes = [
            models.Index(fields=['status', 'scanned_at'], name='tkt_status_scanned_idx'),
            models.Index(fields=['screen_number', 'status', 'scanned_at'], name='tkt_screen_stat_idx'),
            models.Index(fields=['worker', 'status', 'scanned_at'], name='tkt_worker_stat_idx'),
            models.Index(fields=['box', 'status'], name='tkt_box_stat_idx'),
            models.Index(fields=['is_frozen', 'status'], name='tkt_frozen_stat_idx'),
            models.Index(fields=['worker', 'is_frozen'], name='tkt_worker_frozen_idx'),
        ]

    def generate_qr_code(self):
        """
        Ixtiyoriy: agar biron-bir joyda diskka rasm fayli talab qilinsa.
        """
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

    @property
    def qr_code_data_uri(self) -> str:
        """
        Xotirada (RAM) tezkor generatsiya qilinadigan Base64 QR-kod.
        Diskka 0 bayt yozadi, Inode band qilmaydi, brauzerda bir zumda ochiladi.
        Agar mavjud eski diskdagi fayl bo'lsa, o'shandan ham foydalana oladi (100% backward compatible).
        """
        if self.qr_code_image and hasattr(self.qr_code_image, 'url'):
            try:
                if self.qr_code_image.storage.exists(self.qr_code_image.name):
                    return self.qr_code_image.url
            except Exception:
                pass

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=1,
        )
        qr.add_data(f"TICKET:{self.ticket_code}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f"data:image/png;base64,{b64}"

    def save(self, *args, **kwargs):
        if not self.ticket_code:
            random_part = uuid.uuid4().hex[:6].upper()
            self.ticket_code = f"TK-{self.box.order.order_number}-{self.box.box_number}-{self.box.box_code}-{random_part}"
        if not self.stiker_code and not self.pk:
            self.stiker_code = generate_unique_stiker_code()
        if not self.price_per_unit:
            self.price_per_unit = self.article_operation.price_per_unit
        self.total_amount = Decimal(self.quantity) * self.price_per_unit
        
        super().save(*args, **kwargs)

    @property
    def short_hash(self):
        if self.ticket_code:
            parts = self.ticket_code.split('-')
            return parts[-1] if parts else ""
        return ""

    @property
    def stiker_id(self):
        if self.stiker_code:
            return f"#{self.stiker_code}"
        return f"#{self.id}" if self.id else "#"

    def __str__(self):
        return f"{self.ticket_code} ({self.article_operation.operation.name}: {self.quantity} dona)"


class DailyModelProgress(models.Model):
    """
    Model Kunlik Norma Natijasi:
    - Har bir model (ProductModel) uchun har kuni rejalashtirilgan kunlik norma (masalan 1500 dona).
    - O'sha kuni tikilgan jami dona va foizi (masalan 80%).
    - Oldingi kundan qolgan qoldiq foiz / dona (masalan 20%).
    - Jami hisoblangan foiz.
    """
    product_model = models.ForeignKey(
        ProductModel,
        on_delete=models.CASCADE,
        related_name='daily_progress',
        verbose_name="Model"
    )
    date = models.DateField(db_index=True, verbose_name="Sana")
    daily_norm = models.PositiveIntegerField(verbose_name="Kunlik norma (dona)")
    completed_units = models.PositiveIntegerField(default=0, verbose_name="Bugun tikilgan dona")
    completion_percentage = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Bajarilish foizi (%)"
    )
    carried_over_units = models.PositiveIntegerField(
        default=0,
        verbose_name="Oldingi kundan o'tgan qoldiq dona"
    )
    carried_over_percentage = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Qoldiq foizi (%)"
    )
    total_percentage = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Jami hisoblangan foiz (%)"
    )
    is_completed = models.BooleanField(default=False, verbose_name="Norma bajarildimi?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Model Kunlik Norma Natijasi"
        verbose_name_plural = "Modellar Kunlik Norma Natijalari"
        unique_together = ('product_model', 'date')
        ordering = ['-date', 'product_model']

    def __str__(self):
        return f"{self.date} | {self.product_model.code} - {self.completed_units}/{self.daily_norm} ({self.total_percentage}%)"
