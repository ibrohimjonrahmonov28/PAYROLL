from django.db import migrations, models
from django.utils import timezone


def enable_new_meto_flow_for_all_orders(apps, schema_editor):
    """Barcha mavjud zakazlarni yangi tayyorlov oqimiga o'tkazish (egasi talabi 2026-11-25).
    Eski oqimda boshlangan/tasdiqlangan pastallar _old_meto_untouched_q() orqali eski oqimda qoladi."""
    Order = apps.get_model('production', 'Order')
    now = timezone.now()
    Order.objects.filter(meto_beta=False, meto_beta_since__isnull=True).update(
        meto_beta=True,
        meto_beta_since=now,
    )
    Order.objects.filter(meto_beta=False).update(meto_beta=True)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0056_alter_shipment_status_alter_shipmentpastal_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='meto_beta',
            field=models.BooleanField(db_index=True, default=True, verbose_name='Yangi tayyorlov oqimi'),
        ),
        migrations.AlterField(
            model_name='order',
            name='meto_beta_since',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Yangi tayyorlov oqimi yoqilgan vaqt'),
        ),
        migrations.RunPython(enable_new_meto_flow_for_all_orders, migrations.RunPython.noop),
    ]
