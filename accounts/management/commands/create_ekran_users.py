from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Ekran 1 dan 40 gacha bo'lgan Ekran/Monitor (SCREEN) hisoblarini yaratadi yoki parolini yangilaydi."

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=40,
            help="Yaratilishi kerak bo'lgan ekranlar soni (standart: 40)",
        )
        parser.add_argument(
            '--password',
            type=str,
            default="111",
            help="Barcha ekran hisoblari uchun parol (standart: '111')",
        )
        parser.add_argument(
            '--reset-passwords',
            action='store_true',
            default=True,
            help="Mavjud foydalanuvchilar parolini ham yangilash (standart: True)",
        )

    def handle(self, *args, **options):
        count = options['count']
        password = options['password']
        reset_passwords = options['reset_passwords']

        created_count = 0
        updated_count = 0

        from production.services import get_screen_login, get_patok_code

        self.stdout.write(self.style.NOTICE(f"1 dan {count} gacha bo'lgan Sex Ekran / Monitor (SCREEN) hisoblarini yaratish boshlandi..."))

        for i in range(1, count + 1):
            new_username = get_screen_login(i)
            p_code = get_patok_code(i)
            first_name = f"{p_code}-Ekran"
            last_name = "Monitor"

            # 1. Mavjud SCREEN foydalanuvchisini topish (yangi login, eski 'ekran{i}' yoki vaqtincha 'patokk{i}')
            old_username = f"ekran{i}"
            temp_username = f"patokk{i}" if i <= 13 else f"patoku{i - 13}"

            user = User.objects.filter(role=User.Role.SCREEN, username=new_username).first()
            if not user:
                user = User.objects.filter(role=User.Role.SCREEN, username=temp_username).first()
            if not user:
                user = User.objects.filter(role=User.Role.SCREEN, username=old_username).first()
            if user and user.username != new_username:
                prev_name = user.username
                user.username = new_username
                user.save(update_fields=['username'])
                self.stdout.write(self.style.WARNING(f"  ~ SCREEN login ko'chirildi: {prev_name} -> {new_username}"))

            if not user:
                user = User.objects.create(
                    username=new_username,
                    first_name=first_name,
                    last_name=last_name,
                    role=User.Role.SCREEN,
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
                if user.role != User.Role.SCREEN:
                    user.role = User.Role.SCREEN
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
                    self.stdout.write(self.style.WARNING(f"  ~ Yangilandi: {new_username} ({first_name}, Parol: {password})"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTayyor! Jami: {count} ta patok hisobi. Yaratildi: {created_count} ta, Yangilandi: {updated_count} ta. Parol: '{password}'"
            )
        )

