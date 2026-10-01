import io
import datetime
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Q, Count
from django.conf import settings

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from accounts.models import Worker, WorkerPayout
from production.models import Ticket, ArticleOperation
from production.services import get_patok_name, get_patok_code


def get_clean_worker_uid(worker) -> str:
    """
    Xodimning bazadagi 6 xonali toza UID raqamini qaytaradi ('w' yoki 'W' prefikslarisiz).
    Masalan: '670040' (W-002 o'rniga).
    """
    if not worker:
        return "—"
    if hasattr(worker, 'user') and worker.user and worker.user.uid:
        return str(worker.user.uid).strip()
    wid = str(getattr(worker, 'worker_id', '') or '').strip()
    clean_id = wid.replace('W-', '').replace('w-', '').replace('W', '').replace('w', '').strip()
    return clean_id if clean_id else wid


def compact_ticket_ids(tickets, max_ranges=8) -> str:
    """
    Skanerlangan stikerlar ro'yxatini ixcham ko'rinishga keltiradi:
    - Ketma-ket kelgan stiker ID larni diapazon qiladi: #101-#125
    - Qutilar haqida ma'lumot beradi: Quti: #1, #2 (yoki Qutilar: #1-#5 (5 ta))
    - Agar juda ko'p bo'lsa, qisqartirib, to'liq ro'yxat 'Barcha Stikerlar' varag'ida borligini bildiradi.
    """
    if not tickets:
        return "—"

    # 1. Unikal qutilar
    box_nums = sorted(list({t.box.box_number for t in tickets if t.box and t.box.box_number is not None}))
    if box_nums:
        if len(box_nums) <= 3:
            boxes_part = "Quti: " + ", ".join(f"#{b}" for b in box_nums)
        else:
            boxes_part = f"Qutilar: #{box_nums[0]}-#{box_nums[-1]} ({len(box_nums)} ta)"
    else:
        boxes_part = ""

    # 2. Stiker ID diapazonlari
    int_ids = []
    other_codes = []
    for t in tickets:
        code_str = str(t.stiker_code) if t.stiker_code else (str(t.id) if t.id else "")
        if code_str.isdigit():
            int_ids.append(int(code_str))
        elif code_str:
            other_codes.append(code_str)

    int_ids.sort()
    ranges = []
    if int_ids:
        start = int_ids[0]
        prev = int_ids[0]
        for cur in int_ids[1:]:
            if cur == prev + 1:
                prev = cur
            else:
                if start == prev:
                    ranges.append(f"#{start}")
                elif prev == start + 1:
                    ranges.append(f"#{start}, #{prev}")
                else:
                    ranges.append(f"#{start}-#{prev}")
                start = cur
                prev = cur
        if start == prev:
            ranges.append(f"#{start}")
        elif prev == start + 1:
            ranges.append(f"#{start}, #{prev}")
        else:
            ranges.append(f"#{start}-#{prev}")

    all_tokens = ranges + other_codes
    total_count = len(tickets)

    if len(all_tokens) <= max_ranges:
        ids_part = ", ".join(all_tokens)
    else:
        shown = ", ".join(all_tokens[:max_ranges])
        rem_count = len(all_tokens) - max_ranges
        ids_part = f"{shown} ... (+{rem_count} diapazon / 'Barcha Stikerlar' varag'ida)"

    if boxes_part and ids_part:
        return f"{boxes_part} | ID: {ids_part} (jami {total_count} ta)"
    elif ids_part:
        return f"ID: {ids_part} (jami {total_count} ta)"
    elif boxes_part:
        return f"{boxes_part} (jami {total_count} ta stiker)"
