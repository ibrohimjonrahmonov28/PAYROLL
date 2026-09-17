import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from PIL import Image, ImageDraw, ImageFont
from accounts.models import User, Worker

def create_a4_pdf():
    # 300 DPI A4 dimensions
    DPI = 300
    PAGE_W = 2480  # 210 mm at 300 DPI
    PAGE_H = 3508  # 297 mm at 300 DPI

    # Get the 20 newly added workers (IDs 9 to 28)
    workers = Worker.objects.filter(id__gte=8).select_related('user').order_by('worker_id')
    # Filter only those that belong to the new batch (or all active workers)
    # The 20 new workers are W-002 to W-021
    new_workers = [w for w in workers if w.worker_id.startswith('W-0') and w.worker_id != 'W-001']
    print(f"Total new workers to layout: {len(new_workers)}")

    # Font setup
    try:
        font_bold_title = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 38)
        font_bold_name = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 44)
        font_bold_name_large = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 48)
        font_medium = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 30)
        font_small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 24)
        font_uid = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 46)
        font_badge = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 26)
    except Exception as e:
        print("Falling back to default font:", e)
        font_bold_title = font_bold_name = font_bold_name_large = font_medium = font_small = font_uid = font_badge = ImageFont.load_default()

    # Layout dimensions on A4:
    # 2 columns x 4 rows
    COLS = 2
    ROWS = 4
    CARDS_PER_PAGE = COLS * ROWS

    CARD_W = 1080  # ~91.4 mm
    CARD_H = 740   # ~62.6 mm
    GAP_X = 100
    GAP_Y = 70
    MARGIN_X = (PAGE_W - (COLS * CARD_W + (COLS - 1) * GAP_X)) // 2  # centered
    MARGIN_Y = (PAGE_H - (ROWS * CARD_H + (ROWS - 1) * GAP_Y)) // 2  # centered

    pages = []
    
    # Chunk workers into pages of 8
    chunks = [new_workers[i:i + CARDS_PER_PAGE] for i in range(0, len(new_workers), CARDS_PER_PAGE)]

    for page_idx, chunk in enumerate(chunks, 1):
        # White A4 sheet
        img = Image.new('RGB', (PAGE_W, PAGE_H), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        # Page Header (for clarity on top margin)
        header_text = f"TIKUVCHILIK FABRIKASI — XODIMLAR BIRKALARI (A4 Varaq {page_idx}/{len(chunks)})"
        draw.text((MARGIN_X, MARGIN_Y - 55), header_text, fill=(120, 130, 140), font=font_small)

        for idx, w in enumerate(chunk):
            col = idx % COLS
            row = idx // COLS

            x = MARGIN_X + col * (CARD_W + GAP_X)
            y = MARGIN_Y + row * (CARD_H + GAP_Y)

            # 1. Outer Cut Border (dashed lines with scissors guide)
            # Draw subtle dashed cut rectangle
            dash_len = 15
            # Top edge
            for dx in range(0, CARD_W, dash_len * 2):
                draw.line([(x + dx, y), (x + min(dx + dash_len, CARD_W), y)], fill=(180, 190, 200), width=3)
                draw.line([(x + dx, y + CARD_H), (x + min(dx + dash_len, CARD_W), y + CARD_H)], fill=(180, 190, 200), width=3)
            # Left/Right edge
            for dy in range(0, CARD_H, dash_len * 2):
                draw.line([(x, y + dy), (x, y + min(dy + dash_len, CARD_H))], fill=(180, 190, 200), width=3)
                draw.line([(x + CARD_W, y + dy), (x + CARD_W, y + min(dy + dash_len, CARD_H))], fill=(180, 190, 200), width=3)

            # 2. Main Card Inner Body (Solid 4px black industrial border with rounded corners)
            card_padding = 18
            cx = x + card_padding
            cy = y + card_padding
            cw = CARD_W - card_padding * 2
            ch = CARD_H - card_padding * 2
            
            # Card background
            draw.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=24, fill=(255, 255, 255), outline=(15, 23, 42), width=5)

            # 3. Lanyard slot mark (teshik teshish belgisi)
            slot_w, slot_h = 160, 22
            slot_x = cx + (cw - slot_w) // 2
            slot_y = cy + 16
            draw.rounded_rectangle([slot_x, slot_y, slot_x + slot_w, slot_y + slot_h], radius=10, fill=(241, 245, 249), outline=(148, 163, 184), width=2)

            # 4. Top Header Line: Logo/Text & Role Badge
            header_y = cy + 50
            # Fabrika nomi
            draw.text((cx + 30, header_y), "TIKUVCHILIK FABRIKASI", fill=(15, 23, 42), font=font_bold_title)
            draw.text((cx + 30, header_y + 40), "ISHLAB CHIQARISH KOMPLEKSI", fill=(100, 116, 139), font=font_small)

            # Role Badge (Right side of header)
            role_text = "TIKUVCHI / XODIM"
            role_w = 260
            role_h = 44
            rx = cx + cw - role_w - 25
            draw.rounded_rectangle([rx, header_y + 4, rx + role_w, header_y + 4 + role_h], radius=10, fill=(209, 250, 229), outline=(16, 185, 129), width=2)
            draw.text((rx + 20, header_y + 12), role_text, fill=(6, 95, 70), font=font_badge)

            # Separator line
            draw.line([(cx + 25, cy + 145), (cx + cw - 25, cy + 145)], fill=(226, 232, 240), width=3)

            # 5. QR Code on the Right
            # Find QR code image
            qr_img = None
            if w.user and w.user.qr_code and os.path.exists(w.user.qr_code.path):
                qr_img = Image.open(w.user.qr_code.path)
            elif w.qr_code and os.path.exists(w.qr_code.path):
                qr_img = Image.open(w.qr_code.path)

            qr_box_size = 310
            qr_x = cx + cw - qr_box_size - 30
            qr_y = cy + 175

            # QR container frame
            draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_box_size, qr_y + qr_box_size], radius=16, fill=(255, 255, 255), outline=(15, 23, 42), width=4)

            if qr_img:
                qr_resized = qr_img.resize((qr_box_size - 24, qr_box_size - 24), Image.Resampling.LANCZOS)
                img.paste(qr_resized, (qr_x + 12, qr_y + 12))
            
            # Label under QR
            qr_label = "ZEBRA DS22 / TERMINAL"
            draw.text((qr_x + 22, qr_y + qr_box_size + 10), qr_label, fill=(71, 85, 105), font=font_small)

            # 6. Worker Information on the Left
            info_x = cx + 30
            info_y = cy + 175

            # Worker ID Badge
            wid_text = f"ID: {w.worker_id}"
            draw.rounded_rectangle([info_x, info_y, info_x + 180, info_y + 44], radius=8, fill=(241, 245, 249), outline=(203, 213, 225), width=2)
            draw.text((info_x + 16, info_y + 8), wid_text, fill=(30, 41, 59), font=font_badge)

            # Last Name & First Name (Bold, Clear)
            name_y = info_y + 60
            draw.text((info_x, name_y), w.last_name.upper(), fill=(15, 23, 42), font=font_bold_name_large)
            draw.text((info_x, name_y + 55), w.first_name.upper(), fill=(15, 23, 42), font=font_bold_name_large)

            # UID Box (High contrast Black with Amber / White)
            uid_str = w.user.uid if w.user and w.user.uid else "000000"
            uid_box_y = name_y + 130
            uid_w = 400
            uid_h = 75
            draw.rounded_rectangle([info_x, uid_box_y, info_x + uid_w, uid_box_y + uid_h], radius=14, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
            draw.text((info_x + 25, uid_box_y + 14), f"UID:  {uid_str}", fill=(251, 191, 36), font=font_uid)

            # Username
            user_handle = f"@{w.user.username if w.user else w.worker_id.lower()}"
            draw.text((info_x + 6, uid_box_y + 88), user_handle, fill=(100, 116, 139), font=font_small)

            # 7. Card Footer Line
            foot_y = cy + ch - 50
            draw.line([(cx + 25, foot_y), (cx + cw - 25, foot_y)], fill=(226, 232, 240), width=2)
            draw.text((cx + 30, foot_y + 12), "Operatsiyalarni topshirish va qabul qilish uchun", fill=(148, 163, 184), font=font_small)
            draw.text((cx + cw - 260, foot_y + 12), f"USER:{uid_str}", fill=(100, 116, 139), font=font_small)

        pages.append(img)

    # Save to high quality PDF
    output_path = "/Users/macbookpro/Desktop/PAYROLL/A4_YANGI_ISHCHILAR_BIRKALARI.pdf"
    if not pages:
        blank = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        pages.append(blank)
    pages[0].save(output_path, save_all=True, append_images=pages[1:], resolution=DPI)
    print(f"PDF muvaffaqiyatli saqlandi: {output_path} (Jami {len(pages)} sahifa)")

if __name__ == "__main__":
    create_a4_pdf()

