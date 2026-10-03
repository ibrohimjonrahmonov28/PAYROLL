import os
import re
import subprocess
from django.core.management.base import BaseCommand
from django.db import transaction
from production.models import Box, Ticket, ArticleOperation, Order


class Command(BaseCommand):
    help = (
        "Zaxira nusxasi (pg_dump .dump yoki .sql) orqali biletlarning asl "
        "ticket_code va stiker_code larini tiklash. Narxlar yangilanganda qayta "
        "generatsiya bo'lib qolgan, ammo qog'ozda allaqachon chop etilgan stikerlarni "
        "qaytadan chop etmasdan (qog'oz va vaqt sarflamasdan) skanerda tanitish imkonini beradi."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dump-file',
            type=str,
            default='backup_latest.dump',
            help="Postgres zaxira fayli yo'li (.dump yoki .sql). Standart: backup_latest.dump"
        )
        parser.add_argument(
            '--order-id',
            type=int,
            default=None,
            help="Faqat ko'rsatilgan zakaz ID bo'yicha tiklash (masalan --order-id=18)"
        )
        parser.add_argument(
            '--order-number',
            type=str,
            default=None,
            help="Faqat ko'rsatilgan zakaz raqami bo'yicha tiklash (masalan --order-number=93)"
        )
        parser.add_argument(
            '--box-id',
            type=int,
            default=None,
            help="Faqat ko'rsatilgan quti ID bo'yicha tiklash"
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="O'zgarishlarni bazaga yozmasdan faqat tekshirish va hisobot ko'rsatish"
        )
        parser.add_argument(
            '-y', '--yes',
            action='store_true',
            help="Tasdiqlash so'rovisiz darhol bajarish"
        )

    def handle(self, *args, **options):
        dump_file = options['dump_file']
        order_id = options.get('order_id')
        order_number = options.get('order_number')
        box_id = options.get('box_id')
        dry_run = options['dry_run']
        auto_yes = options['yes']

        if not os.path.exists(dump_file):
            self.stderr.write(self.style.ERROR(f"❌ Zaxira fayli topilmadi: {dump_file}"))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"🔍 Zaxira fayli o'qilmoqda: {dump_file}"))

        # pg_restore yoki to'g'ridan-to'g'ri matn sifatida o'qish
        tickets_data = []
        is_custom_dump = False
        try:
            with open(dump_file, 'rb') as f:
                header = f.read(5)
                if header == b'PGDMP':
                    is_custom_dump = True
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Faylni o'qishda xatolik: {e}"))
            return

        if is_custom_dump:
            cmd = f"pg_restore -f - -a -t production_ticket {dump_file}"
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout_data, stderr_data = proc.communicate()
            if proc.returncode != 0 and not stdout_data:
                self.stderr.write(self.style.ERROR(f"pg_restore xatosi: {stderr_data}"))
                return
            lines = stdout_data.splitlines()
        else:
            with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

        self.stdout.write(f"📄 Qatorlar soni: {len(lines)}")

        # production_ticket COPY blokini tahlil qilish
        # Ustunlar:
        # id, ticket_code, quantity, split_index, total_splits, price_per_unit, total_amount, status,
        # screen_number, scanned_at, qr_code_image, article_operation_id, box_id, scanned_by_id, worker_id, stiker_code...
        in_copy = False
        for line in lines:
            line_str = line.strip()
            if line_str.startswith('COPY public.production_ticket') or line_str.startswith('COPY production_ticket'):
                in_copy = True
                continue
            if in_copy:
                if line_str == '\\.' or line_str.startswith('--'):
                    in_copy = False
                    continue
                parts = line.split('\t')
                if len(parts) >= 16:
                    try:
                        t_id = int(parts[0])
                        t_code = parts[1].strip()
                        s_idx = int(parts[3])
                        status = parts[7].strip()
                        ao_id = int(parts[11]) if parts[11] != '\\N' else None
                        b_id = int(parts[12]) if parts[12] != '\\N' else None
                        stiker_code = parts[15].strip() if parts[15] != '\\N' else None
                        if stiker_code == '':
                            stiker_code = None

                        if b_id and ao_id and t_code:
                            tickets_data.append({
                                'dump_id': t_id,
                                'ticket_code': t_code,
                                'split_index': s_idx,
                                'status': status,
                                'article_operation_id': ao_id,
                                'box_id': b_id,
                                'stiker_code': stiker_code
                            })
                    except (ValueError, IndexError):
                        continue

        self.stdout.write(self.style.SUCCESS(f"✅ Zaxiradan {len(tickets_data)} ta bilet yozuvi ajratib olindi."))

        if not tickets_data:
            self.stdout.write(self.style.WARNING("Zaxirada bilet ma'lumotlari topilmadi."))
            return

        # Filtrlash
        target_box_ids = set()
        boxes_qs = Box.objects.all()
        if order_id:
            boxes_qs = boxes_qs.filter(order_id=order_id)
        if order_number:
            boxes_qs = boxes_qs.filter(order__order_number=str(order_number))
        if box_id:
            boxes_qs = boxes_qs.filter(id=box_id)

        target_box_ids = set(boxes_qs.values_list('id', flat=True))
        self.stdout.write(f"🎯 Bazadagi maqsadli qutilar: {len(target_box_ids)} ta")

        filtered_data = [
            t for t in tickets_data
            if t['box_id'] in target_box_ids
        ]
        self.stdout.write(f"🔎 Maqsadli qutilarga tegishli zaxira biletlari: {len(filtered_data)} ta")

        if not filtered_data:
            self.stdout.write(self.style.WARNING("Belgilangan filtrlar bo'yicha mos biletlar topilmadi."))
            return

        # Hozirgi biletlarni topish va moslashtirish
        matched_updates = []
        skipped_already_correct = 0
        skipped_not_found = 0
        skipped_scanned = 0

        # Tezkor qidiruv uchun mavjud biletlarni indekslash
        current_tickets = Ticket.objects.filter(box_id__in=target_box_ids).select_related('box')
        current_by_box_ao_split = {}
        for t in current_tickets:
            key = (t.box_id, t.article_operation_id, t.split_index)
            current_by_box_ao_split[key] = t

        for item in filtered_data:
            key = (item['box_id'], item['article_operation_id'], item['split_index'])
            curr_ticket = current_by_box_ao_split.get(key)

            if not curr_ticket:
                skipped_not_found += 1
                continue

            # Agar bilet allaqachon skanerlangan bo'lsa va kod boshqa bo'lsa, xavfsizlik uchun tegilmaydi
            if curr_ticket.status == Ticket.Status.SCANNED:
                if curr_ticket.ticket_code == item['ticket_code'] or curr_ticket.stiker_code == item['stiker_code']:
                    skipped_already_correct += 1
                else:
                    skipped_scanned += 1
                continue

            # Kodlar bir xilmi?
            if curr_ticket.ticket_code == item['ticket_code'] and curr_ticket.stiker_code == item['stiker_code']:
                skipped_already_correct += 1
                continue

            matched_updates.append((curr_ticket, item['ticket_code'], item['stiker_code']))

        self.stdout.write(f"\n📊 TAHLIL NATIJALARI:")
        self.stdout.write(f"   • Tiklanishi kerak bo'lgan biletlar: {len(matched_updates)} ta")
        self.stdout.write(f"   • Allaqachon to'g'ri bo'lgan biletlar: {skipped_already_correct} ta")
        self.stdout.write(f"   • Topilmagan biletlar (yangilangan operatsiyalar): {skipped_not_found} ta")
        if skipped_scanned > 0:
            self.stdout.write(f"   • Skanerlangan biletlar (daxlsiz qoldirildi): {skipped_scanned} ta")

        if not matched_updates:
            self.stdout.write(self.style.SUCCESS("Barcha biletlarning kodlari allaqachon to'g'ri holatda. Yangilash shart emas."))
            return

        # Namunani ko'rsatish
        self.stdout.write("\n🔍 Namunaviy o'zgarishlar (dastlabki 5 tasi):")
        for curr, new_tk, new_st in matched_updates[:5]:
            self.stdout.write(
                f"   [Box #{curr.box.box_number} (ID: {curr.box_id})] "
                f"ticket_code: '{curr.ticket_code}' -> '{new_tk}' | "
                f"stiker_code: '{curr.stiker_code}' -> '{new_st}'"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️ DRY-RUN rejimida ishga tushirildi. Bazaga hech qanday o'zgarish kiritilmadi."))
            return

        if not auto_yes:
            confirm = input(f"\nUshbu {len(matched_updates)} ta biletning stiker va ticket kodlarini zaxiradagi holatiga qaytarishni tasdiqlaysizmi? (ha/yoq): ")
            if confirm.strip().lower() not in ('ha', 'yes', 'y'):
                self.stdout.write(self.style.WARNING("Bekor qilindi."))
                return

        # Bazani yangilash
        self.stdout.write("\n🔄 Biletlar yangilanmoqda...")
        updated_count = 0
        with transaction.atomic():
            for curr, new_tk, new_st in matched_updates:
                # Xavfsizlik: agar yangi stiker_code boshqa faol biletda band bo'lsa (ehtimoli 0 ga yaqin), o'tkazib yuborish
                if new_st and Ticket.objects.filter(stiker_code=new_st).exclude(id=curr.id).exists():
                    self.stdout.write(self.style.WARNING(f"   ⚠️ Diqqat: {new_st} kodi boshqa biletda band, o'tkazib yuborildi."))
                    continue
                curr.ticket_code = new_tk
                curr.stiker_code = new_st
                curr.save(update_fields=['ticket_code', 'stiker_code'])
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(f"\n🎉 MUVAFFAQIShIYATLI YAKUNLANDI!"))
        self.stdout.write(self.style.SUCCESS(f"   Jami {updated_count} ta biletning asl ticket_code va stiker_code lari tiklandi."))
        self.stdout.write(
            f"   Endi tikuvchilar qo'lidagi barcha chop etilgan qog'oz stikerlar "
            f"Zebra skanerida darhol taniydi va hisob-kitob eng oxirgi narx bo'yicha amalga oshiriladi!\n"
        )