def _build_monthly_tabel_sheet(
    wb,
    target_date: datetime.date,
    sheet_title: str = "Oylik Umumiy Tabel",
    insert_at_index: int = None,
    existing_ws = None
):
    """
    Oylik umumiy ish haqi va xodimlar tabeli varag'ini yaratadi:
    № | Xodim UID | F.I.SH | Ishlagan Kunlari | Hisoblangan Ish Haqi (UZS) | Bonus (UZS) | Berilgan Avans (UZS) | Magazin (UZS) | To'langan Oylik (UZS) | To'lanishi Kerak Qoldiq (UZS)

    Web `/superadmin/payroll` dagi jami hisoblangan ish haqi bilan 100% bir xil chiqadi.
    Shu oyda kamida 1 ta stiker skanerlagan barcha xodimlar qamrab olinadi (oxirgi kuni ishlamaganlar ham).
    """
    if existing_ws is not None:
        ws = existing_ws
        ws.title = sheet_title
    elif insert_at_index is not None:
        ws = wb.create_sheet(title=sheet_title, index=insert_at_index)
    else:
        ws = wb.create_sheet(title=sheet_title)

    ws.views.sheetView[0].showGridLines = True

    selected_year = target_date.year
    selected_month = target_date.month
    start_of_month = target_date.replace(day=1)

    # 1. Tanlangan oy bo'yicha (target_date gacha) barcha skanerlangan biletlar
    month_tickets_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=selected_year,
        scanned_at__month=selected_month,
        scanned_at__date__lte=target_date
    )

    # 0. Ratsenka (operatsiya narxi) o'zgargan bo'lsa, biletlar narxlarini eng so'nggi ratsenkalar bilan yangilash
    ao_ids = list(month_tickets_qs.values_list('article_operation_id', flat=True).distinct())
    for ao in ArticleOperation.objects.filter(id__in=ao_ids):
        ao.sync_price_to_tickets()

    # Biletlar bo'yicha xodimlar statistikasi (web bilan 100% bir xil)
    worker_ticket_stats = month_tickets_qs.values('worker_id').annotate(
        units=Sum('quantity'),
        gross=Sum('total_amount'),
        days_worked=Count('scanned_at__date', distinct=True)
    )
    worker_ticket_map = {item['worker_id']: item for item in worker_ticket_stats}

    # 2. Tanlangan oy to'lovlari (target_date gacha)
    month_payout_qs = WorkerPayout.objects.filter(
        payout_date__year=selected_year,
        payout_date__month=selected_month,
        payout_date__lte=target_date
    ).values('worker_id').annotate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY)),
        bonuses=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.BONUS)),
    )
    month_payout_map = {item['worker_id']: item for item in month_payout_qs}

    # 3. Kunlik KPI bonuslari (agar tizimda belgilangan bo'lsa)
    daily_bonus_amount = getattr(settings, 'DAILY_BONUS_AMOUNT', 0)
    worker_daily_bonuses = {}
    if daily_bonus_amount > 0:
        tickets_list = list(month_tickets_qs.select_related('article_operation__article__model'))
        tickets_by_worker = {}
        for t in tickets_list:
            tickets_by_worker.setdefault(t.worker_id, []).append(t)

        for w_id, w_tickets in tickets_by_worker.items():
            t_by_date = {}
            for t in w_tickets:
                if t.scanned_at:
                    d = timezone.localtime(t.scanned_at).date()
                    t_by_date.setdefault(d, []).append(t)

            w_bonus_sum = Decimal('0.00')
            for d, d_tickets in t_by_date.items():
                model_stats = {}
                for t in d_tickets:
                    ao = t.article_operation
                    art = ao.article if ao else None
                    pmodel = art.model if art else None
                    norm = (pmodel.daily_norm if (pmodel and pmodel.daily_norm) else (art.daily_norm if (art and art.daily_norm) else 1000)) or 1000
                    diff = float(ao.difficulty) if (ao and ao.difficulty) else 1.0
                    pts = t.quantity * diff
                    m_key = f"m_{pmodel.id}" if pmodel else (f"art_{art.id}" if art else "0")
                    if m_key not in model_stats:
                        model_stats[m_key] = {'norm': norm, 'points': 0.0}
                    model_stats[m_key]['points'] += pts

                total_day_pct = Decimal('0.0')
                for mk, mdata in model_stats.items():
                    if mdata['norm'] > 0:
                        total_day_pct += (Decimal(str(mdata['points'])) / Decimal(str(mdata['norm']))) * Decimal('100.0')

                if total_day_pct > Decimal('100.0'):
                    w_bonus_sum += Decimal(str(daily_bonus_amount))

            if w_bonus_sum > 0:
                worker_daily_bonuses[w_id] = w_bonus_sum

    # 4. Barcha xodimlar: ushbu oyda bilet urgan, to'lov olgan yoki faol barcha xodimlar
    relevant_worker_ids = set(worker_ticket_map.keys()) | set(month_payout_map.keys())
    workers = list(Worker.objects.filter(
        Q(id__in=relevant_worker_ids) | Q(is_active=True)
    ).select_related('user').order_by('worker_id'))

    navy_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    zebra_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    total_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

    font_title = Font(name="Arial", size=15, bold=True, color="0F172A")
    font_subtitle = Font(name="Arial", size=10, italic=True, color="475569")
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Arial", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Arial", size=10, color="0F172A")
    font_green_bold = Font(name="Arial", size=10, bold=True, color="166534")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    thick_bottom_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='medium', color='0F172A')
    )

    align_center = Alignment(horizontal='center', vertical='center')
    align_left = Alignment(horizontal='left', vertical='center')
    align_right = Alignment(horizontal='right', vertical='center')

    # Sarlavha
    ws.merge_cells('A1:J1')
    ws['A1'] = "TERRY JAR — OYLIK ISH HAQI VA XODIMLAR TABELI"
    ws['A1'].font = font_title
    ws['A1'].alignment = align_left
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:J2')
    ws['A2'] = (
        f"Davr: {start_of_month.strftime('%d.%m.%Y')} dan {target_date.strftime('%d.%m.%Y')} gacha | "
        f"Shakllantirilgan vaqt: {timezone.localtime().strftime('%d.%m.%Y %H:%M')}"
    )
    ws['A2'].font = font_subtitle
    ws['A2'].alignment = align_left
    ws.row_dimensions[2].height = 18

    headers = [
        ("№", 5, align_center),
        ("Xodim UID", 14, align_center),
        ("F.I.SH", 28, align_left),
        ("Ishlagan Kunlari", 16, align_center),
        ("Hisoblangan Ish Haqi (UZS)", 24, align_right),
        ("Bonus (UZS)", 18, align_right),
        ("Berilgan Avans (UZS)", 22, align_right),
        ("Magazin (UZS)", 18, align_right),
        ("To'langan Oylik (UZS)", 22, align_right),
        ("To'lanishi Kerak Qoldiq (UZS)", 26, align_right),
    ]

    header_row = 4
    ws.row_dimensions[header_row].height = 24
    for col_idx, (h_text, width, aln) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h_text)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_center
        cell.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    row_idx = 5
    counter = 1
    for w in workers:
        t_stat = worker_ticket_map.get(w.id, {})
        g = t_stat.get('gross') or Decimal('0.00')
        days_w = t_stat.get('days_worked') or 0

        p_stat = month_payout_map.get(w.id, {})
        adv = p_stat.get('advances') or Decimal('0.00')
        sal = p_stat.get('salaries') or Decimal('0.00')
        bonus = (p_stat.get('bonuses') or Decimal('0.00')) + worker_daily_bonuses.get(w.id, Decimal('0.00'))

        c_fill = zebra_fill if counter % 2 == 0 else white_fill

        ws.cell(row=row_idx, column=1, value=counter).alignment = align_center

        # Xodim UID — 6 xonali toza son (w yoki W prefikslarisiz)
        clean_uid = get_clean_worker_uid(w)
        c_uid = ws.cell(row=row_idx, column=2, value=clean_uid)
        c_uid.alignment = align_center
        c_uid.font = font_bold

        c_name = ws.cell(row=row_idx, column=3, value=w.full_name)
        c_name.alignment = align_left
        c_name.font = font_bold

        # Ishlagan kunlari (shu oyda kamida 1 ta stiker qilgan kunlar soni)
        c_days = ws.cell(row=row_idx, column=4, value=days_w)
        c_days.alignment = align_center

        # Hisoblangan Ish Haqi
        c_g = ws.cell(row=row_idx, column=5, value=float(g))
        c_g.alignment = align_right
        c_g.number_format = '#,##0'
        if g > 0:
            c_g.font = font_green_bold
            c_g.fill = green_fill

        # Bonus
        c_bonus = ws.cell(row=row_idx, column=6, value=float(bonus))
        c_bonus.alignment = align_right
        c_bonus.number_format = '#,##0'

        # Berilgan Avans
        c_adv = ws.cell(row=row_idx, column=7, value=float(adv))
        c_adv.alignment = align_right
        c_adv.number_format = '#,##0'

        # Magazin (UZS) - 0 kiritiladi, tahrirlanadigan sonli format
        c_mag = ws.cell(row=row_idx, column=8, value=0)
        c_mag.alignment = align_right
        c_mag.number_format = '#,##0'

        # To'langan Oylik
        c_sal = ws.cell(row=row_idx, column=9, value=float(sal))
        c_sal.alignment = align_right
        c_sal.number_format = '#,##0'

        # To'lanishi Kerak Qoldiq formulasi: =(E+F)-G-H-I
        c_qoldiq = ws.cell(row=row_idx, column=10, value=f"=E{row_idx}+F{row_idx}-G{row_idx}-H{row_idx}-I{row_idx}")
        c_qoldiq.alignment = align_right
        c_qoldiq.font = font_bold
        c_qoldiq.number_format = '#,##0'

        for col_idx in range(1, 11):
            c_node = ws.cell(row=row_idx, column=col_idx)
            c_node.border = thin_border
            if col_idx != 5 or g == 0:
                if c_fill.fill_type:
                    c_node.fill = c_fill
            if col_idx not in (2, 3, 5, 10):
                c_node.font = font_regular

        row_idx += 1
        counter += 1

    # JAMI / BARCHASI qatori
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=4)
    c_tot_lbl = ws.cell(row=row_idx, column=1, value="JAMI / BARCHASI:")
    c_tot_lbl.font = font_bold
    c_tot_lbl.alignment = align_right

    c_tot_g = ws.cell(row=row_idx, column=5, value=f"=SUM(E5:E{row_idx-1})")
    c_tot_g.font = font_bold
    c_tot_g.alignment = align_right
    c_tot_g.number_format = '#,##0'

    c_tot_bonus = ws.cell(row=row_idx, column=6, value=f"=SUM(F5:F{row_idx-1})")
    c_tot_bonus.font = font_bold
    c_tot_bonus.alignment = align_right
    c_tot_bonus.number_format = '#,##0'

    c_tot_adv = ws.cell(row=row_idx, column=7, value=f"=SUM(G5:G{row_idx-1})")
    c_tot_adv.font = font_bold
    c_tot_adv.alignment = align_right
    c_tot_adv.number_format = '#,##0'

    c_tot_mag = ws.cell(row=row_idx, column=8, value=f"=SUM(H5:H{row_idx-1})")
    c_tot_mag.font = font_bold
    c_tot_mag.alignment = align_right
    c_tot_mag.number_format = '#,##0'

    c_tot_sal = ws.cell(row=row_idx, column=9, value=f"=SUM(I5:I{row_idx-1})")
    c_tot_sal.font = font_bold
    c_tot_sal.alignment = align_right
    c_tot_sal.number_format = '#,##0'

    c_tot_qoldiq = ws.cell(row=row_idx, column=10, value=f"=SUM(J5:J{row_idx-1})")
    c_tot_qoldiq.font = font_bold
    c_tot_qoldiq.alignment = align_right
    c_tot_qoldiq.number_format = '#,##0'

    ws.row_dimensions[row_idx].height = 24
    for col_idx in range(1, 11):
        c_node = ws.cell(row=row_idx, column=col_idx)
        c_node.fill = total_fill
        c_node.border = thick_bottom_border

    return ws


