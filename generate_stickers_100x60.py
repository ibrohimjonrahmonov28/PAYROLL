import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from PIL import Image, ImageDraw, ImageFont
from accounts.models import Worker, User


def get_fitted_font(draw, text, max_w, font_path, initial_size=46, min_size=26):
    """Matn kengligi max_w dan oshib ketmasligi uchun font o'lchamini dinamik moslash"""
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


def create_worker_sticker_image(worker: Worker, font_bold_path: str, font_reg_path: str) -> Image.Image:
    """
    Bitta xodim uchun 100mm x 60mm (1181 x 709 px, 300 DPI) o'lchamli yuqori sifatli stiker rasmi yaratish.
    """
    W = 1181  # 100 mm at 300 DPI
    H = 709   # 60 mm at 300 DPI

    img = Image.new('RGB', (W, H), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(font_bold_path, 34)
        font_small = ImageFont.truetype(font_reg_path, 20)
        font_medium = ImageFont.truetype(font_reg_path, 24)
        font_uid = ImageFont.truetype(font_bold_path, 44)
        font_badge = ImageFont.truetype(font_bold_path, 22)
    except Exception:
        font_title = font_small = font_medium = font_uid = font_badge = ImageFont.load_default()

    # 1. Tashqi chegara (Outer solid border)
    draw.rounded_rectangle([20, 20, W - 20, H - 20], radius=22, fill='white', outline=(15, 23, 42), width=4)

    # 2. Yuqori sarlavha (Header)
    draw.text((45, 34), "TIKUVCHILIK FABRIKASI", fill=(15, 23, 42), font=font_title)
    draw.text((45, 76), "ISHLAB CHIQARISH • XODIM IDENTIFIKATSIYASI", fill=(100, 116, 139), font=font_small)

    # Rol nishoni (Role Badge)
    role_w, role_h = 230, 40
    rx = W - 45 - role_w
    draw.rounded_rectangle([rx, 34, rx + role_w, 34 + role_h], radius=10, fill=(209, 250, 229), outline=(16, 185, 129), width=2)
    draw.text((rx + 22, 42), "TIKUVCHI / XODIM", fill=(6, 95, 70), font=font_badge)

    # Ajratuvchi chiziq (Header divider)
    draw.line([(45, 116), (W - 45, 116)], fill=(226, 232, 240), width=2)

    # 3. QR Kod (O'ng ustun)
    qr_box_size = 440
    qr_x = W - 45 - qr_box_size
    qr_y = 135
    draw.rounded_rectangle([qr_x, qr_y, qr_x + qr_box_size, qr_y + qr_box_size], radius=18, fill='white', outline=(15, 23, 42), width=4)

    # QR kod faylini topish va joylashtirish
    qr_img = None
    if worker.user and worker.user.qr_code and os.path.exists(worker.user.qr_code.path):
        qr_img = Image.open(worker.user.qr_code.path)
    elif worker.qr_code and os.path.exists(worker.qr_code.path):
        qr_img = Image.open(worker.qr_code.path)

    if qr_img:
        qr_inner_pad = 16
        qr_res = qr_img.resize((qr_box_size - qr_inner_pad * 2, qr_box_size - qr_inner_pad * 2), Image.Resampling.LANCZOS)
        img.paste(qr_res, (qr_x + qr_inner_pad, qr_y + qr_inner_pad))

    # QR ostidagi yozuv
    qr_sub_text = "ZEBRA / TERMINAL QR"
    draw.text((qr_x + 105, qr_y + qr_box_size + 8), qr_sub_text, fill=(100, 116, 139), font=font_small)

    # 4. Xodim ma'lumotlari (Chap ustun)
    info_x = 45
    max_name_w = qr_x - info_x - 30  # Chap blok uchun maksimal kenglik

    # Xodim ID nishoni (Tabel raqami)
    wid_text = f"ID: {worker.worker_id}"
    wid_w = 175
    draw.rounded_rectangle([info_x, 135, info_x + wid_w, 135 + 40], radius=8, fill=(241, 245, 249), outline=(203, 213, 225), width=2)
    draw.text((info_x + 18, 142), wid_text, fill=(30, 41, 59), font=font_badge)

    # Xodim Familiyasi va Ismi (Katta, aniq va sig'adigan qilib)
    last_name_str = worker.last_name.upper() if worker.last_name else ""
    first_name_str = worker.first_name.upper() if worker.first_name else ""

    font_lname = get_fitted_font(draw, last_name_str, max_name_w, font_bold_path, initial_size=46, min_size=28)
    font_fname = get_fitted_font(draw, first_name_str, max_name_w, font_bold_path, initial_size=46, min_size=28)

    draw.text((info_x, 192), last_name_str, fill=(15, 23, 42), font=font_lname)
    draw.text((info_x, 246), first_name_str, fill=(15, 23, 42), font=font_fname)

    # UID Bloki (Qora fonda sariq matn, juda yuqori kontrast)
    uid_str = worker.user.uid if worker.user and worker.user.uid else "000000"
    uid_box_y = 325
    uid_w = min(460, max_name_w)
    uid_h = 76
    draw.rounded_rectangle([info_x, uid_box_y, info_x + uid_w, uid_box_y + uid_h], radius=16, fill=(15, 23, 42), outline=(15, 23, 42), width=2)
    draw.text((info_x + 25, uid_box_y + 14), f"UID:  {uid_str}", fill=(251, 191, 36), font=font_uid)

    # Username va Skanerlash yo'riqnomasi
    user_handle = f"@{worker.user.username if worker.user else worker.worker_id.lower()}"
    draw.text((info_x, 425), user_handle, fill=(100, 116, 139), font=font_medium)
    draw.text((info_x, 468), "Skanerlash uchun terminalga tuting", fill=(148, 163, 184), font=font_small)

    # 5. Pastki qism (Footer)
    draw.line([(45, 620), (W - 45, 620)], fill=(226, 232, 240), width=2)
    draw.text((45, 638), "XODIM BIRKASI • STANDART 100x60 MM", fill=(148, 163, 184), font=font_small)
    draw.text((W - 250, 638), f"USER: {uid_str}", fill=(100, 116, 139), font=font_small)

    return img


def generate_stickers_100x60_pdf(output_path: str = None, workers_qs = None) -> str:
    """
    100mm x 60mm stikerlar bo'yicha ko'p sahifali PDF yaratish.
    Har bir sahifa aynan bitta 100x60 mm stiker bo'ladi.
    """
    if output_path is None:
        output_path = "/Users/macbookpro/Desktop/PAYROLL/STIKER_100x60_ISHCHILAR_QR.pdf"

    font_bold = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
    font_reg = '/System/Library/Fonts/Supplemental/Arial.ttf'

    if workers_qs is None:
        # Standart: yangi qo'shilgan va barcha faol ishchilar
        workers = list(Worker.objects.filter(is_active=True).select_related('user').order_by('worker_id'))
    else:
        workers = list(workers_qs.select_related('user').order_by('worker_id'))

    print(f"100x60 mm stikerlar tayyorlanmoqda: {len(workers)} ta xodim...")

    pages = []
    for w in workers:
        # UID va QR kod mavjudligini ta'minlash
        if w.user:
            if not w.user.uid:
                from accounts.models import generate_unique_user_uid
                w.user.uid = generate_unique_user_uid()
                w.user.save(update_fields=['uid'])
            if not w.user.qr_code or not os.path.exists(w.user.qr_code.path):
                w.user.generate_qr_code()
                w.user.save(update_fields=['qr_code'])

        sticker_img = create_worker_sticker_image(w, font_bold, font_reg)
        pages.append(sticker_img)

    if not pages:
        blank = Image.new('RGB', (1181, 709), 'white')
        pages.append(blank)

    # 300 DPI bilan PDF formatida saqlash (Har bir varaq aynan 100mm x 60mm)
    pages[0].save(
        output_path,
        'PDF',
        resolution=300.0,
        save_all=True,
        append_images=pages[1:]
    )
    print(f"PDF muvaffaqiyatli saqlandi: {output_path} ({len(pages)} sahifa)")
    return output_path


if __name__ == '__main__':
    generate_stickers_100x60_pdf()
