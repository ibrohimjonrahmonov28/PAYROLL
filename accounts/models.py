import io
import secrets
from decimal import Decimal
import qrcode
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.files.base import ContentFile
from django.utils import timezone


def generate_unique_user_uid():
    """6 xonali unikal raqamli UID generatsiya qilish (100000 - 999999)"""
    from accounts.models import User
    while True:
        code = str(secrets.randbelow(900000) + 100000)
        if not User.objects.filter(uid=code).exists():
            return code


class User(AbstractUser):
    class Branch(models.TextChoices):
        HQ = 'HQ', 'Bosh bino (HQ)'
        UYCHI = 'UYCHI', 'Uychi'

    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
        ADMIN = 'ADMIN', 'Admin'
        BRANCH_ADMIN = 'BRANCH_ADMIN', 'Filial Admini'
        MANAGER = 'MANAGER', 'Menejer'
        CUTTER = 'CUTTER', 'Kesimchi (Bichuv)'
        METO = 'METO', 'Metochi (Nomerovka)'
        STICKER = 'STICKER', 'Stiker Chiqaruvchi'
        MASTER = 'MASTER', 'Master'
        CONTROL = 'CONTROL', 'Kontrolchi (Sifat Nazorati)'
        SCREEN = 'SCREEN', 'Ekran (Monitor)'
        USER = 'USER', 'Oddiy User'

    username = models.CharField(
        max_length=150,
        unique=True,
        null=True,
        blank=True,
        default=None,
        verbose_name="Foydalanuvchi nomi"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    branch = models.CharField(
        max_length=50,
        choices=Branch.choices,
        default=Branch.HQ,
        db_index=True,
        verbose_name="Ish joyi (Filial)"
    )
    phone_number = models.CharField(max_length=20, blank=True)
    uid = models.CharField(
        max_length=6,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Unikal 6 xonali UID"
    )
    qr_code = models.ImageField(upload_to='qr_codes/users/', blank=True, null=True, verbose_name="QR Kod")
    telegram_user_id = models.BigIntegerField(
        null=True, 
        blank=True, 
        unique=True,
        help_text="Master Telegram hisobi ID raqami (masalan: 123456789)"
    )
    is_badge_printed = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Birka berilgan / chop etilgan",
        help_text="Xodimga ID birka (beydjik) chop etib berilganmi?"
    )

    def generate_qr_code(self):
        if not self.uid:
            self.uid = generate_unique_user_uid()
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(f"USER:{self.uid}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        filename = f"user_qr_{self.uid}.png"
        self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)

    def save(self, *args, **kwargs):
        if not self.uid:
            self.uid = generate_unique_user_uid()
        if not self.username:
            candidate = f"user_{self.uid}"
            suffix = 1
            while User.objects.filter(username=candidate).exclude(id=self.id).exists():
                candidate = f"user_{self.uid}_{suffix}"
                suffix += 1
            self.username = candidate
        if not self.qr_code:
            self.generate_qr_code()
        super().save(*args, **kwargs)

    def is_superadmin(self):
        return self.is_superuser or self.role == self.Role.SUPER_ADMIN

    def is_admin_user(self):
        return self.is_superadmin() or self.role == self.Role.ADMIN

    def is_manager(self):
        return self.is_superadmin() or self.role == self.Role.MANAGER

    def is_cutter(self):
        return self.is_superadmin() or self.role == self.Role.CUTTER

    def is_meto(self):
        return self.is_superadmin() or self.role == self.Role.METO

    def is_sticker(self):
        return self.is_superadmin() or self.role == self.Role.STICKER

    def is_master(self):
        return self.role == self.Role.MASTER or self.is_superuser

    def is_controller(self):
        return self.role == self.Role.CONTROL or self.is_superuser

    def is_screen(self):
        return self.role == self.Role.SCREEN

    def is_branch_admin(self):
        return self.role == self.Role.BRANCH_ADMIN

    def can_access_payroll(self):
        return self.is_superadmin() or self.role == self.Role.BRANCH_ADMIN

    @property
    def assigned_screen_number(self):
        """
        SCREEN roli foydalanuvchisi uchun biriktirilgan ekran raqami (1..40).
        Masalan, ekran1 -> 1, ..., ekran40 -> 40.
        """
        if self.role == self.Role.SCREEN:
            import re
            match = re.search(r'\d+', self.username or '')
            if match:
                try:
                    num = int(match.group())
                    if 1 <= num <= 40:
                        return num
                except ValueError:
                    pass
            return 1
        return None

    def is_regular_user(self):
        return self.role == self.Role.USER


