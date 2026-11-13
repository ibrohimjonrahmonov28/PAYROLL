"""
OTK ta'mir cheki (egasi talabi 2026-11-11): OTK ta'mir sonini saqlaganda shu patok OTK stolidagi printerdan chek chiqadi
— ta'mir soni, har dona bo'yicha operatsiya va uni tikkan ishchi (6 xonali UID bilan), hamda qutining MAVJUD OTK QR kodi
(CONTROL:<box_code>). Yangi kod yaratilmaydi (oltin qoida). Server chekni PrintJob navbatiga qo'yadi, fabrikadagi pechat
agenti (print_agent/terryjar_print_agent.py) uni printerga yuboradi. Chek chiqmasa ham OTK saqlanadi.

Dizayn (egasi, 2026-11-13): qog'oz imkon qadar kam (eni 80 mm, bo'yi qisqa).
  K2 · TA'MIR · 8 dona            <- qalin, ikki baravar matn
  UID  Ism x2     OPERATSIYA      <- har tikuvchi bir qator (kichik shrift)
  [QR]  zakaz / QUTI / model / artikul   <- QR chapda, yonida 4 qator; sig'masa ".."
Sarlavha va ro'yxat printerning o'z matni; faqat QR bloki kichik rasm (~13 KB, 24 qatorli bo'laklar) — XP-80TS sahifa
rejimini qo'llamaydi, QR yoniga matnni faqat rasm bilan qo'yish mumkin. Butun chekni rasm qilish (30-40 KB) USB'da
"tushunarsiz harf" bo'lib chiqqan.
"""
import base64
import logging
import os
from collections import OrderedDict

import qrcode
from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageChops, ImageDraw, ImageFont

from production.models import PrintJob, ReceiptPrinter, Ticket
from production.services import get_patok_code, parse_patok_number

logger = logging.getLogger(__name__)

ENCODING, CODEPAGE = 'cp866', 17   # kirill; Xprinter'da 17-kod sahifa (PC866)
WIDE_COLS = 24                     # ikki baravar shrift, 80 mm
SMALL_COLS = 64                    # kichik B shrift, 80 mm — ro'yxat bir qatorda
WIDTH = 576                        # 72 mm chop etish eni, 203 dpi
QR_BOX = 8                         # QR moduli 8 nuqta — ~23 mm, qutidagi OTK stikeri QR'i bilan bir xil
BAND = 24                          # rasm 24 qatorli bo'laklarda (printer xotirasi kichik)
APOSTROPHES = str.maketrans({'ʻ': "'", 'ʼ': "'", '‘': "'", '’': "'", '`': "'", '«': '"', '»': '"', '—': '-', '–': '-'})


def _text(text):
    return str(text).translate(APOSTROPHES).encode(ENCODING, errors='replace')


def _cut(text, width):
    text = str(text)
    return text if len(text) <= width else text[:width - 2] + '..'


