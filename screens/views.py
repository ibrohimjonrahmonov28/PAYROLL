from datetime import date
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Max, F, FloatField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce
from production.models import Ticket, OrderItem
from accounts.models import Worker


def get_screen_data(screen_number: int, target_date=None):
    if target_date is None:
        target_date = timezone.localdate()

    # Bugun ushbu ekranga biriktirilgan va skanerlangan biletlar
    tickets = Ticket.objects.filter(
        screen_number=screen_number,
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date,
        worker__isnull=False
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

    # Har bir (worker, order, article) bo'yicha guruhlash
    model_stats = tickets.values(
        'worker__id',
        'box__order_id',
        'box__order__order_number',
        'article_operation__article_id',
        'article_operation__article__code',
        'article_operation__article__name',
        'article_operation__article__daily_norm',
    ).annotate(
        units=Sum('quantity'),
        earned_points=Sum(points_expr),
    )

    worker_models_map = {}
    for item in model_stats:
        w_id = item['worker__id']
        ord_id = item['box__order_id']
        art_id = item['article_operation__article_id']

        norm = order_item_norms.get((ord_id, art_id))
        if not norm:
            norm = item['article_operation__article__daily_norm'] or 1000

        pts = round(item['earned_points'] or 0.0, 1)
        units = item['units'] or 0
        pct = round((pts / norm) * 100, 1) if norm > 0 else 0.0

        pts_display = int(pts) if pts.is_integer() else pts

        if w_id not in worker_models_map:
            worker_models_map[w_id] = []

        worker_models_map[w_id].append({
            'model_code': item['article_operation__article__code'],
            'model_name': item['article_operation__article__name'],
            'order_number': item['box__order__order_number'],
            'units': units,
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

    workers_list = []
    grand_total_earnings = 0
    grand_total_units = 0
    total_pct_sum = 0

    for stat in worker_stats:
        w_id = stat['worker__id']
        earnings = int(stat['total_earnings'] or 0)
        units = int(stat['total_units'] or 0)
        grand_total_earnings += earnings
        grand_total_units += units

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
            'ticket_count': stat['ticket_count'],
            'last_scan': last_scan_time,
        })

    # Eng yuqori foiz va ball bo'yicha saralash
    workers_list.sort(key=lambda w: (w['total_percentage'], w['total_points']), reverse=True)

    for idx, w in enumerate(workers_list, start=1):
        w['rank'] = idx

    avg_screen_kpi = round(total_pct_sum / len(workers_list), 1) if workers_list else 0.0

    return {
        'screen_number': screen_number,
        'date_str': target_date.strftime("%d.%m.%Y"),
        'workers': workers_list,
        'grand_total_earnings': grand_total_earnings,
        'grand_total_earnings_formatted': f"{grand_total_earnings:,.0f}".replace(",", " "),
        'grand_total_units': grand_total_units,
        'avg_screen_kpi': avg_screen_kpi,
        'active_workers_count': len(workers_list),
    }


def screen_view(request, screen_number: int):
    if not 1 <= screen_number <= 10:
        screen_number = 1

    data = get_screen_data(screen_number)
    return render(request, 'screens/monitor.html', data)


def screen_api_view(request, screen_number: int):
    if not 1 <= screen_number <= 10:
        return JsonResponse({'error': 'Invalid screen number'}, status=400)

    data = get_screen_data(screen_number)
    return JsonResponse(data)


def all_screens_overview(request):
    """Barcha 10 ta ekranni umumiy kuzatish sahifasi"""
    screens_summary = []
    today = timezone.localdate()
    for s_num in range(1, 11):
        data = get_screen_data(s_num, today)
        screens_summary.append(data)

    return render(request, 'screens/overview.html', {
        'screens': screens_summary,
        'date_str': today.strftime("%d.%m.%Y")
    })
