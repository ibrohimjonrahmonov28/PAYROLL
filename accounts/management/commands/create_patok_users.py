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
            '--reset-passwords',
            action='store_true',
            help="Mavjud foydalanuvchilar parolini ham patok{i} ga yangilash",
        )

    def handle(self, *args, **options):
        count = options['count']
        reset_passwords = options['reset_passwords']

        created_count = 0
        updated_count = 0

        self.stdout.write(self.style.NOTICE(f"1 dan {count} gacha bo'lgan Patok foydalanuvchilarini yaratish boshlandi..."))

        for i in range(1, count + 1):
            username = f"patok{i}"
            default_password = f"patok{i}"
            first_name = f"Patok {i}"
            last_name = "Kontrolchi"

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'role': User.Role.CONTROL,
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                }
            )

            if created:
                user.set_password(default_password)
                user.save()
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + Yaratildi: {username} (Parol: {default_password}, UID: {user.uid})"))
            else:
                updated = False
                if user.role != User.Role.CONTROL:
                    user.role = User.Role.CONTROL
                    updated = True
                if not user.first_name:
                    user.first_name = first_name
                    updated = True
                if not user.last_name:
                    user.last_name = last_name
                    updated = True
                if not user.is_active:
                    user.is_active = True
                    updated = True
                if reset_passwords:
                    user.set_password(default_password)
                    updated = True
                if updated:
                    user.save()
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f"  ~ Yangilandi: {username} (Rol: CONTROL)"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTayyor! Jami: {count} ta patok. Yaratildi: {created_count} ta, Yangilandi: {updated_count} ta."
            )
        )

