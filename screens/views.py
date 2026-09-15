from datetime import date
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Max
from production.models import Ticket
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
    ).select_related('worker', 'article_operation__operation')

    # Har bir xodim bo'yicha agregatsiya
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
    ).order_by('-total_earnings', 'worker__worker_id')

    workers_list = []
    grand_total_earnings = 0
    grand_total_units = 0

    for idx, stat in enumerate(worker_stats, start=1):
        earnings = int(stat['total_earnings'] or 0)
        units = int(stat['total_units'] or 0)
        grand_total_earnings += earnings
        grand_total_units += units

        last_scan_time = ""
        if stat['last_scan']:
            last_scan_time = timezone.localtime(stat['last_scan']).strftime("%H:%M:%S")

        workers_list.append({
            'rank': idx,
            'worker_id': stat['worker__worker_id'],
            'full_name': f"{stat['worker__first_name']} {stat['worker__last_name']}".strip(),
            'total_units': units,
            'total_earnings': earnings,
            'total_earnings_formatted': f"{earnings:,.0f}".replace(",", " "),
            'ticket_count': stat['ticket_count'],
            'last_scan': last_scan_time,
        })

    return {
        'screen_number': screen_number,
        'date_str': target_date.strftime("%d.%m.%Y"),
        'workers': workers_list,
        'grand_total_earnings': grand_total_earnings,
        'grand_total_earnings_formatted': f"{grand_total_earnings:,.0f}".replace(",", " "),
        'grand_total_units': grand_total_units,
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
