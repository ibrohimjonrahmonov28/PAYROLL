import io
import os
import datetime
import calendar
import httpx
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont

from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q, Count, Max

from production.models import Ticket, Box, CuttingBatch, Article, OperationGroup, OperationGroupItem, DailyPatokProgress
from production.services import get_patok_code, get_patok_name
from production.daily_dazmol import is_main_dazmol_operation, get_day_window, compute_daily_dazmol
from screens.views import get_screen_data, calculate_screen_aniqlik


def _get_font(size: int = 14, bold: bool = False):
    """
    Turli OS va muhitlar (macOS, Linux, Docker Debian) uchun mos shriftni topish.
    Kirillcha (Ў, Қ, Ғ, Ҳ) harflarini to'liq qo'llab-quvvatlaydigan shriftlar.
    """
    candidates = [
        # Loyiha ichiga yuklangan mustaqil shriftlar (Har qanday server/Dockerda 100% kafolatlangan)
        os.path.join(settings.BASE_DIR, 'production', 'fonts', 'Arial-Bold.ttf' if bold else 'Arial.ttf'),
        os.path.join(settings.BASE_DIR, 'production', 'fonts', 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
        # Linux (Debian / Ubuntu / Docker)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS tizim shriftlari
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
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


def get_operation_group_difficulty(group: OperationGroup) -> float:
    """Guruhdagi faol operatsiyalarning qiyinlik darajalari yig'indisi (Aniqlik)"""
    if not group:
        return 0.0
    items = group.items.filter(is_active=True)
    val = sum(float(it.difficulty or 1.0) for it in items)
    return round(val, 2)


def get_branch_daily_report_data(branch: str, target_date: datetime.date = None, dazmol=None) -> dict:
    """
    Kosonsoy yoki Uychi filiali bo'yicha kunlik 18:00 hisobot ma'lumotlarini to'playdi:
    5 ta ustun:
    1. Паток № (1-паток..13-паток yoki 1-паток..21-паток)
    2. Маҳсулот & Дазмол (Asosiy dazmol shablonlar bo'yicha, masalan: 329 / DC BELBOG'LI XALAT)
    3. Давомот (Real faol chevarlar soni)
    4. Операционка (Shablonning aniqlik yig'indisi)
    5. Паток қуввати (%)

    Dazmol = asosiy dazmol (production/daily_dazmol.py): patokka qabul qilingan pastallar dazmoli (planshetda qabul
    qilingan yoki Uychi omboridan U-patokka topshirilgan), qayerda dazmollangan bo'lsa ham. /sewing-statistics/daily/ dagi
    "Asosiy dazmol" bilan bir xil manba.
    dazmol: oldindan hisoblangan DailyDazmol (bir so'rovda ikki filial uchun qayta hisoblamaslik uchun).
    """
    if target_date is None:
        target_date = timezone.localdate()

    day_start, day_end = get_day_window(target_date)
    if dazmol is None:
        dazmol = compute_daily_dazmol(day_start, day_end)

    is_hq = (str(branch).upper() in ['HQ', 'KOSON', 'KOSONSOY', 'BOSH'])

    if is_hq:
        branch_key = 'HQ'
        branch_title = "КОСОНСОЙ ТУМАНИ"
        branch_name_short = "Косонсой"
        screen_numbers = list(range(1, 14))  # 1..13 (K1..K13)
    else:
        branch_key = 'UYCHI'
        branch_title = "УЙЧИ ТУМАНИ"
        branch_name_short = "Уйчи"
        # Uychida U1..U21 (14..34) doim, undan keyingi patoklar shu kuni ishlagan bo'lsa (40 gacha) qo'shiladi
        last_scanned_sn = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__range=(day_start, day_end),
            screen_number__gte=14
        ).aggregate(m=Max('screen_number'))['m'] or 0
        last_owner_sn = max((sn for sn, qty in dazmol.asosiy_by_patok.items() if sn >= 14 and qty > 0), default=0)
        max_screen = min(40, max(34, last_scanned_sn, last_owner_sn))
        screen_numbers = list(range(14, max_screen + 1))

    # Shu kuni terminalida skaner bo'lgan patoklar (faollik uchun)
    scanned_screens = set(Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        screen_number__in=screen_numbers,
    ).values_list('screen_number', flat=True).distinct())

    # Patokka qabul qilingan pastallar (dazmol bo'lmagan kuni shablon nomini ko'rsatish uchun):
    # planshet qabuli yoki Uychi omboridan topshirilgan (daily_dazmol.get_box_owner_screen bilan bir xil)
    assigned_batches = list(CuttingBatch.objects.filter(
        Q(items__boxes__is_received_by_patok=True) | Q(assigned_branch='UYCHI'),
        assigned_screen__in=screen_numbers,
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
    ).distinct().select_related(
        'order_item__article__operation_group',
        'order_item__article__model'
    ).order_by('accepted_at', 'id'))
    assigned_by_screen = {}
    for b in assigned_batches:
        assigned_by_screen.setdefault(b.assigned_screen, []).append(b)

    rows = []
    total_dazmol_sum = 0
    total_davomat_sum = 0
    quvvat_values = []
    dominant_aniqlik_sum = 0

    for sn in screen_numbers:
        patok_num = sn if is_hq else (sn - 13)
        patok_title = f"{patok_num}-паток"

        sn_assigned = assigned_by_screen.get(sn, [])

        # Ekran statistikasi (Davomat, Aniqlik, Quvvat)
        screen_data = get_screen_data(sn, target_date)
        davomat = screen_data.get('davomat', 0)
        patok_quvvati = float(screen_data.get('patok_quvvati', 0.0))

        # 1. Asosiy dazmol narxlar shablonlari bo'yicha (yagona manba: production/daily_dazmol.py)
        shablon_dazmol_map = {
            item['name']: {'group': item['group'], 'qty': item['qty']}
            for item in dazmol.templates(sn)
        }

        # 2. Mahsulotlar ro'yxatini shakllantirish (faqat haqiqiy dazmoldan o'tgan sonlar kesimida)
        products = []
        patok_dazmol_total = 0
        max_group_aniqlik = 0.0

        active_shablons = [item for item in shablon_dazmol_map.items() if item[1]['qty'] > 0]
        if active_shablons:
            # Dazmol soni bo'yicha kamayish tartibida saralaymiz
            sorted_groups = sorted(active_shablons, key=lambda x: x[1]['qty'], reverse=True)
            for g_key, g_info in sorted_groups:
                g_qty = g_info['qty']
                patok_dazmol_total += g_qty
                g_obj = g_info['group']

                if g_obj:
                    g_aniqlik = get_operation_group_difficulty(g_obj)
                else:
                    g_aniqlik = float(screen_data.get('aniqlik', 0.0))

                aniqlik_display = int(g_aniqlik) if g_aniqlik.is_integer() else round(g_aniqlik, 1)
                if g_aniqlik > max_group_aniqlik:
                    max_group_aniqlik = g_aniqlik

                products.append({
                    'shablon_name': g_key,
                    'dazmol_qty': g_qty,
                    'display_str': f"{g_qty} / {g_key}",
                    'aniqlik': aniqlik_display,
                })
        else:
            # Dazmol bo'lmagan holat: agar patokka biriktirilgan asosiy pastal bo'lsa, uning shablonini 0 dona bilan ko'rsatamiz
            # (faqat 1 ta eng oxirgi asosiy pastal olinadi)
            primary_batch = sn_assigned[-1] if sn_assigned else None
            if primary_batch and primary_batch.order_item and primary_batch.order_item.article:
                art = primary_batch.order_item.article
                group = art.operation_group
                g_key = group.name if group else (art.name if art else "Noma'lum")
                if group:
                    g_aniqlik = get_operation_group_difficulty(group)
                else:
                    g_aniqlik = float(screen_data.get('aniqlik', 0.0))
                aniqlik_display = int(g_aniqlik) if g_aniqlik.is_integer() else round(g_aniqlik, 1)
                products.append({
                    'shablon_name': g_key,
                    'dazmol_qty': 0,
                    'display_str': f"0 / {g_key}",
                    'aniqlik': aniqlik_display,
                })
                max_group_aniqlik = g_aniqlik
            else:
                g_aniqlik = float(screen_data.get('aniqlik', 0.0))
                aniqlik_display = int(g_aniqlik) if g_aniqlik.is_integer() else (round(g_aniqlik, 1) if g_aniqlik > 0 else "—")
                products.append({
                    'shablon_name': "—",
                    'dazmol_qty': 0,
                    'display_str': "0 / —",
                    'aniqlik': aniqlik_display,
                })
                max_group_aniqlik = g_aniqlik

        total_dazmol_sum += patok_dazmol_total
        total_davomat_sum += davomat
        dominant_aniqlik_sum += (max_group_aniqlik if max_group_aniqlik > 0 else float(screen_data.get('aniqlik', 0.0)))

        is_active = (sn in scanned_screens or patok_dazmol_total > 0 or davomat > 0)
        if is_active:
            quvvat_values.append(patok_quvvati)

        row_item = {
            'screen_number': sn,
            'patok_code': get_patok_code(sn),
            'patok_num': patok_num,
            'patok_title': patok_title,
            'products': products,
            'dazmol_total': patok_dazmol_total,
            'davomat': davomat,
            'patok_quvvati': patok_quvvati,
            'is_active': is_active,
        }
        rows.append(row_item)

        # DailyPatokProgress ga muhrlash (Tarix va Arxiv uchun)
        if is_active:
            DailyPatokProgress.objects.update_or_create(
                screen_number=sn,
                date=target_date,
                defaults={
                    'patok_code': get_patok_code(sn),
                    'dazmol_qty': patok_dazmol_total,
                    'davomat': davomat,
                    'aniqlik': max_group_aniqlik if max_group_aniqlik > 0 else float(screen_data.get('aniqlik', 0.0)),
                    'patok_kpi': Decimal(str(round(patok_quvvati, 2))),
                }
            )

    # Jami hisob-kitoblar
    avg_quvvat = round(sum(quvvat_values) / len(quvvat_values), 1) if quvvat_values else 0.0
    davomat_foiz = round((total_davomat_sum / dominant_aniqlik_sum * 100), 1) if dominant_aniqlik_sum > 0 else 0.0

    return {
        'branch_key': branch_key,
        'branch_title': branch_title,
        'branch_name_short': branch_name_short,
        'target_date': target_date,
        'rows': rows,
        'total_dazmol': total_dazmol_sum,
        'total_davomat': total_davomat_sum,
        'avg_quvvat': avg_quvvat,
        'davomat_foiz': davomat_foiz,
        'active_patoks_count': len(quvvat_values),
        'total_patoks_count': len(rows),
    }


