from datetime import date, datetime, time
import calendar
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Max, F, FloatField, ExpressionWrapper, Value, Q
from django.db.models.functions import Coalesce
from production.models import Ticket, OrderItem, Operation, OperationGroupItem, ArticleOperation, Article
from production.services import get_patok_code, get_patok_name, parse_patok_number
from accounts.models import Worker
from django.core.cache import cache


MAX_SCREENS = 40


def calculate_quvvat_color_info(quvvat: float):
    """Patok quvvati foizi bo'yicha rang va stil parametrlarini qaytarish"""
    if quvvat < 60.0:
        return {
            'color': 'red',
            'color_class': 'text-rose-400 glow-rose',
            'badge_bg': 'bg-rose-500/20 text-rose-400 border border-rose-500/30',
            'dot_color': 'bg-rose-500',
        }
    elif quvvat < 85.0:
        return {
            'color': 'amber',
            'color_class': 'text-amber-400 glow-amber',
            'badge_bg': 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
            'dot_color': 'bg-amber-500',
        }
    elif quvvat < 100.0:
        return {
            'color': 'emerald',
            'color_class': 'text-emerald-400 glow-emerald',
            'badge_bg': 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
            'dot_color': 'bg-emerald-400',
        }
    else:
        return {
            'color': 'cyan',
            'color_class': 'text-cyan-300 glow-cyan',
            'badge_bg': 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30',
            'dot_color': 'bg-cyan-400',
        }


def calculate_screen_aniqlik(screen_number: int, patok_scans):
    """
    Ushbu patokka kirgan pastallar/biletlar qaysi narxlar shabloniga (OperationGroup) ulanganligini aniqlash.
    Operatsiyalar qiyinligi yig'indisi olinadi.
    Agar 2-3 xil shablonga ulangan bo'lsa:
      - Mutlaq ko'pchilik nima qilingan bo'lsa (eng ko'p skanerlangan shablon), o'shaning qiyinlik yig'indisi olinadi.
      - Agar teng bo'lib qolsa, o'rta arifmetik qilinadi va butun qilib yaxlitlanadi.
    """
    grp_stats = list(patok_scans.filter(
        article_operation__article__operation_group__isnull=False
    ).values('article_operation__article__operation_group_id').annotate(
        cnt=Count('id')
    ))

    art_stats = list(patok_scans.filter(
        article_operation__article__operation_group__isnull=True,
        article_operation__article__isnull=False
    ).values('article_operation__article_id').annotate(
        cnt=Count('id')
    ))

    # Agar bugun hali skan qilinmagan bo'lsa, ushbu ekranning oxirgi skanlaridan olamiz
    if not grp_stats and not art_stats:
        recent_ids = list(Ticket.objects.filter(
            screen_number=screen_number,
            status=Ticket.Status.SCANNED
        ).order_by('-scanned_at').values_list('id', flat=True)[:300])
        if recent_ids:
            recent_scans = Ticket.objects.filter(id__in=recent_ids)
            grp_stats = list(recent_scans.filter(
                article_operation__article__operation_group__isnull=False
            ).values('article_operation__article__operation_group_id').annotate(
                cnt=Count('id')
            ))
            art_stats = list(recent_scans.filter(
                article_operation__article__operation_group__isnull=True,
                article_operation__article__isnull=False
            ).values('article_operation__article_id').annotate(
                cnt=Count('id')
            ))

    if not grp_stats and not art_stats:
        return 0

    group_diffs = {}
    if grp_stats:
        grp_ids = [s['article_operation__article__operation_group_id'] for s in grp_stats]
        group_diffs = {
            row['group_id']: float(row['diff_sum'] or 0.0)
            for row in OperationGroupItem.objects.filter(
                group_id__in=grp_ids,
                is_active=True
            ).values('group_id').annotate(
                diff_sum=Sum(Coalesce(F('difficulty'), Value(1.0)))
            )
        }

    art_diffs = {}
    if art_stats:
        art_ids = [s['article_operation__article_id'] for s in art_stats]
        art_diffs = {
            row['article_id']: float(row['diff_sum'] or 0.0)
            for row in ArticleOperation.objects.filter(
                article_id__in=art_ids,
                is_active=True
            ).values('article_id').annotate(
                diff_sum=Sum(Coalesce(F('difficulty'), Value(1.0)))
            )
        }

    candidates = []
    for s in grp_stats:
        gid = s['article_operation__article__operation_group_id']
        diff = group_diffs.get(gid, 0.0)
        candidates.append({'cnt': s['cnt'], 'diff': diff})

    for s in art_stats:
        aid = s['article_operation__article_id']
        diff = art_diffs.get(aid, 0.0)
        candidates.append({'cnt': s['cnt'], 'diff': diff})

    if not candidates:
        return 0

    max_cnt = max(c['cnt'] for c in candidates)
    top_candidates = [c for c in candidates if c['cnt'] == max_cnt]

    if len(top_candidates) == 1:
        chosen_diff = top_candidates[0]['diff']
        return int(chosen_diff) if chosen_diff.is_integer() else round(chosen_diff, 2)
    else:
        avg_diff = sum(c['diff'] for c in top_candidates) / len(top_candidates)
        return int(round(avg_diff))


