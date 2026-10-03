"""
Management Command: python manage.py sync_article_operations

Artikul operatsiyalarini o'zining shablon guruhi (Narxlar matritsasi) bilan 1-ga-1 sinxronlash:
1. Guruhdan o'chirilgan eski operatsiyalarni (va ularning skanerlanmagan biletlarini) tozalash.
2. Yangi operatsiyalarni to'g'ri tartib raqami (sequence) va narxlari bilan tiklash.
3. Hali bironta ham stikeri urilmagan qutilarning barcha biletlarini yangi operatsiyalar ro'yxatiga 1-ga-1 moslab qayta generatsiya qilish.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from production.models import Article, OperationGroup, Order, Box, Ticket, ArticleOperation
from production.services import generate_box_tickets


class Command(BaseCommand):
    help = "Artikul va quti biletlarini Narxlar matritsasidagi oxirgi holat bilan 1-ga-1 to'liq sinxronlash"

    def add_arguments(self, parser):
        parser.add_argument(
            '--article',
            type=str,
            default='TERJAR93',
            help="Sinxronlanishi kerak bo'lgan artikul kodi (standart: TERJAR93)"
        )
        parser.add_argument(
            '--order',
            type=str,
            default='93',
            help="Zakaz raqami (standart: 93)"
        )

    def handle(self, *args, **options):
        art_code = options.get('article')
        order_num = options.get('order')

        self.stdout.write(self.style.NOTICE(f"🔍 Artikul: {art_code}, Zakaz: {order_num} qidirilmoqda..."))

        article = Article.objects.filter(code__iexact=art_code).first()
        if not article:
            self.stderr.write(self.style.ERROR(f"❌ '{art_code}' kodli artikul topilmadi!"))
            return

        group = article.operation_group
        if not group:
            self.stderr.write(self.style.ERROR(f"❌ '{article.code}' artikuliga hech qanday Narxlar Matritsasi guruhi biriktirilmagan!"))
            return

        self.stdout.write(self.style.SUCCESS(f"📋 Artikul: {article.code} ({article.name})"))
        self.stdout.write(self.style.SUCCESS(f"📁 Biriktirilgan guruh: {group.name} (ID: {group.id})"))

        with transaction.atomic():
            # 1. Guruh elementlarini tartib bilan qayta nomerlash (1 dan N gacha toza tartib)
            group_items = list(group.items.select_related('operation').all().order_by('sequence', 'id'))
            for idx, item in enumerate(group_items, start=1):
                if item.sequence != idx:
                    item.sequence = idx
                    item.save(update_fields=['sequence'])
            
            # 2. Artikul operatsiyalarini guruh bilan to'liq sinxronlash
            article.sync_operations_from_group(sync_unscanned_boxes=False)

            # 3. Natijaviy toza operatsiyalar
            clean_ops = list(article.article_operations.select_related('operation').all().order_by('sequence', 'id'))
            self.stdout.write(self.style.SUCCESS(f"\n✅ Artikulda {len(clean_ops)} ta to'g'ri operatsiya o'rnatildi:"))
            total_rate = 0
            for ao in clean_ops:
                total_rate += ao.price_per_unit
                self.stdout.write(f"   {ao.sequence:2d}. {ao.operation.name:<35} | {ao.price_per_unit:>7.2f} UZS")
            self.stdout.write(self.style.SUCCESS(f"💰 1 dona mahsulotning umumiy tikuv narxi: {total_rate:,.2f} UZS\n"))

            # 4. Zakaz bo'yicha qutilar biletlarini yangilash
            boxes_qs = Box.objects.filter(article=article).exclude(status=Box.Status.CANCELLED)
            if order_num:
                boxes_qs = boxes_qs.filter(order__order_number__icontains=order_num)

            active_boxes = list(boxes_qs)
            self.stdout.write(f"📦 Biletlari yangilanadigan aktiv qutilar soni: {len(active_boxes)} ta")

            updated_boxes = 0
            for box in active_boxes:
                # Agar biron bilet skanerlanmagan bo'lsa, qutini noldan yangi operatsiyalar bilan generatsiya qilamiz
                scanned_count = box.tickets.filter(status=Ticket.Status.SCANNED).count()
                if scanned_count == 0:
                    generate_box_tickets(box)
                    updated_boxes += 1
                else:
                    self.stdout.write(self.style.WARNING(f"⚠️ Quti #{box.box_number} da {scanned_count} ta skanerlangan bilet bor — tegilmadi!"))

            self.stdout.write(self.style.SUCCESS(
                f"\n🎉 MUVAFFAQIShIYATLI YAKUNLANDI!\n"
                f"• {updated_boxes} ta qutining stikerlari (biletlari) oxirgi Narxlar Matritsasiga 1-ga-1 moslandi.\n"
                f"• Har bir qutida aniq {len(clean_ops)} tadan yangi stiker yaratildi.\n"
                f"• Miya markazi va chop etishda eski aralashgan operatsiyalar to'liq yo'qoldi."
            ))
