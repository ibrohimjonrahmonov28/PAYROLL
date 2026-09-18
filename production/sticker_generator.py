import io
import os
from PIL import Image, ImageDraw, ImageFont
from decimal import Decimal


def get_font_paths():
    """macOS va Linux (Docker/Ubuntu) da mavjud shriftlarni avtomatik aniqlash"""
    bold_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/Library/Fonts/Arial Bold.ttf',
    ]
    reg_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    font_bold = next((p for p in bold_candidates if os.path.exists(p)), None)
    font_reg = next((p for p in reg_candidates if os.path.exists(p)), None)
    return font_bold, font_reg


def get_fitted_font(draw, text, max_w, font_path, initial_size=42, min_size=24):
    """Matn berilgan kenglikdan chiqib ketmasligi uchun fontni avtomatik kichraytirish"""
    if not font_path or not os.path.exists(font_path):
        return ImageFont.load_default()
    for sz in range(initial_size, min_size - 1, -2):
        try:
            f = ImageFont.truetype(font_path, sz)
            bbox = draw.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_w:
                return f
        except Exception:
            return ImageFont.load_default()
    try:
        return ImageFont.truetype(font_path, min_size)
    except Exception:
        return ImageFont.load_default()


def render_single_box_ticket_100x60(ticket, font_bold_path: str, font_reg_path: str) -> Image.Image:
    """
    Bitta quti bileti uchun 100mm x 60mm (1181 x 709 px, 300 DPI) o'lchamli termal stiker rasmini chizish.
    """
    W = 1181  # 100 mm @ 300 DPI
    H = 709   # 60 mm @ 300 DPI

    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(font_bold_path, 34) if font_bold_path else ImageFont.load_default()
        font_qty = ImageFont.truetype(font_bold_path, 50) if font_bold_path else ImageFont.load_default()
        font_medium = ImageFont.truetype(font_reg_path, 26) if font_reg_path else ImageFont.load_default()
        font_small = ImageFont.truetype(font_reg_path, 20) if font_reg_path else ImageFont.load_default()
        font_badge = ImageFont.truetype(font_bold_path, 22) if font_bold_path else ImageFont.load_default()
        font_code = ImageFont.truetype(font_bold_path, 20) if font_bold_path else ImageFont.load_default()
    except Exception:
        font_title = font_qty = font_medium = font_small = font_badge = font_code = ImageFont.load_default()

    # 1. Tashqi ramka (Outer border)
    draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=22, fill='white', outline=(15, 23, 42), width=4)

    # 2. Yuqori sarlavha (Header: Zakaz va Quti ID)
    order_num = ticket.box.order.order_number if ticket.box and ticket.box.order else "ZAKAZ"
    draw.text((45, 34), f"ZAKAZ: {order_num[:26]}", fill=(15, 23, 42), font=font_title)

    box_num = ticket.box.box_number if ticket.box else 0
    box_code = ticket.box.box_code if ticket.box else ""
    razmer_val = ticket.box.razmer.strip() if ticket.box and ticket.box.razmer else ""
    box_text = f"QUTI #{box_num} [{box_code}]"
    bx_w = 340
    box_rect_left = W - 45 - bx_w
    draw.rounded_rectangle([box_rect_left, 32, W - 45, 32 + 42], radius=10, fill=(241, 245, 249), outline=(15, 23, 42), width=2)
    draw.text((box_rect_left + 16, 40), box_text, fill=(15, 23, 42), font=font_badge)

    # Razmer nishoni (Header o'ng qismida Quti yonida)
    if razmer_val:
        rz_text = f"RAZMER: {razmer_val}"
        rz_bbox = draw.textbbox((0, 0), rz_text, font=font_badge)
        rz_w = max(180, (rz_bbox[2] - rz_bbox[0]) + 32)
        rz_left = box_rect_left - rz_w - 12
        draw.rounded_rectangle([rz_left, 32, rz_left + rz_w, 32 + 42], radius=10, fill=(243, 232, 255), outline=(107, 33, 168), width=2)
        draw.text((rz_left + 16, 40), rz_text, fill=(88, 28, 135), font=font_badge)

    # Yuqori ajratuvchi chiziq
    draw.line([(45, 110), (W - 45, 110)], fill=(226, 232, 240), width=2)

    # 3. QR Kod (O'ng ustun)
    qr_box_size = 440
    qr_x = W - 45 - qr_box_size
    qr_y = 135
    draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_box_size, qr_y + qr_box_size], radius=18, fill='white', outline=(15, 23, 42), width=4)

    # QR kod faylini ochish
    if not ticket.qr_code_image:
        ticket.generate_qr_code()
        ticket.save(update_fields=['qr_code_image'])

    if ticket.qr_code_image and os.path.exists(ticket.qr_code_image.path):
        qr_img = Image.open(ticket.qr_code_image.path)
        qr_inner_pad = 18
        qr_res = qr_img.resize((qr_box_size - qr_inner_pad * 2, qr_box_size - qr_inner_pad * 2), Image.Resampling.LANCZOS)
        img.paste(qr_res, (qr_x + qr_inner_pad, qr_y + qr_inner_pad))

    # QR ostidagi yozuv (Bilet kodi)
    short_code = ticket.ticket_code[-24:] if ticket.ticket_code else ""
    draw.text((qr_x + 30, qr_y + qr_box_size + 10), short_code, fill=(100, 116, 139), font=font_code)

    # 4. Bilet tafsilotlari (Chap ustun)
    info_x = 45
    max_left_w = qr_x - info_x - 30

    # Model nomi va Artikul kodi
    art_name = ""
    art_code = ""
    if ticket.article_operation and ticket.article_operation.article:
        art_name = ticket.article_operation.article.name
        art_code = ticket.article_operation.article.code
    elif ticket.box and ticket.box.article:
        art_name = ticket.box.article.name
        art_code = ticket.box.article.code

    model_label = f"[{art_code}] {art_name}".strip().upper() if art_code else art_name.upper()
    font_art = get_fitted_font(draw, f"MODEL: {model_label}", max_left_w, font_bold_path, initial_size=32, min_size=20)
    draw.text((info_x, 135), f"MODEL: {model_label[:34]}", fill=(15, 23, 42), font=font_art)

    # Operatsiya nomi (Katta va qalin)
    op_name = ticket.article_operation.operation.name if ticket.article_operation and ticket.article_operation.operation else "Operatsiya"
    font_op = get_fitted_font(draw, op_name.upper(), max_left_w, font_bold_path, initial_size=42, min_size=26)
    draw.text((info_x, 185), op_name.upper(), fill=(15, 23, 42), font=font_op)

    # Mahsulot soni (Kontrastli to'q qutida sariq harflar bilan)
    qty_y = 265
    qty_w = min(460, max_left_w)
    draw.rounded_rectangle([info_x, qty_y, info_x + qty_w, qty_y + 90], radius=16, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
    draw.text((info_x + 25, qty_y + 18), f"{ticket.quantity} DONA", fill=(251, 191, 36), font=font_qty)

    # Qiyinlik, Tarkib va Razmer
    diff_val = ticket.article_operation.difficulty_display if ticket.article_operation else "1"
    split_val = f"Bo'lak {ticket.split_index}/{ticket.total_splits}" if ticket.total_splits > 1 else "To'liq partiya"

    cur_y = 385
    if razmer_val:
        draw.text((info_x, cur_y), f"RAZMER (O'LCHAM): {razmer_val}", fill=(88, 28, 135), font=font_badge)
        cur_y += 40

    draw.text((info_x, cur_y), f"QIYINLIK DARAJASI: {diff_val}", fill=(15, 23, 42), font=font_badge)
    cur_y += 40
    draw.text((info_x, cur_y), f"TARKIBI: {split_val}", fill=(100, 116, 139), font=font_medium)
    cur_y += 42
    draw.text((info_x, cur_y), "Skanerlash uchun terminalga tuting", fill=(148, 163, 184), font=font_small)

    # 5. Pastki qism (Footer)
    draw.line([(45, 620), (W - 45, 620)], fill=(226, 232, 240), width=2)
    draw.text((45, 638), "TERRY JAR • OPERATSIYA QR KODI • 100x60 MM", fill=(148, 163, 184), font=font_small)
    draw.text((W - 360, 638), f"KOD: {short_code[-18:]}", fill=(100, 116, 139), font=font_small)

    return img


def generate_box_stickers_100x60_pdf(tickets_qs, output_destination=None) -> bytes:
    """
    Berilgan birkalar (tickets) uchun 100mm x 60mm stikerlar PDF faylini generatsiya qilish.
    Har bir bilet alohida 100x60 mm varaqda bo'ladi.
    """
    font_bold, font_reg = get_font_paths()

    tickets = list(tickets_qs)
    pages = []

    for t in tickets:
        img = render_single_box_ticket_100x60(t, font_bold, font_reg)
        pages.append(img)

    if not pages:
        blank = Image.new('RGB', (1181, 709), 'white')
        pages.append(blank)

    # Agar fayl yo'li berilgan bo'lsa:
    if isinstance(output_destination, str):
        pages[0].save(
            output_destination,
            'PDF',
            resolution=300.0,
            save_all=True,
            append_images=pages[1:]
        )
        return output_destination

    # Aks holda BytesIO qaytaramiz (HTTP Response uchun)
    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        'PDF',
        resolution=300.0,
        save_all=True,
        append_images=pages[1:]
    )
    buffer.seek(0)
    return buffer.getvalue()

