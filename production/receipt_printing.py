"""
OTK ta'mir cheki (egasi talabi 2026-11-11): OTK ta'mir sonini saqlaganda shu patok OTK stolidagi printerdan chek chiqadi
— ta'mir soni, har tikuvchi (ism, 6 xonali UID) va u qilgan operatsiyalar soni, hamda shu ta'mir chekining QR kodi
(TAMIR1:<box_code>, qayta ta'mirda TAMIR2:... — quti kodi va stikerlar o'zgarmaydi, oltin qoida). Server chekni PrintJob navbatiga qo'yadi, fabrikadagi pechat
agenti (print_agent/terryjar_print_agent.py) uni printerga yuboradi. Chek chiqmasa ham OTK saqlanadi.

Dizayn (egasi tasdiqlagan, 2026-11-14):
  Quti: D11-356 (#1722)
  Pastal / Zakaz / Model (kod + nom) / Razmer
  1-ta'mir: 5 dona                       <- katta (qayta ta'mirda 2-ta'mir, ...)
  ------------------------------------------------
  Iroda Egamberdiyeva (447123):          <- qalin
    ETAK RESHMA 1 ta                     <- shu tikuvchining operatsiyalari ostma-ost
  ------------------------------------------------
  OTK: Muattar Abdumalikova
  K13 · 13.11.2026 16:48
          [QR]                           <- kattaroq; tagida quti raqami yo'q, darhol kesiladi
Hammasi printerning o'z matni va QR buyrug'i (~1 KB). Rasm (raster) XP-80TS'da USB orqali buzilib chiqqan — ishlatilmaydi.
Tepada/pastda bo'sh qator yo'q (qog'oz tejaladi).
"""
import base64
import logging
from collections import OrderedDict

from django.utils import timezone

from production.models import PrintJob, ReceiptPrinter, Ticket
from production.services import get_patok_code, parse_patok_number

logger = logging.getLogger(__name__)

ENCODING, CODEPAGE = 'cp866', 17   # kirill; Xprinter'da 17-kod sahifa (PC866)
COLS, WIDE_COLS = 48, 24           # A shrift va ikki baravar shrift, 80 mm
QR_MODULE = 10                     # ~26 mm (avvalgi chekdagidan kattaroq)
APOSTROPHES = str.maketrans({'ʻ': "'", 'ʼ': "'", '‘': "'", '’': "'", '`': "'", '«': '"', '»': '"', '—': '-', '–': '-'})

# Boshida bo'sh baytlar: XP-80TS USB'da ba'zan ishning birinchi baytlarini yo'qotadi (sarlavha tushib qolgan,
# avvalgi chekning tekislashi qolgan) — NUL'larni printer e'tiborsiz qoldiradi, ESC @ holatni tiklaydi,
# FS . zavoddagi xitoy rejimini o'chiradi (aks holda kirill "xitoycha" chiqadi).
START = b'\x00' * 64 + b'\x1b@\x1b@\x1c.\x1bt' + bytes([CODEPAGE])
LEFT, CENTER = b'\x1ba\x00', b'\x1ba\x01'
BOLD, NORMAL = b'\x1bE\x01', b'\x1bE\x00'
BIG, SMALL = b'\x1d!\x11', b'\x1d!\x00'
CUT = b'\x1dVB\x00'                # kesish joyigacha surib kesadi (ortiqcha bo'sh qatorsiz)


def _text(text):
    return str(text).translate(APOSTROPHES).encode(ENCODING, errors='replace')


def _line(text, width=COLS):
    text = str(text)
    return _text(text if len(text) <= width else text[:width - 2] + '..') + b'\n'


