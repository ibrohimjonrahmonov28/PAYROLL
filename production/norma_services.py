from decimal import Decimal
import datetime
from django.utils import timezone
from django.db.models import Sum, Q, Count
from .models import ProductModel, Article, Ticket, OrderItem, DailyModelProgress
from accounts.models import Worker


def calculate_daily_model_progress(product_model: ProductModel, target_date: datetime.date = None) -> DailyModelProgress:
    """
    Berilgan sana (default: bugun) uchun modelning kunlik normasini va bajarilishini hisoblash:
    - Bugun shu modelga tegishli artikullardan skanerlangan stikerlar yig'indisi (completed_units)
    - Bugungi kunlik norma (daily_norm, masalan 1500 dona)
    - Bugungi bajarilish foizi: (completed_units / daily_norm) * 100
    - Kechagi kundan qolgan qoldiq foiz: agar kecha 80% bo'lsa, qoldiq = 20%
    - Jami hisoblangan foiz: bugungi foiz + qoldiq foiz
    """
    if target_date is None:
        target_date = timezone.localdate()

    daily_norm = product_model.daily_norm or 1000

    # Modelga tegishli barcha artikullar
    articles = product_model.articles.all()
    article_ids = list(articles.values_list('id', flat=True))

    # Shu kuni ushbu artikullardan tikilgan (skanerlangan) biletlar
    tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date,
        article_operation__article_id__in=article_ids
    )

    completed_units = tickets.aggregate(total=Sum('quantity'))['total'] or 0

    # Bugungi kun uchun sof foiz
    if daily_norm > 0:
        completion_percentage = round((Decimal(completed_units) / Decimal(daily_norm)) * Decimal('100.0'), 2)
    else:
        completion_percentage = Decimal('0.00')

    # Oldingi eng oxirgi kundan qolgan qoldiqni topish
    prev_progress = DailyModelProgress.objects.filter(
        product_model=product_model,
        date__lt=target_date
    ).order_by('-date').first()

    carried_over_units = 0
    carried_over_percentage = Decimal('0.00')

    if prev_progress:
        # Agar oldingi kunda norma 100% dan kam bo'lgan bo'lsa, qoldiq o'tadi
        if prev_progress.completion_percentage < Decimal('100.00'):
            carried_over_percentage = Decimal('100.00') - prev_progress.completion_percentage
            deficit_units = max(0, prev_progress.daily_norm - prev_progress.completed_units)
            carried_over_units = deficit_units

    total_percentage = round(completion_percentage + carried_over_percentage, 2)
    is_completed = (total_percentage >= Decimal('100.00'))

    progress, _ = DailyModelProgress.objects.update_or_create(
        product_model=product_model,
        date=target_date,
        defaults={
            'daily_norm': daily_norm,
            'completed_units': completed_units,
            'completion_percentage': completion_percentage,
            'carried_over_units': carried_over_units,
            'carried_over_percentage': carried_over_percentage,
            'total_percentage': total_percentage,
            'is_completed': is_completed,
        }
    )
    return progress


def sync_all_models_for_date(target_date: datetime.date = None):
    """Barcha mavjud modellar uchun ko'rsatilgan sanada kunlik normani qayta hisoblash"""
    if target_date is None:
        target_date = timezone.localdate()

    models = ProductModel.objects.all()
    results = []
    for pm in models:
        res = calculate_daily_model_progress(pm, target_date)
        results.append(res)
    return results


