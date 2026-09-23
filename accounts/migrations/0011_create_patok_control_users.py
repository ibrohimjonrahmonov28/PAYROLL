import io
import secrets
from django.db import migrations
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile


def create_patok_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    existing_uids = set(User.objects.values_list('uid', flat=True))

    try:
        import qrcode
    except ImportError:
        qrcode = None

    for i in range(1, 41):
        username = f"patok{i}"
        user = User.objects.filter(username=username).first()

        if not user:
            while True:
                uid_candidate = str(secrets.randbelow(900000) + 100000)
                if uid_candidate not in existing_uids:
                    existing_uids.add(uid_candidate)
                    break

            user = User.objects.create(
                username=username,
                first_name=f"Patok {i}",
                last_name="Kontrolchi",
                role="CONTROL",
                uid=uid_candidate,
                password=make_password(username),
                is_active=True,
                is_staff=False,
                is_superuser=False,
            )

            if qrcode:
                try:
                    qr = qrcode.QRCode(
                        version=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_H,
                        box_size=10,
                        border=2,
                    )
                    qr.add_data(f"USER:{user.uid}")
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    filename = f"user_qr_{user.uid}.png"
                    user.qr_code.save(filename, ContentFile(buffer.getvalue()), save=True)
                except Exception:
                    pass
        else:
            updated_fields = []
            if user.role != "CONTROL":
                user.role = "CONTROL"
                updated_fields.append("role")
            if not user.uid:
                while True:
                    uid_candidate = str(secrets.randbelow(900000) + 100000)
                    if uid_candidate not in existing_uids:
                        existing_uids.add(uid_candidate)
                        break
                user.uid = uid_candidate
                updated_fields.append("uid")
            if updated_fields:
                user.save(update_fields=updated_fields)


def remove_patok_users(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(create_patok_users, reverse_code=remove_patok_users),
    ]

