from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_monthlyclosing'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('SUPER_ADMIN', 'Super Admin'),
                    ('ADMIN', 'Admin'),
                    ('BRANCH_ADMIN', 'Filial Admini'),
                    ('MANAGER', 'Menejer'),
                    ('CUTTER', 'Kesimchi (Bichuv)'),
                    ('METO', 'Metochi (Nomerovka)'),
                    ('STICKER', 'Stiker Chiqaruvchi'),
                    ('MASTER', 'Master'),
                    ('CONTROL', 'Kontrolchi (Sifat Nazorati)'),
                    ('SCREEN', 'Ekran (Monitor)'),
                    ('USER', 'Oddiy User'),
                ],
                default='USER',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='branch',
            field=models.CharField(
                choices=[('HQ', 'Bosh bino (HQ)'), ('UYCHI', 'Uychi')],
                db_index=True,
                default='HQ',
                max_length=50,
                verbose_name='Ish joyi (Filial)',
            ),
        ),
        migrations.AddField(
            model_name='worker',
            name='branch',
            field=models.CharField(
                choices=[('HQ', 'Bosh bino (HQ)'), ('UYCHI', 'Uychi')],
                db_index=True,
                default='HQ',
                max_length=50,
                verbose_name='Ish joyi (Filial)',
            ),
        ),
    ]
