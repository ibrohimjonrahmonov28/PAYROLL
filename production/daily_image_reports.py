import io
import os
import datetime
import httpx
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont

from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q

from production.models import Ticket, Box, BoxQualityInspectionLog, Article, DailyPatokProgress
from production.services import get_patok_code, get_patok_name


def _get_font(size: int = 14, bold: bool = False):
    """
    Turli OS va muhitlar (macOS, Linux, Docker Debian) uchun mos shriftni topish.
    Agar tizimda TrueType shrift topilmasa, Pillow'ning o'rnatilgan shriftidan foydalanadi.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "DejaVuSans.ttf",
        "Arial.ttf"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def get_branch_patoks_data(branch: str, target_date: datetime.date = None) -> dict:
    """
    Ko'rsatilgan filial bo'yicha kunlik patoklar hisobot ma'lumotlarini to'playdi:
    - branch: 'HQ' (Bosh bino / Kosonsoy, 1..13 patoklar) yoki 'UYCHI' (Uychi, 14.. patoklar)
    - target_date: hisobot sanasi (default: bugun)
    """
    if target_date is None:
        target_date = timezone.localdate()

    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), tz)
    now = timezone.localtime()
    
    if target_date == now.date():
        day_end = timezone.now()
    else:
        day_end = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time(23, 59, 59, 999999)), tz)

    # 1. Filialga tegishli patoklar raqamini aniqlash
    is_hq = (str(branch).upper() == 'HQ' or 'BOSH' in str(branch).upper())
    if is_hq:
        branch_key = 'HQ'
        branch_name = "BOSH BINO (KOSONSOY)"
        screen_numbers = list(range(1, 14))  # 1..13
    else:
        branch_key = 'UYCHI'
        branch_name = "UYCHI FILIALI"
        # Uychi uchun faol ekranlarni aniqlash (kamida 14..18)
        max_active = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__range=(day_start, day_end),
            screen_number__gte=14
        ).values_list('screen_number', flat=True).order_by('-screen_number').first() or 18
        max_screen = max(18, max_active)
        screen_numbers = list(range(14, max_screen + 1))

    # 2. Shu kunda skanerlangan barcha biletlar
    tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        screen_number__in=screen_numbers
    ).select_related(
        'article_operation__operation',
        'article_operation__article',
        'box'
    )

    tickets_by_screen = {}
    box_ids_by_screen = {}
    for t in tickets_qs:
        tickets_by_screen.setdefault(t.screen_number, []).append(t)
        if t.box_id:
            box_ids_by_screen.setdefault(t.screen_number, set()).add(t.box_id)

    # 3. OTK jurnallarini olish
    all_box_ids = set()
    for bids in box_ids_by_screen.values():
        all_box_ids.update(bids)

    logs_by_box = {}
    if all_box_ids:
        inspection_logs = BoxQualityInspectionLog.objects.filter(
            box_id__in=all_box_ids,
            created_at__range=(day_start, day_end)
        )
        for log in inspection_logs:
            logs_by_box.setdefault(log.box_id, []).append(log)

    # 4. Har bir patok bo'yicha hisoblash
    patoks_list = []
    total_norm = 0
    total_dazmol = 0
    total_controlled = 0

    for sn in screen_numbers:
        p_code = get_patok_code(sn)
        p_name = get_patok_name(sn)
        t_list = tickets_by_screen.get(sn, [])
        sn_box_ids = box_ids_by_screen.get(sn, set())

        # Kunlik norma
        art_ids = {t.article_operation.article_id for t in t_list if t.article_operation and t.article_operation.article_id}
        sn_norms = []
        if art_ids:
            for art in Article.objects.filter(id__in=art_ids):
                norm = art.daily_norm or (art.model.daily_norm if art.model else 0) or 1200
                if norm > 0:
                    sn_norms.append(norm)
        patok_norm = int(sum(sn_norms) / len(sn_norms)) if sn_norms else 1200

        # Dazmoldan chiqqan soni
        dazmol_qty = 0
        for t in t_list:
            ao = t.article_operation
            op_name = ao.operation.name.upper() if ao and ao.operation else ""
            op_code = (ao.operation.code or "").upper() if ao and ao.operation else ""
            if 'DAZMOL' in op_name or 'DAZMOL' in op_code:
                dazmol_qty += t.quantity

        # Kontroldan (OTK) o'tgan soni (1-sort)
        controlled_qty = 0
        for bid in sn_box_ids:
            for l in logs_by_box.get(bid, []):
                controlled_qty += (l.first_sort_qty or 0)

        # Foiz
        pct = round((dazmol_qty / patok_norm * 100), 1) if patok_norm > 0 else 0.0

        patok_item = {
            'screen_number': sn,
            'patok_code': p_code,
            'patok_name': p_name,
            'daily_norm': patok_norm,
            'dazmol_qty': dazmol_qty,
            'controlled_qty': controlled_qty,
            'percent': pct,
            'is_active': (len(t_list) > 0 or dazmol_qty > 0 or controlled_qty > 0)
        }
        patoks_list.append(patok_item)

        total_norm += patok_norm
        total_dazmol += dazmol_qty
        total_controlled += controlled_qty

        # DailyPatokProgress ga sinxronlash
        try:
            DailyPatokProgress.objects.update_or_create(
                screen_number=sn,
                date=target_date,
                defaults={
                    'patok_code': p_code,
                    'daily_norm': patok_norm,
                    'completed_units': dazmol_qty,
                    'completion_percentage': Decimal(str(pct)),
                    'dazmol_qty': dazmol_qty,
                    'controlled_qty': controlled_qty,
                    'is_completed': (dazmol_qty >= patok_norm)
                }
            )
        except Exception:
            pass

    avg_branch_pct = round((total_dazmol / total_norm * 100), 1) if total_norm > 0 else 0.0

    return {
        'branch_key': branch_key,
        'branch_name': branch_name,
        'target_date': target_date,
        'patoks': patoks_list,
        'total_norm': total_norm,
        'total_dazmol': total_dazmol,
        'total_controlled': total_controlled,
        'avg_percent': avg_branch_pct,
    }


def generate_branch_report_image(branch_data: dict) -> io.BytesIO:
    """
    Pillow kutubxonasi orqali filial hisoboti uchun yuqori sifatli (1080px kenglikda)
    zamonaviy korporativ dizayndagi PNG rasm chizadi.
    """
    branch_key = branch_data['branch_key']
    branch_name = branch_data['branch_name']
    target_date = branch_data['target_date']
    patoks = branch_data['patoks']
    total_norm = branch_data['total_norm']
    total_dazmol = branch_data['total_dazmol']
    total_controlled = branch_data['total_controlled']
    avg_percent = branch_data['avg_percent']

    # Ochiq rangli (Light Theme) dizayn sxemasi
    is_hq = (branch_key == 'HQ')

    # Asosiy aksent ranglar
    header_accent = (67, 56, 202) if is_hq else (13, 148, 136)       # Indigo (HQ) yoki Teal (Uychi)
    badge_accent_bg = (238, 242, 255) if is_hq else (240, 253, 250)
    bg_color = (244, 246, 250)                                       # Ochiq silliq fon (#F4F6FA)
    card_bg = (255, 255, 255)                                        # Oq fon (#FFFFFF)
    row_alt_bg = (248, 250, 252)                                     # Yengil chiziqli fon (#F8FAFC)
    border_color = (226, 232, 240)                                   # Yengil hoshiya (#E2E8F0)
    text_dark = (15, 23, 42)                                         # To'q qora-ko'k matn (#0F172A)
    text_muted = (100, 116, 139)                                     # Kulrang matn (#64748B)
    text_dazmol = (5, 150, 105)                                       # To'q yashil (#059669)
    text_otk = (2, 132, 199)                                         # To'q moviy (#0284C7)
    total_bg = (238, 242, 255) if is_hq else (240, 253, 250)

    # Shriftlar
    font_brand = _get_font(13, bold=True)
    font_title = _get_font(24, bold=True)
    font_subtitle = _get_font(13, bold=False)
    font_badge = _get_font(14, bold=True)
    font_header = _get_font(13, bold=True)
    font_row = _get_font(14, bold=False)
    font_row_bold = _get_font(14, bold=True)
    font_total = _get_font(15, bold=True)
    font_footer = _get_font(11, bold=False)

    width = 1080
    header_height = 130
    th_height = 44
    row_height = 38
    total_height = 54
    footer_height = 42

    height = header_height + th_height + (len(patoks) * row_height) + total_height + footer_height + 40

    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # 1. TASHQI RAMKA VA HEADER (Ochiq oq karta)
    padding = 24
    content_width = width - (padding * 2)

    # Header qutisi (Oq fon, chiroyli soya chegarasi)
    draw.rounded_rectangle(
        [(padding, padding), (width - padding, padding + header_height - 10)],
        radius=16,
        fill=card_bg,
        outline=header_accent,
        width=2
    )

    # Header bezak yorlig'i (Brand badge)
    draw.rounded_rectangle(
        [(padding + 20, padding + 16), (padding + 235, padding + 40)],
        radius=8,
        fill=badge_accent_bg,
        outline=header_accent,
        width=1
    )
    draw.text((padding + 32, padding + 20), "TERRY JAR MANUFACTURING", fill=header_accent, font=font_brand)

    # Sarlavha (To'q matn)
    draw.text((padding + 20, padding + 48), branch_name, fill=text_dark, font=font_title)
    sub_text = f"Sana: {target_date.strftime('%d.%m.%Y')}   |   Vaqt: 18:00 (O'zbekiston)   |   Holat: Kunlik Hisobot"
    draw.text((padding + 20, padding + 85), sub_text, fill=text_muted, font=font_subtitle)

    # Header o'ng tomon: Filial o'rtacha foizi ko'rsatkichi
    kpi_box_w = 210
    kpi_box_x = width - padding - kpi_box_w - 20
    draw.rounded_rectangle(
        [(kpi_box_x, padding + 18), (kpi_box_x + kpi_box_w, padding + header_height - 28)],
        radius=12,
        fill=badge_accent_bg,
        outline=header_accent,
        width=1
    )
    draw.text((kpi_box_x + 18, padding + 26), "FILIAL KPI (O'RTACHA)", fill=text_muted, font=font_footer)
    draw.text((kpi_box_x + 18, padding + 46), f"{avg_percent}%", fill=text_dazmol, font=_get_font(28, bold=True))

    # 2. JADVAL QUTISI (Oq fon)
    table_top = padding + header_height + 6
    table_bottom = table_top + th_height + (len(patoks) * row_height) + total_height
    draw.rounded_rectangle(
        [(padding, table_top), (width - padding, table_bottom)],
        radius=16,
        fill=card_bg,
        outline=border_color,
        width=1
    )

    # Ustun koordinatalari (X pozitsiyalar)
    col_num_x = padding + 20
    col_patok_x = padding + 65
    col_norma_x = padding + 340
    col_dazmol_x = padding + 550
    col_otk_x = padding + 760
    col_pct_x = width - padding - 40

    # Jadval Header qatori (Ochiq kulrang fon)
    draw.rectangle([(padding + 1, table_top + 1), (width - padding - 1, table_top + th_height)], fill=(241, 245, 249))
    draw.line([(padding, table_top + th_height), (width - padding, table_top + th_height)], fill=border_color, width=1)

    draw.text((col_num_x, table_top + 13), "#", fill=text_muted, font=font_header)
    draw.text((col_patok_x, table_top + 13), "PATOK NOMI", fill=(71, 85, 105), font=font_header)
    draw.text((col_norma_x, table_top + 13), "KUNLIK NORMA", fill=(71, 85, 105), font=font_header, anchor="ra")
    draw.text((col_dazmol_x, table_top + 13), "DAZMOLDAN CHIQQAN", fill=(71, 85, 105), font=font_header, anchor="ra")
    draw.text((col_otk_x, table_top + 13), "KONTROL (OTK)", fill=(71, 85, 105), font=font_header, anchor="ra")
    draw.text((col_pct_x, table_top + 13), "BAJARILISH (%)", fill=(71, 85, 105), font=font_header, anchor="ra")

    # 3. PATOKLAR QATORLARI (Oq / yengil ochiq kulrang chiziqlar)
    cur_y = table_top + th_height
    for idx, p in enumerate(patoks, 1):
        row_bg = row_alt_bg if (idx % 2 == 0) else card_bg
        draw.rectangle([(padding + 1, cur_y), (width - padding - 1, cur_y + row_height)], fill=row_bg)
        draw.line([(padding, cur_y + row_height), (width - padding, cur_y + row_height)], fill=(241, 245, 249), width=1)

        y_text = cur_y + 10

        # #
        draw.text((col_num_x, y_text), str(idx), fill=text_muted, font=font_row)
        # Patok nomi
        p_name = p['patok_name']
        draw.text((col_patok_x, y_text), p_name, fill=text_dark, font=font_row_bold)
        # Norma
        draw.text((col_norma_x, y_text), f"{p['daily_norm']:,}".replace(",", " "), fill=text_muted, font=font_row, anchor="ra")
        # Dazmol
        d_val = f"{p['dazmol_qty']:,}".replace(",", " ")
        draw.text((col_dazmol_x, y_text), d_val, fill=text_dazmol, font=font_row_bold, anchor="ra")
        # OTK
        otk_val = f"{p['controlled_qty']:,}".replace(",", " ")
        draw.text((col_otk_x, y_text), otk_val, fill=text_otk, font=font_row_bold, anchor="ra")

        # Foiz nishoni (Badge - Ochiq fonda mos ranglar)
        pct = p['percent']
        pct_str = f"{pct:.1f}%"
        if pct >= 100:
            badge_bg = (209, 250, 229)
            badge_fg = (6, 95, 70)
        elif pct >= 80:
            badge_bg = (220, 252, 231)
            badge_fg = (22, 101, 52)
        elif pct >= 50:
            badge_bg = (254, 243, 199)
            badge_fg = (146, 64, 14)
        else:
            badge_bg = (255, 228, 230) if p['is_active'] else (241, 245, 249)
            badge_fg = (159, 18, 57) if p['is_active'] else text_muted

        badge_w = 70
        badge_h = 24
        bx1 = col_pct_x - badge_w
        by1 = cur_y + 7
        draw.rounded_rectangle([(bx1, by1), (bx1 + badge_w, by1 + badge_h)], radius=6, fill=badge_bg)
        draw.text((bx1 + (badge_w // 2), by1 + 4), pct_str, fill=badge_fg, font=font_badge, anchor="mt")

        cur_y += row_height

    # 4. JAMI / O'RTACHA QATORI (Ochiq aksentli fon)
    total_y = cur_y
    draw.rectangle([(padding + 1, total_y), (width - padding - 1, total_y + total_height - 1)], fill=total_bg)
    draw.line([(padding, total_y), (width - padding, total_y)], fill=header_accent, width=2)

    ty_text = total_y + 16
    draw.text((col_num_x, ty_text), "Σ", fill=header_accent, font=font_total)
    draw.text((col_patok_x, ty_text), "JAMI / O'RTACHA:", fill=text_dark, font=font_total)
    draw.text((col_norma_x, ty_text), f"{total_norm:,}".replace(",", " "), fill=text_dark, font=font_total, anchor="ra")
    draw.text((col_dazmol_x, ty_text), f"{total_dazmol:,} dona".replace(",", " "), fill=text_dazmol, font=font_total, anchor="ra")
    draw.text((col_otk_x, ty_text), f"{total_controlled:,} dona".replace(",", " "), fill=text_otk, font=font_total, anchor="ra")

    # Umumiy foiz nishoni (Aksent rangli tugma)
    tot_badge_w = 80
    tot_badge_h = 28
    tbx1 = col_pct_x - tot_badge_w
    tby1 = total_y + 13
    draw.rounded_rectangle([(tbx1, tby1), (tbx1 + tot_badge_w, tby1 + tot_badge_h)], radius=8, fill=header_accent)
    draw.text((tbx1 + (tot_badge_w // 2), tby1 + 5), f"{avg_percent:.1f}%", fill=(255, 255, 255), font=_get_font(15, bold=True), anchor="mt")

    # 5. FOOTER QISMI (Ochiq mayda matn)
    foot_y = table_bottom + 14
    draw.text(
        (padding + 8, foot_y),
        "Ushbu hisobot Terry Jar ERP tizimi tomonidan har kuni soat 18:00 da avtomatik tarzda shakllantiriladi.",
        fill=text_muted,
        font=font_footer
    )
    draw.text(
        (width - padding - 8, foot_y),
        "terryjar.uz",
        fill=text_muted,
        font=font_footer,
        anchor="ra"
    )

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


def send_single_branch_report(branch: str, target_date: datetime.date = None, chat_id: str = None, bot_token: str = None) -> dict:
    """
    Muayyan filial (HQ yoki UYCHI) uchun kunlik hisobot rasmini generatsiya qilib,
    tegishli Telegram guruhiga yuboruvchi funksiya.
    """
    if target_date is None:
        target_date = timezone.localdate()

    is_hq = (str(branch).upper() == 'HQ' or 'BOSH' in str(branch).upper())
    branch_key = 'HQ' if is_hq else 'UYCHI'

    # Bot token
    token = bot_token or getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or os.getenv('BOT_TOKEN', '') or '8706205290:AAGYsyaQHmx1n5EVo9I3T9Ecjz4Xyz_xEZg'
    if not token:
        return {'success': False, 'message': "Telegram BOT_TOKEN sozlanmagan!"}

    # Chat ID
    if chat_id:
        target_chat_id = str(chat_id)
    else:
        if is_hq:
            target_chat_id = str(getattr(settings, 'TELEGRAM_HQ_REPORT_CHAT_ID', '-2111243086') or os.getenv('TELEGRAM_HQ_REPORT_CHAT_ID', '-2111243086'))
        else:
            target_chat_id = str(getattr(settings, 'TELEGRAM_UYCHI_REPORT_CHAT_ID', '-2697507612') or os.getenv('TELEGRAM_UYCHI_REPORT_CHAT_ID', '-2697507612'))

    if not target_chat_id:
        return {'success': False, 'message': f"{branch_key} filiali uchun Telegram Chat ID topilmadi!"}

    # Ma'lumotlarni yig'ish va rasm chizish
    try:
        data = get_branch_patoks_data(branch_key, target_date)
        img_buffer = generate_branch_report_image(data)
    except Exception as e:
        return {'success': False, 'message': f"Rasm hisobotini shakllantirishda xatolik: {str(e)}"}

    # Telegram caption matni
    b_name = data['branch_name']
    tot_dazmol = f"{data['total_dazmol']:,}".replace(",", " ")
    tot_otk = f"{data['total_controlled']:,}".replace(",", " ")
    tot_norm = f"{data['total_norm']:,}".replace(",", " ")
    avg_pct = data['avg_percent']

    caption = (
        f"📊 *TERRY JAR — KUNLIK ISHLAB CHIQARISH HISOBOTI*\n"
        f"🏢 *Filial:* {b_name}\n"
        f"📅 *Sana:* {target_date.strftime('%d.%m.%Y')} (18:00 holatiga)\n\n"
        f"📈 *Filial o'rtacha foizi:* *{avg_pct}%*\n"
        f"👕 *Dazmoldan chiqqan:* *{tot_dazmol} dona*\n"
        f"✅ *Kontroldan o'tgan (OTK):* *{tot_otk} dona*\n"
        f"🎯 *Kunlik reja (Norma):* {tot_norm} dona\n\n"
        f"📌 _Barcha patoklar bo'yicha batafsil tafsilotlar yuqoridagi rasmda aks etgan._"
    )

    filename = f"Hisobot_{branch_key}_{target_date.strftime('%Y_%m_%d')}.png"

    # Telegram API orqali rasm yuborish (Superguruh va oddiy guruh ID fallback bilan)
    chat_candidates = [target_chat_id]
    if not target_chat_id.startswith("-100") and target_chat_id.startswith("-"):
        chat_candidates.append(f"-100{target_chat_id[1:]}")
    elif target_chat_id.startswith("-100"):
        chat_candidates.append(f"-{target_chat_id[4:]}")

    api_url = f"https://api.telegram.org/bot{token}/sendPhoto"
    last_error = ""

    for cid in chat_candidates:
        files = {'photo': (filename, img_buffer.getvalue(), 'image/png')}
        payload = {
            'chat_id': cid,
            'caption': caption,
            'parse_mode': 'Markdown'
        }
        try:
            with httpx.Client(timeout=45.0) as client:
                res = client.post(api_url, data=payload, files=files)
                res_data = res.json()
                if res.status_code == 200 and res_data.get('ok'):
                    return {
                        'success': True,
                        'branch': branch_key,
                        'chat_id': cid,
                        'message': f"{b_name} hisobot rasmi Telegram guruhga ({cid}) muvaffaqiyatli yuborildi."
                    }
                else:
                    last_error = res_data.get('description', 'Noma\'lum xatolik')
        except Exception as ex:
            last_error = str(ex)

    return {
        'success': False,
        'branch': branch_key,
        'chat_id': target_chat_id,
        'message': f"Telegramga rasm yuborishda xatolik: {last_error}"
    }


def send_daily_1800_reports(target_date: datetime.date = None) -> dict:
    """
    Har kuni soat 18:00 da ikkala filial uchun alohida-alohida hisobot rasmlarini
    o'zlarining tegishli guruhlariga jo'natish:
    1. Bosh bino -> TELEGRAM_HQ_REPORT_CHAT_ID (-2111243086)
    2. Uychi filiali -> TELEGRAM_UYCHI_REPORT_CHAT_ID (-2697507612)
    """
    if target_date is None:
        target_date = timezone.localdate()

    res_hq = send_single_branch_report('HQ', target_date)
    res_uychi = send_single_branch_report('UYCHI', target_date)

    return {
        'target_date': str(target_date),
        'HQ': res_hq,
        'UYCHI': res_uychi,
        'success': (res_hq.get('success') or res_uychi.get('success'))
    }
