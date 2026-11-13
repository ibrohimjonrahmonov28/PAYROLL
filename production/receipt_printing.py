"""
OTK ta'mir cheki (egasi talabi 2026-11-11): OTK ta'mir sonini saqlaganda shu patok OTK stolidagi printerdan chek chiqadi
— ta'mir soni, har dona bo'yicha operatsiya va uni tikkan ishchi, hamda qutining MAVJUD OTK QR kodi (CONTROL:<box_code>).
Yangi kod yaratilmaydi (oltin qoida). Server chekni PrintJob navbatiga qo'yadi, fabrikadagi pechat agenti
(print_agent/terryjar_print_agent.py) uni printerga yuboradi. Chek chiqmasa ham OTK saqlanadi.
"""
import base64
import logging
import textwrap

from django.utils import timezone

from production.models import PrintJob, ReceiptPrinter, Ticket
from production.services import get_patok_code

logger = logging.getLogger(__name__)

LINE_WIDTH = 48          # 80 mm qog'oz, A shrift
ENCODING = 'cp866'       # kirill + lotin; Xprinter'da 17-kod sahifa (PC866)
CODEPAGE = 17

ESC_INIT = b'\x1b@'
ALIGN_LEFT, ALIGN_CENTER = b'\x1ba\x00', b'\x1ba\x01'
BOLD_ON, BOLD_OFF = b'\x1bE\x01', b'\x1bE\x00'
SIZE_NORMAL, SIZE_DOUBLE = b'\x1d!\x00', b'\x1d!\x11'
FEED_AND_CUT = b'\n\n\n\x1dVB\x00'
APOSTROPHES = str.maketrans({'ʻ': "'", 'ʼ': "'", '‘': "'", '’': "'", '`': "'", '«': '"', '»': '"', '—': '-', '–': '-'})


