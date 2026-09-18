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
    Foydalanuvchi chizmasi (obrazes) bo'yicha 100mm x 60mm (1181 x 709 px, 300 DPI) termal stiker rasmini chizish:
    - Yuqori sarlavha: Chapda katta "OPERATSIYA NOMI" | O'ngda "QUTI ID"
    - Asosiy qism chapda: Katta "QR code unical" + ostida unikal "STIKER ID" katagi
    - Asosiy qism o'ngda: "Model name" + "Artikul" + pastda "Son", "Qiyinlik", "Razmer" kataklari
    """
    W = 1181  # 100 mm @ 300 DPI
    H = 709   # 60 mm @ 300 DPI

    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(font_bold_path, 34) if font_bold_path else ImageFont.load_default()
        font_qty = ImageFont.truetype(font_bold_path, 48) if font_bold_path else ImageFont.load_default()
        font_medium = ImageFont.truetype(font_reg_path, 26) if font_reg_path else ImageFont.load_default()
        font_small = ImageFont.truetype(font_bold_path, 20) if font_bold_path else ImageFont.load_default()
        font_badge = ImageFont.truetype(font_bold_path, 22) if font_bold_path else ImageFont.load_default()
        font_code = ImageFont.truetype(font_bold_path, 18) if font_bold_path else ImageFont.load_default()
    except Exception:
        font_title = font_qty = font_medium = font_small = font_badge = font_code = ImageFont.load_default()

    # 1. Tashqi ramka (Outer border)
    draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=22, fill='white', outline=(15, 23, 42), width=4)

    # 2. YUQORI SARLAVHA (Chizma bo'yicha: OPERATSIYA NOMI chapda | QUTI ID o'ngda)
    header_divider_y = 125
    header_col_x = 750

    # Gorizontal ajratuvchi chiziq
    draw.line([(22, header_divider_y), (W - 22, header_divider_y)], fill=(15, 23, 42), width=3)
    # Vertikal ajratuvchi chiziq
    draw.line([(header_col_x, 22), (header_col_x, header_divider_y)], fill=(15, 23, 42), width=3)

    # Chap: OPERATSIYA NOMI
    op_name = ""
    if ticket.article_operation and ticket.article_operation.operation:
        op_name = ticket.article_operation.operation.name
    else:
        op_name = "Operatsiya"

    draw.text((45, 30), "OPERATSIYA NOMI:", fill=(100, 116, 139), font=font_small)
    font_op = get_fitted_font(draw, op_name.upper(), header_col_x - 70, font_bold_path, initial_size=38, min_size=24)
    draw.text((45, 58), op_name.upper(), fill=(15, 23, 42), font=font_op)

    # O'ng: QUTI ID
    box_num = ticket.box.box_number if ticket.box else 0
    box_code = ticket.box.box_code if ticket.box else ""
    draw.text((header_col_x + 25, 30), "QUTI ID:", fill=(100, 116, 139), font=font_small)
    box_badge_text = f"QUTI #{box_num} [{box_code}]"
    draw.rounded_rectangle([header_col_x + 25, 58, W - 45, 108], radius=8, fill=(241, 245, 249), outline=(15, 23, 42), width=2)
    draw.text((header_col_x + 38, 70), box_badge_text, fill=(15, 23, 42), font=font_badge)

    # 3. ASOSIY QISM CHAPDA: QR CODE (UNICAL) + STIKER ID (Chizma bo'yicha)
    qr_col_w = 400
    qr_box_size = 350
    qr_x = 45 + (qr_col_w - qr_box_size) // 2
    qr_y = 145

    # QR kod ramkasi
    draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_box_size, qr_y + qr_box_size], radius=16, fill='white', outline=(15, 23, 42), width=3)

    # QR kod fayli
    if not ticket.qr_code_image:
        ticket.generate_qr_code()
        ticket.save(update_fields=['qr_code_image'])

    if ticket.qr_code_image and os.path.exists(ticket.qr_code_image.path):
        qr_img = Image.open(ticket.qr_code_image.path)
        qr_pad = 12
        qr_res = qr_img.resize((qr_box_size - qr_pad * 2, qr_box_size - qr_pad * 2), Image.Resampling.LANCZOS)
        img.paste(qr_res, (qr_x + qr_pad, qr_y + qr_pad))

    # STIKER ID Katagi (QR kodning ostida, chizma bo'yicha aniq joylashuv)
    stiker_y = qr_y + qr_box_size + 14
    stiker_h = 75
    draw.rounded_rectangle([qr_x, stiker_y, qr_x + qr_box_size, stiker_y + stiker_h], radius=12, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
    draw.text((qr_x + 18, stiker_y + 8), "STIKER ID (UNIKAL):", fill=(251, 191, 36), font=font_code)

    short_hash = ticket.short_hash
    stiker_num_text = f"#{ticket.id} [{short_hash}]" if short_hash else f"#{ticket.id}"
    draw.text((qr_x + 18, stiker_y + 32), stiker_num_text, fill='white', font=font_title)

    # 4. ASOSIY QISM O'NGDA: MODEL NAME, ARTIKUL, SON, QIYINLIK, RAZMER
    right_x = 480
    right_max_w = W - 45 - right_x

    # Model Nomi
    art_name = ""
    art_code = ""
    if ticket.article_operation and ticket.article_operation.article:
        art_name = ticket.article_operation.article.name
        art_code = ticket.article_operation.article.code
    elif ticket.box and ticket.box.article:
        art_name = ticket.box.article.name
        art_code = ticket.box.article.code

    draw.text((right_x, 142), "MODEL NOMI:", fill=(100, 116, 139), font=font_small)
    font_model = get_fitted_font(draw, art_name.upper(), right_max_w, font_bold_path, initial_size=36, min_size=24)
    draw.text((right_x, 172), art_name.upper()[:36], fill=(15, 23, 42), font=font_model)
    # Chizmadagi ostiga chizilgan chiziq
    draw.line([(right_x, 222), (W - 45, 222)], fill=(226, 232, 240), width=2)

    # Artikul
    draw.text((right_x, 236), "ARTIKUL:", fill=(100, 116, 139), font=font_small)
    font_art = get_fitted_font(draw, art_code.upper(), right_max_w, font_bold_path, initial_size=32, min_size=22)
    draw.text((right_x, 266), art_code.upper()[:28], fill=(15, 23, 42), font=font_art)
    # Chizmadagi ostiga chizilgan chiziq
    draw.line([(right_x, 314), (W - 45, 314)], fill=(226, 232, 240), width=2)

    # Zakaz va Bo'lak
    order_num = ticket.box.order.order_number if ticket.box and ticket.box.order else "ZAKAZ"
    draw.text((right_x, 328), f"ZAKAZ: {order_num}", fill=(100, 116, 139), font=font_medium)
    if ticket.total_splits > 1:
        draw.text((right_x, 365), f"BO'LAK (SPLIT): {ticket.split_index} / {ticket.total_splits}", fill=(79, 70, 229), font=font_medium)

    # SON, QIYINLIK, RAZMER Kataklari (Chizmadagi [Son] [Qiyinlik] kataklari)
    boxes_y = 475
    box_h = 110
    diff_val = ticket.article_operation.difficulty_display if ticket.article_operation else "1"
    razmer_val = ticket.box.razmer.strip() if ticket.box and ticket.box.razmer else ""

    # Son katagi
    son_w = 200
    draw.rounded_rectangle([right_x, boxes_y, right_x + son_w, boxes_y + box_h], radius=12, fill=(248, 250, 252), outline=(15, 23, 42), width=3)
    draw.text((right_x + 14, boxes_y + 12), "SONI:", fill=(100, 116, 139), font=font_small)
    draw.text((right_x + 14, boxes_y + 40), f"{ticket.quantity}", fill=(15, 23, 42), font=font_qty)
    draw.text((right_x + 120, boxes_y + 60), "dona", fill=(100, 116, 139), font=font_small)

    # Qiyinlik katagi
    q_x = right_x + son_w + 14
    q_w = 180
    draw.rounded_rectangle([q_x, boxes_y, q_x + q_w, boxes_y + box_h], radius=12, fill=(248, 250, 252), outline=(15, 23, 42), width=3)
    draw.text((q_x + 14, boxes_y + 12), "QIYINLIK:", fill=(100, 116, 139), font=font_small)
    draw.text((q_x + 14, boxes_y + 40), f"{diff_val}", fill=(15, 23, 42), font=font_qty)

    # Razmer katagi (agar mavjud bo'lsa)
    if razmer_val:
        rz_x = q_x + q_w + 14
        rz_w = max(180, W - 45 - rz_x)
        draw.rounded_rectangle([rz_x, boxes_y, rz_x + rz_w, boxes_y + box_h], radius=12, fill=(243, 232, 255), outline=(107, 33, 168), width=3)
        draw.text((rz_x + 14, boxes_y + 12), "RAZMER:", fill=(107, 33, 168), font=font_small)
        font_rz = get_fitted_font(draw, razmer_val, rz_w - 28, font_bold_path, initial_size=42, min_size=24)
        draw.text((rz_x + 14, boxes_y + 44), razmer_val, fill=(88, 28, 135), font=font_rz)

    # 5. PASTKI QISM (Footer)
    footer_y = 635
    draw.line([(45, footer_y), (W - 45, footer_y)], fill=(226, 232, 240), width=2)
    draw.text((45, footer_y + 14), "TERRY JAR • OPERATSIYA QR BILETI • 100x60 MM", fill=(148, 163, 184), font=font_code)
    draw.text((W - 350, footer_y + 14), f"KOD: {ticket.ticket_code[-20:]}", fill=(100, 116, 139), font=font_code)

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

