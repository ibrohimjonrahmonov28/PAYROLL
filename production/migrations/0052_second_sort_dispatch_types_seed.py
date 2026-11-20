from django.db import migrations


def create_default_dispatch_types(apps, schema_editor):
    """Boshlang'ich 3 ta 2-sort jo'natish turi: Sotuvga, Zakaz uchun, Qayta omborga."""
    DispatchType = apps.get_model('production', 'SecondSortDispatchType')
    defaults = [
        {'name': 'Sotuvga', 'color': 'emerald', 'sort_order': 1, 'description': "2-sort savdoga chiqarildi"},
        {'name': 'Zakaz uchun', 'color': 'cyan', 'sort_order': 2, 'description': "Buyurtma kamomadi yoki zakazga berildi"},
        {'name': 'Qayta omborga', 'color': 'amber', 'sort_order': 3, 'description': "Ombor / qayta ishlashga topshirildi"},
    ]
    for item in defaults:
        DispatchType.objects.get_or_create(name=item['name'], defaults=item)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0051_second_sort_dispatch'),
    ]

    operations = [
        migrations.RunPython(create_default_dispatch_types, migrations.RunPython.noop),
    ]

