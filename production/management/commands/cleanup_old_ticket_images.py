import os
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from production.models import Ticket


class Command(BaseCommand):
    help = "Eski skanerlangan biletlarning diskdagi ortiqcha PNG rasmlarini tozalash (disk va inode larni bo'shatish)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help="Necha kundan oshgan skanerlangan biletlarning rasmlari tozalansin (standart: 30 kun)."
        )
        parser.add_argument(
            '--all-scanned',
            action='store_true',
            help="Barcha skanerlangan biletlarning diskdagi rasmlarini tozalash."
        )

    def handle(self, *args, **options):
        days = options['days']
        all_scanned = options['all_scanned']

        qs = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            qr_code_image__isnull=False
        ).exclude(qr_code_image='')

        if not all_scanned:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(scanned_at__lt=cutoff)

        total_found = qs.count()
        if total_found == 0:
            self.stdout.write(self.style.SUCCESS("Tozalash uchun eski disk fayllari topilmadi. Server toza!"))
            return

        cleaned_count = 0
        deleted_bytes = 0

        for ticket in qs.iterator(chunk_size=500):
            try:
                if ticket.qr_code_image and hasattr(ticket.qr_code_image, 'path') and os.path.exists(ticket.qr_code_image.path):
                    fsize = os.path.getsize(ticket.qr_code_image.path)
                    os.remove(ticket.qr_code_image.path)
                    deleted_bytes += fsize
                ticket.qr_code_image = None
                ticket.save(update_fields=['qr_code_image'])
                cleaned_count += 1
            except Exception as e:
                self.stderr.write(f"Xato (Ticket #{ticket.id}): {e}")

        mb_freed = deleted_bytes / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f"Muvaffaqiyatli yakunlandi! {cleaned_count} ta fayl tozalandi, {mb_freed:.2f} MB disk joyi bo'shatildi."
        ))

