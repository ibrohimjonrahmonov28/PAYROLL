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


def render_single_box_ticket_100x60(ticket, font_bold_path: str = None, font_reg_path: str = None) -> Image.Image:
    """
    Foydalanuvchi chizmasi (obrazes) bo'yicha 100mm x 60mm (1181 x 709 px, 300 DPI) termal stiker rasmini chizish:
    - Yuqori sarlavha: Chapda katta "OPERATSIYA NOMI" | O'ngda "QUTI ID"
    - Asosiy qism chapda: Katta unikal QR kod + ostida qora fonda "STIKER ID" katagi
    - Asosiy qism o'ngda: "Model nomi" + "Artikul" + pastda "Son", "Qiyinlik", "Razmer" kataklari
    - Pastki qism: TERRY JAR brendi va qisqa kod
    """
    import qrcode
    if not font_bold_path or not font_reg_path:
        fb, fr = get_font_paths()
        font_bold_path = font_bold_path or fb
        font_reg_path = font_reg_path or fr

    W = 1181  # 100 mm @ 300 DPI
    H = 709   # 60 mm @ 300 DPI

    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(font_bold_path, 34) if font_bold_path else ImageFont.load_default()
        font_qty = ImageFont.truetype(font_bold_path, 48) if font_bold_path else ImageFont.load_default()
        font_medium = ImageFont.truetype(font_reg_path, 26) if font_reg_path else ImageFont.load_default()
        font_small = ImageFont.truetype(font_bold_path, 20) if font_bold_path else ImageFont.load_default()
        font_badge = ImageFont.truetype(font_bold_path, 24) if font_bold_path else ImageFont.load_default()
        font_code = ImageFont.truetype(font_bold_path, 18) if font_bold_path else ImageFont.load_default()
    except Exception:
        font_title = font_qty = font_medium = font_small = font_badge = font_code = ImageFont.load_default()

    # 1. Tashqi ramka (Outer border)
    draw.rounded_rectangle([18, 18, W - 18, H - 18], radius=20, fill='white', outline=(15, 23, 42), width=4)

    # 2. YUQORI SARLAVHA: OPERATSIYA NOMI (Butun qator bo'ylab katta va qalin)
    header_h = 120
    draw.line([(18, header_h), (W - 18, header_h)], fill=(15, 23, 42), width=3)

    # OPERATSIYA NOMI VA TARTIB RAQAMI
    op_name = ""
    seq_num = None
    if ticket.article_operation and ticket.article_operation.operation:
        op_name = ticket.article_operation.operation.name
        seq_num = ticket.article_operation.sequence
    else:
        op_name = "Operatsiya"

    op_display = f"№{seq_num}. {op_name.upper()}" if seq_num else op_name.upper()

    draw.text((42, 24), "OPERATSIYA NOMI:", fill=(100, 116, 139), font=font_small)
    font_op = get_fitted_font(draw, op_display, W - 84, font_bold_path, initial_size=44, min_size=24)
    draw.text((42, 52), op_display, fill=(15, 23, 42), font=font_op)

    # 3. KATTA QR KOD VA KICHIKROQ STIKER ID (User talabi)
    qr_x = 40
    qr_y = 135
    qr_size = 415  # Oldingi 350 o'rniga KATTA 415 px

    draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_size, qr_y + qr_size], radius=16, fill='white', outline=(15, 23, 42), width=3)

    # QR kod tasvirini olish (mavjud bo'lsa ochadi, bo'lmasa xotirada tezkor yaratadi)
    qr_img = None
    if ticket.qr_code_image and hasattr(ticket.qr_code_image, 'path') and os.path.exists(ticket.qr_code_image.path):
        try:
            qr_img = Image.open(ticket.qr_code_image.path)
        except Exception:
            qr_img = None

    if not qr_img:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=1,
        )
        qr.add_data(f"TICKET:{ticket.ticket_code}")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

    qr_inner = 385  # Oldingi 318 o'rniga KATTA 385 px
    qr_res = qr_img.resize((qr_inner, qr_inner), Image.Resampling.LANCZOS)
    draw_qr_x = qr_x + (qr_size - qr_inner) // 2
    draw_qr_y = qr_y + (qr_size - qr_inner) // 2
    img.paste(qr_res, (draw_qr_x, draw_qr_y))

    # STIKER ID Katagi: Kichikroq, ixcham (64 px)
    stiker_y = qr_y + qr_size + 12  # 562 px
    stiker_h = 64
    draw.rounded_rectangle([qr_x, stiker_y, qr_x + qr_size, stiker_y + stiker_h], radius=10, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
    draw.text((qr_x + 14, stiker_y + 8), "STIKER ID (UNIKAL):", fill=(251, 191, 36), font=font_code)

    # Yangilarida 8 xonali unikal stiker_code, eskilari uchun esa odatiy #id
    if getattr(ticket, 'stiker_code', None):
        stiker_text = f"#{ticket.stiker_code}"
    else:
        short_hash = ticket.short_hash
        stiker_text = f"#{ticket.id} [{short_hash}]" if short_hash else f"#{ticket.id}"

    font_id = get_fitted_font(draw, stiker_text, qr_size - 28, font_bold_path, initial_size=30, min_size=18)
    draw.text((qr_x + 14, stiker_y + 28), stiker_text, fill='white', font=font_id)

    # 4. ASOSIY QISM O'NGDA: MODEL NAME, ARTIKUL, ZAKAZ, KATAKLAR
    right_x = 485
    right_w = W - 40 - right_x

    art_name = ""
    art_code = ""
    if ticket.article_operation and ticket.article_operation.article:
        art_name = ticket.article_operation.article.name
        art_code = ticket.article_operation.article.code
    elif ticket.box and ticket.box.article:
        art_name = ticket.box.article.name
        art_code = ticket.box.article.code

    # Model Nomi (Chizmadagi ostiga chizilgan qator)
    draw.text((right_x, 140), "MODEL NOMI:", fill=(100, 116, 139), font=font_small)
    font_model = get_fitted_font(draw, art_name.upper(), right_w, font_bold_path, initial_size=36, min_size=24)
    draw.text((right_x, 168), art_name.upper()[:36], fill=(15, 23, 42), font=font_model)
    draw.line([(right_x, 214), (W - 40, 214)], fill=(203, 213, 225), width=2)

    # Artikul (Chizmadagi ostiga chizilgan qator)
    draw.text((right_x, 228), "ARTIKUL:", fill=(100, 116, 139), font=font_small)
    font_art = get_fitted_font(draw, art_code.upper(), right_w, font_bold_path, initial_size=32, min_size=22)
    draw.text((right_x, 256), art_code.upper()[:28], fill=(15, 23, 42), font=font_art)
    draw.line([(right_x, 302), (W - 40, 302)], fill=(203, 213, 225), width=2)

    # Bo'lak (Split) agar mavjud bo'lsa
    if ticket.total_splits > 1:
        draw.text((right_x, 320), f"BO'LAK (SPLIT): {ticket.split_index} / {ticket.total_splits}", fill=(79, 70, 229), font=font_medium)

    # Pastki Kataklar (Soni, Qiyinlik, Razmer) - STIKER ID bilan bir xil chiziqda pastda
    boxes_y = stiker_y
    box_h = stiker_h
    diff_val = ticket.article_operation.difficulty_display if ticket.article_operation else "1"
    razmer_val = ticket.box.razmer.strip() if ticket.box and ticket.box.razmer else ""
    pastal_val = (ticket.box.pastal_number or "").strip() if ticket.box else ""

    font_box_val = ImageFont.truetype(font_bold_path, 30) if font_bold_path else ImageFont.load_default()

    # Ro'yxatdagi kataklar: (label, value, suffix, is_purple)
    badges = [
        ('SONI:', f"{ticket.quantity}", 'dona', False),
        ('QIYINLIK:', f"{diff_val}", '', False),
    ]
    if razmer_val:
        badges.append(('RAZMER:', razmer_val, '', True))
    if pastal_val:
        badges.append(('PASTAL CODE:', pastal_val, '', False))

    b_count = len(badges)
    b_gap = 10 if b_count >= 4 else 12
    b_w = (right_w - b_gap * (b_count - 1)) // b_count

    for i, (b_label, b_val, b_suf, is_purp) in enumerate(badges):
        bx = right_x + i * (b_w + b_gap)
        bw_cur = b_w if i < b_count - 1 else (W - 40 - bx)
        if is_purp:
            draw.rounded_rectangle([bx, boxes_y, bx + bw_cur, boxes_y + box_h], radius=10, fill=(243, 232, 255), outline=(107, 33, 168), width=2)
            draw.text((bx + 8, boxes_y + 8), b_label, fill=(107, 33, 168), font=font_code)
            font_v = get_fitted_font(draw, b_val, bw_cur - 16, font_bold_path, initial_size=30, min_size=18)
            draw.text((bx + 8, boxes_y + 26), b_val, fill=(88, 28, 135), font=font_v)
        else:
            draw.rounded_rectangle([bx, boxes_y, bx + bw_cur, boxes_y + box_h], radius=10, fill=(248, 250, 252), outline=(15, 23, 42), width=2)
            draw.text((bx + 8, boxes_y + 8), b_label, fill=(100, 116, 139), font=font_code)
            font_v = get_fitted_font(draw, b_val, bw_cur - (42 if b_suf else 16), font_bold_path, initial_size=30, min_size=18)
            draw.text((bx + 8, boxes_y + 26), b_val, fill=(15, 23, 42), font=font_v)
            if b_suf:
                draw.text((bx + bw_cur - 38, boxes_y + 36), b_suf, fill=(100, 116, 139), font=font_code)

    # 5. PASTKI QISM (Footer): TERRY JAR, QUTI ID (Zakaz o'rnida), va Kod
    draw.line([(35, 642), (W - 35, 642)], fill=(226, 232, 240), width=2)
    draw.text((42, 656), "TERRY JAR", fill=(15, 23, 42), font=font_badge)

    # Quti ID badge markazda
    box_num = ticket.box.box_number if ticket.box else 0
    box_code = ticket.box.box_code if ticket.box else ""
    box_badge_text = f"QUTI #{box_num} [{box_code}]"
    box_badge_w = 340
    box_badge_x = (W - box_badge_w) // 2
    draw.rounded_rectangle([box_badge_x, 648, box_badge_x + box_badge_w, 694], radius=8, fill=(254, 243, 199), outline=(15, 23, 42), width=2)
    font_badge_box = get_fitted_font(draw, box_badge_text, box_badge_w - 20, font_bold_path, initial_size=24, min_size=18)
    draw.text((box_badge_x + 18, 658), box_badge_text, fill=(15, 23, 42), font=font_badge_box)

    short_code = getattr(ticket, 'stiker_code', None) or ticket.short_hash or ticket.ticket_code[-12:]
    draw.text((W - 220, 656), f"#{short_code}", fill=(100, 116, 139), font=font_code)

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

