import io
import datetime
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Q

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from accounts.models import Worker
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

