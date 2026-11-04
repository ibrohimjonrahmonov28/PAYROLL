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
from production.daily_dazmol import is_main_dazmol_operation, get_day_window, compute_daily_dazmol, get_article_norm
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


def _report_line(name, group, norm, qty, fallback_aniqlik, aniqlik_cache):
    """18:00 hisobot qatori: shablon, norma, ish soni va operatsion (shablon operatsiyalari aniqligi yig'indisi)."""
    if group:
        if group.id not in aniqlik_cache:
            aniqlik_cache[group.id] = float(get_operation_group_difficulty(group))
        g = aniqlik_cache[group.id]
    else:
        g = float(fallback_aniqlik)
    return {
        'shablon_name': name,
        'norm': norm,
        'dazmol_qty': qty,
        'display_str': f"{qty} / {name}",
        'aniqlik': int(g) if g.is_integer() else round(g, 1),
        'aniqlik_value': g,
    }


def patok_foiz(products):
    """
    Patok foizi: ish soni yig'indisi / normalarning o'rtacha arifmetigi * 100 (egasi formulasi).
    Masalan Futbolka 100 (norma 800) + Tunika 900 (norma 1000): 1000 / ((800 + 1000) / 2) = 111.1%.
    """
    norms = [p['norm'] for p in products if p.get('norm')]
    if not norms:
        return 0.0
    total = sum(p['dazmol_qty'] for p in products)
    return round(total / (sum(norms) / len(norms)) * 100, 1)


def get_assigned_batches_by_screen(screen_numbers) -> dict:
    """
    {patok: [pastal, ...]} — patokka qabul qilingan pastallar (accepted_at bo'yicha): planshet qabuli yoki Uychi omboridan
    topshirilgan (daily_dazmol.get_box_owner_screen bilan bir xil). Dazmol bo'lmagan kuni norma ko'rsatish uchun.
    """
    assigned_batches = CuttingBatch.objects.filter(
        Q(items__boxes__is_received_by_patok=True) | Q(assigned_branch='UYCHI'),
        assigned_screen__in=list(screen_numbers),
        dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
    ).distinct().select_related(
        'order_item__article__operation_group',
        'order_item__article__model'
    ).order_by('accepted_at', 'id')
    assigned_by_screen = {}
    for b in assigned_batches:
        assigned_by_screen.setdefault(b.assigned_screen, []).append(b)
    return assigned_by_screen