def get_today_norma_dashboard_data(target_date: datetime.date = None) -> dict:
    """
    /norma/ boshqaruv paneli uchun real-vaqt ma'lumotlarini tayyorlash:
    - Bugungi sana
    - Har bir model bo'yicha kunlik norma, tikilgan dona, foizlar, qoldiqlar
    - Umumiy korxona normasi agregatlari
    - Hali modelga biriktirilmagan artikullar
    """
    if target_date is None:
        target_date = timezone.localdate()

    models = ProductModel.objects.all().prefetch_related('articles', 'articles__article_operations')
    
    models_data = []
    total_target_norm = 0
    total_completed_units = 0
    total_carried_over_units = 0

    for pm in models:
        prog = calculate_daily_model_progress(pm, target_date)
        
        # Shu modelda bugun ishlagan tikuvchilar soni
        art_ids = list(pm.articles.values_list('id', flat=True))
        active_workers_count = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__date=target_date,
            article_operation__article_id__in=art_ids
        ).values('scanned_by').distinct().count()

        # Bog'langan artikullarning umumiy buyurtma hajmi
        total_order_qty = OrderItem.objects.filter(article__in=pm.articles.all()).aggregate(
            tot=Sum('quantity')
        )['tot'] or 0

        models_data.append({
            'model': pm,
            'progress': prog,
            'articles_count': pm.articles.count(),
            'articles': pm.articles.all()[:5],
            'total_order_qty': total_order_qty,
            'active_workers_count': active_workers_count,
            'remaining_units': max(0, prog.daily_norm - prog.completed_units),
        })

        total_target_norm += prog.daily_norm
        total_completed_units += prog.completed_units
        total_carried_over_units += prog.carried_over_units

    # Umumiy korxona foizi
    if total_target_norm > 0:
        overall_percentage = round((Decimal(total_completed_units) / Decimal(total_target_norm)) * Decimal('100.0'), 1)
    else:
        overall_percentage = Decimal('0.0')

    # Modelga biriktirilmagan erkin artikullar
    unassigned_articles = Article.objects.filter(model__isnull=True)

    return {
        'target_date': target_date,
        'models_data': models_data,
        'models_count': len(models_data),
        'total_target_norm': total_target_norm,
        'total_completed_units': total_completed_units,
        'total_carried_over_units': total_carried_over_units,
        'overall_percentage': overall_percentage,
        'unassigned_articles': unassigned_articles,
        'unassigned_count': unassigned_articles.count(),
    }


def get_workers_norma_breakdown(target_date: datetime.date = None) -> list:
    """
    Har bir tikuvchi (xodim) kesimida ko'rsatilgan sanada qaysi modellarni tikkanligi
    va har bir model bo'yicha uning hissasini hisoblash.
    """
    if target_date is None:
        target_date = timezone.localdate()

    tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date,
        scanned_by__isnull=False
    ).select_related(
        'scanned_by',
        'article_operation__article__model',
        'article_operation__operation'
    )

    worker_map = {}
    for t in tickets:
        w = t.scanned_by
        if not w:
            continue
        if w.id not in worker_map:
            worker_map[w.id] = {
                'worker': w,
                'total_units': 0,
                'total_earned': Decimal('0.00'),
                'models': {},
            }

        worker_map[w.id]['total_units'] += t.quantity
        worker_map[w.id]['total_earned'] += (t.total_amount or Decimal('0.00'))

        ao = t.article_operation
        art = ao.article if ao else None
        pmodel = art.model if art else None
        model_name = pmodel.name if pmodel else (art.name if art else "Boshqa")
        model_id = pmodel.id if pmodel else 0
        norm = pmodel.daily_norm if (pmodel and pmodel.daily_norm) else 1000

        if model_id not in worker_map[w.id]['models']:
            worker_map[w.id]['models'][model_id] = {
                'name': model_name,
                'norm': norm,
                'units': 0,
            }
        worker_map[w.id]['models'][model_id]['units'] += t.quantity

    result = []
    for wid, data in worker_map.items():
        # Xodimning har bir model bo'yicha bajargan foizlari yig'indisi
        total_worker_pct = Decimal('0.0')
        for mid, mdata in data['models'].items():
            if mdata['norm'] > 0:
                pct = (Decimal(mdata['units']) / Decimal(mdata['norm'])) * Decimal('100.0')
                total_worker_pct += pct
                mdata['percentage'] = round(pct, 1)

        data['total_percentage'] = round(total_worker_pct, 1)
        data['has_bonus'] = (data['total_percentage'] > Decimal('100.0'))
        result.append(data)

    result.sort(key=lambda x: x['total_percentage'], reverse=True)
    return result

