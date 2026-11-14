"""
Beta qadoqlash tartibi (egasi talabi 2026-11-12; ta'mir cheklari aylanma bo'yicha — 2026-11-14).

Qutining 1-sorti qismlarga bo'linadi, har biri o'z qog'ozi bilan:
- CONTROL qismi = OTK birinchi tekshiruvdagi 1-sort (qutidagi CONTROL stikeri, "CONTROL:<box_code>");
- TAMIRn qismi  = n-ta'mir chekidan tuzalib chiqqan 1-sort ("TAMIR1:<box_code>", "TAMIR2:<box_code>", ...).
OTK ta'mirga yuborsa TAMIR1 cheki chiqadi; ta'mirdan qaytganda ochiq chek yopiladi (tuzalganlar — shu chekning
1-sorti), yana ta'mirga ketganlari uchun keyingi chek (TAMIR2, ...) chiqadi. Qog'ozdagi son emas, bazadagi son olinadi;
bitta qog'ozni qayta urish hech narsa qo'shmaydi. Eski "TAMIR:<box_code>" cheki = 1-chek.
"""
import re

from django.db.models import Max, Sum
from django.utils import timezone

from production.models import Box, BoxQualityInspectionLog, PackingScan, RepairReceipt

CODE_RE = re.compile(r'^(CONTROL|TAMIR(\d*)|BOX|QUTI)\s*:\s*(.+)$', re.IGNORECASE)


def beta_group(box):
    """Quti beta shablon bo'yicha ishlaydimi: artikul shabloni beta va quti belgi yoqilgandan keyin yaratilgan.
    Ha bo'lsa — shu shablon (CONTROL stikeri "OTK Control/Taxlov" narxi va qiyinligi undan), aks holda None."""
    article = box.target_article
    group = article.operation_group if article else None
    if group and group.is_beta and group.beta_since and box.created_at and box.created_at >= group.beta_since:
        return group
    return None


def _details(repair_details):
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
    return details


def create_repair_receipt(box, quantity, repair_details, inspector, operator_name='', log=None):
    """Navbatdagi ta'mir cheki (TAMIR1, TAMIR2, ...); har dona uchun operatsiya va tikuvchi (OTK tanlagan) saqlanadi."""
    cycle = (box.repair_receipts.aggregate(m=Max('cycle'))['m'] or 0) + 1
    return RepairReceipt.objects.create(box=box, cycle=cycle, quantity=quantity, details=_details(repair_details),
                                        inspector=inspector, operator_name=(operator_name or '')[:150], created_log=log)


def close_repair_receipt(box, log, fixed_qty, re_repair_qty, repair_details, inspector, operator_name=''):
    """Ta'mirdan qaytish: ochiq chek yopiladi (tuzalganlar uning 1-sorti), yana ta'mirga ketganlari uchun keyingi chek.
    Keyingi chek (bo'lsa) qaytariladi."""
    receipt = box.repair_receipts.filter(returned_at__isnull=True).order_by('-cycle').first()
    if receipt is not None:
        receipt.first_sort_qty += fixed_qty          # eski chek (qutiga bitta) bir necha qaytishni yig'ishi mumkin
        receipt.returned_log, receipt.returned_at = log, timezone.now()
        receipt.save(update_fields=['first_sort_qty', 'returned_log', 'returned_at'])
    if re_repair_qty > 0:
        return create_repair_receipt(box, re_repair_qty, repair_details, inspector, operator_name, log)
    return None


def parse_packing_code(raw):
    """
    'CONTROL:A10-394' -> (CONTROL, 'A10-394', None); 'TAMIR2:A10-394' -> (TAMIR, 'A10-394', 2);
    eski 'TAMIR:A10-394' -> (TAMIR, ..., 1). Skaner '+' xatosi ham. Qo'lda yozilgan quti kodi ('A10-394', 'BOX:'/'QUTI:'
    bilan ham) = CONTROL stikeri. Tikuvchi stikeri (TICKET:, TK-) yoki bo'sh -> (None, None, None).
    """
    text = (raw or '').strip()
    if '+' in text:
        text = re.sub(r'^(CONTROL|TAMIR\d*|BOX|QUTI)\+', r'\1:', text, flags=re.IGNORECASE).replace('+', '-')
    text = re.sub(r'^(TAMIR\d*)-', r'\1:', text, flags=re.IGNORECASE)   # "+" ni "-" ga aylantirgan sahifadan kelsa
    if not text or re.match(r'^(TICKET:|TK-)', text, flags=re.IGNORECASE):
        return None, None, None
    match = CODE_RE.match(text)
    if not match:
        return PackingScan.Source.CONTROL, text, None
    if match.group(1).upper().startswith('TAMIR'):
        return PackingScan.Source.TAMIR, match.group(3).strip(), int(match.group(2) or 1)
    return PackingScan.Source.CONTROL, match.group(3).strip(), None


def find_receipt(box, cycle):
    return box.repair_receipts.filter(cycle=cycle).first()


def portion_total(box, source, receipt=None):
    """Qism bo'yicha jami 1-sort: CONTROL — birlamchi tekshiruv, TAMIRn — shu chekdan tuzalib chiqqanlar."""
    if source == PackingScan.Source.TAMIR:
        return receipt.first_sort_qty if receipt else 0
    return (box.quality_inspection_logs.filter(action_type=BoxQualityInspectionLog.ActionType.INITIAL)
            .aggregate(s=Sum('first_sort_qty'))['s'] or 0)


def packed_total(box, source, receipt=None):
    scans = box.packing_scans.filter(source=source, is_cancelled=False)
    if source == PackingScan.Source.TAMIR:
        scans = scans.filter(repair_receipt=receipt)
    return scans.aggregate(s=Sum('quantity'))['s'] or 0


def available_for_packing(box, source, receipt=None):
    return max(0, portion_total(box, source, receipt) - packed_total(box, source, receipt))


def find_box(box_code):
    return Box.objects.select_related('order', 'article').filter(box_code__iexact=box_code).first()
