import io
import datetime
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Q, Count

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from accounts.models import Worker, WorkerPayout
from production.models import Ticket


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

    # Xodimlar bo'yicha guruhlash
    # O'sha kuni kamida 1 ta bilet skanerlagan yoki faol bo'lgan barcha ishchilar
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
    ws1.merge_cells("A1:J1")
    ws1["A1"] = f"TERRY JAR — KUNLIK ISH HAQI VA STIKERLAR HISOBOTI ({target_date.strftime('%d.%m.%Y')})"
    ws1["A1"].font = font_title
    ws1["A1"].alignment = align_left

    ws1.merge_cells("A2:J2")
    ws1["A2"] = f"Hisobot shakllantirilgan vaqt: {timezone.localtime().strftime('%d.%m.%Y %H:%M')} | Avtomatik Telegram eksport"
    ws1["A2"].font = font_subtitle
    ws1["A2"].alignment = align_left

    headers_ws1 = [
        ("№", 5, align_center),
        ("Xodim ID", 12, align_center),
        ("Ism Familiya", 26, align_left),
        ("Telefon Raqami", 16, align_center),
        ("Bugun Tikkan (Dona)", 18, align_right),
        ("Bugungi Ish Haqi (UZS)", 22, align_right),
        ("Shu Oydagi Ish Haqi (UZS)", 24, align_right),
        ("Joriy Balans / Qoldiq (UZS)", 24, align_right),
        ("Biletlar Soni", 14, align_center),
        ("Urgan Stikerlar ID lari", 45, align_wrap),
    ]

    # Header qatori
    header_row_ws1 = 4
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
    total_month_earned = Decimal('0.00')
    total_balance = Decimal('0.00')
    total_tickets_count = 0

    # Xodimlarni bugungi topgan puli bo'yicha kamayish tartibida saralaymiz
    def worker_sort_key(w):
        t_list = worker_tickets_map.get(w.id, [])
        return sum(t.total_amount for t in t_list)

    sorted_workers = sorted(workers, key=worker_sort_key, reverse=True)

    for w in sorted_workers:
        w_tickets = worker_tickets_map.get(w.id, [])
        
        # Faqat bugun ishlagan yoki faol xodimlar
        today_u = sum(t.quantity for t in w_tickets)
        today_e = sum(t.total_amount for t in w_tickets)
        
        # Agar xodim umuman ishlamagan bo'lsa va ro'yxatda biletlar bo'lsa, keyinroq ko'rsatiladi
        # Shu oydagi jami hisoblangan maosh
        month_e = Ticket.objects.filter(
            worker=w,
            status=Ticket.Status.SCANNED,
            scanned_at__date__gte=start_of_month,
            scanned_at__date__lte=target_date
        ).aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')

        w_balance = w.balance

        # Urgan stikerlar ID lari
        # Har bir bilet uchun stiker_code yoki ticket_code yoki id
        stiker_ids = []
        for t in w_tickets:
            code_str = t.stiker_code or t.ticket_code or f"TK#{t.id}"
            stiker_ids.append(str(code_str))
        stikers_joined = ", ".join(stiker_ids) if stiker_ids else "—"

        is_zebra = (counter % 2 == 0)
        current_fill = zebra_fill if is_zebra else PatternFill(fill_type=None)

        ws1.cell(row=row_idx, column=1, value=counter).alignment = align_center
        ws1.cell(row=row_idx, column=2, value=w.worker_id).alignment = align_center
        ws1.cell(row=row_idx, column=3, value=w.full_name).alignment = align_left
        ws1.cell(row=row_idx, column=4, value=w.phone_number or "—").alignment = align_center

        c_today_u = ws1.cell(row=row_idx, column=5, value=today_u)
        c_today_u.alignment = align_right
        c_today_u.number_format = '#,##0'

        c_today_e = ws1.cell(row=row_idx, column=6, value=float(today_e))
        c_today_e.alignment = align_right
        c_today_e.number_format = '#,##0'
        if today_e > 0:
            c_today_e.font = font_green_bold
            c_today_e.fill = green_fill

        c_month_e = ws1.cell(row=row_idx, column=7, value=float(month_e))
        c_month_e.alignment = align_right
        c_month_e.number_format = '#,##0'

        c_balance = ws1.cell(row=row_idx, column=8, value=float(w_balance))
        c_balance.alignment = align_right
        c_balance.number_format = '#,##0'

        c_t_cnt = ws1.cell(row=row_idx, column=9, value=len(w_tickets))
        c_t_cnt.alignment = align_center

        c_stikers = ws1.cell(row=row_idx, column=10, value=stikers_joined)
        c_stikers.alignment = align_wrap

        for col_idx in range(1, 11):
            cell = ws1.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx != 6 or today_e == 0:
                if current_fill.fill_type:
                    cell.fill = current_fill
            if col_idx not in (3, 6):
                cell.font = font_regular

        total_today_units += today_u
        total_today_earned += today_e
        total_month_earned += month_e
        total_balance += w_balance
        total_tickets_count += len(w_tickets)

        row_idx += 1
        counter += 1

    # JAMI / TOTAL qatori
    ws1.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=4)
    c_tot_label = ws1.cell(row=row_idx, column=1, value="JAMI / UMUMIY:")
    c_tot_label.font = font_bold
    c_tot_label.alignment = align_right

    c_tot_units = ws1.cell(row=row_idx, column=5, value=total_today_units)
    c_tot_units.font = font_bold
    c_tot_units.alignment = align_right
    c_tot_units.number_format = '#,##0'

    c_tot_earned = ws1.cell(row=row_idx, column=6, value=float(total_today_earned))
    c_tot_earned.font = font_bold
    c_tot_earned.alignment = align_right
    c_tot_earned.number_format = '#,##0'

    c_tot_month = ws1.cell(row=row_idx, column=7, value=float(total_month_earned))
    c_tot_month.font = font_bold
    c_tot_month.alignment = align_right
    c_tot_month.number_format = '#,##0'

    c_tot_bal = ws1.cell(row=row_idx, column=8, value=float(total_balance))
    c_tot_bal.font = font_bold
    c_tot_bal.alignment = align_right
    c_tot_bal.number_format = '#,##0'

    c_tot_t_cnt = ws1.cell(row=row_idx, column=9, value=total_tickets_count)
    c_tot_t_cnt.font = font_bold
    c_tot_t_cnt.alignment = align_center

    ws1.cell(row=row_idx, column=10, value="")

    for col_idx in range(1, 11):
        cell = ws1.cell(row=row_idx, column=col_idx)
        cell.fill = total_fill
        cell.border = thick_bottom_border

    # -------------------------------------------------------------
    # 2-VARAQ: BARCHA SKANERLANGAN STIKERLAR TAFSILOTI
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Skanerlangan Stikerlar")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:K1")
    ws2["A1"] = f"KUN DAVOMIDA SKANERLANGAN BARCHA STIKERLAR (BILETLAR) TAFSILOTI — {target_date.strftime('%d.%m.%Y')}"
    ws2["A1"].font = font_title
    ws2["A1"].alignment = align_left

    ws2.merge_cells("A2:K2")
    ws2["A2"] = "Ushbu varaq orqali har qanday stiker ID si bo'yicha qaysi zakaz, model va operatsiya bajarilganini tekshirish mumkin."
    ws2["A2"].font = font_subtitle
    ws2["A2"].alignment = align_left

    headers_ws2 = [
        ("№", 5, align_center),
        ("Stiker ID / Kod", 16, align_center),
        ("Bilet Kodi", 22, align_center),
        ("Skanerlangan Vaqt", 16, align_center),
        ("Tikuvchi (F.I.SH)", 24, align_left),
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
    navy_fill,
    zebra_fill,
    green_fill,
    total_fill,
    font_title,
    font_subtitle,
    font_header,
    font_bold,
    font_regular,
    font_green_bold,
    thin_border,
    thick_bottom_border,
    align_center,
    align_left,
    align_right,
    align_wrap
):
    """
    Har bir kun uchun alohida varaq (list) yaratuvchi funksiya.
    Masalan: '01.09', '02.09', ... '10.09'.
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
    ws.merge_cells("A1:H1")
    ws["A1"] = f"TERRY JAR — KUNLIK ISH HAQI VA STIKERLAR HISOBOTI ({target_date.strftime('%d.%m.%Y')})"
    ws["A1"].font = font_title
    ws["A1"].alignment = align_left
    ws.row_dimensions[1].height = 26

    ws.merge_cells("A2:H2")
    ws["A2"] = (
        f"Sana: {target_date.strftime('%d.%m.%Y')} ({weekday_name}) | "
        f"Faol xodimlar: {active_count} nafar | "
        f"Tikilgan jami: {day_units:,} dona | "
        f"Hisoblangan ish haqi: {day_earned:,.0f} UZS"
    ).replace(",", " ")
    ws["A2"].font = font_subtitle
    ws["A2"].alignment = align_left
    ws.row_dimensions[2].height = 18

    # 2. Jadval sarlavhalari
    headers = [
        ("№", 5, align_center),
        ("Xodim ID", 12, align_center),
        ("Ism Familiya", 26, align_left),
        ("Telefon", 16, align_center),
        ("Tikilgan Dona", 16, align_right),
        ("Bugungi Ish Haqi (UZS)", 22, align_right),
        ("Biletlar Soni", 14, align_center),
        ("Urgan Stikerlar ID lari", 45, align_wrap),
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
        ws.merge_cells("A5:H5")
        c_empty = ws.cell(row=5, column=1, value="Ushbu kunda tikuv operatsiyalari qayd etilmagan (Dam olish kuni yoki ish bo'lmagan).")
        c_empty.font = Font(name="Arial", size=10, italic=True, color="64748B")
        c_empty.alignment = align_center
        ws.row_dimensions[5].height = 30
        for col in range(1, 9):
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
    total_t = 0

    for w in sorted_workers:
        w_tickets = worker_tickets_map.get(w.id, [])
        u = sum(t.quantity for t in w_tickets)
        e = sum(t.total_amount for t in w_tickets)

        # Ushbu kunda ishlamagan xodimlarni o'tkazib yuboramiz
        if u == 0 and e == Decimal('0.00'):
            continue

        stiker_ids = [str(t.stiker_code or t.ticket_code or f"TK#{t.id}") for t in w_tickets]
        stikers_str = ", ".join(stiker_ids) if stiker_ids else "—"

        is_zebra = (counter % 2 == 0)
        c_fill = zebra_fill if is_zebra else PatternFill(fill_type=None)

        ws.cell(row=row_idx, column=1, value=counter).alignment = align_center
        ws.cell(row=row_idx, column=2, value=w.worker_id).alignment = align_center
        ws.cell(row=row_idx, column=3, value=w.full_name).alignment = align_left
        ws.cell(row=row_idx, column=4, value=w.phone_number or "—").alignment = align_center

        c_u = ws.cell(row=row_idx, column=5, value=u)
        c_u.alignment = align_right
        c_u.number_format = '#,##0'

        c_e = ws.cell(row=row_idx, column=6, value=float(e))
        c_e.alignment = align_right
        c_e.number_format = '#,##0'
        if e > 0:
            c_e.font = font_green_bold
            c_e.fill = green_fill

        c_cnt = ws.cell(row=row_idx, column=7, value=len(w_tickets))
        c_cnt.alignment = align_center

        c_st = ws.cell(row=row_idx, column=8, value=stikers_str)
        c_st.alignment = align_wrap

        for col_idx in range(1, 9):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx != 6 or e == 0:
                if c_fill.fill_type:
                    cell.fill = c_fill
            if col_idx not in (3, 6):
                cell.font = font_regular

        total_u += u
        total_e += e
        total_t += len(w_tickets)

        row_idx += 1
        counter += 1

    # JAMI / KUNLIK qatori
    ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=4)
    c_tot_label = ws.cell(row=row_idx, column=1, value="JAMI / KUNLIK:")
    c_tot_label.font = font_bold
    c_tot_label.alignment = align_right

    c_tot_u = ws.cell(row=row_idx, column=5, value=total_u)
    c_tot_u.font = font_bold
    c_tot_u.alignment = align_right
    c_tot_u.number_format = '#,##0'

    c_tot_e = ws.cell(row=row_idx, column=6, value=float(total_e))
    c_tot_e.font = font_bold
    c_tot_e.alignment = align_right
    c_tot_e.number_format = '#,##0'

    c_tot_t = ws.cell(row=row_idx, column=7, value=total_t)
    c_tot_t.font = font_bold
    c_tot_t.alignment = align_center
    c_tot_t.number_format = '#,##0'

    ws.cell(row=row_idx, column=8, value="")

    for col in range(1, 9):
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
        scanned_at__range=(month_start_dt, month_end_dt)
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

    # Payouts (avans va oyliklar)
    month_payout_qs = WorkerPayout.objects.filter(
        payout_date__range=(start_of_month, target_date)
    ).values('worker_id').annotate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY)),
    )
    month_payout_map = {item['worker_id']: item for item in month_payout_qs}

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
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "Oylik Umumiy Tabel"
    ws1.views.sheetView[0].showGridLines = True

    # Sarlavha
    ws1.merge_cells('A1:L1')
    c_title = ws1['A1']
    c_title.value = "TERRY JAR — OYLIK ISH HAQI VA XODIMLAR TABELI"
    c_title.font = font_title
    c_title.alignment = align_left
    ws1.row_dimensions[1].height = 26

    ws1.merge_cells('A2:L2')
    c_sub = ws1['A2']
    c_sub.value = f"Davr: {start_of_month.strftime('%d.%m.%Y')} 00:00 dan {target_date.strftime('%d.%m.%Y')} {now.strftime('%H:%M')} gacha | Shakllantirilgan vaqt: {now.strftime('%d.%m.%Y %H:%M')}"
    c_sub.font = font_subtitle
    c_sub.alignment = align_left
    ws1.row_dimensions[2].height = 18

    headers1 = [
        "№", "Xodim ID", "F.I.SH", "Telefon", "Ishlagan Kunlari",
        "Tikilgan Dona", "Hisoblangan Ish Haqi (UZS)", "Berilgan Avans (UZS)",
        "To'langan Oylik (UZS)", "To'lanishi Kerak Qoldiq (UZS)", "Joriy Balans (UZS)", "Holati"
    ]

    ws1.row_dimensions[4].height = 24
    for col_idx, h in enumerate(headers1, start=1):
        cell = ws1.cell(row=4, column=col_idx, value=h)
        cell.font = font_header
        cell.fill = navy_fill
        cell.alignment = align_center
        cell.border = thin_border

    tot_units = 0
    tot_gross = Decimal('0.00')
    tot_adv = Decimal('0.00')
    tot_sal = Decimal('0.00')
    tot_net = Decimal('0.00')
    tot_bal = Decimal('0.00')

    row_idx = 5
    counter = 1
    for w in workers:
        t_stat = worker_ticket_map.get(w.id, {})
        u = t_stat.get('units') or 0
        g = t_stat.get('gross') or Decimal('0.00')
        days_w = t_stat.get('days_worked') or 0

        p_stat = month_payout_map.get(w.id, {})
        adv = p_stat.get('advances') or Decimal('0.00')
        sal = p_stat.get('salaries') or Decimal('0.00')
        net = g - adv - sal
        bal = w.balance

        if g == Decimal('0.00') and adv == Decimal('0.00') and sal == Decimal('0.00'):
            status_str = "Ishlamagan"
        elif net <= Decimal('0.00'):
            status_str = "To'liq to'langan"
        elif adv > Decimal('0.00'):
            status_str = "Avans berilgan"
        else:
            status_str = "To'lov kutilmoqda"

        c_fill = zebra_fill if counter % 2 == 0 else white_fill

        ws1.cell(row=row_idx, column=1, value=counter).alignment = align_center
        ws1.cell(row=row_idx, column=2, value=w.worker_id).alignment = align_center
        ws1.cell(row=row_idx, column=3, value=w.full_name).alignment = align_left
        ws1.cell(row=row_idx, column=4, value=w.phone_number or "—").alignment = align_center
        ws1.cell(row=row_idx, column=5, value=days_w).alignment = align_center

        c_u = ws1.cell(row=row_idx, column=6, value=u)
        c_u.alignment = align_right
        c_u.number_format = '#,##0'

        c_g = ws1.cell(row=row_idx, column=7, value=float(g))
        c_g.alignment = align_right
        c_g.number_format = '#,##0'

        c_adv = ws1.cell(row=row_idx, column=8, value=float(adv))
        c_adv.alignment = align_right
        c_adv.number_format = '#,##0'

        c_sal = ws1.cell(row=row_idx, column=9, value=float(sal))
        c_sal.alignment = align_right
        c_sal.number_format = '#,##0'

        c_net = ws1.cell(row=row_idx, column=10, value=float(net))
        c_net.alignment = align_right
        c_net.number_format = '#,##0'

        c_bal = ws1.cell(row=row_idx, column=11, value=float(bal))
        c_bal.alignment = align_right
        c_bal.number_format = '#,##0'

        ws1.cell(row=row_idx, column=12, value=status_str).alignment = align_center

        for col_idx in range(1, 13):
            c_node = ws1.cell(row=row_idx, column=col_idx)
            c_node.border = thin_border
            if c_fill.fill_type:
                c_node.fill = c_fill
            c_node.font = font_regular

        tot_units += u
        tot_gross += g
        tot_adv += adv
        tot_sal += sal
        tot_net += net
        tot_bal += bal

        row_idx += 1
        counter += 1

    # Jami qatori
    ws1.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=5)
    c_tot_lbl = ws1.cell(row=row_idx, column=1, value="JAMI / BARCHASI:")
    c_tot_lbl.font = font_bold
    c_tot_lbl.alignment = align_right

    c_tot_u = ws1.cell(row=row_idx, column=6, value=tot_units)
    c_tot_u.font = font_bold
    c_tot_u.alignment = align_right
    c_tot_u.number_format = '#,##0'

    c_tot_g = ws1.cell(row=row_idx, column=7, value=float(tot_gross))
    c_tot_g.font = font_bold
    c_tot_g.alignment = align_right
    c_tot_g.number_format = '#,##0'

    c_tot_adv = ws1.cell(row=row_idx, column=8, value=float(tot_adv))
    c_tot_adv.font = font_bold
    c_tot_adv.alignment = align_right
    c_tot_adv.number_format = '#,##0'

    c_tot_sal = ws1.cell(row=row_idx, column=9, value=float(tot_sal))
    c_tot_sal.font = font_bold
    c_tot_sal.alignment = align_right
    c_tot_sal.number_format = '#,##0'

    c_tot_net = ws1.cell(row=row_idx, column=10, value=float(tot_net))
    c_tot_net.font = font_bold
    c_tot_net.alignment = align_right
    c_tot_net.number_format = '#,##0'

    c_tot_bal = ws1.cell(row=row_idx, column=11, value=float(tot_bal))
    c_tot_bal.font = font_bold
    c_tot_bal.alignment = align_right
    c_tot_bal.number_format = '#,##0'

    ws1.cell(row=row_idx, column=12, value="")

    for col_idx in range(1, 13):
        c_node = ws1.cell(row=row_idx, column=col_idx)
        c_node.fill = total_fill
        c_node.border = thick_bottom_border

    # Ustunlar kengligi
    col_widths1 = [5, 12, 28, 16, 16, 15, 22, 20, 20, 24, 20, 18]
    for i, w_val in enumerate(col_widths1, start=1):
        ws1.column_dimensions[get_column_letter(i)].width = w_val

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
        _build_day_sheet(
            wb=wb,
            target_date=cur_date,
            day_tickets=cur_day_tickets,
            all_workers=workers,
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
        "№", "Stiker ID", "Skanerlangan Vaqt", "Xodim ID", "Xodim F.I.SH",
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
        worker_id = t.worker.worker_id if t.worker else "—"
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

