# Generated manually for CancelledBatchLog

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0027_defectreason'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CancelledBatchLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.PositiveIntegerField(db_index=True, verbose_name='Kesim Partiya ID')),
                ('batch_number', models.PositiveIntegerField(blank=True, null=True, verbose_name='Kesim Raqami')),
                ('pastal_code', models.CharField(blank=True, max_length=100, verbose_name='Pastal Kodi')),
                ('order_number', models.CharField(blank=True, max_length=100, verbose_name='Zakaz Raqami')),
                ('article_code', models.CharField(blank=True, max_length=100, verbose_name='Artikul')),
                ('cancelled_boxes_count', models.PositiveIntegerField(default=0, verbose_name='Bekor qilingan qutilar soni')),
                ('cancelled_tickets_count', models.PositiveIntegerField(default=0, verbose_name='Bekor qilingan stikerlar soni')),
                ('reason', models.TextField(blank=True, verbose_name='Bekor qilish sababi')),
                ('cancelled_at', models.DateTimeField(auto_now_add=True, verbose_name='Bekor qilingan vaqt')),
                ('cancelled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_batches', to=settings.AUTH_USER_MODEL, verbose_name='Bekor qiluvchi')),
            ],
            options={
                'verbose_name': 'Bekor Qilingan Pastal Jurnali',
                'verbose_name_plural': 'Bekor Qilingan Pastallar Jurnali',
                'ordering': ['-cancelled_at'],
            },
        ),
    ]
