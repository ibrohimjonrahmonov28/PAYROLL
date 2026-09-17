import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Q
from .models import Article, Operation, ArticleOperation, Order, Box, Ticket, OrderItem
from .services import allocate_ticket_quantities, generate_box_tickets, create_boxes_for_order, create_box_with_tickets
from accounts.models import Worker


def dashboard_view(request):
    if not (request.user.is_authenticated and request.user.is_superadmin()):
        return redirect('production:order_list')

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
    if not request.user.is_authenticated:
        if (
            request.headers.get('accept') == 'application/json' or
            request.GET.get('format') == 'json' or
            request.content_type == 'application/json'
        ):
            return JsonResponse({'status': 'UNAUTHORIZED', 'message': "Tizimga kirish talab qilinadi!"}, status=401)
        return redirect('root_login')

    is_json_request = (
        request.headers.get('accept') == 'application/json' or
        request.GET.get('format') == 'json' or
        request.content_type == 'application/json'
    )

    if request.method == 'POST':
        if request.content_type == 'application/json':
            try:
                body_data = json.loads(request.body.decode('utf-8'))
            except Exception:
                return JsonResponse({'status': 'ERROR', 'message': "Noto'g'ri JSON formati!"}, status=400)
            order_number = str(body_data.get('order_number', '')).strip()
            article_id = body_data.get('article_id')
            total_quantity = body_data.get('total_quantity')
            client_name = str(body_data.get('client_name', '')).strip()
            deadline = body_data.get('deadline') or None
        else:
            order_number = request.POST.get('order_number', '').strip()
            article_id = request.POST.get('article_id')
            total_quantity = request.POST.get('total_quantity')
            client_name = request.POST.get('client_name', '').strip()
            deadline = request.POST.get('deadline') or None

        if not order_number or not article_id or not total_quantity:
            if is_json_request:
                return JsonResponse({'status': 'ERROR', 'message': "Iltimos, barcha majburiy maydonlarni to'ldiring."}, status=400)
            messages.error(request, "Iltimos, barcha majburiy maydonlarni to'ldiring.")
        elif Order.objects.filter(order_number=order_number).exists():
            if is_json_request:
                return JsonResponse({'status': 'ERROR', 'message': f"{order_number} raqamli buyurtma allaqachon mavjud!"}, status=400)
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
            if is_json_request:
                return JsonResponse({
                    'status': 'SUCCESS',
                    'message': f"Buyurtma {order.order_number} muvaffaqiyatli yaratildi!",
                    'order': {
                        'id': order.id,
                        'order_number': order.order_number,
                        'client_name': order.client_name,
                        'total_quantity': order.total_quantity,
                        'status': order.status,
                    }
                }, status=201)
            messages.success(request, f"Buyurtma {order.order_number} muvaffaqiyatli yaratildi!")
            return redirect('production:order_detail', order_id=order.id)

    search_q = request.GET.get('q', '').strip()
    orders = Order.objects.annotate(
        annotated_completed_tickets=Count('boxes__tickets', filter=Q(boxes__tickets__status=Ticket.Status.SCANNED), distinct=True),
        annotated_total_tickets=Count('boxes__tickets', distinct=True),
        annotated_total_boxes_qty=Sum('boxes__quantity', distinct=True)
    ).prefetch_related('items__article', 'boxes').select_related('article').order_by('-created_at')
    if search_q:
        orders = orders.filter(
            Q(order_number__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(article__name__icontains=search_q) |
            Q(article__code__icontains=search_q) |
            Q(boxes__box_code__icontains=search_q)
        ).distinct()

    if is_json_request:
        orders_data = []
        for o in orders:
            orders_data.append({
                'id': o.id,
                'order_number': o.order_number,
                'client_name': o.client_name,
                'article': {
                    'id': o.article.id,
                    'code': o.article.code,
                    'name': o.article.name,
                } if o.article else None,
                'total_quantity': o.total_quantity,
                'status': o.status,
                'deadline': str(o.deadline) if o.deadline else None,
                'boxes_count': o.boxes.count(),
                'created_at': o.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            })
        return JsonResponse({
            'status': 'SUCCESS',
            'count': len(orders_data),
            'orders': orders_data
        })

    articles = Article.objects.all().order_by('code')
    return render(request, 'production/order_list.html', {
        'orders': orders,
        'articles': articles,
        'search_q': search_q,
    })


def order_detail_view(request, order_id: int):
    if not request.user.is_authenticated:
        if (
            request.headers.get('accept') == 'application/json' or
            request.GET.get('format') == 'json' or
            request.content_type == 'application/json'
        ):
            return JsonResponse({'status': 'UNAUTHORIZED', 'message': "Tizimga kirish talab qilinadi!"}, status=401)
        return redirect('root_login')
    order = get_object_or_404(
        Order.objects.prefetch_related('items__article__article_operations__operation', 'boxes__tickets', 'boxes__article'), 
        id=order_id
    )
    boxes = order.boxes.all().select_related('article', 'order').prefetch_related('tickets').order_by('box_number')

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'create_boxes' or 'create_boxes' in request.POST:
            box_count = int(request.POST.get('box_count', 1))
            box_size = int(request.POST.get('box_size', 100))
            article_id = request.POST.get('article_id')

            article = None
            if article_id:
                article = Article.objects.filter(id=article_id).first()
            if not article:
                first_item = order.items.first()
                article = first_item.article if first_item else order.article

            if not article:
                messages.error(request, "Quti yaratish uchun avval zakazga model biriktirilgan bo'lishi kerak!")
                return redirect('production:order_detail', order_id=order.id)

            created = create_box_with_tickets(order=order, article=article, quantity=box_size, count=box_count)
            messages.success(request, f"{len(created)} ta {box_size} talik quti va ularning barcha QR stikerlari muvaffaqiyatli yaratildi!")
            return redirect('production:order_detail', order_id=order.id)

        elif action == 'delete_box':
            box_id = request.POST.get('box_id')
            box = get_object_or_404(Box, id=box_id, order=order)
            box_num = box.box_number
            box.delete()
            messages.success(request, f"Quti #{box_num} o'chirildi.")
            return redirect('production:order_detail', order_id=order.id)

    order_items = order.items.all().select_related('article').prefetch_related('article__article_operations__operation')
    for item in order_items:
        item.boxes_count = order.boxes.filter(article=item.article).count()
        item.boxes_total_qty = order.boxes.filter(article=item.article).aggregate(s=Sum('quantity'))['s'] or 0

    available_articles = []
    if order_items.exists():
        available_articles = [item.article for item in order_items]
    elif order.article:
        available_articles = [order.article]
    else:
        available_articles = list(Article.objects.all()[:10])

    return render(request, 'production/order_detail.html', {
        'order': order,
        'boxes': boxes,
        'order_items': order_items,
        'available_articles': available_articles,
    })


def box_split_wizard_view(request, box_id: int):
    """
    SRS 3.2 bo'yicha Admin quti operatsiyalarini bo'lish (Split) va QR stikerlarni generatsiya qilish oynasi.
    """
    box = get_object_or_404(Box.objects.select_related('order', 'article'), id=box_id)
    article = box.target_article
    if not article:
        messages.error(request, "Qutiga biriktirilgan model topilmadi.")
        return redirect('production:order_detail', order_id=box.order.id)

    article_operations = article.article_operations.select_related('operation').order_by('sequence')

    if request.method == 'POST':
        operation_splits = {}
        for art_op in article_operations:
            split_val = request.POST.get(f"split_{art_op.id}", "1")
            try:
                operation_splits[art_op.id] = max(1, int(split_val))
            except ValueError:
                operation_splits[art_op.id] = 1

        generate_box_tickets(box, operation_splits)
        messages.success(request, f"Quti #{box.box_number} uchun operatsiyalar muvaffaqiyatli bo'lindi va yangi QR biletlar generatsiya qilindi!")
        return redirect('production:order_detail', order_id=box.order.id)

    # Har bir operatsiya uchun joriy biletlar soni
    ops_with_preview = []
    for art_op in article_operations:
        existing_tickets = box.tickets.filter(article_operation=art_op)
        current_split = existing_tickets.count() or 1
        allocations = allocate_ticket_quantities(box.quantity, current_split)
        ops_with_preview.append({
            'ao': art_op,
            'current_split': current_split,
            'allocations': allocations
        })

    return render(request, 'production/box_split_wizard.html', {
        'box': box,
        'article': article,
        'ops_with_preview': ops_with_preview
    })


def box_print_stickers_view(request, box_id: int):
    box = get_object_or_404(Box.objects.select_related('order', 'article'), id=box_id)
    tickets = box.tickets.all().select_related(
        'article_operation__operation', 'article_operation__article', 'box__order', 'box__article'
    ).order_by('article_operation__sequence', 'split_index')
    art = box.target_article

    articles_grouped_list = [{
        'article': art,
        'article_id': art.id if art else 0,
        'article_code': art.code if art else "N/A",
        'article_name': art.name if art else "Model",
        'boxes': [{
            'box': box,
            'tickets': list(tickets),
        }],
        'boxes_count': 1,
        'total_tickets': len(tickets),
        'total_quantity': box.quantity,
    }]

    return render(request, 'production/box_stickers_print.html', {
        'box': box,
        'tickets': tickets,
        'order': box.order,
        'is_order_all': False,
        'articles_grouped_list': articles_grouped_list,
        'selected_article': art,
        'available_articles': [art] if art else [],
    })


def order_print_all_stickers_view(request, order_id: int):
    from collections import OrderedDict
    order = get_object_or_404(Order, id=order_id)
    article_id = request.GET.get('article_id')
    box_id = request.GET.get('box_id')

    selected_article = None
    if article_id:
        try:
            selected_article = Article.objects.filter(id=int(article_id)).first()
        except (ValueError, TypeError):
            selected_article = None

    tickets_qs = Ticket.objects.filter(box__order=order)
    if selected_article:
        tickets_qs = tickets_qs.filter(
            Q(box__article=selected_article) | Q(article_operation__article=selected_article)
        )
    if box_id:
        try:
            tickets_qs = tickets_qs.filter(box_id=int(box_id))
        except (ValueError, TypeError):
            pass

    tickets = tickets_qs.select_related(
        'box', 'box__order', 'box__article', 'article_operation__operation', 'article_operation__article'
    ).order_by('box__article__code', 'box__box_number', 'article_operation__sequence', 'split_index')

    grouped_data = OrderedDict()
    for ticket in tickets:
        art = (ticket.article_operation.article if ticket.article_operation else None) or (ticket.box.target_article if ticket.box else None)
        art_id = art.id if art else 0
        art_code = art.code if art else "N/A"
        art_name = art.name if art else "Model"

        if art_id not in grouped_data:
            grouped_data[art_id] = {
                'article': art,
                'article_id': art_id,
                'article_code': art_code,
                'article_name': art_name,
                'boxes': OrderedDict(),
                'total_tickets': 0,
                'total_quantity': 0,
            }

        grouped_data[art_id]['total_tickets'] += 1
        box = ticket.box
        box_id_val = box.id if box else 0
        if box_id_val not in grouped_data[art_id]['boxes']:
            grouped_data[art_id]['boxes'][box_id_val] = {
                'box': box,
                'tickets': [],
            }
            if box:
                grouped_data[art_id]['total_quantity'] += box.quantity

        grouped_data[art_id]['boxes'][box_id_val]['tickets'].append(ticket)

    articles_grouped_list = []
    for g in grouped_data.values():
        boxes_list = list(g['boxes'].values())
        articles_grouped_list.append({
            'article': g['article'],
            'article_id': g['article_id'],
            'article_code': g['article_code'],
            'article_name': g['article_name'],
            'boxes': boxes_list,
            'boxes_count': len(boxes_list),
            'total_tickets': g['total_tickets'],
            'total_quantity': g['total_quantity'],
        })

    order_items = order.items.all().select_related('article')
    available_articles = [item.article for item in order_items] if order_items.exists() else ([order.article] if order.article else [])

    return render(request, 'production/box_stickers_print.html', {
        'box': {'box_number': f"BARCHA (Buyurtma {order.order_number})", 'order': order, 'quantity': order.all_models_quantity},
        'tickets': tickets,
        'order': order,
        'is_order_all': True,
        'articles_grouped_list': articles_grouped_list,
        'selected_article': selected_article,
        'available_articles': available_articles,
    })


def box_download_stickers_100x60_pdf(request, box_id: int):
    """Bitta qutidagi barcha biletlarni 100x60 mm stiker PDF formatida yuklab olish"""
    from .sticker_generator import generate_box_stickers_100x60_pdf
    box = get_object_or_404(Box.objects.select_related('order', 'article'), id=box_id)
    tickets = box.tickets.all().select_related(
        'article_operation__operation', 'article_operation__article', 'box__order', 'box__article'
    ).order_by('article_operation__sequence', 'split_index')

    pdf_bytes = generate_box_stickers_100x60_pdf(tickets)
    art = box.target_article
    art_code = "".join(c for c in (art.code if art else "ARTIKUL") if c.isalnum() or c in ('-', '_')).strip() or "ARTIKUL"
    clean_ord = "".join(c for c in (box.order.order_number if box.order else f"ORD_{box.order_id}") if c.isalnum() or c in ('-', '_')).strip()
    filename = f"{art_code}_QUTI_{box.box_number}_{box.box_code}_{clean_ord}.pdf"

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def order_download_all_stickers_100x60_pdf(request, order_id: int):
    """Buyurtmadagi barcha qutilar yoki tanlangan model biletlarini 100x60 mm stiker PDF formatida yuklab olish"""
    from .sticker_generator import generate_box_stickers_100x60_pdf
    order = get_object_or_404(Order, id=order_id)
    article_id = request.GET.get('article_id')
    tickets_qs = Ticket.objects.filter(box__order=order)
    clean_ord = "".join(c for c in order.order_number if c.isalnum() or c in ('-', '_')).strip() or f"ORDER_{order.id}"

    if article_id:
        try:
            art = Article.objects.get(id=int(article_id))
            tickets_qs = tickets_qs.filter(Q(box__article=art) | Q(article_operation__article=art))
            clean_art = "".join(c for c in art.code if c.isalnum() or c in ('-', '_')).strip() or "ARTIKUL"
            filename = f"{clean_art}_BARCHA_QUTILAR_{clean_ord}_STIKERLAR_100x60.pdf"
        except (Article.DoesNotExist, ValueError):
            filename = f"BUYURTMA_{clean_ord}_BARCHA_STIKERLAR_100x60.pdf"
    else:
        filename = f"BUYURTMA_{clean_ord}_BARCHA_STIKERLAR_100x60.pdf"

    tickets = tickets_qs.select_related(
        'box', 'box__order', 'box__article', 'article_operation__operation', 'article_operation__article'
    ).order_by('box__article__code', 'box__box_number', 'article_operation__sequence', 'split_index')

    pdf_bytes = generate_box_stickers_100x60_pdf(tickets)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def articles_catalog_view(request):
    if not (request.user.is_authenticated and request.user.is_superadmin()):
        return redirect('production:order_list')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_article':
            code = request.POST.get('code', '').strip().upper()
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            checked_operations = request.POST.getlist('selected_operations')
            if code and name:
                art = Article.objects.create(code=code, name=name, description=description)
                seq = 1
                for op_id in checked_operations:
                    price_val = request.POST.get(f"price_{op_id}", "0").strip()
                    try:
                        price = Decimal(price_val)
                    except Exception:
                        price = Decimal("0.00")
                    op_obj = Operation.objects.filter(id=op_id).first()
                    if op_obj:
                        ArticleOperation.objects.update_or_create(
                            article=art,
                            operation=op_obj,
                            defaults={'price_per_unit': price, 'sequence': seq}
                        )
                        seq += 1
                messages.success(request, f"Model {code} muvaffaqiyatli yaratildi ({len(checked_operations)} ta operatsiya bilan).")
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
