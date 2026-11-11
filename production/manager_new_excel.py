"""
Managerlar NEW: "Zakaz list" va "Mato zakazi" Excel fayllari (bo'yoqxona va mijoz uchun, namunadagi ruscha sarlavhalar
bilan). Qadoqlash va shtrix-kod hozircha yo'q (egasi qarori 2026-11-11).
"""
import io
from collections import OrderedDict
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

PRODUCER = 'ООО "ТЕРРИ ЖАР ТЕКСТИЛЬ"'
THIN = Side(style='thin', color='999999')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill('solid', fgColor='E8EEF7')
BOLD = Font(bold=True)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)


def _items(order):
    return list(order.items.select_related('article').prefetch_related('sizes', 'fabrics').order_by('id'))


def _manager_name(order):
    if not order.manager:
        return ''
    return order.manager.get_full_name() or order.manager.username


def _sheet_title(order):
    title = ''.join(ch for ch in order.order_number if ch not in '[]:*?/\\')[:31]
    return title or 'Zakaz'


def _cell(ws, row, col, value, *, bold=False, fill=False, fmt=None, align=CENTER, border=True):
    cell = ws.cell(row=row, column=col, value=value)
    cell.alignment = align
    if bold:
        cell.font = BOLD
    if fill:
        cell.fill = HEAD_FILL
    if fmt:
        cell.number_format = fmt
    if border:
        cell.border = BORDER
    return cell


def _add_image(ws, image_field, anchor, size=110):
    """Rasmni katakka joylaydi (fayl topilmasa jim o'tkazib yuboradi)."""
    if not image_field:
        return False
    try:
        with image_field.open('rb') as fh:
            pic = PILImage.open(fh)
            pic.load()
        pic = pic.convert('RGB')
        pic.thumbnail((size * 2, size * 2))
        buffer = io.BytesIO()
        pic.save(buffer, 'PNG')
        buffer.seek(0)
        image = XLImage(buffer)
        ratio = min(size / image.width, size / image.height)
        image.width, image.height = int(image.width * ratio), int(image.height * ratio)
        ws.add_image(image, anchor)
        return True
    except Exception:
        return False


def _header(ws, pairs):
    """Yuqoridagi "Заказчик / № / ..." qatorlari: (sarlavha, qiymat) juftliklari ikki qatorda."""
    col = 1
    for label, value in pairs:
        _cell(ws, 1, col, label, bold=True, fill=True)
        _cell(ws, 2, col, value, fmt='DD.MM.YYYY' if hasattr(value, 'year') else None)
        col += 1


def _totals(ws, row, items, finished, raw, label_col, value_col):
    by_model = OrderedDict()
    for item in items:
        by_model[item.article.name] = by_model.get(item.article.name, 0) + item.quantity
    lines = [("Всего изделий штук:", sum(by_model.values()), '#,##0')]
    lines += [(f"{name} штук:", qty, '#,##0') for name, qty in by_model.items()]
    lines += [("Всего кг тайёр:", float(finished), '#,##0.0'), ("Всего кг хом:", float(raw), '#,##0.0')]
    for label, value, fmt in lines:
        _cell(ws, row, label_col, label, bold=True, align=LEFT)
        _cell(ws, row, value_col, value, bold=True, fmt=fmt)
        row += 1


def build_order_list_xlsx(order):
    items = _items(order)
    wb = Workbook()
    ws = wb.active
    ws.title = _sheet_title(order)
    _header(ws, [("Заказчик:", order.client_name), ("№", order.order_number), ("Производитель:", PRODUCER),
                 ("Дата заказа:", order.order_date), ("Дата экспорта:", order.export_dates),
                 ("Таблицу подготовил:", _manager_name(order))])

    first_size_col = 6
    last_col = 11
    finished_total = raw_total = Decimal('0')
    row = 4
    for number, item in enumerate(items, start=1):
        fabrics = list(item.fabrics.all())
        sizes = item.size_list   # har artikul o'z razmerlari bilan
        total_col = first_size_col + len(sizes)
        last_col = max(last_col, total_col)
        qty_by_size = {s.size_name: s.planned_quantity for s in item.sizes.all()}
        block_rows = max(7, 4 + len(fabrics))
        ws.merge_cells(start_row=row, start_column=1, end_row=row + block_rows - 1, end_column=1)
        ws.merge_cells(start_row=row, start_column=2, end_row=row + block_rows - 1, end_column=2)
        ws.merge_cells(start_row=row, start_column=4, end_row=row + block_rows - 1, end_column=4)
        _cell(ws, row, 1, number, bold=True)
        _cell(ws, row, 2, item.article_code, bold=True)
        _cell(ws, row, 3, item.article.name, bold=True)
        _add_image(ws, item.article.image, f"D{row}", size=120)
        _cell(ws, row + 1, 3, "Печать" if item.print_image else "", align=LEFT)

        _cell(ws, row, 5, "Размер", bold=True, fill=True)
        for i, size in enumerate(sizes):
            _cell(ws, row, first_size_col + i, size, bold=True, fill=True)
            _cell(ws, row + 1, first_size_col + i, qty_by_size.get(size, 0), fmt='#,##0')
        _cell(ws, row, total_col, "Всего", bold=True, fill=True)
        _cell(ws, row + 1, 5, "Количество", bold=True)
        _cell(ws, row + 1, total_col, item.quantity, bold=True, fmt='#,##0')
        _cell(ws, row + 2, 5, "Просчет", bold=True)
        _cell(ws, row + 2, first_size_col, item.prosjyot or item.quantity, fmt='#,##0')
        _cell(ws, row + 2, first_size_col + 1, "Дата отгрузки:", bold=True)
        _cell(ws, row + 2, first_size_col + 2, item.ship_date, fmt='DD.MM.YYYY')

        for col, title in enumerate(("Детали", "Цвет", "Плотность гр/м2", "Состав", "Эн", "Вес изделия кг", "Общий кг"),
                                    start=5):
            _cell(ws, row + 3, col, title, bold=True, fill=True)
        for offset, fabric in enumerate(fabrics, start=4):
            values = (fabric.name, fabric.color, fabric.density_gsm, fabric.composition, fabric.width_cm,
                      float(fabric.kg_per_piece), float(fabric.finished_kg))
            for col, value in enumerate(values, start=5):
                _cell(ws, row + offset, col, value, fmt='0.000' if col == 10 else '#,##0.0' if col == 11 else None)
            finished_total += fabric.finished_kg
            raw_total += fabric.raw_kg
        for r in range(row, row + block_rows):
            ws.row_dimensions[r].height = 20
        row += block_rows + 1

    _totals(ws, row, items, finished_total, raw_total, label_col=3, value_col=5)
    widths = {1: 6, 2: 22, 3: 26, 4: 18, 5: 26}
    for col in range(1, last_col + 1):
        ws.column_dimensions[get_column_letter(col)].width = widths.get(col, 14)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