class Receipt:
    """Oddiy ESC/POS quruvchi: matn qatorlari, katta sarlavha, chiziq, QR va kesish."""

    def __init__(self):
        self.data = bytearray(ESC_INIT + b'\x1bt' + bytes([CODEPAGE]))

    def _text(self, text):
        return str(text).translate(APOSTROPHES).encode(ENCODING, errors='replace')

    def line(self, text='', *, bold=False, center=False, big=False):
        width = LINE_WIDTH // 2 if big else LINE_WIDTH
        self.data += (ALIGN_CENTER if center else ALIGN_LEFT) + (BOLD_ON if bold else BOLD_OFF)
        self.data += SIZE_DOUBLE if big else SIZE_NORMAL
        for part in textwrap.wrap(str(text), width, subsequent_indent='  ') or ['']:
            self.data += self._text(part) + b'\n'
        self.data += SIZE_NORMAL + BOLD_OFF
        return self

    def rule(self):
        return self.line('-' * LINE_WIDTH)

    def qr(self, content, size=8):
        payload = content.encode('ascii', errors='replace')
        store = len(payload) + 3
        self.data += ALIGN_CENTER
        self.data += b'\x1d(k\x04\x001A2\x00'                                    # model 2
        self.data += b'\x1d(k\x03\x001C' + bytes([size])                         # modul o'lchami
        self.data += b'\x1d(k\x03\x001E1'                                        # xatoni tuzatish M
        self.data += b'\x1d(k' + bytes([store % 256, store // 256]) + b'1P0' + payload
        self.data += b'\x1d(k\x03\x001Q0\n'                                      # chop etish
        return self

    def build(self):
        return bytes(self.data + FEED_AND_CUT)


def _repair_lines(box, repair_details):
    """Har ta'mir donasi: operatsiya + tikkan ishchi. Stiker ID'lari bo'yicha bazadan olinadi (planshet matniga ishonmaydi)."""
    ticket_ids = {int(i) for item in repair_details for i in (item.get('operation_ids') or []) if str(i).isdigit()}
    tickets = {t.id: t for t in Ticket.objects.filter(box=box, id__in=ticket_ids).select_related(
        'article_operation__operation', 'worker')}
    lines = []
    for idx, item in enumerate(repair_details, start=1):
        number = item.get('item_number', idx)
        parts = []
        for tid in item.get('operation_ids') or []:
            t = tickets.get(int(tid)) if str(tid).isdigit() else None
            if t is None:
                continue
            op = t.article_operation.operation.name if t.article_operation and t.article_operation.operation else "Operatsiya"
            worker = t.worker.full_name if t.worker else "egasi yo'q"
            uid = f" ({t.worker.worker_id})" if t.worker and t.worker.worker_id else ''
            parts.append((op, f"{worker}{uid}"))
        if not parts:   # stiker topilmasa planshet yuborgan nomlar
            names = item.get('operation_names') or ["Noma'lum operatsiya"]
            workers = item.get('workers') or []
            parts = [(name, workers[i] if i < len(workers) else '') for i, name in enumerate(names)]
        lines.append((number, parts, (item.get('notes') or '').strip()))
    return lines


def build_repair_receipt(box, repair_qty, repair_details, *, screen_number, operator_name='', terminal='', is_return=False):
    article = box.article
    r = Receipt()
    r.line("QAYTA TA'MIR" if is_return else "TA'MIR", bold=True, center=True, big=True)
    r.line(get_patok_code(screen_number), bold=True, center=True, big=True)
    r.rule()
    r.line(f"Quti: {box.box_code} (#{box.box_number})", bold=True)
    if box.pastal_code:
        r.line(f"Pastal: {box.pastal_code}")
    if box.order_id:
        r.line(f"Zakaz: {box.order.order_number}")
    if article:
        r.line(f"Model: {article.code} {article.name}")
    if box.razmer:
        r.line(f"Razmer: {box.razmer}")
    r.line(f"Ta'mirga: {repair_qty} dona", bold=True, big=True)
    r.rule()
    for number, parts, note in _repair_lines(box, repair_details):
        r.line(f"#{number} dona", bold=True)
        for op, worker in parts:
            r.line(f"  {op}")
            if worker:
                r.line(f"    {worker}", bold=True)
        if note:
            r.line(f"  Izoh: {note}")
    if repair_details:
        r.rule()
    if operator_name:
        r.line(f"OTK: {operator_name}")
    r.line(f"{terminal} · {timezone.localtime().strftime('%d.%m.%Y %H:%M')}".strip(' ·'))
    r.line()
    r.qr(f"CONTROL:{box.box_code}")
    r.line(box.box_code, bold=True, center=True)
    return r.build()


def queue_repair_receipt(box, log, repair_qty, repair_details, *, user, operator_name='', is_return=False):
    """Terminal foydalanuvchisi patokining faol printeriga chek qo'yadi. Printer yo'q yoki xato bo'lsa jim qaytadi."""
    try:
        screen = user.assigned_screen_number
        printer = ReceiptPrinter.objects.filter(screen_number=screen, is_active=True).first() if screen else None
        if printer is None or repair_qty <= 0:
            return None
        data = build_repair_receipt(box, repair_qty, repair_details if isinstance(repair_details, list) else [],
                                    screen_number=screen, operator_name=operator_name,
                                    terminal=user.username or '', is_return=is_return)
        return PrintJob.objects.create(
            printer=printer, box=box, inspection_log=log,
            title=f"{'Qayta ta' if is_return else 'Ta'}'mir · {box.box_code} · {repair_qty} dona",
            payload=base64.b64encode(data).decode('ascii'),
        )
    except Exception:   # chek hech qachon OTK saqlashini buzmasin
        logger.exception("Ta'mir chekini navbatga qo'yib bo'lmadi (quti %s)", box.box_code)
        return None


def build_test_receipt(printer):
    r = Receipt()
    r.line("SINOV CHEKI", bold=True, center=True, big=True)
    r.line(get_patok_code(printer.screen_number), bold=True, center=True, big=True)
    r.rule()
    r.line(f"Printer: {printer.host}")
    r.line("Lotin: O'zbekiston, g'isht, ta'mir")
    r.line("Kirill: Шорты мужские, Футболка")
    r.line(timezone.localtime().strftime('%d.%m.%Y %H:%M'))
    r.qr("CONTROL:TEST-001")
    return r.build()
