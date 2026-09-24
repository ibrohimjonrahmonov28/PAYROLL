import io
import secrets
from django.db import migrations
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile


def create_ekran_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    existing_uids = set(User.objects.values_list('uid', flat=True))

    try:
        import qrcode
    except ImportError:
        qrcode = None

    for i in range(1, 41):
        username = f"ekran{i}"
        user = User.objects.filter(username=username).first()

        if not user:
            while True:
                uid_candidate = str(secrets.randbelow(900000) + 100000)
                if uid_candidate not in existing_uids:
                    existing_uids.add(uid_candidate)
                    break

            user = User.objects.create(
                username=username,
                first_name=f"{i}-Ekran",
                last_name="Monitor",
                role="SCREEN",
                uid=uid_candidate,
                password=make_password("111"),
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
            if user.role != "SCREEN":
                user.role = "SCREEN"
                updated_fields.append("role")
            user.password = make_password("111")
            updated_fields.append("password")
            if not user.uid:
                while True:
                    uid_candidate = str(secrets.randbelow(900000) + 100000)
                    if uid_candidate not in existing_uids:
                        existing_uids.add(uid_candidate)
                        break
                user.uid = uid_candidate
                updated_fields.append("uid")
            if not user.is_active:
                user.is_active = True
                updated_fields.append("is_active")
            if updated_fields:
                user.save(update_fields=updated_fields)


def remove_ekran_users(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(create_ekran_users, reverse_code=remove_ekran_users),
    ]