def patok_report_lines(dazmol, sn, sn_assigned) -> list:
    """
    18:00 hisobot qatorlari (TV ekranlar ham shuni ishlatadi, foizlar bir xil bo'lishi uchun): asosiy dazmolning
    shablon x norma qatorlari [{'name', 'group', 'norm', 'qty'}]. Dazmol bo'lmagan kuni patokka qabul qilingan oxirgi
    pastal modeli 0 dona bilan; u ham bo'lmasa — bo'sh ro'yxat.
    """
    lines = [ln for ln in dazmol.template_lines(sn) if ln['qty'] > 0]
    if lines:
        return lines
    primary_batch = sn_assigned[-1] if sn_assigned else None
    if primary_batch and primary_batch.order_item and primary_batch.order_item.article:
        art = primary_batch.order_item.article
        group = art.operation_group
        name = group.name if group else (art.name or "Noma'lum")
        return [{'name': name, 'group': group, 'norm': get_article_norm(art), 'qty': 0}]
    return []


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

    # Patokka qabul qilingan pastallar (dazmol bo'lmagan kuni shablon nomini ko'rsatish uchun)
    assigned_by_screen = get_assigned_batches_by_screen(screen_numbers)

    rows = []
    total_dazmol_sum = 0
    total_davomat_sum = 0
    quvvat_values = []
    foiz_values = []
    dominant_aniqlik_sum = 0
    aniqlik_cache = {}

    for sn in screen_numbers:
        patok_num = sn if is_hq else (sn - 13)
        patok_title = f"{patok_num}-паток"

        sn_assigned = assigned_by_screen.get(sn, [])

        # Ekran statistikasi (Davomat, Aniqlik, Quvvat)
        screen_data = get_screen_data(sn, target_date)
        davomat = screen_data.get('davomat', 0)
        patok_quvvati = float(screen_data.get('patok_quvvati', 0.0))

        # 1. Asosiy dazmol narxlar shablonlari va normalari bo'yicha (yagona manba: production/daily_dazmol.py).
        #    Bir shablonda ikki xil norma bo'lsa alohida qator. Dona yig'indisi = /daily/ dagi asosiy dazmol.
        fallback_aniqlik = float(screen_data.get('aniqlik', 0.0))
        products = [
            _report_line(ln['name'], ln['group'], ln['norm'], ln['qty'], fallback_aniqlik, aniqlik_cache)
            for ln in patok_report_lines(dazmol, sn, sn_assigned)
        ]
        if not products:
            g = fallback_aniqlik
            products.append({
                'shablon_name': "—", 'norm': 0, 'dazmol_qty': 0, 'display_str': "0 / —",
                'aniqlik': int(g) if g.is_integer() else (round(g, 1) if g > 0 else "—"),
                'aniqlik_value': g,
            })
        patok_dazmol_total = sum(p['dazmol_qty'] for p in products)
        max_group_aniqlik = max((p['aniqlik_value'] for p in products), default=0.0)
        foiz = patok_foiz(products)

        total_dazmol_sum += patok_dazmol_total
        total_davomat_sum += davomat
        dominant_aniqlik_sum += (max_group_aniqlik if max_group_aniqlik > 0 else float(screen_data.get('aniqlik', 0.0)))

        is_active = (sn in scanned_screens or patok_dazmol_total > 0 or davomat > 0)
        if is_active:
            quvvat_values.append(patok_quvvati)
            foiz_values.append(foiz)

        row_item = {
            'screen_number': sn,
            'patok_code': get_patok_code(sn),
            'patok_num': patok_num,
            'patok_title': patok_title,
            'products': products,
            'dazmol_total': patok_dazmol_total,
            'davomat': davomat,
            'patok_quvvati': patok_quvvati,
            'foiz': foiz,
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
    avg_foiz = round(sum(foiz_values) / len(foiz_values), 1) if foiz_values else 0.0   # faol patoklar o'rtachasi
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
        'avg_foiz': avg_foiz,
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
    avg_foiz = report_data.get('avg_foiz', 0.0)
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

    # Jadval o'lchamlari va ustun kengliklari (7 ustun: Паток, Иш номи, Норма, Иш сони, Давомат, Операцион, Фоиз)
    width = 1260
    margin_x = 30
    margin_top = 30
    margin_bottom = 30

    columns = [
        ('patok', "Паток", 110),
        ('name', "Иш номи", 380),
        ('norm', "Норма", 120),
        ('qty', "Иш сони", 130),
        ('davomat', "Давомат", 120),
        ('op', "Операцион", 150),
        ('foiz', "Фоиз", 190),
    ]
    edges = [margin_x]
    for _key, _title, col_w in columns:
        edges.append(edges[-1] + col_w)
    col_x = {key: (edges[i], edges[i + 1]) for i, (key, _t, _w) in enumerate(columns)}
    x_left, x_right = edges[0], edges[-1]
    inner_edges = edges[1:-1]

    def center(key):
        a_x, b_x = col_x[key]
        return (a_x + b_x) // 2

    # Har bir qator balandligi (shablon qatorlari soniga qarab)
    base_line_h = 24
    row_heights = [max(40, 12 + max(1, len(r['products'])) * base_line_h) for r in rows]

    header_box_h = 80
    th_height = 46
    total_row_h = 56
    total_height = margin_top + header_box_h + th_height + sum(row_heights) + total_row_h + margin_bottom + 10

    img = Image.new('RGB', (width, total_height), bg_color)
    draw = ImageDraw.Draw(img)

    # 1. SARLAVHA
    sub_text = f"Сана: {target_date.strftime('%d.%m.%Y')}   |   Соат: 18:00 ҳолатига"
    draw.text((width // 2, margin_top + 16), branch_title, fill=text_color, font=font_main_title, anchor="mm")
    draw.text((width // 2, margin_top + 50), sub_text, fill=text_muted, font=font_sub_title, anchor="mm")

    # 2. JADVAL SARLAVHASI
    table_y = margin_top + header_box_h
    draw.rectangle([(x_left, table_y), (x_right, table_y + th_height)], fill=header_bg, outline=border_dark, width=2)
    for x in inner_edges:
        draw.line([(x, table_y), (x, table_y + th_height)], fill=border_dark, width=1)
    for key, title, _w in columns:
        if key == 'name':
            draw.text((col_x[key][0] + 16, table_y + th_height // 2), title, fill=header_text_color, font=font_th, anchor="lm")
        else:
            draw.text((center(key), table_y + th_height // 2), title, fill=header_text_color, font=font_th, anchor="mm")

    # 3. QATORLAR
    curr_y = table_y + th_height
    for idx, (r, row_h) in enumerate(zip(rows, row_heights)):
        row_bg = row_alt_bg if (idx % 2 == 1) else (255, 255, 255)
        draw.rectangle([(x_left, curr_y), (x_right, curr_y + row_h)], fill=row_bg, outline=grid_color, width=1)
        draw.line([(x_left, curr_y), (x_left, curr_y + row_h)], fill=border_dark, width=2)
        draw.line([(x_right, curr_y), (x_right, curr_y + row_h)], fill=border_dark, width=2)
        for x in inner_edges:
            draw.line([(x, curr_y), (x, curr_y + row_h)], fill=grid_color, width=1)
        mid_y = curr_y + row_h // 2

        draw.text((center('patok'), mid_y), r['patok_title'], fill=text_color, font=font_cell_bold, anchor="mm")

        # Shablon qatorlari: Иш номи, Норма, Иш сони, Операцион bir chiziqda
        prods = r['products']
        first_line_y = curr_y + (row_h - (len(prods) * base_line_h)) // 2 + (base_line_h // 2)
        for p_idx, p in enumerate(prods):
            line_y = first_line_y + p_idx * base_line_h
            draw.text((col_x['name'][0] + 16, line_y), str(p['shablon_name']), fill=text_color, font=font_cell, anchor="lm")
            norm_str = f"{p['norm']:,}".replace(",", " ") if p.get('norm') else "—"
            draw.text((center('norm'), line_y), norm_str, fill=text_color, font=font_cell, anchor="mm")
            qty_str = f"{p['dazmol_qty']:,}".replace(",", " ")
            draw.text((center('qty'), line_y), qty_str, fill=text_color, font=font_cell_bold, anchor="mm")
            draw.text((center('op'), line_y), str(p['aniqlik']), fill=text_color, font=font_cell_bold, anchor="mm")

        davomat_str = str(r['davomat']) if r['davomat'] > 0 else "—"
        draw.text((center('davomat'), mid_y), davomat_str, fill=text_color, font=font_cell_bold, anchor="mm")

        if r['is_active'] and r.get('foiz', 0) > 0:
            draw.text((center('foiz'), mid_y), f"{r['foiz']:.1f}%", fill=text_green, font=font_cell_bold, anchor="mm")
        else:
            draw.text((center('foiz'), mid_y), "—", fill=text_muted, font=font_cell, anchor="mm")

        curr_y += row_h

    # 4. ЖАМИ QATORI
    draw.rectangle([(x_left, curr_y), (x_right, curr_y + total_row_h)], fill=total_bg, outline=border_dark, width=2)
    for x in inner_edges:
        draw.line([(x, curr_y), (x, curr_y + total_row_h)], fill=border_dark, width=1)
    mid_y = curr_y + total_row_h // 2
    draw.text((center('patok'), mid_y), "ЖАМИ", fill=border_dark, font=font_total, anchor="mm")
    draw.text((center('qty'), mid_y), f"{total_dazmol:,}".replace(",", " "), fill=border_dark, font=font_total, anchor="mm")
    draw.text((center('davomat'), mid_y - 8), str(total_davomat), fill=border_dark, font=font_total, anchor="mm")
    if davomat_foiz > 0:
        draw.text((center('davomat'), mid_y + 12), f"({davomat_foiz:.0f}%)", fill=text_muted, font=font_sub_pct, anchor="mm")
    for key in ('name', 'norm', 'op'):
        draw.text((center(key), mid_y), "—", fill=text_muted, font=font_total, anchor="mm")
    draw.text((center('foiz'), mid_y), f"{avg_foiz:.1f}%", fill=border_dark, font=font_total, anchor="mm")

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
    avg_foiz = report_data['avg_foiz']
    davomat_foiz = report_data['davomat_foiz']

    caption = (
        f"📊 *TERRY JAR — КУНЛИК ИШЛАБ ЧИҚАРИШ ҲИСОБОТИ*\n"
        f"🏢 *Филиал:* {branch_name}\n"
        f"📅 *Сана:* {target_date.strftime('%d.%m.%Y')} (18:00 ҳолатига)\n\n"
        f"👕 *Дазмолдан ўтган:* *{tot_dazmol} дона*\n"
        f"👥 *Жами давомот:* *{tot_davomat} нафар* ({davomat_foiz:.0f}% таъминланганлик)\n"
        f"📈 *Ўртача фоиз (иш сони / норма):* *{avg_foiz}%*\n\n"
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
