from django.db import migrations


def create_screens(apps, schema_editor):
    """Taxlovchilar uchun 3 ta TV (patoklar keyin superadmin sozlaydi)."""
    PackerScreen = apps.get_model('production', 'PackerScreen')
    for number in (1, 2, 3):
        PackerScreen.objects.get_or_create(number=number)


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0048_packer_screens'),
    ]

    operations = [
        migrations.RunPython(create_screens, migrations.RunPython.noop),
    ]
