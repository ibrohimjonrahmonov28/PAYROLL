from datetime import date, datetime, time
import calendar
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Max, F, FloatField, ExpressionWrapper, Value, Q
from django.db.models.functions import Coalesce
from production.models import Ticket, OrderItem, Operation
from accounts.models import Worker


MAX_SCREENS = 40


def get_screen_data(screen_number: int, target_date=None):
    if target_date is None:
        target_date = timezone.localdate()

    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(target_date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(target_date, time.max), tz)

    # Bugun faol bo'lgan barcha xodimlarning oxirgi skanerlangan patokini aniqlash:
    # Qoidaga ko'ra: Xodim bir nechta patokda ishlashi mumkin, mobodo almashsa oxirgi stiker urilgan patokda ismi chiqadi
    all_today_scans = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        worker__isnull=False
    ).values('worker_id', 'screen_number', 'scanned_at').order_by('scanned_at')

    worker_latest_screen = {}
    for scan in all_today_scans:
        worker_latest_screen[scan['worker_id']] = scan['screen_number']

    # Ushbu ekranga oxirgi stikeri to'g'ri kelgan xodimlar
    screen_worker_ids = [w_id for w_id, s_num in worker_latest_screen.items() if s_num == screen_number]

    # Bugun dazmoldan o'tgan mahsulotlar soni:
    # Foydalanuvchi talabi: Bugun tikilgan dona operatsiyalardan bugun dazmoldan o'tgan sonlar yig'indisi bo'ladi
    dazmol_qs = Ticket.objects.filter(
        Q(screen_number=screen_number) | (Q(worker_id__in=screen_worker_ids) if screen_worker_ids else Q(pk__in=[])),
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end),
        article_operation__operation__name__icontains='DAZMOL'
    ).distinct()
    dazmol_units = dazmol_qs.aggregate(s=Sum('quantity'))['s'] or 0

    if not screen_worker_ids:
        return {
            'screen_number': screen_number,
            'date_str': target_date.strftime("%d.%m.%Y"),
            'workers': [],
            'grand_total_earnings': 0,
            'grand_total_earnings_formatted': "0",
            'grand_total_units': dazmol_units,
            'dazmol_units': dazmol_units,
            'avg_screen_kpi': 0.0,
            'active_workers_count': 0,
            'all_screens_list': list(range(1, MAX_SCREENS + 1)),
        }

    # Ushbu xodimlarning bugungi barcha skanerlangan biletlari
    tickets = Ticket.objects.filter(
        worker_id__in=screen_worker_ids,
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end)
    )

    # OrderItem lardan har bir (order_id, article_id) ning normasini olish
    order_item_norms = {
        (oi['order_id'], oi['article_id']): oi['norm']
        for oi in OrderItem.objects.values('order_id', 'article_id', 'norm')
    }

    points_expr = ExpressionWrapper(
        F('quantity') * Coalesce(F('article_operation__difficulty'), Value(1.0)),
        output_field=FloatField()
    )

    # Har bir model bo'yicha guruhlash
    model_stats = tickets.values(
        'worker__id',
        'box__order_id',
        'box__order__order_number',
        'article_operation__article_id',
        'article_operation__article__code',
        'article_operation__article__name',
        'article_operation__article__daily_norm',
        'article_operation__article__model_id',
        'article_operation__article__model__code',
        'article_operation__article__model__name',
        'article_operation__article__model__daily_norm',
    ).annotate(
        units=Sum('quantity'),
        earned_points=Sum(points_expr),
    )

    worker_models_dict = {}
    for item in model_stats:
        w_id = item['worker__id']
        ord_id = item['box__order_id']
        art_id = item['article_operation__article_id']
        pmodel_id = item['article_operation__article__model_id']

        if pmodel_id:
            m_key = f"m_{pmodel_id}"
            m_code = item['article_operation__article__model__code'] or item['article_operation__article__code']
            m_name = item['article_operation__article__model__name'] or item['article_operation__article__name']
            norm = item['article_operation__article__model__daily_norm'] or item['article_operation__article__daily_norm'] or 1000
        else:
            m_key = f"art_{art_id}"
            m_code = item['article_operation__article__code']
            m_name = item['article_operation__article__name']
            norm = order_item_norms.get((ord_id, art_id)) or item['article_operation__article__daily_norm'] or 1000

        pts = round(item['earned_points'] or 0.0, 1)
        units = item['units'] or 0
        ord_num = item['box__order__order_number']

        if w_id not in worker_models_dict:
            worker_models_dict[w_id] = {}

        if m_key not in worker_models_dict[w_id]:
            worker_models_dict[w_id][m_key] = {
                'model_code': m_code,
                'model_name': m_name,
                'norm': norm,
                'units': 0,
                'points': 0.0,
                'orders': set(),
            }

        worker_models_dict[w_id][m_key]['units'] += units
        worker_models_dict[w_id][m_key]['points'] += pts
        if ord_num:
            worker_models_dict[w_id][m_key]['orders'].add(ord_num)

    worker_models_map = {}
    for w_id, m_dict in worker_models_dict.items():
        worker_models_map[w_id] = []
        for m_key, m_info in m_dict.items():
            norm = m_info['norm']
            pts = round(m_info['points'], 1)
            pts_display = int(pts) if pts.is_integer() else pts
            pct = round((pts / norm) * 100, 1) if norm > 0 else 0.0
            orders_str = ", ".join(sorted(m_info['orders']))

            worker_models_map[w_id].append({
                'model_code': m_info['model_code'],
                'model_name': m_info['model_name'],
                'order_number': orders_str,
                'units': m_info['units'],
                'points': pts_display,
                'norm': norm,
                'ratio_str': f"{pts_display}/{norm}",
                'percentage': pct,
            })

    # Har bir xodim bo'yicha umumiy agregatsiya
    worker_stats = tickets.values(
        'worker__id',
        'worker__worker_id',
        'worker__first_name',
        'worker__last_name'
    ).annotate(
        total_units=Sum('quantity'),
        total_earnings=Sum('total_amount'),
        ticket_count=Count('id'),
        last_scan=Max('scanned_at')
    )

    worker_ids = [stat['worker__id'] for stat in worker_stats]
    # Joriy oy bo'yicha har bir xodimning jami hisoblangan oylik maoshi (Index-friendly datetime range)
    _, last_day = calendar.monthrange(target_date.year, target_date.month)
    month_start = timezone.make_aware(datetime.combine(date(target_date.year, target_date.month, 1), time.min), tz)
    month_end = timezone.make_aware(datetime.combine(date(target_date.year, target_date.month, last_day), time.max), tz)

    month_stats = Ticket.objects.filter(
        worker_id__in=worker_ids,
        status=Ticket.Status.SCANNED,
        scanned_at__range=(month_start, month_end)
    ).values('worker_id').annotate(
        month_earnings=Sum('total_amount')
    )
    month_earnings_map = {item['worker_id']: (item['month_earnings'] or 0) for item in month_stats}

    workers_list = []
    grand_total_earnings = 0
    total_scanned_units = 0
    total_pct_sum = 0

    for stat in worker_stats:
        w_id = stat['worker__id']
        earnings = int(stat['total_earnings'] or 0)
        units = int(stat['total_units'] or 0)
        grand_total_earnings += earnings
        total_scanned_units += units

        month_earnings = int(month_earnings_map.get(w_id, earnings) or 0)
        month_earnings_formatted = f"{month_earnings:,.0f}".replace(",", " ")

        last_scan_time = ""
        if stat['last_scan']:
            last_scan_time = timezone.localtime(stat['last_scan']).strftime("%H:%M:%S")

        models = worker_models_map.get(w_id, [])
        total_points = sum(m['points'] for m in models)
        total_percentage = round(sum(m['percentage'] for m in models), 1)
        total_pct_sum += total_percentage

        models_display = []
        for m in models:
            models_display.append({
                'label': f"{m['model_code']}: {m['ratio_str']}",
                'ratio_str': m['ratio_str'],
                'model_code': m['model_code'],
                'model_name': m['model_name'],
                'percentage': m['percentage'],
            })

        workers_list.append({
            'worker_id': stat['worker__worker_id'],
            'full_name': f"{stat['worker__first_name']} {stat['worker__last_name']}".strip(),
            'total_units': units,
            'total_points': total_points,
            'total_percentage': total_percentage,
            'is_above_norm': (total_percentage >= 100.0),
            'progress_width': min(100, int(total_percentage)),
            'models': models_display,
            'models_str': ", ".join(m['label'] for m in models_display),
            'total_earnings': earnings,
            'total_earnings_formatted': f"{earnings:,.0f}".replace(",", " "),
            'month_earnings': month_earnings,
            'month_earnings_formatted': month_earnings_formatted,
            'ticket_count': stat['ticket_count'],
            'last_scan': last_scan_time,
        })

    # Eng yuqori foiz va ball bo'yicha saralash
    workers_list.sort(key=lambda w: (w['total_percentage'], w['total_points']), reverse=True)

    for idx, w in enumerate(workers_list, start=1):
        w['rank'] = idx

    avg_screen_kpi = round(total_pct_sum / len(workers_list), 1) if workers_list else 0.0

    has_dazmol_op = Operation.objects.filter(name__icontains='DAZMOL').exists()
    grand_total_units = dazmol_units if has_dazmol_op else total_scanned_units

    return {
        'screen_number': screen_number,
        'date_str': target_date.strftime("%d.%m.%Y"),
        'workers': workers_list,
        'grand_total_earnings': grand_total_earnings,
        'grand_total_earnings_formatted': f"{grand_total_earnings:,.0f}".replace(",", " "),
        'grand_total_units': grand_total_units,
        'dazmol_units': dazmol_units,
        'avg_screen_kpi': avg_screen_kpi,
        'active_workers_count': len(workers_list),
        'all_screens_list': list(range(1, MAX_SCREENS + 1)),
    }


def screen_view(request, screen_number: int):
    if not 1 <= screen_number <= MAX_SCREENS:
        screen_number = 1

    data = get_screen_data(screen_number)
    return render(request, 'screens/monitor.html', data)


def screen_api_view(request, screen_number: int):
    if not 1 <= screen_number <= MAX_SCREENS:
        return JsonResponse({'error': f'Invalid screen number (must be 1-{MAX_SCREENS})'}, status=400)

    data = get_screen_data(screen_number)
    return JsonResponse(data)


def all_screens_overview(request):
    """Barcha 40 ta patok ekranini umumiy kuzatish sahifasi"""
    screens_summary = []
    today = timezone.localdate()
    for s_num in range(1, MAX_SCREENS + 1):
        data = get_screen_data(s_num, today)
        screens_summary.append(data)

    return render(request, 'screens/overview.html', {
        'screens': screens_summary,
        'date_str': today.strftime("%d.%m.%Y"),
        'max_screens': MAX_SCREENS,
    })
