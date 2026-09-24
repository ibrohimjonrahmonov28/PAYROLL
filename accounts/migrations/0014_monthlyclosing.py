import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_create_ekran_users'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthlyClosing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveIntegerField(verbose_name='Yil')),
                ('month', models.PositiveIntegerField(verbose_name='Oy')),
                ('status', models.CharField(choices=[('OPEN', 'Ochiq (Aktiv / Qayta hisoblash mumkin)'), ('PENDING_FREEZE', 'Oylik berilgan (7 kunlik taymerda)'), ('FROZEN', 'Muzlatilgan (Qulflangan / Arxiv)')], db_index=True, default='OPEN', max_length=20, verbose_name='Holati')),
                ('payout_marked_at', models.DateTimeField(blank=True, null=True, verbose_name="Oylik to'langan vaqt")),
                ('freeze_deadline', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='Muzlash muddati (Taymer tugashi)')),
                ('frozen_at', models.DateTimeField(blank=True, null=True, verbose_name='Muzlatilgan vaqt')),
                ('total_workers_count', models.PositiveIntegerField(default=0, verbose_name='Xodimlar soni')),
                ('total_units', models.PositiveIntegerField(default=0, verbose_name='Jami donalar')),
                ('total_gross_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=16, verbose_name='Jami hisoblangan ish haqi')),
                ('total_advances', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=16, verbose_name='Jami avanslar')),
                ('total_paid_salary', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=16, verbose_name="Jami to'langan oylik")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('payout_marked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='monthly_closings_marked', to=settings.AUTH_USER_MODEL, verbose_name="Oylik to'lagan mas'ul")),
            ],
            options={
                'verbose_name': 'Oylik Yopish Reestri',
                'verbose_name_plural': 'Oylik Yopish Reestrlari',
                'ordering': ['-year', '-month'],
                'unique_together': {('year', 'month')},
            },
        ),
    ]