def _font(size, bold=False):
    name = 'Arial-Bold.ttf' if bold else 'Arial.ttf'
    for path in (os.path.join(settings.BASE_DIR, 'production', 'fonts', name),
                 f"/usr/share/fonts/truetype/dejavu/{'DejaVuSans-Bold' if bold else 'DejaVuSans'}.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _qr_block(data):
    """QR chapda, yonida zakaz / quti ID / model / artikul (sig'masa ".."), ESC/POS rasm (GS v 0) bo'laklari."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=QR_BOX, border=1)
    qr.add_data(data['qr'])
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color='black', back_color='white').convert('L')
    img = Image.new('L', (WIDTH, qr_img.height), 255)
    img.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = '1'                                     # silliqlashsiz — termo printerda tiniq
    x = qr_img.width + 14
    width = WIDTH - x
    y = 6
    for text, size, bold in ((data['order'], 26, True), (data['box_code'], 34, True),
                             (data['model'], 22, False), (data['article'], 22, False)):
        if text:
            font = _font(size, bold)
            if draw.textlength(text, font=font) > width:
                while text and draw.textlength(text + '..', font=font) > width:
                    text = text[:-1]
                text += '..'
            draw.text((x, y), text, font=font, fill=0)
        y += size + 14
    bw = ImageChops.invert(img).convert('1', dither=Image.Dither.NONE)   # 1 bit = qora nuqta
    out = bytearray()
    for top in range(0, bw.height, BAND):
        band = bw.crop((0, top, WIDTH, min(top + BAND, bw.height)))
        out += b'\x1dv0\x00' + bytes([WIDTH // 8, 0, band.height % 256, band.height // 256]) + band.tobytes()
    return bytes(out)


def _render(data):
    # Boshida bo'sh baytlar: XP-80TS USB'da ba'zan ishning birinchi baytlarini yo'qotadi (sarlavha tushib qoldi,
    # avvalgi chekning tekislashi qoldi) — NUL'larni printer e'tiborsiz qoldiradi, ESC @ esa holatni tiklaydi.
    out = bytearray(b'\x00' * 64 + b'\x1b@\x1b@\x1c.\x1bt' + bytes([CODEPAGE]))   # FS . — xitoy rejimi o'chiq
    out += b'\x1ba\x00\x1bE\x01\x1d!\x11' + _text(_cut(data['title'], WIDE_COLS)) + b'\n'
    out += b'\x1d!\x00\x1bE\x00\x1bM\x01\x1b3\x36'                    # kichik shrift, qator oralig'i 54 nuqta (~7 mm)
    for uid, name, op, n in data['rows']:
        who = name + (f" x{n}" if n > 1 else '')
        line = f"{uid:<6} {_cut(who, 24):<24} {op}" if uid else f"{_cut(who, 31):<31} {op}"
        out += b'\x1bE\x01' + _text(_cut(line, SMALL_COLS)) + b'\x1bE\x00\n'
    for note in data['notes']:
        out += _text(_cut(f"Izoh: {note}", SMALL_COLS)) + b'\n'
    out += b'\x1bM\x00\x1b2' + _qr_block(data)
    return bytes(out + b'\x1dVB\x00')                                  # kesish joyigacha surib kesadi


def _worker_uid(worker):
    return worker.user.uid if worker.user_id and worker.user.uid else (worker.worker_id or '')


def _repair_rows(box, repair_details):
    """(UID, ism, operatsiya, soni): bir xil tikuvchi+operatsiya bitta qatorda ×N. Stiker ID'lari bo'yicha bazadan
    (planshet matniga ishonmaydi); stiker topilmasa planshet yuborgan nomlar."""
    ticket_ids = {int(i) for item in repair_details for i in (item.get('operation_ids') or []) if str(i).isdigit()}
    tickets = {t.id: t for t in Ticket.objects.filter(box=box, id__in=ticket_ids).select_related(
        'article_operation__operation', 'worker__user')}
    groups, notes = OrderedDict(), []
    for item in repair_details:
        keys = []
        for tid in item.get('operation_ids') or []:
            t = tickets.get(int(tid)) if str(tid).isdigit() else None
            if t is None:
                continue
            op = t.article_operation.operation.name if t.article_operation and t.article_operation.operation else "Operatsiya"
            keys.append((_worker_uid(t.worker), t.worker.full_name, op) if t.worker else ('', "egasi yo'q", op))
        if not keys:
            names = item.get('operation_names') or ["Noma'lum operatsiya"]
            workers = item.get('workers') or []
            keys = [('', workers[i] if i < len(workers) else '', name) for i, name in enumerate(names)]
        for key in keys:
            groups[key] = groups.get(key, 0) + 1
        if (item.get('notes') or '').strip():
            notes.append(item['notes'].strip())
    return [(uid, name, op, n) for (uid, name, op), n in groups.items()], notes


def repair_receipt_data(box, repair_qty, repair_details, *, screen_number, operator_name='', is_return=False, when=None):
    """Chek mazmuni (ESC/POS baytlaridan alohida — testlar shu yerni tekshiradi)."""
    rows, notes = _repair_rows(box, repair_details)
    return {
        'title': f"{get_patok_code(screen_number)} · {'QAYTA TA' if is_return else 'TA'}'MIR · {repair_qty} dona",
        'qr': f"CONTROL:{box.box_code}",
        'box_code': box.box_code,
        'order': box.order.order_number if box.order_id else '',
        'model': box.article.name if box.article else '',
        'article': box.article.code if box.article else '',
        'rows': rows,
        'notes': notes,
    }


def build_repair_receipt(box, repair_qty, repair_details, *, screen_number, operator_name='', is_return=False, when=None):
    return _render(repair_receipt_data(box, repair_qty, repair_details, screen_number=screen_number,
                                       operator_name=operator_name, is_return=is_return, when=when))


def queue_repair_receipt(box, log, repair_qty, repair_details, *, user, operator_name='', is_return=False):
    """Terminal foydalanuvchisi patokining faol printeriga chek qo'yadi. Printer yo'q yoki xato bo'lsa jim qaytadi."""
    try:
        # patok OTK userlarida assigned_screen yozilmagan — patok login nomidan (patok3, patokk3, patoku5)
        screen = user.assigned_screen_number or parse_patok_number(user.username)
        printer = ReceiptPrinter.objects.filter(screen_number=screen, is_active=True).first() if screen else None
        if printer is None or repair_qty <= 0:
            return None
        data = build_repair_receipt(box, repair_qty, repair_details if isinstance(repair_details, list) else [],
                                    screen_number=screen, operator_name=operator_name, is_return=is_return)
        return PrintJob.objects.create(
            printer=printer, box=box, inspection_log=log,
            title=f"{'Qayta ta' if is_return else 'Ta'}'mir · {box.box_code} · {repair_qty} dona",
            payload=base64.b64encode(data).decode('ascii'),
        )
    except Exception:   # chek hech qachon OTK saqlashini buzmasin
        logger.exception("Ta'mir chekini navbatga qo'yib bo'lmadi (quti %s)", box.box_code)
        return None


def build_test_receipt(printer):
    return _render({
        'title': f"{get_patok_code(printer.screen_number)} · SINOV", 'qr': "CONTROL:TEST-001", 'box_code': "TEST-001",
        'order': printer.host, 'model': "Шорты мужские", 'article': "O'zbek",
        'rows': [('123456', 'Sinov Ishchi', 'YOQA TIKISH', 1)], 'notes': [],
    })