def generate_branch_report_image(report_data: dict) -> io.BytesIO:
    """
    Pillow kutubxonasi yordamida foydalanuvchi taqdim etgan Excel uslubidagi
    5 ustunli yuqori sifatli (1260px kenglikda) PNG rasm chizadi.
    Ustunlar:
    1. Паток №
    2. Маҳсулот & Дазмол
    3. Давомот
    4. Операционка
    5. Паток қуввати
    """
    branch_title = report_data['branch_title']
    target_date = report_data['target_date']
    rows = report_data['rows']
    total_dazmol = report_data['total_dazmol']
    total_davomat = report_data['total_davomat']
    avg_quvvat = report_data['avg_quvvat']
    davomat_foiz = report_data['davomat_foiz']

    # Ranglar sxemasi (Excel uslubidagi ochiq, tiniq dizayn)
    bg_color = (255, 255, 255)                  # Butun varaq oq
    grid_color = (210, 220, 230)                # Katak chiziqlari (yengil kulrang)
    border_dark = (15, 23, 42)                  # Asosiy ramka qora
    header_bg = (220, 238, 255)                 # Sarlavha och moviy fon (#DCEEFF)
    header_text_color = (15, 23, 42)            # Sarlavha matni to'q ko'k/qora
    row_alt_bg = (248, 250, 252)                # Juft qatorlar foni
    total_bg = (218, 235, 254)                  # Jami qatori foni (#DAEBFE)
    text_color = (15, 23, 42)                   # Asosiy matn qora
    text_green = (5, 150, 105)                  # Quvvat yashil
    text_muted = (100, 116, 139)                # Yordamchi matn kulrang

    # Shriftlar
    font_main_title = _get_font(26, bold=True)
    font_sub_title = _get_font(14, bold=False)
    font_th = _get_font(15, bold=True)
    font_cell = _get_font(14, bold=False)
    font_cell_bold = _get_font(14, bold=True)
    font_total = _get_font(16, bold=True)
    font_sub_pct = _get_font(13, bold=True)

    # Jadval o'lchamlari va ustun kengliklari
    width = 1260
    margin_x = 30
    margin_top = 30
    margin_bottom = 30

    col_w_patok = 130
    col_w_product = 570
    col_w_davomat = 130
    col_w_op = 170
    col_w_quvvat = 200

    table_width = col_w_patok + col_w_product + col_w_davomat + col_w_op + col_w_quvvat
    start_x = margin_x

    # Har bir qator balandligini hisoblash (dinamik)
    base_line_h = 24
    row_heights = []
    for r in rows:
        p_count = max(1, len(r['products']))
        h = max(40, 12 + (p_count * base_line_h))
        row_heights.append(h)

    header_box_h = 80
    th_height = 46
    total_row_h = 56
    table_content_h = sum(row_heights)

    total_height = margin_top + header_box_h + th_height + table_content_h + total_row_h + margin_bottom + 10

    img = Image.new('RGB', (width, total_height), bg_color)
    draw = ImageDraw.Draw(img)

    # 1. SARLAVHA (Header Text)
    title_text = branch_title
    sub_text = f"Сана: {target_date.strftime('%d.%m.%Y')}   |   Соат: 18:00 ҳолатига"

    draw.text((width // 2, margin_top + 16), title_text, fill=text_color, font=font_main_title, anchor="mm")
    draw.text((width // 2, margin_top + 50), sub_text, fill=text_muted, font=font_sub_title, anchor="mm")

    # 2. JADVAL SARLAVHASI (Table TH)
    table_y = margin_top + header_box_h
    x0 = start_x
    x1 = x0 + col_w_patok
    x2 = x1 + col_w_product
    x3 = x2 + col_w_davomat
    x4 = x3 + col_w_op
    x5 = x4 + col_w_quvvat

    # TH Foni
    draw.rectangle([(x0, table_y), (x5, table_y + th_height)], fill=header_bg, outline=border_dark, width=2)

    # TH Ajratuvchi chiziqlar
    for x in [x1, x2, x3, x4]:
        draw.line([(x, table_y), (x, table_y + th_height)], fill=border_dark, width=1)

    # TH Matnlari
    draw.text((x0 + col_w_patok // 2, table_y + th_height // 2), "Паток №", fill=header_text_color, font=font_th, anchor="mm")
    draw.text((x1 + 16, table_y + th_height // 2), "Маҳсулот & Дазмол (Дазмол / Нархлар шаблони)", fill=header_text_color, font=font_th, anchor="lm")
    draw.text((x2 + col_w_davomat // 2, table_y + th_height // 2), "Давомот", fill=header_text_color, font=font_th, anchor="mm")
    draw.text((x3 + col_w_op // 2, table_y + th_height // 2), "Операционка", fill=header_text_color, font=font_th, anchor="mm")
    draw.text((x4 + col_w_quvvat // 2, table_y + th_height // 2), "Паток қуввати", fill=header_text_color, font=font_th, anchor="mm")

    # 3. JADVAL QATORLARI (Data Rows)
    curr_y = table_y + th_height
    for idx, (r, row_h) in enumerate(zip(rows, row_heights)):
        row_bg = row_alt_bg if (idx % 2 == 1) else (255, 255, 255)
        draw.rectangle([(x0, curr_y), (x5, curr_y + row_h)], fill=row_bg, outline=grid_color, width=1)

        # Tashqi chegaralarni qoraroq chizamiz
        draw.line([(x0, curr_y), (x0, curr_y + row_h)], fill=border_dark, width=2)
        draw.line([(x5, curr_y), (x5, curr_y + row_h)], fill=border_dark, width=2)

        # Ichki vertikal chiziqlar
        for x in [x1, x2, x3, x4]:
            draw.line([(x, curr_y), (x, curr_y + row_h)], fill=grid_color, width=1)

        # 1-ustun: Patok nomi (O'rtada)
        draw.text((x0 + col_w_patok // 2, curr_y + row_h // 2), r['patok_title'], fill=text_color, font=font_cell_bold, anchor="mm")

        # 2-ustun va 4-ustun: Mahsulotlar va Operatsionka
        prods = r['products']
        p_count = len(prods)
        start_prod_y = curr_y + (row_h - (p_count * base_line_h)) // 2 + (base_line_h // 2)

        for p_idx, p in enumerate(prods):
            line_y = start_prod_y + (p_idx * base_line_h)

            # Mahsulot nomi va dazmol soni (Chapga qarab)
            prod_text = p['display_str']
            # Dazmol qismini ajratib ko'rsatamiz
            draw.text((x1 + 16, line_y), prod_text, fill=text_color, font=font_cell, anchor="lm")

            # Operatsionka (O'rtada)
            op_str = str(p['aniqlik'])
            draw.text((x3 + col_w_op // 2, line_y), op_str, fill=text_color, font=font_cell_bold, anchor="mm")

        # 3-ustun: Davomot (O'rtada)
        davomat_str = str(r['davomat']) if r['davomat'] > 0 else "—"
        draw.text((x2 + col_w_davomat // 2, curr_y + row_h // 2), davomat_str, fill=text_color, font=font_cell_bold, anchor="mm")

        # 5-ustun: Patok quvvati (O'rtada)
        quvvat_val = r['patok_quvvati']
        if r['is_active'] and quvvat_val > 0:
            quvvat_str = f"{quvvat_val:.1f}%"
            draw.text((x4 + col_w_quvvat // 2, curr_y + row_h // 2), quvvat_str, fill=text_green, font=font_cell_bold, anchor="mm")
        else:
            draw.text((x4 + col_w_quvvat // 2, curr_y + row_h // 2), "—", fill=text_muted, font=font_cell, anchor="mm")

        curr_y += row_h

    # 4. YIG'INDI (ЖАМИ) QATORI
    draw.rectangle([(x0, curr_y), (x5, curr_y + total_row_h)], fill=total_bg, outline=border_dark, width=2)
    for x in [x1, x2, x3, x4]:
        draw.line([(x, curr_y), (x, curr_y + total_row_h)], fill=border_dark, width=1)

    # ЖАМИ
    draw.text((x0 + col_w_patok // 2, curr_y + total_row_h // 2), "ЖАМИ", fill=border_dark, font=font_total, anchor="mm")

    # Jami Dazmol
    tot_dazmol_str = f"{total_dazmol:,} дона".replace(",", " ")
    draw.text((x1 + 16, curr_y + total_row_h // 2), tot_dazmol_str, fill=border_dark, font=font_total, anchor="lm")

    # Jami Davomat va pastida Davomat %
    draw.text((x2 + col_w_davomat // 2, curr_y + (total_row_h // 2) - 8), str(total_davomat), fill=border_dark, font=font_total, anchor="mm")
    if davomat_foiz > 0:
        draw.text((x2 + col_w_davomat // 2, curr_y + (total_row_h // 2) + 12), f"({davomat_foiz:.0f}%)", fill=text_muted, font=font_sub_pct, anchor="mm")

    # Operatsionka jami: bo'sh yoki chiziqcha ("—")
    draw.text((x3 + col_w_op // 2, curr_y + total_row_h // 2), "—", fill=text_muted, font=font_total, anchor="mm")

    # O'rtacha patok quvvati
    avg_quvvat_str = f"{avg_quvvat:.1f}%"
    draw.text((x4 + col_w_quvvat // 2, curr_y + total_row_h // 2), avg_quvvat_str, fill=border_dark, font=font_total, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format='PNG', quality=95)
    buf.seek(0)
    return buf


def send_single_branch_report(branch_key: str, target_date: datetime.date = None) -> dict:
    """
    Belgilangan filial hisobot rasmini generatsiya qilib Telegram guruhga jo'natish:
    - KOSON (HQ): -1004425170756 (-4425170756)
    - UYCHI: -1003903019133 (-3903019133)
    """
    if target_date is None:
        target_date = timezone.localdate()

    is_hq = (str(branch_key).upper() in ['HQ', 'KOSON', 'KOSONSOY', 'BOSH'])
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return {'success': False, 'message': "TELEGRAM_BOT_TOKEN sozlamalarda topilmadi!"}

    if is_hq:
        target_chat_id = str(getattr(settings, 'TELEGRAM_HQ_REPORT_CHAT_ID', '-1004425170756') or '-1004425170756')
        branch_name = "КОСОНСОЙ"
        b_code = "HQ"
    else:
        target_chat_id = str(getattr(settings, 'TELEGRAM_UYCHI_REPORT_CHAT_ID', '-1003903019133') or '-1003903019133')
        branch_name = "УЙЧИ"
        b_code = "UYCHI"

    try:
        report_data = get_branch_daily_report_data(b_code, target_date)
        img_buf = generate_branch_report_image(report_data)
    except Exception as e:
        return {'success': False, 'message': f"Hisobot rasmini tayyorlashda xatolik: {str(e)}"}

    tot_dazmol = f"{report_data['total_dazmol']:,}".replace(",", " ")
    tot_davomat = report_data['total_davomat']
    avg_quvvat = report_data['avg_quvvat']
    davomat_foiz = report_data['davomat_foiz']

    caption = (
        f"📊 *TERRY JAR — КУНЛИК ИШЛАБ ЧИҚАРИШ ҲИСОБОТИ*\n"
        f"🏢 *Филиал:* {branch_name}\n"
        f"📅 *Сана:* {target_date.strftime('%d.%m.%Y')} (18:00 ҳолатига)\n\n"
        f"👕 *Дазмолдан ўтган:* *{tot_dazmol} дона*\n"
        f"👥 *Жами давомот:* *{tot_davomat} нафар* ({davomat_foiz:.0f}% таъминланганлик)\n"
        f"⚡ *Ўртача паток қуввати:* *{avg_quvvat}%*\n\n"
        f"📌 _Барча патоклар ва маҳсулотлар бўйича батафсил маълумот юқоридаги расмда кўрсатилган._"
    )

    filename = f"Hisobot_{b_code}_{target_date.strftime('%Y_%m_%d')}.png"

    # 1. Bot tokenini aniqlash: Har doim Payroll Master Bot (8706205290)
    token = str(getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip().strip("'\"")
    if not token or token.startswith('8679375634'):
        token = '8706205290:AAGYsyaQHmx1n5EVo9I3T9Ecjz4Xyz_xEZg'

    # 2. Superguruh chat ID sini normalizatsiya qilish (har doim -100 prefiks bilan)
    cid = target_chat_id.strip().strip("'\"")
    if not cid.startswith("@"):
        if not cid.startswith("-100"):
            if cid.startswith("-"):
                cid = f"-100{cid[1:]}"
            elif cid.isdigit():
                cid = f"-100{cid}"

    api_url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {'photo': (filename, img_buf.getvalue(), 'image/png')}
    payload = {
        'chat_id': cid,
        'caption': caption,
        'parse_mode': 'Markdown',
    }

    try:
        with httpx.Client(timeout=45.0) as client:
            res = client.post(api_url, data=payload, files=files)
            res_data = res.json()
            if res.status_code == 200 and res_data.get('ok'):
                return {
                    'success': True,
                    'branch': b_code,
                    'chat_id': cid,
                    'message': f"{branch_name} hisoboti Telegram guruhga ({cid}) muvaffaqiyatli yuborildi."
                }
            else:
                last_error = res_data.get('description', f"HTTP {res.status_code}")
    except Exception as ex:
        last_error = str(ex)

    if "kicked" in last_error.lower() or "not a member" in last_error.lower() or "chat not found" in last_error.lower():
        helpful_msg = (
            f"Telegram xatoligi ({cid}): {last_error}. "
            f"Iltimos, guruhda @PAYROLL_MASTER_BOT botini tekshiring: bot a'zolar ro'yxatida bo'lishi va unga xabar/rasm yuborish ruxsati berilgan bo'lishi kerak!"
        )
    else:
        helpful_msg = f"Telegramga yuborishda xatolik ({cid}): {last_error}"

    return {
        'success': False,
        'branch': b_code,
        'chat_id': cid,
        'message': helpful_msg
    }


def send_daily_1800_reports(target_date: datetime.date = None) -> dict:
    """
    Har kuni 18:00 da O'zbekiston vaqti bilan ikkala filial hisobot rasmlarini
    o'zlarining Telegram guruhlariga yuborish.
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