def get_screen_data(screen_number: int, target_date=None):
    if target_date is None:
        target_date = timezone.localdate()

    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(target_date, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(target_date, time.max), tz)

    # Bugun faol bo'lgan barcha xodimlarning oxirgi skanerlangan patokini aniqlash
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

    # Ushbu patokda bugun skanerlangan barcha biletlar
    patok_scans = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end)
    ).filter(
        Q(screen_number=screen_number) | (Q(worker_id__in=screen_worker_ids) if screen_worker_ids else Q(pk__in=[]))
    )

    aniqlik = calculate_screen_aniqlik(screen_number, patok_scans)

    # Dazmol operatsiyalari ID lari
    dazmol_op_ids = list(Operation.objects.filter(name__icontains='DAZMOL').values_list('id', flat=True))

    # Bugun dazmoldan o'tgan mahsulotlar soni
    if dazmol_op_ids:
        dazmol_qs = Ticket.objects.filter(
            Q(screen_number=screen_number) | (Q(worker_id__in=screen_worker_ids) if screen_worker_ids else Q(pk__in=[])),
            status=Ticket.Status.SCANNED,
            scanned_at__range=(day_start, day_end),
            article_operation__operation_id__in=dazmol_op_ids
        )
        dazmol_units = dazmol_qs.aggregate(s=Sum('quantity'))['s'] or 0
    else:
        dazmol_units = 0

    if not screen_worker_ids:
        quvvat_info = calculate_quvvat_color_info(0.0)
        return {
            'screen_number': screen_number,
            'screen_code': get_patok_code(screen_number),
            'screen_name': get_patok_name(screen_number),
            'patok_code': get_patok_code(screen_number),
            'patok_name': get_patok_name(screen_number),
            'date_str': target_date.strftime("%d.%m.%Y"),
            'workers': [],
            'grand_total_earnings': 0,
            'grand_total_earnings_formatted': "0",
            'grand_total_units': dazmol_units,
            'dazmol_units': dazmol_units,
            'aniqlik': aniqlik,
            'davomat': 0,
            'foiz': 0.0,
            'avg_screen_kpi': 0.0,
            'patok_kpi': 0.0,
            'patok_quvvati': 0.0,
            'quvvat_color': quvvat_info['color'],
            'quvvat_color_class': quvvat_info['color_class'],
            'quvvat_badge_bg': quvvat_info['badge_bg'],
            'active_workers_count': 0,
            'all_screens_list': list(range(1, MAX_SCREENS + 1)),
        }

    # Ushbu xodimlarning bugungi barcha skanerlangan biletlari
    tickets = Ticket.objects.filter(
        worker_id__in=screen_worker_ids,
        status=Ticket.Status.SCANNED,
        scanned_at__range=(day_start, day_end)
    )

    # OrderItem lardan faqat bugun ishlangan tegishli order larning normasini olish (butun bazani emas!)
    order_ids = list(tickets.values_list('box__order_id', flat=True).distinct())
    order_item_norms = {
        (oi['order_id'], oi['article_id']): oi['norm']
        for oi in OrderItem.objects.filter(order_id__in=order_ids).values('order_id', 'article_id', 'norm')
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

    davomat = len(workers_list)
    foiz = round((davomat / aniqlik) * 100, 1) if (aniqlik and aniqlik > 0) else 0.0
    avg_screen_kpi = round(total_pct_sum / len(workers_list), 1) if workers_list else 0.0

    # ---------------------------------------------------------
    # PATOK QUVVATI HISOBLASH (Earned Value / Mehnat Qiymati):
    # 1. Bugun patokda ishlangan barcha artikullarning to'liq 1 dona narxi x normasi
    # 2. Bir nechta model bo'lsa, o'rtacha arifmetik reja olinadi
    # 3. Quvvat = (grand_total_earnings / avg_daily_target) * 100%
    # ---------------------------------------------------------
    patok_quvvati = 0.0
    article_ids = list(tickets.values_list('article_operation__article_id', flat=True).distinct())

    if grand_total_earnings > 0 and article_ids:
        art_unit_prices = {}
        for row in ArticleOperation.objects.filter(article_id__in=article_ids, is_active=True).values('article_id').annotate(tot_price=Sum('price_per_unit')):
            art_unit_prices[row['article_id']] = float(row['tot_price'] or 0.0)

        articles_qs = Article.objects.filter(id__in=article_ids).select_related('model')
        target_reja_values = []
        for art in articles_qs:
            u_price = art_unit_prices.get(art.id, 0.0)
            norm = art.daily_norm or (art.model.daily_norm if art.model else None) or 1000
            if u_price > 0 and norm > 0:
                target_reja_values.append(u_price * float(norm))

        if target_reja_values:
            avg_daily_target = sum(target_reja_values) / len(target_reja_values)
            if avg_daily_target > 0:
                patok_quvvati = round((grand_total_earnings / avg_daily_target) * 100, 1)

    quvvat_info = calculate_quvvat_color_info(patok_quvvati)
    patok_kpi = patok_quvvati  # To'liq moslik uchun

    has_dazmol_op = bool(dazmol_op_ids)
    grand_total_units = dazmol_units if has_dazmol_op else total_scanned_units

    return {
        'screen_number': screen_number,
        'screen_code': get_patok_code(screen_number),
        'screen_name': get_patok_name(screen_number),
        'patok_code': get_patok_code(screen_number),
        'patok_name': get_patok_name(screen_number),
        'date_str': target_date.strftime("%d.%m.%Y"),
        'workers': workers_list,
        'grand_total_earnings': grand_total_earnings,
        'grand_total_earnings_formatted': f"{grand_total_earnings:,.0f}".replace(",", " "),
        'grand_total_units': grand_total_units,
        'dazmol_units': dazmol_units,
        'aniqlik': aniqlik,
        'davomat': davomat,
        'foiz': foiz,
        'avg_screen_kpi': avg_screen_kpi,
        'patok_kpi': patok_kpi,
        'patok_quvvati': patok_quvvati,
        'quvvat_color': quvvat_info['color'],
        'quvvat_color_class': quvvat_info['color_class'],
        'quvvat_badge_bg': quvvat_info['badge_bg'],
        'active_workers_count': len(workers_list),
        'all_screens_list': list(range(1, MAX_SCREENS + 1)),
    }


def parse_date_param(val):
    if not val:
        return None
    val = val.strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            pass
    return None


def screen_view(request, screen_number=None, screen_identifier=None):
    raw = screen_number if screen_number is not None else screen_identifier
    parsed = parse_patok_number(raw)
    sn = parsed if (parsed and 1 <= parsed <= MAX_SCREENS) else 1

    date_param = request.GET.get('date')
    target_date = parse_date_param(date_param)

    data = get_screen_data(sn, target_date=target_date)
    effective_date = target_date or timezone.localdate()
    data['target_date_iso'] = effective_date.strftime('%Y-%m-%d')
    data['is_custom_date'] = bool(target_date and target_date != timezone.localdate())
    return render(request, 'screens/monitor.html', data)


def screen_api_view(request, screen_number=None, screen_identifier=None):
    raw = screen_number if screen_number is not None else screen_identifier
    sn = parse_patok_number(raw)
    if not sn or not 1 <= sn <= MAX_SCREENS:
        return JsonResponse({'error': f'Invalid screen number or code (must be 1-{MAX_SCREENS}, K1-K13, U1-U27)'}, status=400)

    date_param = request.GET.get('date')
    target_date = parse_date_param(date_param)
    effective_date = target_date or timezone.localdate()

    cache_key = f"screen_api_data_{sn}_{effective_date.isoformat()}"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return JsonResponse(cached_data)

    data = get_screen_data(sn, target_date=target_date)
    data['target_date_iso'] = effective_date.strftime('%Y-%m-%d')
    data['is_custom_date'] = bool(target_date and target_date != timezone.localdate())
    cache.set(cache_key, data, timeout=10)
    return JsonResponse(data)


def all_screens_overview(request):
    """Barcha 40 ta patok ekranini umumiy kuzatish sahifasi"""
    date_param = request.GET.get('date')
    target_date = parse_date_param(date_param)
    effective_date = target_date or timezone.localdate()
    cache_key = f"all_screens_overview_data_{effective_date.isoformat()}"

    cached_summary = cache.get(cache_key)
    if cached_summary is not None:
        return render(request, 'screens/overview.html', {
            'screens': cached_summary,
            'date_str': effective_date.strftime("%d.%m.%Y"),
            'target_date_iso': effective_date.strftime('%Y-%m-%d'),
            'is_custom_date': bool(target_date and target_date != timezone.localdate()),
            'max_screens': MAX_SCREENS,
        })

    screens_summary = []
    for s_num in range(1, MAX_SCREENS + 1):
        data = get_screen_data(s_num, effective_date)
        screens_summary.append(data)

    cache.set(cache_key, screens_summary, timeout=10)
    return render(request, 'screens/overview.html', {
        'screens': screens_summary,
        'date_str': effective_date.strftime("%d.%m.%Y"),
        'target_date_iso': effective_date.strftime('%Y-%m-%d'),
        'is_custom_date': bool(target_date and target_date != timezone.localdate()),
        'max_screens': MAX_SCREENS,
    })