def generate_daily_excel_report(target_date: datetime.date = None) -> io.BytesIO:
    """
    Har kuni kechki payt 00:00 da Telegram orqali yuboriladigan 2 varaqli professional Excel hisobot:
    - 1-varaq: "Xodimlar Kunlik Hisoboti" (Xodim, bugungi donasi, bugungi puli, oyligi, balansi, urgan stikerlar ID lari)
    - 2-varaq: "Barcha Skanerlangan Stikerlar" (Har bir stiker ID si bo'yicha operatsiya, zakaz, narx va vaqt tafsiloti)
    """
    if not HAS_OPENPYXL:
        raise ImportError(
            "Serverda 'openpyxl' kutubxonasi o'rnatilmagan. "
            "Iltimos, serverda 'docker compose exec web pip install openpyxl' buyrug'ini bajaring."
        )

    if target_date is None:
        target_date = timezone.localdate()

    # O'sha kunning boshlanishi va tugashi
    start_of_month = target_date.replace(day=1)
    tz = timezone.get_current_timezone()
    day_start_dt = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), tz)

    # 1. Barcha o'sha kuni skanerlangan biletlar
    daily_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date
    ).select_related(
        'worker',
        'article_operation__operation',
        'article_operation__article__model',
        'box__order'
    ).order_by('scanned_at')

    # 0. Ratsenka (operatsiya narxi) o'zgargan bo'lsa, ushbu kunda skanerlangan barcha biletlar
    # narxlarini va jami summalarini eng so'nggi ratsenkalar bilan kafolatli qayta hisoblash:
    ao_ids = list(daily_tickets.values_list('article_operation_id', flat=True).distinct())
    for ao in ArticleOperation.objects.filter(id__in=ao_ids):
        ao.sync_price_to_tickets()

    # Qayta yangilangan biletlarni yuklash
    daily_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date
    ).select_related(
        'worker',
        'article_operation__operation',
        'article_operation__article__model',
        'box__order'
    ).order_by('scanned_at')

    # Xodimlar bo'yicha guruhlash
    active_worker_ids = set(daily_tickets.values_list('worker_id', flat=True))
    workers = Worker.objects.filter(
        Q(id__in=active_worker_ids) | Q(is_active=True)
    ).order_by('worker_id')

    # Har bir xodim uchun ushbu kundagi biletlarni jamlash
    worker_tickets_map = {}
    for t in daily_tickets:
        if t.worker_id not in worker_tickets_map:
            worker_tickets_map[t.worker_id] = []
        worker_tickets_map[t.worker_id].append(t)

    # Ushbu kunga qadar bo'lgan balanslar (kechagi balans) va ishlagan kunlar sonini hisoblash
    tickets_pre_day = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__lt=day_start_dt
    ).values('worker_id').annotate(total=Sum('total_amount'))
    pre_day_earned_map = {item['worker_id']: item['total'] for item in tickets_pre_day}

    payouts_pre_day = WorkerPayout.objects.filter(
        payout_date__lt=target_date
    ).values('worker_id').annotate(total=Sum('amount'))
    pre_day_paid_map = {item['worker_id']: item['total'] for item in payouts_pre_day}

    yesterday_balances = {}
    for w in workers:
        e_pre = pre_day_earned_map.get(w.id, Decimal('0.00')) or Decimal('0.00')
        p_pre = pre_day_paid_map.get(w.id, Decimal('0.00')) or Decimal('0.00')
        yesterday_balances[w.id] = e_pre - p_pre

    days_worked_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=target_date.year,
        scanned_at__month=target_date.month,
        scanned_at__date__lte=target_date
    ).values('worker_id').annotate(cnt=Count('scanned_at__date', distinct=True))
    days_worked_map = {item['worker_id']: item['cnt'] for item in days_worked_qs}

    # Excel workbook yaratish
    wb = openpyxl.Workbook()

    # Ranglar va shriftlar
    navy_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    dark_slate_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    light_slate_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    zebra_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    total_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

    font_title = Font(name="Arial", size=15, bold=True, color="0F172A")
    font_subtitle = Font(name="Arial", size=10, italic=True, color="475569")
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Arial", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Arial", size=10, color="0F172A")
    font_green_bold = Font(name="Arial", size=10, bold=True, color="166534")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    thick_bottom_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='medium', color='0F172A')
    )

    align_center = Alignment(horizontal='center', vertical='center')
    align_left = Alignment(horizontal='left', vertical='center')
    align_right = Alignment(horizontal='right', vertical='center')
    align_wrap = Alignment(horizontal='left', vertical='center', wrap_text=True)

    # -------------------------------------------------------------
    # 1-VARAQ: XODIMLAR KUNLIK HISOBOTI
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "Xodimlar Kunlik Hisoboti"
    ws1.views.sheetView[0].showGridLines = True

    # Sarlavha
    ws1.merge_cells("A1:L1")
    ws1["A1"] = f"TERRY JAR — KUNLIK ISH HAQI VA STIKERLAR HISOBOTI ({target_date.strftime('%d.%m.%Y')})"
    ws1["A1"].font = font_title
    ws1["A1"].alignment = align_left
    ws1.row_dimensions[1].height = 26

    ws1.merge_cells("A2:L2")
    ws1["A2"] = f"Hisobot shakllantirilgan vaqt: {timezone.localtime().strftime('%d.%m.%Y %H:%M')} | Avtomatik Telegram eksport"
    ws1["A2"].font = font_subtitle
    ws1["A2"].alignment = align_left
    ws1.row_dimensions[2].height = 18

    headers_ws1 = [
        ("№", 5, align_center),
        ("Xodim UID", 13, align_center),
        ("F.I.SH", 26, align_left),
        ("Patok", 12, align_center),
        ("Ishlagan Kunlari", 16, align_center),
        ("Tikilgan Ishlar (Operatsiyalar)", 32, align_wrap),
        ("Bugungi Ish Haqi (UZS)", 22, align_right),
        ("Kechagi Balans (UZS)", 22, align_right),
        ("Joriy Balans (UZS)", 22, align_right),
        ("Qutilar Soni", 14, align_center),
        ("Stikerlar Soni", 14, align_center),
        ("Skanerlangan Stikerlar", 45, align_wrap),
    ]

    # Header qatori
    header_row_ws1 = 4
    ws1.row_dimensions[header_row_ws1].height = 24
    for col_idx, (header_text, width, alignment) in enumerate(headers_ws1, 1):
        cell = ws1.cell(row=header_row_ws1, column=col_idx, value=header_text)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_center
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws1.column_dimensions[col_letter].width = width

    row_idx = 5
    counter = 1
    total_today_units = 0
    total_today_earned = Decimal('0.00')
    total_boxes_count = 0
    total_tickets_count = 0

    # Patoklar bo'yicha xulosa (Sheet 3 uchun ma'lumot to'plash)
    patok_summary = {}

    # Xodimlarni bugungi topgan puli bo'yicha kamayish tartibida saralaymiz
    def worker_sort_key(w):
        t_list = worker_tickets_map.get(w.id, [])
        return sum(t.total_amount for t in t_list)

    sorted_workers = sorted(workers, key=worker_sort_key, reverse=True)

    for w in sorted_workers:
        w_tickets = worker_tickets_map.get(w.id, [])
        # Faqat bugun ishlagan xodimlar
        if not w_tickets:
            continue

        today_u = sum(t.quantity for t in w_tickets)
        today_e = sum(t.total_amount for t in w_tickets)
        days_w = days_worked_map.get(w.id, 0)
        yesterday_bal = yesterday_balances.get(w.id, Decimal('0.00'))

        # Xodimning kun oxiridagi oxirgi turgan patogi (ekran raqami)
        last_screen_num = None
        for t in reversed(w_tickets):
            if t.screen_number is not None:
                last_screen_num = t.screen_number
                break
        patok_display = get_patok_name(last_screen_num) if last_screen_num else "—"

        # Patoklar xulosasi uchun jamlash
        p_key = last_screen_num
        if p_key not in patok_summary:
            patok_summary[p_key] = {
                'screen_number': p_key,
                'patok_name': get_patok_name(p_key) if p_key is not None else "Biriktirilmagan (Patoksiz)",
                'workers_count': 0,
                'workers_names': [],
                'total_units': 0,
                'total_earned': Decimal('0.00'),
            }
        patok_summary[p_key]['workers_count'] += 1
        patok_summary[p_key]['workers_names'].append(w.full_name)
        patok_summary[p_key]['total_units'] += today_u
        patok_summary[p_key]['total_earned'] += today_e

        # Operatsiyalar xulosasi
        op_stats = {}
        for t in w_tickets:
            op_name = t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else "Operatsiya"
            op_stats[op_name] = op_stats.get(op_name, 0) + (t.quantity or 0)
        sorted_ops = sorted(op_stats.items(), key=lambda x: -x[1])
        ops_display = "\n".join(f"{name}: {qty:,} ta".replace(",", " ") for name, qty in sorted_ops)

        num_ops = len(sorted_ops)
        if num_ops > 1:
            ws1.row_dimensions[row_idx].height = max(24, num_ops * 18)
        else:
            ws1.row_dimensions[row_idx].height = 24

        boxes_count = len({t.box_id for t in w_tickets if t.box_id})
        stikers_display = compact_ticket_ids(w_tickets)

        is_zebra = (counter % 2 == 0)
        current_fill = zebra_fill if is_zebra else PatternFill(fill_type=None)

        ws1.cell(row=row_idx, column=1, value=counter).alignment = align_center

        c_uid = ws1.cell(row=row_idx, column=2, value=get_clean_worker_uid(w))
        c_uid.alignment = align_center
        c_uid.font = font_bold

        c_name = ws1.cell(row=row_idx, column=3, value=w.full_name)
        c_name.alignment = align_left
        c_name.font = font_bold

        c_patok = ws1.cell(row=row_idx, column=4, value=patok_display)
        c_patok.alignment = align_center
        c_patok.font = font_bold

        ws1.cell(row=row_idx, column=5, value=days_w).alignment = align_center

        c_ops = ws1.cell(row=row_idx, column=6, value=ops_display)
        c_ops.alignment = align_wrap

        c_today_e = ws1.cell(row=row_idx, column=7, value=float(today_e))
        c_today_e.alignment = align_right
        c_today_e.number_format = '#,##0'
        if today_e > 0:
            c_today_e.font = font_green_bold
            c_today_e.fill = green_fill

        c_yest = ws1.cell(row=row_idx, column=8, value=float(yesterday_bal))
        c_yest.alignment = align_right
        c_yest.number_format = '#,##0'

        # Joriy Balans formulasi: Kechagi Balans + Bugungi Ish Haqi
        c_curr = ws1.cell(row=row_idx, column=9, value=f"=H{row_idx}+G{row_idx}")
        c_curr.alignment = align_right
        c_curr.font = font_bold
        c_curr.number_format = '#,##0'

        c_boxes = ws1.cell(row=row_idx, column=10, value=boxes_count)
        c_boxes.alignment = align_center
        c_boxes.number_format = '#,##0'

        c_t_cnt = ws1.cell(row=row_idx, column=11, value=len(w_tickets))
        c_t_cnt.alignment = align_center
        c_t_cnt.number_format = '#,##0'

        c_stikers = ws1.cell(row=row_idx, column=12, value=stikers_display)
        c_stikers.alignment = align_wrap

        for col_idx in range(1, 13):
            cell = ws1.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx != 7 or today_e == 0:
                if current_fill.fill_type:
                    cell.fill = current_fill
            if col_idx not in (2, 3, 4, 7, 9):
                cell.font = font_regular

        total_today_units += today_u
        total_today_earned += today_e
        total_boxes_count += boxes_count
        total_tickets_count += len(w_tickets)

        row_idx += 1
        counter += 1

    # JAMI / TOTAL qatori
    ws1.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=5)
    c_tot_label = ws1.cell(row=row_idx, column=1, value="JAMI / UMUMIY:")
    c_tot_label.font = font_bold
    c_tot_label.alignment = align_right

    c_tot_ops = ws1.cell(row=row_idx, column=6, value=f"{total_today_units:,} ta".replace(",", " "))
    c_tot_ops.font = font_bold
    c_tot_ops.alignment = align_right

    c_tot_earned = ws1.cell(row=row_idx, column=7, value=f"=SUM(G5:G{row_idx-1})")
    c_tot_earned.font = font_bold
    c_tot_earned.alignment = align_right
    c_tot_earned.number_format = '#,##0'

    c_tot_yest = ws1.cell(row=row_idx, column=8, value=f"=SUM(H5:H{row_idx-1})")
    c_tot_yest.font = font_bold
    c_tot_yest.alignment = align_right
    c_tot_yest.number_format = '#,##0'

    c_tot_bal = ws1.cell(row=row_idx, column=9, value=f"=SUM(I5:I{row_idx-1})")
    c_tot_bal.font = font_bold
    c_tot_bal.alignment = align_right
    c_tot_bal.number_format = '#,##0'

    c_tot_b = ws1.cell(row=row_idx, column=10, value=f"=SUM(J5:J{row_idx-1})")
    c_tot_b.font = font_bold
    c_tot_b.alignment = align_center
    c_tot_b.number_format = '#,##0'

    c_tot_t_cnt = ws1.cell(row=row_idx, column=11, value=f"=SUM(K5:K{row_idx-1})")
    c_tot_t_cnt.font = font_bold
    c_tot_t_cnt.alignment = align_center
    c_tot_t_cnt.number_format = '#,##0'

    ws1.cell(row=row_idx, column=12, value="")

    ws1.row_dimensions[row_idx].height = 24
    for col_idx in range(1, 13):
        cell = ws1.cell(row=row_idx, column=col_idx)
        cell.fill = total_fill
        cell.border = thick_bottom_border

    # -------------------------------------------------------------
    # 2-VARAQ: OYLIK UMUMIY TABEL (OY BO'YICHA BARCHA XODIMLAR VA ISH HAQI)
    # -------------------------------------------------------------
    _build_monthly_tabel_sheet(wb, target_date=target_date, insert_at_index=1)

    # -------------------------------------------------------------
    # 3-VARAQ: BARCHA SKANERLANGAN STIKERLAR TAFSILOTI
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Skanerlangan Stikerlar")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:M1")
    ws2["A1"] = f"KUN DAVOMIDA SKANERLANGAN BARCHA STIKERLAR (BILETLAR) TAFSILOTI — {target_date.strftime('%d.%m.%Y')}"
    ws2["A1"].font = font_title
    ws2["A1"].alignment = align_left

    ws2.merge_cells("A2:M2")
    ws2["A2"] = "Ushbu varaq orqali har qanday stiker ID si bo'yicha qaysi zakaz, model va operatsiya bajarilganini tekshirish mumkin."
    ws2["A2"].font = font_subtitle
    ws2["A2"].alignment = align_left

    headers_ws2 = [
        ("№", 5, align_center),
        ("Stiker ID / Kod", 16, align_center),
        ("Bilet Kodi", 22, align_center),
        ("Skanerlangan Vaqt", 16, align_center),
        ("Tikuvchi (F.I.SH)", 24, align_left),
        ("Patok", 12, align_center),
        ("Zakaz #", 14, align_center),
        ("Quti #", 10, align_center),
        ("Model / Mahsulot", 26, align_left),
        ("Operatsiya Nomi", 30, align_left),
        ("Dona Soni", 12, align_right),
        ("Dona Narxi (UZS)", 16, align_right),
        ("Jami Summa (UZS)", 18, align_right),
    ]

    header_row_ws2 = 4
    for col_idx, (header_text, width, alignment) in enumerate(headers_ws2, 1):
        cell = ws2.cell(row=header_row_ws2, column=col_idx, value=header_text)
        cell.font = font_header
        cell.fill = dark_slate_fill
        cell.alignment = align_center
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws2.column_dimensions[col_letter].width = width

    row_idx2 = 5
    detail_counter = 1
    total_detail_units = 0
    total_detail_amount = Decimal('0.00')

    for t in daily_tickets:
        is_zebra2 = (detail_counter % 2 == 0)
        c_fill2 = zebra_fill if is_zebra2 else PatternFill(fill_type=None)

        stiker_id_val = t.stiker_code or f"TK#{t.id}"
        ticket_code_val = t.ticket_code or "—"
        scan_time_str = timezone.localtime(t.scanned_at).strftime('%H:%M:%S') if t.scanned_at else "—"
        worker_name = t.worker.full_name if t.worker else "—"
        patok_str = get_patok_name(t.screen_number) if t.screen_number else "—"
        order_num = t.box.order.order_number if t.box and t.box.order else "—"
        box_num = f"#{t.box.box_number}" if t.box else "—"
        
        model_name = "—"
        if t.article_operation and t.article_operation.article:
            art = t.article_operation.article
            model_name = art.model.name if art.model else art.name
        
        op_name = t.article_operation.operation.name if t.article_operation and t.article_operation.operation else "—"
        qty = t.quantity or 0
        price = t.price_per_unit or Decimal('0.00')
        tot_amt = t.total_amount or Decimal('0.00')

        ws2.cell(row=row_idx2, column=1, value=detail_counter).alignment = align_center
        ws2.cell(row=row_idx2, column=2, value=stiker_id_val).alignment = align_center
        ws2.cell(row=row_idx2, column=3, value=ticket_code_val).alignment = align_center
        ws2.cell(row=row_idx2, column=4, value=scan_time_str).alignment = align_center
        ws2.cell(row=row_idx2, column=5, value=worker_name).alignment = align_left

        c_p_col = ws2.cell(row=row_idx2, column=6, value=patok_str)
        c_p_col.alignment = align_center
        c_p_col.font = font_bold

        ws2.cell(row=row_idx2, column=7, value=order_num).alignment = align_center
        ws2.cell(row=row_idx2, column=8, value=box_num).alignment = align_center
        ws2.cell(row=row_idx2, column=9, value=model_name).alignment = align_left
        ws2.cell(row=row_idx2, column=10, value=op_name).alignment = align_left

        c_q = ws2.cell(row=row_idx2, column=11, value=qty)
        c_q.alignment = align_right
        c_q.number_format = '#,##0'

        c_p = ws2.cell(row=row_idx2, column=12, value=float(price))
        c_p.alignment = align_right
        c_p.number_format = '#,##0'

        c_t = ws2.cell(row=row_idx2, column=13, value=float(tot_amt))
        c_t.alignment = align_right
        c_t.number_format = '#,##0'

        for col_idx in range(1, 14):
            c_node = ws2.cell(row=row_idx2, column=col_idx)
            c_node.border = thin_border
            if c_fill2.fill_type:
                c_node.fill = c_fill2
            if col_idx != 6:
                c_node.font = font_regular

        total_detail_units += qty
        total_detail_amount += tot_amt

        row_idx2 += 1
        detail_counter += 1

    # Sheet 2 Jami qatori
    ws2.merge_cells(start_row=row_idx2, start_column=1, end_row=row_idx2, end_column=10)
    c_tot_label2 = ws2.cell(row=row_idx2, column=1, value="JAMI / BARCHASI:")
    c_tot_label2.font = font_bold
    c_tot_label2.alignment = align_right

    c_tot_q2 = ws2.cell(row=row_idx2, column=11, value=total_detail_units)
    c_tot_q2.font = font_bold
    c_tot_q2.alignment = align_right
    c_tot_q2.number_format = '#,##0'

    ws2.cell(row=row_idx2, column=12, value="")

    c_tot_amt2 = ws2.cell(row=row_idx2, column=13, value=float(total_detail_amount))
    c_tot_amt2.font = font_bold
    c_tot_amt2.alignment = align_right
    c_tot_amt2.number_format = '#,##0'

    for col_idx in range(1, 14):
        c_node = ws2.cell(row=row_idx2, column=col_idx)
        c_node.fill = total_fill
        c_node.border = thick_bottom_border

    # -------------------------------------------------------------
    # 4-VARAQ: PATOKLAR (EKRANLAR) XULOSASI VA KUNLIK ISH HAQI
    # -------------------------------------------------------------
    ws3 = wb.create_sheet(title="Patoklar Xulosasi")
    ws3.views.sheetView[0].showGridLines = True

    ws3.merge_cells("A1:G1")
    ws3["A1"] = f"PATOKLAR (EKRANLAR) BO'YICHA KUNLIK XULOSA VA ISH HAQI — {target_date.strftime('%d.%m.%Y')}"
    ws3["A1"].font = font_title
    ws3["A1"].alignment = align_left
    ws3.row_dimensions[1].height = 26

    ws3.merge_cells("A2:G2")
    ws3["A2"] = "Har bir patok (ekran) kesimida kun davomida biriktirilgan faol xodimlar soni, tikilgan jami mahsulot va topilgan umumiy ish haqi"
    ws3["A2"].font = font_subtitle
    ws3["A2"].alignment = align_left
    ws3.row_dimensions[2].height = 18

    headers_ws3 = [
        ("№", 5, align_center),
        ("Patok (Ekran)", 18, align_center),
        ("Faol Xodimlar Soni", 20, align_center),
        ("Xodimlar (F.I.SH)", 45, align_wrap),
        ("Tikilgan Mahsulot (Dona)", 24, align_right),
        ("Jami Ish Haqi (UZS)", 24, align_right),
        ("O'rtacha Ish Haqi / Xodim (UZS)", 26, align_right),
    ]

    header_row_ws3 = 4
    ws3.row_dimensions[header_row_ws3].height = 24
    for col_idx, (header_text, width, alignment) in enumerate(headers_ws3, 1):
        cell = ws3.cell(row=header_row_ws3, column=col_idx, value=header_text)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_center
        cell.border = thin_border
        col_letter = get_column_letter(col_idx)
        ws3.column_dimensions[col_letter].width = width

    sorted_patoks = sorted(
        patok_summary.values(),
        key=lambda p: (0 if p['screen_number'] is not None else 1, p['screen_number'] or 9999)
    )

    row_idx3 = 5
    patok_counter = 1
    tot_patok_workers = 0
    tot_patok_units = 0
    tot_patok_earned = Decimal('0.00')

    for p_data in sorted_patoks:
        is_zebra3 = (patok_counter % 2 == 0)
        c_fill3 = zebra_fill if is_zebra3 else PatternFill(fill_type=None)

        w_names_str = ", ".join(p_data['workers_names'])
        w_cnt = p_data['workers_count']
        u_cnt = p_data['total_units']
        e_amt = p_data['total_earned']
        avg_amt = (e_amt / w_cnt) if w_cnt > 0 else Decimal('0.00')

        ws3.cell(row=row_idx3, column=1, value=patok_counter).alignment = align_center

        c_pname = ws3.cell(row=row_idx3, column=2, value=p_data['patok_name'])
        c_pname.alignment = align_center
        c_pname.font = font_bold

        c_wcnt = ws3.cell(row=row_idx3, column=3, value=w_cnt)
        c_wcnt.alignment = align_center
        c_wcnt.font = font_bold
        c_wcnt.number_format = '#,##0'

        c_wnames = ws3.cell(row=row_idx3, column=4, value=w_names_str)
        c_wnames.alignment = align_wrap

        c_u = ws3.cell(row=row_idx3, column=5, value=u_cnt)
        c_u.alignment = align_right
        c_u.font = font_bold
        c_u.number_format = '#,##0'

        c_e = ws3.cell(row=row_idx3, column=6, value=float(e_amt))
        c_e.alignment = align_right
        c_e.number_format = '#,##0'
        if e_amt > 0:
            c_e.font = font_green_bold
            c_e.fill = green_fill

        c_avg = ws3.cell(row=row_idx3, column=7, value=float(avg_amt))
        c_avg.alignment = align_right
        c_avg.font = font_bold
        c_avg.number_format = '#,##0'

        for col_idx in range(1, 8):
            c_node = ws3.cell(row=row_idx3, column=col_idx)
            c_node.border = thin_border
            if col_idx != 6 or e_amt == 0:
                if c_fill3.fill_type:
                    c_node.fill = c_fill3
            if col_idx not in (2, 3, 5, 6, 7):
                c_node.font = font_regular

        tot_patok_workers += w_cnt
        tot_patok_units += u_cnt
        tot_patok_earned += e_amt

        row_idx3 += 1
        patok_counter += 1

    # Sheet 3 JAMI qatori
    ws3.merge_cells(start_row=row_idx3, start_column=1, end_row=row_idx3, end_column=2)
    c_tot_p_lbl = ws3.cell(row=row_idx3, column=1, value="JAMI / BARCHA PATOKLAR:")
    c_tot_p_lbl.font = font_bold
    c_tot_p_lbl.alignment = align_right

    c_tot_w = ws3.cell(row=row_idx3, column=3, value=f"=SUM(C5:C{row_idx3-1})") if row_idx3 > 5 else ws3.cell(row=row_idx3, column=3, value=0)
    c_tot_w.font = font_bold
    c_tot_w.alignment = align_center
    c_tot_w.number_format = '#,##0'

    ws3.cell(row=row_idx3, column=4, value="")

    c_tot_u3 = ws3.cell(row=row_idx3, column=5, value=f"=SUM(E5:E{row_idx3-1})") if row_idx3 > 5 else ws3.cell(row=row_idx3, column=5, value=0)
    c_tot_u3.font = font_bold
    c_tot_u3.alignment = align_right
    c_tot_u3.number_format = '#,##0'

    c_tot_e3 = ws3.cell(row=row_idx3, column=6, value=f"=SUM(F5:F{row_idx3-1})") if row_idx3 > 5 else ws3.cell(row=row_idx3, column=6, value=0)
    c_tot_e3.font = font_bold
    c_tot_e3.alignment = align_right
    c_tot_e3.number_format = '#,##0'

    c_tot_avg3 = ws3.cell(row=row_idx3, column=7, value=f"=IF(C{row_idx3}>0, F{row_idx3}/C{row_idx3}, 0)")
    c_tot_avg3.font = font_bold
    c_tot_avg3.alignment = align_right
    c_tot_avg3.number_format = '#,##0'

    ws3.row_dimensions[row_idx3].height = 24
    for col_idx in range(1, 8):
        c_node = ws3.cell(row=row_idx3, column=col_idx)
        c_node.fill = total_fill
        c_node.border = thick_bottom_border

    # Faylni xotiraga yozish
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _build_day_sheet(
    wb,
    target_date: datetime.date,
    day_tickets: list,
    all_workers: list,
    yesterday_balances: dict = None,
    days_worked_map: dict = None,
    navy_fill=None,
    zebra_fill=None,
    green_fill=None,
    total_fill=None,
    font_title=None,
    font_subtitle=None,
    font_header=None,
    font_bold=None,
    font_regular=None,
    font_green_bold=None,
    thin_border=None,
    thick_bottom_border=None,
    align_center=None,
    align_left=None,
    align_right=None,
    align_wrap=None
):
    """
    Har bir kun uchun alohida varaq (list) yaratuvchi funksiya.
    Masalan: '01.09', '02.09', ... '10.09'.
    Ustunlar:
    1. №
    2. Xodim UID
    3. F.I.SH
    4. Ishlagan Kunlari
    5. Tikilgan Ishlar (Operatsiyalar) - masalan: Meto: 400 ta, Dazmol: 100 ta
    6. Bugungi Ish Haqi (UZS)
    7. Kechagi Balans (UZS)
    8. Joriy Balans (UZS) - Formula: =G{row}+F{row}
    9. Qutilar Soni
    10. Stikerlar Soni
    11. Skanerlangan Stikerlar (Ixcham format: Qutilar va ketma-ket ID diapazonlari)
    """
    sheet_title = f"{target_date.day:02d}.{target_date.month:02d}"
    ws = wb.create_sheet(title=sheet_title)
    ws.views.sheetView[0].showGridLines = True

    WEEKDAYS_UZ = {
        0: "Dushanba",
        1: "Seshanba",
        2: "Chorshanba",
        3: "Payshanba",
        4: "Juma",
        5: "Shanba",
        6: "Yakshanba"
    }
    weekday_name = WEEKDAYS_UZ.get(target_date.weekday(), "")

    # Group tickets by worker
    worker_tickets_map = {}
    for t in day_tickets:
        worker_tickets_map.setdefault(t.worker_id, []).append(t)

    day_units = sum(t.quantity for t in day_tickets)
    day_earned = sum(t.total_amount for t in day_tickets)
    active_count = len(worker_tickets_map)

    # 1. Sarlavha
    ws.merge_cells("A1:L1")
    ws["A1"] = f"TERRY JAR — KUNLIK ISH HAQI VA STIKERLAR HISOBOTI ({target_date.strftime('%d.%m.%Y')})"
    ws["A1"].font = font_title
    ws["A1"].alignment = align_left
    ws.row_dimensions[1].height = 26

    ws.merge_cells("A2:L2")
    ws["A2"] = (
        f"Sana: {target_date.strftime('%d.%m.%Y')} ({weekday_name}) | "
        f"Faol xodimlar: {active_count} nafar | "
        f"Tikilgan jami: {day_units:,} dona | "
        f"Bugungi hisoblangan ish haqi: {day_earned:,.0f} UZS"
    ).replace(",", " ")
    ws["A2"].font = font_subtitle
    ws["A2"].alignment = align_left
    ws.row_dimensions[2].height = 18

    # 2. Jadval sarlavhalari (12 ta ustun)
    headers = [
        ("№", 5, align_center),
        ("Xodim UID", 13, align_center),
        ("F.I.SH", 26, align_left),
        ("Patok", 12, align_center),
        ("Ishlagan Kunlari", 16, align_center),
        ("Tikilgan Ishlar (Operatsiyalar)", 32, align_wrap),
        ("Bugungi Ish Haqi (UZS)", 22, align_right),
        ("Kechagi Balans (UZS)", 22, align_right),
        ("Joriy Balans (UZS)", 22, align_right),
        ("Qutilar Soni", 14, align_center),
        ("Stikerlar Soni", 14, align_center),
        ("Skanerlangan Stikerlar", 45, align_wrap),
    ]

    ws.row_dimensions[4].height = 24
    for col_idx, (h_text, width, aln) in enumerate(headers, 1):
        c = ws.cell(row=4, column=col_idx, value=h_text)
        c.font = font_header
        c.fill = navy_fill
        c.alignment = align_center
        c.border = thin_border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # Agar bu kunda hech qanday stiker urilmagan bo'lsa
    if not day_tickets:
        ws.merge_cells("A5:L5")
        c_empty = ws.cell(row=5, column=1, value="Ushbu kunda tikuv operatsiyalari qayd etilmagan (Dam olish kuni yoki ish bo'lmagan).")
        c_empty.font = Font(name="Arial", size=10, italic=True, color="64748B")
        c_empty.alignment = align_center
        ws.row_dimensions[5].height = 30
        for col in range(1, 13):
            ws.cell(row=5, column=col).border = thin_border
        return ws

    # Xodimlarni ushbu kunda topgan puli bo'yicha kamayish tartibida saralaymiz
    def sort_key(w):
        t_list = worker_tickets_map.get(w.id, [])
        return (len(t_list) > 0, sum(t.total_amount for t in t_list))

    sorted_workers = sorted(all_workers, key=sort_key, reverse=True)

    row_idx = 5
    counter = 1
    total_u = 0
    total_e = Decimal('0.00')
    total_boxes = 0
    total_t = 0

    for w in sorted_workers:
        w_tickets = worker_tickets_map.get(w.id, [])
        # Faqat ushbu kunda ishlagan xodimlarni chiqaramiz
        if not w_tickets:
            continue

        u = sum(t.quantity for t in w_tickets)
        e = sum(t.total_amount for t in w_tickets)
        days_w = days_worked_map.get(w.id, 0) if days_worked_map else 1
        yesterday_bal = yesterday_balances.get(w.id, Decimal('0.00')) if yesterday_balances else Decimal('0.00')

        # Xodimning kun oxiridagi oxirgi turgan patogi (ekran raqami)
        last_screen_num = None
        for t in reversed(w_tickets):
            if t.screen_number is not None:
                last_screen_num = t.screen_number
                break
        patok_display = get_patok_name(last_screen_num) if last_screen_num else "—"

        # Operatsiyalar xulosasi
        op_stats = {}
        for t in w_tickets:
            op_name = t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else "Operatsiya"
            op_stats[op_name] = op_stats.get(op_name, 0) + (t.quantity or 0)
        sorted_ops = sorted(op_stats.items(), key=lambda x: -x[1])
        ops_display = "\n".join(f"{name}: {qty:,} ta".replace(",", " ") for name, qty in sorted_ops)

        num_ops = len(sorted_ops)
        if num_ops > 1:
            ws.row_dimensions[row_idx].height = max(24, num_ops * 18)
        else:
            ws.row_dimensions[row_idx].height = 24

        boxes_count = len({t.box_id for t in w_tickets if t.box_id})
        stikers_display = compact_ticket_ids(w_tickets)

        is_zebra = (counter % 2 == 0)
        c_fill = zebra_fill if is_zebra else PatternFill(fill_type=None)

        ws.cell(row=row_idx, column=1, value=counter).alignment = align_center

        c_uid = ws.cell(row=row_idx, column=2, value=get_clean_worker_uid(w))
        c_uid.alignment = align_center
        c_uid.font = font_bold

        c_name = ws.cell(row=row_idx, column=3, value=w.full_name)
        c_name.alignment = align_left
        c_name.font = font_bold

        c_patok = ws.cell(row=row_idx, column=4, value=patok_display)
        c_patok.alignment = align_center
        c_patok.font = font_bold

        ws.cell(row=row_idx, column=5, value=days_w).alignment = align_center

        c_ops = ws.cell(row=row_idx, column=6, value=ops_display)
        c_ops.alignment = align_wrap

        c_e = ws.cell(row=row_idx, column=7, value=float(e))
        c_e.alignment = align_right
        c_e.number_format = '#,##0'
        if e > 0:
            c_e.font = font_green_bold
            c_e.fill = green_fill

        c_yest = ws.cell(row=row_idx, column=8, value=float(yesterday_bal))
        c_yest.alignment = align_right
        c_yest.number_format = '#,##0'

        # Joriy Balans formulasi: Kechagi Balans + Bugungi Ish Haqi
        c_curr = ws.cell(row=row_idx, column=9, value=f"=H{row_idx}+G{row_idx}")
        c_curr.alignment = align_right
        c_curr.font = font_bold
        c_curr.number_format = '#,##0'

        c_boxes = ws.cell(row=row_idx, column=10, value=boxes_count)
        c_boxes.alignment = align_center
        c_boxes.number_format = '#,##0'

        c_t_cnt = ws.cell(row=row_idx, column=11, value=len(w_tickets))
        c_t_cnt.alignment = align_center
        c_t_cnt.number_format = '#,##0'

        c_st = ws.cell(row=row_idx, column=12, value=stikers_display)
        c_st.alignment = align_wrap

        for col_idx in range(1, 13):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx != 7 or e == 0:
                if c_fill.fill_type:
                    cell.fill = c_fill
            if col_idx not in (2, 3, 4, 7, 9):
                cell.font = font_regular

        total_u += u
        total_e += e
        total_boxes += boxes_count
        total_t += len(w_tickets)

        row_idx += 1
        counter += 1

    # JAMI / KUNLIK qatori
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=5)
    c_tot_label = ws.cell(row=row_idx, column=1, value="JAMI / KUNLIK:")
    c_tot_label.font = font_bold
    c_tot_label.alignment = align_right

    c_tot_u = ws.cell(row=row_idx, column=6, value=f"{total_u:,} ta".replace(",", " "))
    c_tot_u.font = font_bold
    c_tot_u.alignment = align_right

    c_tot_e = ws.cell(row=row_idx, column=7, value=f"=SUM(G5:G{row_idx-1})")
    c_tot_e.font = font_bold
    c_tot_e.alignment = align_right
    c_tot_e.number_format = '#,##0'

    c_tot_yest = ws.cell(row=row_idx, column=8, value=f"=SUM(H5:H{row_idx-1})")
    c_tot_yest.font = font_bold
    c_tot_yest.alignment = align_right
    c_tot_yest.number_format = '#,##0'

    c_tot_curr = ws.cell(row=row_idx, column=9, value=f"=SUM(I5:I{row_idx-1})")
    c_tot_curr.font = font_bold
    c_tot_curr.alignment = align_right
    c_tot_curr.number_format = '#,##0'

    c_tot_b = ws.cell(row=row_idx, column=10, value=f"=SUM(J5:J{row_idx-1})")
    c_tot_b.font = font_bold
    c_tot_b.alignment = align_center
    c_tot_b.number_format = '#,##0'

    c_tot_t = ws.cell(row=row_idx, column=11, value=f"=SUM(K5:K{row_idx-1})")
    c_tot_t.font = font_bold
    c_tot_t.alignment = align_center
    c_tot_t.number_format = '#,##0'

    ws.cell(row=row_idx, column=12, value="")

    ws.row_dimensions[row_idx].height = 24
    for col in range(1, 13):
        c_n = ws.cell(row=row_idx, column=col)
        c_n.fill = total_fill
        c_n.border = thick_bottom_border

    return ws