FABRIC_HEADERS = ("№", "Артикул", "Модел номи", "Фото", "Лаб. карта", "Клиент ранг коди", "Фабрика ранг коди",
                  "Мато номи", "Таркиб", "Эн", "Гр/м2", "Мато кг тайёр", "Мато кг хом", "Битта иш учун кг",
                  "Иш сони", "Инспекция датаси", "Просчет", "Просчет %", "Экспорт санаси")
ARTICLE_COLS = (1, 2, 3, 4, 15, 16, 17)   # artikul darajasidagi ustunlar mato qatorlari bo'ylab birlashtiriladi


def build_fabric_order_xlsx(order):
    items = _items(order)
    wb = Workbook()
    ws = wb.active
    ws.title = _sheet_title(order)
    _header(ws, [("Производитель:", PRODUCER), ("Заказчик:", order.client_name), ("Номер заказа:", order.order_number),
                 ("Дата заказа:", order.order_date), ("Дата экспорта:", order.export_dates),
                 ("Менеджер:", _manager_name(order))])
    for col, title in enumerate(FABRIC_HEADERS, start=1):
        _cell(ws, 4, col, title, bold=True, fill=True)
    ws.row_dimensions[4].height = 32

    finished_total = raw_total = Decimal('0')
    row = 5
    for number, item in enumerate(items, start=1):
        fabrics = list(item.fabrics.all()) or [None]
        first = row
        for fabric in fabrics:
            if fabric is not None:
                values = {
                    6: fabric.color, 7: fabric.factory_color_code, 8: fabric.name, 9: fabric.composition,
                    10: fabric.width_cm, 11: fabric.density_gsm, 12: float(fabric.finished_kg), 13: float(fabric.raw_kg),
                    14: float(fabric.kg_per_piece), 18: float(fabric.prosjyot_percent / 100), 19: fabric.export_date,
                }
                formats = {12: '#,##0.0', 13: '#,##0.0', 14: '0.000', 18: '0.00%', 19: 'DD.MM.YYYY'}
                for col in range(5, 20):
                    if col in ARTICLE_COLS:
                        continue
                    _cell(ws, row, col, values.get(col), fmt=formats.get(col))
                if _add_image(ws, fabric.lab_card_image, f"E{row}", size=90):
                    ws.row_dimensions[row].height = 72
                finished_total += fabric.finished_kg
                raw_total += fabric.raw_kg
            row += 1
        last = row - 1
        article_values = {1: number, 2: item.article_code, 3: item.article.name, 4: None, 15: item.quantity,
                          16: item.ship_date, 17: item.prosjyot or item.quantity}
        for col in ARTICLE_COLS:
            if last > first:
                ws.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)
            _cell(ws, first, col, article_values[col], bold=col in (1, 2),
                  fmt='DD.MM.YYYY' if col == 16 else '#,##0' if col in (15, 17) else None)
        if _add_image(ws, item.article.image, f"D{first}", size=100):
            rows_span = last - first + 1
            for r in range(first, last + 1):
                ws.row_dimensions[r].height = max(ws.row_dimensions[r].height or 0, 80 / rows_span + 4)

    _totals(ws, row + 1, items, finished_total, raw_total, label_col=8, value_col=12)
    widths = {1: 5, 2: 20, 3: 22, 4: 16, 5: 14, 6: 20, 7: 14, 8: 24, 9: 24}
    for col in range(1, len(FABRIC_HEADERS) + 1):
        ws.column_dimensions[get_column_letter(col)].width = widths.get(col, 12)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