class Worker(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='worker_profile',
        verbose_name="Foydalanuvchi hisobi"
    )
    worker_id = models.CharField(
        max_length=30, 
        unique=True, 
        db_index=True,
        help_text="Noyob identifikator (masalan: W-001, TIK-102)"
    )
    first_name = models.CharField(max_length=100, verbose_name="Ismi")
    last_name = models.CharField(max_length=100, verbose_name="Familiyasi")
    phone_number = models.CharField(max_length=25, blank=True, verbose_name="Telefon raqami")
    branch = models.CharField(
        max_length=50,
        choices=User.Branch.choices,
        default=User.Branch.HQ,
        db_index=True,
        verbose_name="Ish joyi (Filial)"
    )
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Faolmi?")
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
        # 1. Yangi ishchi qo'shilganda avtomatik 'ODDIY USER' hisobini yaratish yoki bog'lash
        if not self.user:
            base_username = f"worker_{self.worker_id.lower().replace('-', '_')}"
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1

            new_user = User(
                username=username,
                first_name=self.first_name,
                last_name=self.last_name,
                phone_number=self.phone_number,
                branch=self.branch,
                role=User.Role.USER,
                is_staff=False,
                is_superuser=False
            )
            new_user.set_password("worker123")
            new_user.save()
            self.user = new_user
        else:
            # Agar ishchining ma'lumotlari o'zgarsa, user hisobini ham yangilash
            if self.user:
                update_fields_arg = kwargs.get('update_fields')
                update_fields_set = set(update_fields_arg) if update_fields_arg is not None else None
                update_user_fields = []
                if (update_fields_set is None or 'first_name' in update_fields_set) and self.user.first_name != self.first_name:
                    self.user.first_name = self.first_name
                    update_user_fields.append('first_name')
                if (update_fields_set is None or 'last_name' in update_fields_set) and self.user.last_name != self.last_name:
                    self.user.last_name = self.last_name
                    update_user_fields.append('last_name')
                if (update_fields_set is None or 'phone_number' in update_fields_set) and self.user.phone_number != self.phone_number:
                    self.user.phone_number = self.phone_number
                    update_user_fields.append('phone_number')
                if (update_fields_set is None or 'branch' in update_fields_set) and self.user.branch != self.branch:
                    self.user.branch = self.branch
                    update_user_fields.append('branch')
                if update_user_fields:
                    self.user.save(update_fields=update_user_fields)

        if not self.qr_code:
            self.generate_qr_code()
        super().save(*args, **kwargs)

    @property
    def total_earned(self):
        from production.models import Ticket
        res = self.tickets.filter(status=Ticket.Status.SCANNED).aggregate(s=models.Sum('total_amount'))['s']
        return res or Decimal('0.00')

    @property
    def today_earned(self):
        from production.models import Ticket
        today = timezone.localdate()
        res = self.tickets.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__date=today
        ).aggregate(s=models.Sum('total_amount'))['s']
        return res or Decimal('0.00')

    @property
    def today_units(self):
        from production.models import Ticket
        today = timezone.localdate()
        res = self.tickets.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__date=today
        ).aggregate(s=models.Sum('quantity'))['s']
        return res or 0

    @property
    def total_paid(self):
        res = self.payouts.aggregate(s=models.Sum('amount'))['s']
        return res or Decimal('0.00')

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
    payout_type = models.CharField(max_length=20, choices=PayoutType.choices, default=PayoutType.ADVANCE, db_index=True, verbose_name="To'lov turi")
    payout_date = models.DateField(db_index=True, verbose_name="To'lov sanasi")
    note = models.CharField(max_length=255, blank=True, verbose_name="Izoh")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="To'lovchi")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Xodimga to'lov / Avans"
        verbose_name_plural = "Xodimlar to'lovlari va avanslari"
        ordering = ['-payout_date', '-created_at']
        indexes = [
            models.Index(fields=['worker', 'payout_date'], name='payout_worker_date_idx'),
            models.Index(fields=['payout_type', 'payout_date'], name='payout_type_date_idx'),
        ]

    def __str__(self):
        return f"{self.worker.worker_id} - {int(self.amount):,} UZS ({self.get_payout_type_display()})"