def generate_month_to_date_excel_report(target_date: datetime.date = None) -> io.BytesIO:
    """
    Joriy oyning boshidan (1-kuni 00:00 dan) to hozirgi kungacha bo'lgan to'liq oylik
    ish haqi hisoboti (Har bir kun alohida varaq/list bo'lib, kunlar yig'ilib boradi):
    - 1-varaq: "Oylik Umumiy Tabel" (Xodim, ishlagan kunlari, jami dona, hisoblangan ish haqi, avanslar, oylik, qoldiq)
    - 2...N-varaqlari: "01.09", "02.09", ... "10.09" (Har bir kunning alohida hisobot varag'i)
    - Oxirgi varaq: "Barcha Stikerlar" (Oy boshidan beri urilgan barcha stikerlar tafsiloti)
    """
    if not HAS_OPENPYXL:
        raise ImportError("Serverda 'openpyxl' kutubxonasi o'rnatilmagan.")

    now = timezone.localtime()
    if target_date is None:
        target_date = now.date()

    start_of_month = target_date.replace(day=1)
    tz = timezone.get_current_timezone()
    month_start_dt = timezone.make_aware(datetime.datetime.combine(start_of_month, datetime.time.min), tz)
    month_end_dt = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.max), tz)

    # 1. Oy boshidan beri skanerlangan barcha biletlar
    month_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=target_date.year,
        scanned_at__month=target_date.month,
        scanned_at__date__lte=target_date
    ).select_related(
        'worker',
        'article_operation__operation',
        'article_operation__article__model',
        'box__order'
    ).order_by('scanned_at')

    # 0. Ratsenka (operatsiya narxi) o'zgargan bo'lsa, ushbu oyda skanerlangan barcha biletlar
    # narxlarini va jami summalarini eng so'nggi ratsenkalar bilan kafolatli qayta hisoblash:
    ao_ids = list(month_tickets.values_list('article_operation_id', flat=True).distinct())
    for ao in ArticleOperation.objects.filter(id__in=ao_ids):
        ao.sync_price_to_tickets()

    # Qayta yangilangan biletlarni yuklash
    month_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=target_date.year,
        scanned_at__month=target_date.month,
        scanned_at__date__lte=target_date
    ).select_related(
        'worker',
        'article_operation__operation',
        'article_operation__article__model',
        'box__order'
    ).order_by('scanned_at')

    # Barcha xodimlar
    active_worker_ids = set(month_tickets.values_list('worker_id', flat=True))
    workers = list(Worker.objects.filter(
        Q(id__in=active_worker_ids) | Q(is_active=True)
    ).select_related('user').order_by('worker_id'))

    # Xodimlar bo'yicha agregatsiya
    worker_ticket_stats = month_tickets.values('worker_id').annotate(
        units=Sum('quantity'),
        gross=Sum('total_amount'),
        days_worked=Count('scanned_at__date', distinct=True)
    )
    worker_ticket_map = {item['worker_id']: item for item in worker_ticket_stats}

    # Payouts (avans, oylik va bonuslar)
    month_payout_qs = WorkerPayout.objects.filter(
        payout_date__range=(start_of_month, target_date)
    ).values('worker_id').annotate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY)),
        bonuses=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.BONUS)),
    )
    month_payout_map = {item['worker_id']: item for item in month_payout_qs}

    # Kunlik norma bonuslari (agar tizim sozlamalarida DAILY_BONUS_AMOUNT belgilangan bo'lsa)
    daily_bonus_amount = getattr(settings, 'DAILY_BONUS_AMOUNT', 0)
    worker_daily_bonuses = {}
    if daily_bonus_amount > 0:
        for w in workers:
            w_tickets = [t for t in month_tickets if t.worker_id == w.id]
            t_by_date = {}
            for t in w_tickets:
                if t.scanned_at:
                    d = timezone.localtime(t.scanned_at).date()
                    t_by_date.setdefault(d, []).append(t)

            w_bonus_sum = Decimal('0.00')
            for d, d_tickets in t_by_date.items():
                model_stats = {}
                for t in d_tickets:
                    ao = t.article_operation
                    art = ao.article if ao else None
                    pmodel = art.model if art else None
                    norm = (pmodel.daily_norm if (pmodel and pmodel.daily_norm) else (art.daily_norm if (art and art.daily_norm) else 1000)) or 1000
                    diff = float(ao.difficulty) if (ao and ao.difficulty) else 1.0
                    pts = t.quantity * diff
                    m_key = f"m_{pmodel.id}" if pmodel else (f"art_{art.id}" if art else "0")
                    if m_key not in model_stats:
                        model_stats[m_key] = {'norm': norm, 'points': 0.0}
                    model_stats[m_key]['points'] += pts

                total_day_pct = Decimal('0.0')
                for mk, mdata in model_stats.items():
                    if mdata['norm'] > 0:
                        total_day_pct += (Decimal(str(mdata['points'])) / Decimal(str(mdata['norm']))) * Decimal('100.0')

                if total_day_pct > Decimal('100.0'):
                    w_bonus_sum += Decimal(str(daily_bonus_amount))
            if w_bonus_sum > 0:
                worker_daily_bonuses[w.id] = w_bonus_sum

    # Oy boshidan oldingi balanslar (1 martalik tezkor agregatsiya):
    tickets_pre_month = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__lt=month_start_dt
    ).values('worker_id').annotate(total=Sum('total_amount'))
    pre_month_earned_map = {item['worker_id']: item['total'] for item in tickets_pre_month}

    payouts_pre_month = WorkerPayout.objects.filter(
        payout_date__lt=start_of_month
    ).values('worker_id').annotate(total=Sum('amount'))
    pre_month_paid_map = {item['worker_id']: item['total'] for item in payouts_pre_month}

    # Ushbu oy davomidagi barcha to'lovlar (worker_id, date) bo'yicha:
    month_payouts_by_worker_date = {}
    for p in WorkerPayout.objects.filter(payout_date__range=(start_of_month, target_date)):
        key = (p.worker_id, p.payout_date)
        month_payouts_by_worker_date[key] = month_payouts_by_worker_date.get(key, Decimal('0.00')) + p.amount

    running_balances = {}
    for w in workers:
        earned_pre = pre_month_earned_map.get(w.id, Decimal('0.00')) or Decimal('0.00')
        paid_pre = pre_month_paid_map.get(w.id, Decimal('0.00')) or Decimal('0.00')
        running_balances[w.id] = earned_pre - paid_pre

    worker_worked_days_set = {w.id: set() for w in workers}

    wb = openpyxl.Workbook()

    # Ranglar va shriftlar
    navy_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    dark_slate_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    zebra_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    total_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")

    font_title = Font(name="Arial", size=15, bold=True, color="0F172A")
    font_subtitle = Font(name="Arial", size=10, italic=True, color="475569")
    font_header = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    font_bold = Font(name="Arial", size=10, bold=True, color="0F172A")
    font_regular = Font(name="Arial", size=10, color="0F172A")
    font_green_bold = Font(name="Arial", size=10, bold=True, color="166534")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )
    thick_bottom_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='medium', color='0F172A')
    )

    align_center = Alignment(horizontal='center', vertical='center')
    align_left = Alignment(horizontal='left', vertical='center')
    align_right = Alignment(horizontal='right', vertical='center')
    align_wrap = Alignment(horizontal='left', vertical='center', wrap_text=True)

    # -------------------------------------------------------------
    # 1-VARAQ: XODIMLAR OYLIK TABELI (UMUMIY)
    # Ustunlar:
    # 1. №
    # 2. Xodim UID
    # 3. F.I.SH
    # 4. Ishlagan Kunlari
    # 5. Hisoblangan Ish Haqi (UZS) (dona-bay tikilgan ishlar summasi)
    # 6. Bonus (UZS) (tizimdagi mukofot va bonuslar)
    # 7. Berilgan Avans (UZS) (qo'lda kiritiladi / tahrirlanadi)
    # 8. Magazin (UZS) (qo'lda kiritiladi)
    # 9. To'langan Oylik (UZS)
    # 10. To'lanishi Kerak Qoldiq (UZS) - Formula: =(E+F)-G-H-I
    # -------------------------------------------------------------
    # -------------------------------------------------------------
    # 1-VARAQ: XODIMLAR OYLIK TABELI (UMUMIY)
    # -------------------------------------------------------------
    _build_monthly_tabel_sheet(
        wb=wb,
        target_date=target_date,
        existing_ws=wb.active
    )

    # -------------------------------------------------------------
    # 2...N-VARAQLAR: KUNLIK HISOBOT VARAQLARI (01.MM dan BUGUN.MM gacha)
    # Har bir kun alohida varaq (list) sifatida saqlanadi
    # -------------------------------------------------------------
    tickets_by_date = {}
    for t in month_tickets:
        if t.scanned_at:
            t_date = timezone.localtime(t.scanned_at).date()
            if t_date not in tickets_by_date:
                tickets_by_date[t_date] = []
            tickets_by_date[t_date].append(t)

    for day_num in range(1, target_date.day + 1):
        cur_date = datetime.date(target_date.year, target_date.month, day_num)
        cur_day_tickets = tickets_by_date.get(cur_date, [])

        # Kechagi balanslar (bu kunga kirish holatidagi balans):
        yesterday_balances = {w.id: running_balances[w.id] for w in workers}

        # Agar bugun bilet skanerlagan bo'lsa, ishlagan kunlar to'plamiga qo'shish:
        for t in cur_day_tickets:
            if t.worker_id and t.worker_id in worker_worked_days_set:
                worker_worked_days_set[t.worker_id].add(cur_date)

        days_worked_map = {w.id: len(worker_worked_days_set[w.id]) for w in workers}

        _build_day_sheet(
            wb=wb,
            target_date=cur_date,
            day_tickets=cur_day_tickets,
            all_workers=workers,
            yesterday_balances=yesterday_balances,
            days_worked_map=days_worked_map,
            navy_fill=navy_fill,
            zebra_fill=zebra_fill,
            green_fill=green_fill,
            total_fill=total_fill,
            font_title=font_title,
            font_subtitle=font_subtitle,
            font_header=font_header,
            font_bold=font_bold,
            font_regular=font_regular,
            font_green_bold=font_green_bold,
            thin_border=thin_border,
            thick_bottom_border=thick_bottom_border,
            align_center=align_center,
            align_left=align_left,
            align_right=align_right,
            align_wrap=align_wrap
        )

        # Ushbu kun yakunida har bir xodimning balansini yangilab qo'yamiz (keyingi kunlar uchun):
        for w in workers:
            w_tickets_today = [t for t in cur_day_tickets if t.worker_id == w.id]
            today_earned = sum(t.total_amount for t in w_tickets_today)
            today_paid = month_payouts_by_worker_date.get((w.id, cur_date), Decimal('0.00'))
            running_balances[w.id] += (today_earned - today_paid)

    # -------------------------------------------------------------
    # OXIRGI VARAQ: BARCHA SKANERLANGAN STIKERLAR (OYLIK RO'YXAT)
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Barcha Stikerlar")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells('A1:L1')
    c_title2 = ws2['A1']
    c_title2.value = "TERRY JAR — OY BO'YICHA SKANERLANGAN BARCHA STIKERLAR RO'YXATI"
    c_title2.font = font_title
    c_title2.alignment = align_left
    ws2.row_dimensions[1].height = 26

    ws2.merge_cells('A2:L2')
    c_sub2 = ws2['A2']
    c_sub2.value = f"Davr: {start_of_month.strftime('%d.%m.%Y')} — {target_date.strftime('%d.%m.%Y')} | Jami stikerlar: {month_tickets.count()} ta"
    c_sub2.font = font_subtitle
    c_sub2.alignment = align_left
    ws2.row_dimensions[2].height = 18

    headers2 = [
        "№", "Stiker ID", "Skanerlangan Vaqt", "Xodim UID", "Xodim F.I.SH",
        "Zakaz №", "Quti №", "Model", "Operatsiya",
        "Soni (dona)", "Narxi (UZS)", "Jami Summa (UZS)"
    ]

    ws2.row_dimensions[4].height = 24
    for col_idx, h in enumerate(headers2, start=1):
        cell = ws2.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = dark_slate_fill
        cell.alignment = align_center
        cell.border = thin_border

    row_idx2 = 5
    detail_counter = 1
    total_detail_units = 0
    total_detail_amount = Decimal('0.00')

    for t in month_tickets:
        scan_time_str = timezone.localtime(t.scanned_at).strftime("%d.%m.%Y %H:%M:%S") if t.scanned_at else "—"
        worker_id = get_clean_worker_uid(t.worker) if t.worker else "—"
        worker_name = t.worker.full_name if t.worker else "Noma'lum"

        order_num = "—"
        box_num = "—"
        if t.box:
            box_num = str(t.box.box_number)
            if t.box.order:
                order_num = t.box.order.order_number

        model_name = "—"
        op_name = "—"
        if t.article_operation:
            if t.article_operation.operation:
                op_name = t.article_operation.operation.name
            if t.article_operation.article:
                if t.article_operation.article.model:
                    model_name = t.article_operation.article.model.name
                else:
                    model_name = t.article_operation.article.name

        qty = t.quantity or 0
        price = t.price_per_unit or Decimal('0.00')
        tot_amt = t.total_amount or Decimal('0.00')

        c_fill2 = zebra_fill if detail_counter % 2 == 0 else white_fill

        ws2.cell(row=row_idx2, column=1, value=detail_counter).alignment = align_center
        ws2.cell(row=row_idx2, column=2, value=t.id).alignment = align_center
        ws2.cell(row=row_idx2, column=3, value=scan_time_str).alignment = align_center
        ws2.cell(row=row_idx2, column=4, value=worker_id).alignment = align_center
        ws2.cell(row=row_idx2, column=5, value=worker_name).alignment = align_left
        ws2.cell(row=row_idx2, column=6, value=order_num).alignment = align_center
        ws2.cell(row=row_idx2, column=7, value=box_num).alignment = align_center
        ws2.cell(row=row_idx2, column=8, value=model_name).alignment = align_left
        ws2.cell(row=row_idx2, column=9, value=op_name).alignment = align_left

        c_q = ws2.cell(row=row_idx2, column=10, value=qty)
        c_q.alignment = align_right
        c_q.number_format = '#,##0'

        c_p = ws2.cell(row=row_idx2, column=11, value=float(price))
        c_p.alignment = align_right
        c_p.number_format = '#,##0'

        c_t = ws2.cell(row=row_idx2, column=12, value=float(tot_amt))
        c_t.alignment = align_right
        c_t.number_format = '#,##0'

        for col_idx in range(1, 13):
            c_node = ws2.cell(row=row_idx2, column=col_idx)
            c_node.border = thin_border
            if c_fill2.fill_type:
                c_node.fill = c_fill2
            c_node.font = font_regular

        total_detail_units += qty
        total_detail_amount += tot_amt

        row_idx2 += 1
        detail_counter += 1

    # Sheet 2 Jami qatori
    ws2.merge_cells(start_row=row_idx2, start_column=1, end_row=row_idx2, end_column=9)
    c_tot_label2 = ws2.cell(row=row_idx2, column=1, value="JAMI / BARCHASI:")
    c_tot_label2.font = font_bold
    c_tot_label2.alignment = align_right

    c_tot_q2 = ws2.cell(row=row_idx2, column=10, value=total_detail_units)
    c_tot_q2.font = font_bold
    c_tot_q2.alignment = align_right
    c_tot_q2.number_format = '#,##0'

    ws2.cell(row=row_idx2, column=11, value="")

    c_tot_amt2 = ws2.cell(row=row_idx2, column=12, value=float(total_detail_amount))
    c_tot_amt2.font = font_bold
    c_tot_amt2.alignment = align_right
    c_tot_amt2.number_format = '#,##0'

    for col_idx in range(1, 13):
        c_node = ws2.cell(row=row_idx2, column=col_idx)
        c_node.fill = total_fill
        c_node.border = thick_bottom_border

    col_widths2 = [6, 12, 20, 12, 26, 16, 10, 22, 24, 14, 16, 18]
    for i, w_val in enumerate(col_widths2, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w_val

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

