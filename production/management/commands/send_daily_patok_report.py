import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from production.daily_image_reports import (
    send_single_branch_report,
    send_daily_1800_reports,
    get_branch_daily_report_data,
    generate_branch_report_image
)


class Command(BaseCommand):
    help = "18:00 Filiallar kunlik patoklar hisobotini (PNG rasm formatida) Telegram guruhlarga yuborish"

    def add_arguments(self, parser):
        parser.add_argument(
            '--branch',
            type=str,
            choices=['HQ', 'UYCHI', 'ALL'],
            default='ALL',
            help="Qaysi filial hisoboti jo'natilsin: HQ (Bosh bino), UYCHI (Uychi), yoki ALL (Ikkala filial alohida guruhlarga)"
        )
        parser.add_argument(
            '--date',
            type=str,
            help="Hisobot sanasi (YYYY-MM-DD). Belgilanmasa, bugungi sana olinadi."
        )
        parser.add_argument(
            '--chat-id',
            type=str,
            help="Maxsus Telegram chat ID (sozlamalardagi guruh ID o'rniga)"
        )
        parser.add_argument(
            '--save-file',
            type=str,
            help="Rasmni fayl sifatida saqlash (masalan: /tmp/hisobot.png)"
        )

    def handle(self, *args, **options):
        branch = options.get('branch', 'ALL').upper()
        date_str = options.get('date')
        chat_id = options.get('chat_id')
        save_file = options.get('save_file')

        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Xato sana formati: {date_str}. YYYY-MM-DD formatida kiriting."))
                return
        else:
            target_date = timezone.localdate()

        self.stdout.write(self.style.NOTICE(f"📅 Hisobot sanasi: {target_date} (Filial: {branch})"))

        # Faylga saqlash rejimi (Telegramga yubormasdan test qilish uchun)
        if save_file:
            target_branch = 'HQ' if branch in ['HQ', 'ALL'] else 'UYCHI'
            self.stdout.write(f"Rasm faylga saqlanmoqda: {save_file} (Filial: {target_branch})...")
            data = get_branch_daily_report_data(target_branch, target_date)
            img_buf = generate_branch_report_image(data)
            with open(save_file, 'wb') as f:
                f.write(img_buf.getvalue())
            self.stdout.write(self.style.SUCCESS(f"✅ Rasm muvaffaqiyatli saqlandi: {save_file}"))
            return

        # Telegramga jo'natish
        if branch == 'ALL':
            self.stdout.write("🚀 Ikkala filial (Bosh bino va Uychi) uchun hisobotlar jo'natilmoqda...")
            res = send_daily_1800_reports(target_date)
            self.stdout.write(f"1. Bosh bino: {res.get('HQ')}")
            self.stdout.write(f"2. Uychi: {res.get('UYCHI')}")
        else:
            self.stdout.write(f"🚀 {branch} filiali hisoboti jo'natilmoqda...")
            res = send_single_branch_report(branch, target_date=target_date, chat_id=chat_id)
            if res.get('success'):
                self.stdout.write(self.style.SUCCESS(f"✅ {res.get('message')}"))
            else:
                self.stdout.write(self.style.ERROR(f"❌ {res.get('message')}"))
