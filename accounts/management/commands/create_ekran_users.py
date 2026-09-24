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

        self.stdout.write(self.style.NOTICE(f"1 dan {count} gacha bo'lgan Ekran (SCREEN) hisoblarini yaratish boshlandi..."))

        for i in range(1, count + 1):
            username = f"ekran{i}"
            first_name = f"{i}-Ekran"
            last_name = "Monitor"

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'role': User.Role.SCREEN,
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                }
            )

            if created:
                user.set_password(password)
                user.save()
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + Yaratildi: {username} (Parol: {password}, UID: {user.uid})"))
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
                    self.stdout.write(self.style.WARNING(f"  ~ Yangilandi: {username} (Rol: SCREEN, Parol: {password})"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTayyor! Jami: {count} ta ekran hisobi. Yaratildi: {created_count} ta, Yangilandi: {updated_count} ta. Parol: '{password}'"
            )
        )

