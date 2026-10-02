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

        from production.services import get_patok_login, get_patok_name

        self.stdout.write(self.style.NOTICE(f"1 dan {count} gacha bo'lgan Patok/Ekran (SCREEN) hisoblarini yaratish boshlandi..."))

        for i in range(1, count + 1):
            new_username = get_patok_login(i)
            first_name = get_patok_name(i)
            last_name = "Monitor"

            # 1. Eski 'ekran{i}' mavjud bo'lsa, yangi loginga o'tkazish
            old_username = f"ekran{i}"
            user = User.objects.filter(username=new_username).first()
            if not user:
                user = User.objects.filter(username=old_username).first()
                if user:
                    user.username = new_username
                    user.save(update_fields=['username'])
                    self.stdout.write(self.style.WARNING(f"  ~ Eski login ko'chirildi: {old_username} -> {new_username}"))

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

