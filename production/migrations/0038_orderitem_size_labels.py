from django.db import migrations, models


def copy_order_sizes(apps, schema_editor):
    """Managerlar NEW: zakazdagi razmerlarni har artikulga ko'chiradi (artikulda bor, ro'yxatda yo'q razmer oxiriga)."""
    OrderItem = apps.get_model('production', 'OrderItem')
    for item in OrderItem.objects.filter(order__is_new_flow=True).select_related('order'):
        sizes = [s.strip() for s in item.order.size_labels.split(',') if s.strip()]
        for name in item.sizes.order_by('id').values_list('size_name', flat=True):
            if name not in sizes:
                sizes.append(name)
        item.size_labels = ', '.join(sizes)[:300]
        item.save(update_fields=['size_labels'])


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0037_managers_new'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='size_labels',
            field=models.CharField(blank=True, max_length=300, verbose_name='Razmerlar (vergul bilan)'),
        ),
        migrations.RunPython(copy_order_sizes, migrations.RunPython.noop),
    ]
