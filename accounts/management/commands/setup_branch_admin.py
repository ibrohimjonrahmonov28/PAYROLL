from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Filial admini (BRANCH_ADMIN) foydalanuvchisini yaratish yoki yangilash"

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default='uychi_admin', help='Admin logini (standart: uychi_admin)')
        parser.add_argument('--password', type=str, default='uychi2026', help='Admin paroli (standart: uychi2026)')
        parser.add_argument('--branch', type=str, default='UYCHI', choices=['HQ', 'UYCHI'], help='Filial (HQ yoki UYCHI)')
        parser.add_argument('--name', type=str, default='Uychi Admini', help='F.I.SH')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        branch = options['branch']
        name = options['name']

        user, created = User.objects.get_or_create(username=username)
        user.first_name = name
        user.role = User.Role.BRANCH_ADMIN
        user.branch = branch
        user.is_active = True
        user.is_staff = False
        user.set_password(password)
        user.save()

        action = "yaratildi" if created else "yangilandi"
        self.stdout.write(self.style.SUCCESS(
            f"Muvaffaqiyatli {action}: Login: {username} | Parol: {password} | Filial: {branch} ({user.get_branch_display()}) | Rol: {user.get_role_display()}"
        ))
