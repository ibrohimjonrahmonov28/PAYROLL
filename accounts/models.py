import io
import qrcode
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.files.base import ContentFile


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
        ADMIN = 'ADMIN', 'Admin'
        MASTER = 'MASTER', 'Master'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADMIN)
    phone_number = models.CharField(max_length=20, blank=True)
    telegram_user_id = models.BigIntegerField(
        null=True, 
        blank=True, 
        unique=True,
        help_text="Master Telegram hisobi ID raqami (masalan: 123456789)"
    )

    def is_superadmin(self):
        return self.is_superuser or self.role == self.Role.SUPER_ADMIN

    def is_admin_user(self):
        return self.is_superadmin() or self.role == self.Role.ADMIN

    def is_master(self):
        return self.role == self.Role.MASTER or self.is_superuser


class Worker(models.Model):
    worker_id = models.CharField(
        max_length=30, 
        unique=True, 
        db_index=True,
        help_text="Noyob identifikator (masalan: W-001, TIK-102)"
    )
    first_name = models.CharField(max_length=100, verbose_name="Ismi")
    last_name = models.CharField(max_length=100, verbose_name="Familiyasi")
    phone_number = models.CharField(max_length=25, blank=True, verbose_name="Telefon raqami")
    is_active = models.BooleanField(default=True, verbose_name="Faolmi?")
    qr_code = models.ImageField(upload_to='qr_codes/workers/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tikuvchi / Xodim"
        verbose_name_plural = "Tikuvchilar / Xodimlar"
        ordering = ['worker_id']

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        # QR kod formati: WORKER:<worker_id>
        qr.add_data(f"WORKER:{self.worker_id}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        filename = f"worker_qr_{self.worker_id}.png"
        self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)

    def save(self, *args, **kwargs):
        if not self.qr_code:
            self.generate_qr_code()
        super().save(*args, **kwargs)

    @property
    def total_earned(self):
        from production.models import Ticket
        res = self.tickets.filter(status=Ticket.Status.SCANNED).aggregate(s=models.Sum('total_amount'))['s']
        return res or 0

    @property
    def total_paid(self):
        res = self.payouts.aggregate(s=models.Sum('amount'))['s']
        return res or 0

    @property
    def balance(self):
        return self.total_earned - self.total_paid

    def __str__(self):
        return f"{self.worker_id} - {self.full_name}"


class WorkerPayout(models.Model):
    class PayoutType(models.TextChoices):
        SALARY = 'SALARY', "Oylik to'lov"
        ADVANCE = 'ADVANCE', "Avans"
        BONUS = 'BONUS', "Mukofot / Bonus"

    worker = models.ForeignKey(Worker, on_delete=models.CASCADE, related_name='payouts', verbose_name="Tikuvchi")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="To'lov summasi (UZS)")
    payout_type = models.CharField(max_length=20, choices=PayoutType.choices, default=PayoutType.ADVANCE, verbose_name="To'lov turi")
    payout_date = models.DateField(verbose_name="To'lov sanasi")
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="To'lovchi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Xodimga to'lov / Avans"
        verbose_name_plural = "Xodimlar to'lovlari va avanslari"
        ordering = ['-payout_date', '-created_at']

    def __str__(self):
        return f"{self.worker.worker_id} - {int(self.amount):,} UZS ({self.get_payout_type_display()})"