class DailyWorkerClosing(models.Model):
    """
    Har kuni kechqurun soat 12:00 (00:00) da xodimning kunlik ishlab topgan summasini
    va tikkan donalarini doimiy reestr sifatida muhrlab boruvchi model.
    """
    worker = models.ForeignKey(
        Worker, 
        on_delete=models.CASCADE, 
        related_name='daily_closings', 
        verbose_name="Tikuvchi"
    )
    date = models.DateField(db_index=True, verbose_name="Hisob sanasi")
    total_units = models.PositiveIntegerField(default=0, verbose_name="Tikilgan donalar")
    total_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'), 
        verbose_name="Kunlik ish haqi (UZS)"
    )
    ticket_count = models.PositiveIntegerField(default=0, verbose_name="Biletlar soni")
    closed_at = models.DateTimeField(auto_now_add=True, verbose_name="Yopilgan vaqti")

    class Meta:
        verbose_name = "Kunlik Ish Haqi Yopilishi"
        verbose_name_plural = "Kunlik Ish Haqi Yopilishlari"
        unique_together = ('worker', 'date')
        ordering = ['-date', 'worker']
        indexes = [
            models.Index(fields=['date', 'worker'], name='daily_date_worker_idx'),
        ]

    def __str__(self):
        return f"{self.date} | {self.worker.worker_id} - {self.total_amount:,.0f} UZS ({self.total_units} dona)"


class MonthlyClosing(models.Model):
    """
    Oylik ish haqi hisobini yopish, xodimlarga oylik berilganini qayd etish
    va 7 kunlik taymer orqali stikerlarni avtomatik muzlatish (freeze) modeli.
    """
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Ochiq (Aktiv / Qayta hisoblash mumkin)'
        PENDING_FREEZE = 'PENDING_FREEZE', 'Oylik berilgan (7 kunlik taymerda)'
        FROZEN = 'FROZEN', 'Muzlatilgan (Qulflangan / Arxiv)'

    year = models.PositiveIntegerField(verbose_name="Yil")
    month = models.PositiveIntegerField(verbose_name="Oy")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name="Holati"
    )
    payout_marked_at = models.DateTimeField(null=True, blank=True, verbose_name="Oylik to'langan vaqt")
    payout_marked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='monthly_closings_marked',
        verbose_name="Oylik to'lagan mas'ul"
    )
    freeze_deadline = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name="Muzlash muddati (Taymer tugashi)")
    frozen_at = models.DateTimeField(null=True, blank=True, verbose_name="Muzlatilgan vaqt")
    total_workers_count = models.PositiveIntegerField(default=0, verbose_name="Xodimlar soni")
    total_units = models.PositiveIntegerField(default=0, verbose_name="Jami donalar")
    total_gross_amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'), verbose_name="Jami hisoblangan ish haqi")
    total_advances = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'), verbose_name="Jami avanslar")
    total_paid_salary = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0.00'), verbose_name="Jami to'langan oylik")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Oylik Yopish Reestri"
        verbose_name_plural = "Oylik Yopish Reestrlari"
        unique_together = ('year', 'month')
        ordering = ['-year', '-month']

    @property
    def is_timer_active(self):
        if self.status == self.Status.PENDING_FREEZE and self.freeze_deadline:
            return timezone.now() < self.freeze_deadline
        return False

    @property
    def time_remaining_display(self):
        if not self.is_timer_active:
            return ""
        diff = self.freeze_deadline - timezone.now()
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if days > 0:
            return f"{days} kun, {hours} soat qoldi"
        return f"{hours} soat, {minutes} daqiqa qoldi"

    def __str__(self):
        return f"{self.year}-{self.month:02d} | {self.get_status_display()}"