def _qr(content):
    payload = content.encode('ascii', errors='replace')
    store = len(payload) + 3
    return (b'\x1d(k\x04\x001A2\x00'                                  # model 2
            + b'\x1d(k\x03\x001C' + bytes([QR_MODULE])                  # modul o'lchami
            + b'\x1d(k\x03\x001E1'                                      # xatoni tuzatish M
            + b'\x1d(k' + bytes([store % 256, store // 256]) + b'1P0' + payload
            + b'\x1d(k\x03\x001Q0')                                     # chop etish


def _render(data):
    out = bytearray(START + LEFT + SMALL + NORMAL)
    for line in data['info']:
        out += _line(line)
    out += BOLD + BIG + _line(data['qty_line'], WIDE_COLS) + SMALL + NORMAL
    out += _line('-' * COLS)
    for (name, uid), ops in data['workers']:
        out += BOLD + _line(f"{name} ({uid}):" if uid else f"{name}:") + NORMAL
        for op, n in ops:
            out += _line(f"  {op} {n} ta")
    for note in data['notes']:
        out += _line(f"Izoh: {note}")
    out += _line('-' * COLS)
    if data['operator']:
        out += _line(f"OTK: {data['operator']}")
    out += _line(data['footer'])
    out += CENTER + _qr(data['qr'])
    return bytes(out + CUT)


def _worker_uid(worker):
    return worker.user.uid if worker.user_id and worker.user.uid else (worker.worker_id or '')


def _repair_workers(box, repair_details):
    """[((ism, UID), [(operatsiya, soni), ...]), ...] — tikuvchi bo'yicha guruhlangan. Stiker ID'lari bo'yicha bazadan
    (planshet matniga ishonmaydi); stiker topilmasa planshet yuborgan nomlar."""
    ticket_ids = {int(i) for item in repair_details for i in (item.get('operation_ids') or []) if str(i).isdigit()}
    tickets = {t.id: t for t in Ticket.objects.filter(box=box, id__in=ticket_ids).select_related(
        'article_operation__operation', 'worker__user')}
    workers, notes = OrderedDict(), []
    for item in repair_details:
        pairs = []
        for tid in item.get('operation_ids') or []:
            t = tickets.get(int(tid)) if str(tid).isdigit() else None
            if t is None:
                continue
            op = t.article_operation.operation.name if t.article_operation and t.article_operation.operation else "Operatsiya"
            pairs.append(((t.worker.full_name, _worker_uid(t.worker)) if t.worker else ("Egasi yo'q", ''), op))
        if not pairs:
            names = item.get('operation_names') or ["Noma'lum operatsiya"]
            given = item.get('workers') or []
            pairs = [((given[i] if i < len(given) else "Noma'lum", ''), name) for i, name in enumerate(names)]
        for who, op in pairs:
            ops = workers.setdefault(who, OrderedDict())
            ops[op] = ops.get(op, 0) + 1
        if (item.get('notes') or '').strip():
            notes.append(item['notes'].strip())
    return [(who, list(ops.items())) for who, ops in workers.items()], notes


def repair_receipt_data(receipt, repair_details, *, screen_number, operator_name='', when=None):
    """Chek mazmuni (ESC/POS baytlaridan alohida — testlar shu yerni tekshiradi). QR — shu ta'mir chekining kodi
    ("TAMIR1:<box_code>", "TAMIR2:..."), quti kodi va stikerlar o'zgarmaydi."""
    box = receipt.box
    when = timezone.localtime(when or timezone.now())
    article = box.article
    info = [f"Quti: {box.box_code} (#{box.box_number})"]
    if box.pastal_code:
        info.append(f"Pastal: {box.pastal_code}")
    if box.order_id:
        info.append(f"Zakaz: {box.order.order_number}")
    if article:
        info.append(f"Model: {article.code} {article.name}")
    if box.razmer:
        info.append(f"Razmer: {box.razmer}")
    workers, notes = _repair_workers(box, repair_details)
    return {
        'info': info,
        'qty_line': f"{receipt.cycle}-ta'mir: {receipt.quantity} dona",
        'workers': workers,
        'notes': notes,
        'operator': operator_name,
        'footer': f"{get_patok_code(screen_number)} · {when:%d.%m.%Y %H:%M}",
        'qr': receipt.qr_text,
    }


def build_repair_receipt(receipt, repair_details, *, screen_number, operator_name='', when=None):
    return _render(repair_receipt_data(receipt, repair_details, screen_number=screen_number,
                                       operator_name=operator_name, when=when))


def queue_repair_receipt(box, log, receipt, repair_details, *, user, operator_name=''):
    """Terminal foydalanuvchisi patokining faol printeriga ta'mir chekini qo'yadi. Printer yo'q yoki xato bo'lsa jim qaytadi."""
    try:
        # patok OTK userlarida assigned_screen yozilmagan — patok login nomidan (patok3, patokk3, patoku5)
        screen = user.assigned_screen_number or parse_patok_number(user.username)
        printer = ReceiptPrinter.objects.filter(screen_number=screen, is_active=True).first() if screen else None
        if printer is None or receipt is None:
            return None
        data = build_repair_receipt(receipt, repair_details if isinstance(repair_details, list) else [],
                                    screen_number=screen, operator_name=operator_name)
        return PrintJob.objects.create(
            printer=printer, box=box, inspection_log=log,
            title=f"{receipt.cycle}-ta'mir · {box.box_code} · {receipt.quantity} dona",
            payload=base64.b64encode(data).decode('ascii'),
        )
    except Exception:   # chek hech qachon OTK saqlashini buzmasin
        logger.exception("Ta'mir chekini navbatga qo'yib bo'lmadi (quti %s)", box.box_code)
        return None


def build_test_receipt(printer):
    return _render({
        'info': [f"SINOV CHEKI · {printer.host}", "Kirill: Шорты мужские"], 'qty_line': "1-ta'mir: 1 dona",
        'workers': [(('Sinov Ishchi', '123456'), [('YOQA TIKISH', 1)])], 'notes': [], 'operator': "O'zbek",
        'footer': f"{get_patok_code(printer.screen_number)} · {timezone.localtime():%d.%m.%Y %H:%M}",
        'qr': "TAMIR1:TEST-001",
    })
