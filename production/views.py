from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count
from .models import Article, Operation, ArticleOperation, Order, Box, Ticket
from .services import allocate_ticket_quantities, generate_box_tickets, create_boxes_for_order
from accounts.models import Worker


def dashboard_view(request):
    today = timezone.localdate()

    # Bugungi statistika
    today_tickets = Ticket.objects.filter(status=Ticket.Status.SCANNED, scanned_at__date=today)
    
    total_wages_today = today_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0')
    total_units_today = today_tickets.aggregate(s=Sum('quantity'))['s'] or 0
    active_workers_today = today_tickets.values('worker').distinct().count()

    # Top tikuvchilar (bugungi)
    top_workers = today_tickets.values(
        'worker__worker_id',
        'worker__first_name',
        'worker__last_name'
    ).annotate(
        total_earnings=Sum('total_amount'),
        total_units=Sum('quantity')
    ).order_by('-total_earnings')[:5]

    # So'nggi skanerlangan biletlar
    recent_scans = today_tickets.select_related(
        'worker', 'article_operation__operation', 'box__order'
    ).order_by('-scanned_at')[:8]

    # Buyurtmalar
    orders = Order.objects.all().order_by('-created_at')[:5]

    return render(request, 'production/dashboard.html', {
        'total_wages_today': total_wages_today,
        'total_wages_today_formatted': f"{int(total_wages_today):,}".replace(",", " "),
        'total_units_today': total_units_today,
        'active_workers_today': active_workers_today,
        'top_workers': top_workers,
        'recent_scans': recent_scans,
        'orders': orders,
        'today_str': today.strftime("%d.%m.%Y")
    })


def order_list_view(request):
    if request.method == 'POST':
        order_number = request.POST.get('order_number', '').strip()
        article_id = request.POST.get('article_id')
        total_quantity = request.POST.get('total_quantity')
        client_name = request.POST.get('client_name', '').strip()
        deadline = request.POST.get('deadline') or None

        if not order_number or not article_id or not total_quantity:
            messages.error(request, "Iltimos, barcha majburiy maydonlarni to'ldiring.")
        elif Order.objects.filter(order_number=order_number).exists():
            messages.error(request, f"{order_number} raqamli buyurtma allaqachon mavjud!")
        else:
            article = get_object_or_404(Article, id=article_id)
            order = Order.objects.create(
                order_number=order_number,
                article=article,
                total_quantity=int(total_quantity),
                client_name=client_name,
                deadline=deadline,
                status=Order.Status.IN_PROGRESS
            )
            messages.success(request, f"Buyurtma {order.order_number} muvaffaqiyatli yaratildi!")
            return redirect('production:order_detail', order_id=order.id)

    orders = Order.objects.all().select_related('article').order_by('-created_at')
    articles = Article.objects.all().order_by('code')
    return render(request, 'production/order_list.html', {
        'orders': orders,
        'articles': articles
    })


def order_detail_view(request, order_id: int):
    order = get_object_or_404(Order.objects.select_related('article'), id=order_id)
    boxes = order.boxes.all().prefetch_related('tickets').order_by('box_number')

    if request.method == 'POST' and 'create_boxes' in request.POST:
        # Masalan 1000 donani har bir qutiga 100 tadan taqsimlash
        box_count = int(request.POST.get('box_count', 1))
        box_size = int(request.POST.get('box_size', 100))

        box_sizes = [box_size] * box_count
        created = create_boxes_for_order(order, box_sizes)
        messages.success(request, f"{len(created)} ta yangi quti yaratildi.")
        return redirect('production:order_detail', order_id=order.id)

    article_operations = order.article.article_operations.select_related('operation').order_by('sequence')

    return render(request, 'production/order_detail.html', {
        'order': order,
        'boxes': boxes,
        'article_operations': article_operations
    })


def box_split_wizard_view(request, box_id: int):
    """
    SRS 3.2 bo'yicha Admin quti operatsiyalarini bo'lish (Split) va QR stikerlarni generatsiya qilish oynasi.
    """
    box = get_object_or_404(Box.objects.select_related('order__article'), id=box_id)
    article = box.order.article
    article_operations = article.article_operations.select_related('operation').order_by('sequence')

    if request.method == 'POST':
        operation_splits = {}
        for art_op in article_operations:
            split_val = request.POST.get(f"split_{art_op.id}", "1")
            try:
                operation_splits[art_op.id] = max(1, int(split_val))
            except ValueError:
                operation_splits[art_op.id] = 1

        tickets = generate_box_tickets(box, operation_splits)
        messages.success(request, f"Quti #{box.box_number} uchun {len(tickets)} ta QR stiker muvaffaqiyatli yaratildi!")
        return redirect('production:box_print_stickers', box_id=box.id)

    # Initial data
    ops_with_preview = []
    for art_op in article_operations:
        default_split = 1
        allocations = allocate_ticket_quantities(box.quantity, default_split)
        ops_with_preview.append({
            'art_op': art_op,
            'split': default_split,
            'allocations': allocations
        })

    return render(request, 'production/box_split_wizard.html', {
        'box': box,
        'ops_with_preview': ops_with_preview
    })


def box_print_stickers_view(request, box_id: int):
    box = get_object_or_404(Box.objects.select_related('order__article'), id=box_id)
    tickets = box.tickets.all().select_related('article_operation__operation', 'box__order__article').order_by('article_operation__sequence', 'split_index')
    return render(request, 'production/box_stickers_print.html', {
        'box': box,
        'tickets': tickets
    })


def order_print_all_stickers_view(request, order_id: int):
    order = get_object_or_404(Order.objects.select_related('article'), id=order_id)
    tickets = Ticket.objects.filter(box__order=order).select_related(
        'box', 'article_operation__operation', 'box__order__article'
    ).order_by('box__box_number', 'article_operation__sequence', 'split_index')

    return render(request, 'production/box_stickers_print.html', {
        'box': {'box_number': f"BARCHA (Buyurtma {order.order_number})", 'order': order, 'quantity': order.total_quantity},
        'tickets': tickets
    })


def articles_catalog_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_article':
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            if code and name:
                Article.objects.create(code=code, name=name, description=description)
                messages.success(request, f"Model {code} muvaffaqiyatli yaratildi.")
        elif action == 'create_operation':
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            if code and name:
                Operation.objects.create(code=code, name=name)
                messages.success(request, f"Operatsiya {code} - {name} katalogga qo'shildi.")
        elif action == 'link_operation':
            article_id = request.POST.get('article_id')
            operation_id = request.POST.get('operation_id')
            price_per_unit = request.POST.get('price_per_unit', '0')
            sequence = request.POST.get('sequence', '1')

            art = get_object_or_404(Article, id=article_id)
            op = get_object_or_404(Operation, id=operation_id)
            ArticleOperation.objects.update_or_create(
                article=art,
                operation=op,
                defaults={
                    'price_per_unit': Decimal(price_per_unit),
                    'sequence': int(sequence)
                }
            )
            messages.success(request, f"{art.code} ga '{op.name}' operatsiyasi ({price_per_unit} UZS) biriktirildi.")
        return redirect('production:articles_catalog')

    articles = Article.objects.all().prefetch_related('article_operations__operation').order_by('code')
    operations = Operation.objects.all().order_by('code')

    return render(request, 'production/articles_catalog.html', {
        'articles': articles,
        'operations': operations
    })
