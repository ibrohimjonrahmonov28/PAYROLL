"""
Beta qadoqlash tartibi (egasi talabi 2026-11-12, faqat Order.packing_beta zakazlarda).

Qutining 1-sorti ikki qismga bo'linadi, har biri o'z qog'ozi bilan:
- CONTROL qismi = OTK birinchi tekshiruvdagi 1-sort (CONTROL stikeri, "CONTROL:<box_code>"), keyin o'zgarmaydi;
- TAMIR qismi   = ta'mirdan qaytganda 1-sort bo'lganlar (ta'mir cheki, "TAMIR:<box_code>"), har qaytishda ko'payadi.
Taxlovchi qog'ozni urganda shu qismning hali qadoqlanmagani yoziladi: qaysi tartibda urilsa ham har biri o'z qismini
oladi, bitta qog'ozni qayta urish hech narsa qo'shmaydi. Qayta ta'mirda yangi chek chiqmaydi (o'sha chek qaytadi).
"""
import re

from django.db.models import Sum

from production.models import Box, BoxQualityInspectionLog, PackingScan, RepairReceipt

QR_PREFIXES = {'CONTROL': PackingScan.Source.CONTROL, 'TAMIR': PackingScan.Source.TAMIR}


def create_repair_receipt(box, quantity, repair_details, inspector, operator_name=''):
    """OTK ta'mirga yuborganda chek; har dona uchun operatsiya va tikuvchi (OTK tanlagan) saqlanadi."""
    details = []
    for index, item in enumerate(repair_details or [], start=1):
        if not isinstance(item, dict):
            continue
        details.append({
            'number': item.get('item_number', index),
            'operations': [str(x) for x in (item.get('operation_names') or [])],
            'workers': [str(x) for x in (item.get('workers') or [])],
            'notes': str(item.get('notes') or ''),
        })
    receipt, _ = RepairReceipt.objects.update_or_create(box=box, defaults={
        'quantity': quantity,
        'details': details,
        'inspector': inspector,
        'operator_name': operator_name[:150],
    })
    return receipt


def parse_packing_code(raw):
    """'CONTROL:A10-394' / 'TAMIR:A10-394' (skaner '+' xatosi ham) -> (manba, quti kodi); boshqasi -> (None, None)."""
    text = (raw or '').strip()
    if '+' in text:
        text = re.sub(r'^(CONTROL|TAMIR)\+', r'\1:', text, flags=re.IGNORECASE).replace('+', '-')
    match = re.match(r'^(CONTROL|TAMIR)\s*:\s*(.+)$', text, flags=re.IGNORECASE)
    if not match:
        return None, None
    return QR_PREFIXES[match.group(1).upper()], match.group(2).strip()


def portion_total(box, source):
    """Qism bo'yicha jami 1-sort: CONTROL — birlamchi tekshiruv, TAMIR — ta'mirdan qaytishlarda tuzalganlar."""
    action = (BoxQualityInspectionLog.ActionType.INITIAL if source == PackingScan.Source.CONTROL
              else BoxQualityInspectionLog.ActionType.REPAIR_RETURN)
    return box.quality_inspection_logs.filter(action_type=action).aggregate(s=Sum('first_sort_qty'))['s'] or 0


def packed_total(box, source):
    return box.packing_scans.filter(source=source, is_cancelled=False).aggregate(s=Sum('quantity'))['s'] or 0


def available_for_packing(box, source):
    return max(0, portion_total(box, source) - packed_total(box, source))


def find_box(box_code):
    return Box.objects.select_related('order', 'article').filter(box_code__iexact=box_code).first()
