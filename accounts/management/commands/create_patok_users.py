from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Patok 1 dan N gacha bo'lgan Kontrolchi (CONTROL) foydalanuvchilarini yaratadi yoki yangilaydi."

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=40,
            help="Yaratilishi kerak bo'lgan patoklar soni (standart: 40)",
        )
        parser.add_argument(
            '--password',
            type=str,
            default="111",
            help="Barcha patok kontrolchi hisoblari uchun parol (standart: '111')",
        )
        parser.add_argument(
            '--reset-passwords',
            action='store_true',
            default=True,
            help="Mavjud foydalanuvchilar parolini ham yangilash (standart: True)",
        )

    def handle(self, *args, **options):
        count = options['count']
        password = options.get('password', '111')
        reset_passwords = options['reset_passwords']

        created_count = 0
        updated_count = 0

        from production.services import get_patok_login, get_patok_code

        self.stdout.write(self.style.NOTICE(f"1 dan {count} gacha bo'lgan Patok Kontrolchi (CONTROL) hisoblarini yaratish boshlandi..."))

        for i in range(1, count + 1):
            new_username = get_patok_login(i)
            p_code = get_patok_code(i)
            first_name = f"{p_code}-Patok"
            last_name = "Kontrolchi"

            # 1. Mavjud CONTROL foydalanuvchisini topish (yangi login yoki eski 'patok{i}')
            old_username = f"patok{i}"

            user = User.objects.filter(role=User.Role.CONTROL, username=new_username).first()
            if not user:
                user = User.objects.filter(role=User.Role.CONTROL, username=old_username).first()
            if user and user.username != new_username:
                prev_name = user.username
                user.username = new_username
                user.save(update_fields=['username'])
                self.stdout.write(self.style.WARNING(f"  ~ CONTROL login ko'chirildi: {prev_name} -> {new_username}"))

            if not user:
                user = User.objects.create(
                    username=new_username,
                    first_name=first_name,
                    last_name=last_name,
                    role=User.Role.CONTROL,
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                )
                user.set_password(password)
                user.save()
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + Yaratildi: {new_username} ({first_name}, Parol: {password}, UID: {user.uid})"))
            else:
                updated = False
                if user.role != User.Role.CONTROL:
                    user.role = User.Role.CONTROL
                    updated = True
                if user.first_name != first_name:
                    user.first_name = first_name
                    updated = True
                if user.last_name != last_name:
                    user.last_name = last_name
                    updated = True
                if not user.is_active:
                    user.is_active = True
                    updated = True
                if reset_passwords:
                    user.set_password(password)
                    updated = True
                if updated:
                    user.save()
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f"  ~ Yangilandi: {new_username} (Rol: CONTROL, Parol: {password})"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTayyor! Jami: {count} ta patok. Yaratildi: {created_count} ta, Yangilandi: {updated_count} ta."
            )
        )

