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


def render_single_box_ticket_65x45(ticket, font_bold_path: str = None, font_reg_path: str = None) -> Image.Image:
    """
    Foydalanuvchi talabi bo'yicha 65mm x 45mm (768 x 531 px, 300 DPI) termal stiker rasmini chizish:
    - Yuqori sarlavha: Butun eni bo'ylab katta va aniq "OPERATSIYA NOMI"
    - Asosiy qism chapda: Katta unikal QR kod (320px) + ostida to'q fonda katta "STIKER ID" katagi
    - Asosiy qism o'ngda: Katta "QUTI ID" (sariq/amber fonda) + "Model nomi" + "Artikul" + pastda "Soni", "Razmer", "Pastal", "Qiyinlik" kataklari
    - Pastki qism: TERRY JAR brendi va qisqa kod
    """
    import qrcode
    if not font_bold_path or not font_reg_path:
        fb, fr = get_font_paths()
        font_bold_path = font_bold_path or fb
        font_reg_path = font_reg_path or fr

    W = 768  # 65 mm @ 300 DPI
    H = 531  # 45 mm @ 300 DPI

    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(font_bold_path, 30) if font_bold_path else ImageFont.load_default()
        font_qty = ImageFont.truetype(font_bold_path, 32) if font_bold_path else ImageFont.load_default()
        font_medium = ImageFont.truetype(font_reg_path, 22) if font_reg_path else ImageFont.load_default()
        font_small = ImageFont.truetype(font_bold_path, 15) if font_bold_path else ImageFont.load_default()
        font_badge = ImageFont.truetype(font_bold_path, 18) if font_bold_path else ImageFont.load_default()
        font_code = ImageFont.truetype(font_bold_path, 14) if font_bold_path else ImageFont.load_default()
    except Exception:
        font_title = font_qty = font_medium = font_small = font_badge = font_code = ImageFont.load_default()

    # 1. Tashqi ramka (Outer border)
    draw.rounded_rectangle([12, 12, W - 12, H - 12], radius=16, fill='white', outline=(15, 23, 42), width=3)

    # 2. YUQORI SARLAVHA: OPERATSIYA NOMI (Butun qator bo'ylab katta va qalin)
    header_h = 86
    draw.line([(12, header_h), (W - 12, header_h)], fill=(15, 23, 42), width=2)

    op_name = ""
    seq_num = None
    if ticket.article_operation and ticket.article_operation.operation:
        op_name = ticket.article_operation.operation.name
        seq_num = ticket.article_operation.sequence
    else:
        op_name = "Operatsiya"

    op_display = f"№{seq_num}. {op_name.upper()}" if seq_num else op_name.upper()

    # Pastal kodini olish (barcha raqamlari bilan to'liq chiqishi uchun)
    pastal_val = (ticket.box.pastal_number or "").strip() if ticket.box else ""
    if not pastal_val and ticket.box and getattr(ticket.box, 'cutting_batch_item', None) and getattr(ticket.box.cutting_batch_item, 'batch', None):
        pastal_val = (ticket.box.cutting_batch_item.batch.pastal_code or "").strip()
    pc_display = f"PC: {pastal_val}" if pastal_val else "PC: —"

    # PC badge (ong tomon yuqorida):
    font_pc = get_fitted_font(draw, pc_display, 280, font_bold_path, initial_size=24, min_size=15)
    pc_bbox = draw.textbbox((0, 0), pc_display, font=font_pc)
    pc_text_w = pc_bbox[2] - pc_bbox[0]
    pc_badge_w = max(pc_text_w + 20, 80)
    pc_badge_x = W - 24 - pc_badge_w
    pc_badge_y = 16
    pc_badge_h = 36

    draw.rounded_rectangle([pc_badge_x, pc_badge_y, pc_badge_x + pc_badge_w, pc_badge_y + pc_badge_h], radius=6, fill=(241, 245, 249), outline=(15, 23, 42), width=2)
    pc_text_y = pc_badge_y + (pc_badge_h - (pc_bbox[3] - pc_bbox[1])) // 2 - 2
    draw.text((pc_badge_x + 10, pc_text_y), pc_display, fill=(15, 23, 42), font=font_pc)

    draw.text((24, 18), "OPERATSIYA NOMI:", fill=(100, 116, 139), font=font_small)
    font_op = get_fitted_font(draw, op_display, pc_badge_x - 36, font_bold_path, initial_size=38, min_size=20)
    draw.text((24, 38), op_display, fill=(15, 23, 42), font=font_op)

    # 3. KATTA QR KOD VA KATTA STIKER ID (User talabi)
    qr_x = 22
    qr_y = 94
    qr_size = 340

    draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_size, qr_y + qr_size], radius=12, fill='white', outline=(15, 23, 42), width=2)

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

    qr_inner = 320
    qr_res = qr_img.resize((qr_inner, qr_inner), Image.Resampling.LANCZOS)
    draw_qr_x = qr_x + (qr_size - qr_inner) // 2
    draw_qr_y = qr_y + (qr_size - qr_inner) // 2
    img.paste(qr_res, (draw_qr_x, draw_qr_y))

    # STIKER ID Katagi: Katta va ko'zga tashlanadigan (User talabi)
    stiker_y = qr_y + qr_size + 6  # 440 px
    stiker_h = 75
    draw.rounded_rectangle([qr_x, stiker_y, qr_x + qr_size, stiker_y + stiker_h], radius=10, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
    draw.text((qr_x + 12, stiker_y + 8), "STIKER ID (UNIKAL):", fill=(251, 191, 36), font=font_code)

    if getattr(ticket, 'stiker_code', None):
        stiker_text = f"#{ticket.stiker_code}"
    else:
        short_hash = ticket.short_hash
        stiker_text = f"#{ticket.id} [{short_hash}]" if short_hash else f"#{ticket.id}"

    font_id = get_fitted_font(draw, stiker_text, qr_size - 24, font_bold_path, initial_size=34, min_size=20)
    draw.text((qr_x + 12, stiker_y + 30), stiker_text, fill='white', font=font_id)

    # 4. ASOSIY QISM O'NGDA: QUTI ID (Katta), MODEL NOMI, ARTIKUL, KATAKLAR
    right_x = 374
    right_w = W - 22 - right_x  # 372 px

    # QUTI ID: Katta va yorqin badge (User talabi)
    box_y = 94
    box_h = 72
    box_num = ticket.box.box_number if ticket.box else 0
    box_code = ticket.box.box_code if ticket.box else ""
    box_badge_text = f"QUTI #{box_num} [{box_code}]"

    draw.rounded_rectangle([right_x, box_y, right_x + right_w, box_y + box_h], radius=10, fill=(254, 243, 199), outline=(217, 119, 6), width=2)
    draw.text((right_x + 12, box_y + 8), "QUTI ID:", fill=(180, 83, 9), font=font_code)
    font_badge_box = get_fitted_font(draw, box_badge_text, right_w - 24, font_bold_path, initial_size=32, min_size=20)
    draw.text((right_x + 12, box_y + 30), box_badge_text, fill=(15, 23, 42), font=font_badge_box)

    art_name = ""
    art_code = ""
    if ticket.article_operation and ticket.article_operation.article:
        art_name = ticket.article_operation.article.name
        art_code = ticket.article_operation.article.code
    elif ticket.box and ticket.box.article:
        art_name = ticket.box.article.name
        art_code = ticket.box.article.code

    # Model Nomi
    draw.text((right_x + 2, 174), "MODEL NOMI:", fill=(100, 116, 139), font=font_code)
    font_model = get_fitted_font(draw, art_name.upper(), right_w - 4, font_bold_path, initial_size=26, min_size=18)
    draw.text((right_x + 2, 192), art_name.upper()[:28], fill=(15, 23, 42), font=font_model)
    draw.line([(right_x + 2, 222), (right_x + right_w, 222)], fill=(226, 232, 240), width=1)

    # Artikul
    draw.text((right_x + 2, 226), "ARTIKUL:", fill=(100, 116, 139), font=font_code)
    font_art = get_fitted_font(draw, art_code.upper(), right_w - 4, font_bold_path, initial_size=26, min_size=18)
    draw.text((right_x + 2, 244), art_code.upper()[:24], fill=(15, 23, 42), font=font_art)
    draw.line([(right_x + 2, 274), (right_x + right_w, 274)], fill=(226, 232, 240), width=1)

    # Pastki Kataklar (2 qator):
    diff_val = ticket.article_operation.difficulty_display if ticket.article_operation else "1"
    razmer_val = ticket.box.razmer.strip() if ticket.box and ticket.box.razmer else ""

    badge_h = 88
    col_gap = 10
    col_w = (right_w - col_gap) // 2

    # Qator 1: SONI | RAZMER
    r1_y = 282
    draw.rounded_rectangle([right_x, r1_y, right_x + col_w, r1_y + badge_h], radius=8, fill=(248, 250, 252), outline=(15, 23, 42), width=2)
    draw.text((right_x + 8, r1_y + 8), "SONI:", fill=(100, 116, 139), font=font_code)
    draw.text((right_x + 8, r1_y + 30), f"{ticket.quantity}", fill=(15, 23, 42), font=font_qty)
    draw.text((right_x + col_w - 44, r1_y + 60), "dona", fill=(100, 116, 139), font=font_code)

    draw.rounded_rectangle([right_x + col_w + col_gap, r1_y, right_x + right_w, r1_y + badge_h], radius=8, fill=(243, 232, 255), outline=(107, 33, 168), width=2)
    draw.text((right_x + col_w + col_gap + 8, r1_y + 8), "RAZMER:", fill=(107, 33, 168), font=font_code)
    font_rz = get_fitted_font(draw, razmer_val or "—", col_w - 16, font_bold_path, initial_size=32, min_size=18)
    draw.text((right_x + col_w + col_gap + 8, r1_y + 34), razmer_val or "—", fill=(88, 28, 135), font=font_rz)

    # Qator 2: PASTAL | QIYINLIK (yoki BO'LAK)
    r2_y = 378
    draw.rounded_rectangle([right_x, r2_y, right_x + col_w, r2_y + badge_h], radius=8, fill=(248, 250, 252), outline=(15, 23, 42), width=2)
    draw.text((right_x + 8, r2_y + 8), "PASTAL:", fill=(100, 116, 139), font=font_code)
    font_pst = get_fitted_font(draw, pastal_val or "—", col_w - 16, font_bold_path, initial_size=24, min_size=16)
    draw.text((right_x + 8, r2_y + 36), pastal_val or "—", fill=(15, 23, 42), font=font_pst)

    if ticket.total_splits > 1:
        draw.rounded_rectangle([right_x + col_w + col_gap, r2_y, right_x + right_w, r2_y + badge_h], radius=8, fill=(238, 242, 255), outline=(79, 70, 229), width=2)
        draw.text((right_x + col_w + col_gap + 8, r2_y + 8), "BO'LAK:", fill=(79, 70, 229), font=font_code)
        split_txt = f"{ticket.split_index}/{ticket.total_splits}"
        font_spl = get_fitted_font(draw, split_txt, col_w - 16, font_bold_path, initial_size=28, min_size=18)
        draw.text((right_x + col_w + col_gap + 8, r2_y + 34), split_txt, fill=(49, 46, 129), font=font_spl)
    else:
        draw.rounded_rectangle([right_x + col_w + col_gap, r2_y, right_x + right_w, r2_y + badge_h], radius=8, fill=(248, 250, 252), outline=(15, 23, 42), width=2)
        draw.text((right_x + col_w + col_gap + 8, r2_y + 8), "QIYINLIK:", fill=(100, 116, 139), font=font_code)
        draw.text((right_x + col_w + col_gap + 8, r2_y + 34), f"{diff_val}", fill=(15, 23, 42), font=font_qty)

    # 5. FOOTER: TERRY JAR va Kod
    draw.line([(right_x, 474), (right_x + right_w, 474)], fill=(226, 232, 240), width=1)
    draw.text((right_x + 2, 484), "TERRY JAR", fill=(15, 23, 42), font=font_badge)
    short_code = getattr(ticket, 'stiker_code', None) or ticket.short_hash or ticket.ticket_code[-10:]
    draw.text((right_x + right_w - 130, 486), f"#{short_code}", fill=(100, 116, 139), font=font_code)

    return img


# Backwards compatibility alias
render_single_box_ticket_100x60 = render_single_box_ticket_65x45
generate_box_stickers_65x45_pdf = None  # defined below


def generate_box_stickers_100x60_pdf(tickets_qs, output_destination=None) -> bytes:
    """
    Berilgan birkalar (tickets) uchun 65mm x 45mm stikerlar PDF faylini generatsiya qilish.
    Har bir bilet alohida 65x45 mm varaqda bo'ladi.
    """
    font_bold, font_reg = get_font_paths()

    tickets = list(tickets_qs)
    pages = []

    for t in tickets:
        img = render_single_box_ticket_65x45(t, font_bold, font_reg)
        pages.append(img)

    if not pages:
        blank = Image.new('RGB', (768, 531), 'white')
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


generate_box_stickers_65x45_pdf = generate_box_stickers_100x60_pdf

