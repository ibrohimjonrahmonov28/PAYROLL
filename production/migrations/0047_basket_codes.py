import random

from django.db import migrations, models


def start_beta_orders_today(apps, schema_editor):
    """Hozir beta belgisi bor zakazlar "bugundan" boshlanadi: bundan oldin OTK dan o'tgan qutilari eski tartibda qoladi."""
    from django.utils import timezone
    apps.get_model('production', 'Order').objects.filter(packing_beta=True, packing_beta_since__isnull=True).update(
        packing_beta_since=timezone.now())


def fill_basket_codes(apps, schema_editor):
    """Avvalgi savatlarga ham "SV-123456" kodi (takrorlanmas). Avval SENT = "upakovka omborida" edi (master bosqichi
    yo'q edi) — balans o'zgarmasligi uchun ular qabul qilingan (ACCEPTED) deb belgilanadi."""
    SecondSortBatch = apps.get_model('production', 'SecondSortBatch')
    SecondSortBatch.objects.filter(status='SENT').update(status='ACCEPTED', accepted_at=models.F('sent_at'))
    used = set(SecondSortBatch.objects.exclude(code__isnull=True).values_list('code', flat=True))
    for basket in SecondSortBatch.objects.filter(code__isnull=True):
        code = f"SV-{random.randint(100000, 999999)}"
        while code in used:
            code = f"SV-{random.randint(100000, 999999)}"
        used.add(code)
        basket.code = code
        basket.save(update_fields=['code'])


class Migration(migrations.Migration):
    """Ma'lumot o'zgarishi alohida migratsiyada: 0046 dagi indekslar yaratilib bo'lgach (PostgreSQL "pending trigger
    events" xatosi bo'lmasligi uchun)."""

    dependencies = [
        ('production', '0046_basket_master'),
    ]

    operations = [
        migrations.RunPython(fill_basket_codes, migrations.RunPython.noop),
        migrations.RunPython(start_beta_orders_today, migrations.RunPython.noop),
    ]
