import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Q, F, Prefetch
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from .models import Customer, ProductModel, ProductModelOperation, Article, Operation, ArticleOperation, Order, Box, Ticket, OrderItem, CuttingBatch, CuttingBatchItem
from .services import allocate_ticket_quantities, generate_box_tickets, create_boxes_for_order, create_box_with_tickets
from accounts.models import Worker


def dashboard_view(request):
    if not (request.user.is_authenticated and request.user.is_superadmin()):
        return redirect('production:order_list')

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    from datetime import datetime, time
    day_start = timezone.make_aware(datetime.combine(today, time.min), tz)
    day_end = timezone.make_aware(datetime.combine(today, time.max), tz)

    # Bugungi statistika (Index-friendly datetime range)
    today_tickets = Ticket.objects.filter(status=Ticket.Status.SCANNED, scanned_at__range=(day_start, day_end))
    
    today_agg = today_tickets.aggregate(
        wages=Sum('total_amount'),
        units=Sum('quantity'),
        workers=Count('worker', distinct=True)
    )
    total_wages_today = today_agg['wages'] or Decimal('0')
    total_units_today = today_agg['units'] or 0
    active_workers_today = today_agg['workers'] or 0

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
    boxes = order.boxes.exclude(status=Box.Status.CANCELLED).select_related('article', 'order').prefetch_related('tickets').order_by('box_number')

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'create_boxes' or 'create_boxes' in request.POST:
            box_count = int(request.POST.get('box_count', 1))
            box_size = int(request.POST.get('box_size', 100))
            article_id = request.POST.get('article_id')
            razmer = request.POST.get('razmer', '').strip() or None
            pastal_number = request.POST.get('pastal_number', '').strip()

            article = None
            if article_id:
                article = Article.objects.filter(id=article_id).first()
            if not article:
                first_item = order.items.first()
                article = first_item.article if first_item else order.article

            if not article:
                messages.error(request, "Quti yaratish uchun avval zakazga model biriktirilgan bo'lishi kerak!")
                return redirect('production:order_detail', order_id=order.id)

            created = create_box_with_tickets(
                order=order,
                article=article,
                quantity=box_size,
                count=box_count,
                razmer=razmer,
                pastal_number=pastal_number
            )
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
        item.boxes_count = order.boxes.exclude(status=Box.Status.CANCELLED).filter(article=item.article).count()
        item.boxes_total_qty = order.boxes.exclude(status=Box.Status.CANCELLED).filter(article=item.article).aggregate(s=Sum('quantity'))['s'] or 0

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
        razmer_val = request.POST.get('razmer')
        if razmer_val is not None:
            box.razmer = razmer_val.strip() or None
            box.save(update_fields=['razmer'])

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
            'art_op': art_op,
            'split': current_split,
            'current_split': current_split,
            'allocations': allocations
        })

    return render(request, 'production/box_split_wizard.html', {
        'box': box,
        'article': article,
        'ops_with_preview': ops_with_preview
    })


def box_print_stickers_view(request, box_id: int):
    box = get_object_or_404(
        Box.objects.exclude(status=Box.Status.CANCELLED).select_related('order', 'article'), 
        id=box_id
    )
    tickets = box.tickets.exclude(status=Ticket.Status.CANCELLED).select_related(
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

    if not box.is_printed:
        box.is_printed = True
        box.printed_at = timezone.now()
        box.save(update_fields=['is_printed', 'printed_at'])
        if box.cutting_batch_item:
            box.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
            box.cutting_batch_item.save(update_fields=['status'])

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
    pastal_code = request.GET.get('pastal', '').strip()
    batch_id = request.GET.get('batch_id', '').strip()

    selected_article = None
    if article_id:
        try:
            selected_article = Article.objects.filter(id=int(article_id)).first()
        except (ValueError, TypeError):
            selected_article = None

    selected_pastal = pastal_code
    if batch_id and not selected_pastal:
        try:
            b_obj = CuttingBatch.objects.filter(id=int(batch_id)).first()
            if b_obj:
                selected_pastal = b_obj.pastal_code or b_obj.name
        except (ValueError, TypeError):
            pass

    tickets_qs = Ticket.objects.filter(
        box__order=order
    ).exclude(
        box__status=Box.Status.CANCELLED
    ).exclude(
        status=Ticket.Status.CANCELLED
    )
    if selected_article:
        tickets_qs = tickets_qs.filter(
            Q(box__article=selected_article) | Q(article_operation__article=selected_article)
        )
    if box_id:
        try:
            tickets_qs = tickets_qs.filter(box_id=int(box_id))
        except (ValueError, TypeError):
            pass
    if pastal_code:
        tickets_qs = tickets_qs.filter(
            Q(box__pastal_number=pastal_code) |
            Q(box__cutting_batch_item__batch__pastal_code=pastal_code)
        )
    if batch_id:
        try:
            tickets_qs = tickets_qs.filter(box__cutting_batch_item__batch_id=int(batch_id))
        except (ValueError, TypeError):
            pass

    tickets = tickets_qs.select_related(
        'box', 'box__order', 'box__article', 'box__cutting_batch_item__batch', 'article_operation__operation', 'article_operation__article'
    ).order_by('box__article__code', 'box__box_number', 'article_operation__sequence', 'split_index')

    grouped_data = OrderedDict()
    for ticket in tickets:
        if not ticket.box or ticket.box.status == Box.Status.CANCELLED or ticket.status == Ticket.Status.CANCELLED:
            continue
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

    box_display_title = f"BARCHA (Buyurtma {order.order_number})"
    if selected_pastal:
        box_display_title = f"Pastal: {selected_pastal} (Buyurtma {order.order_number})"

    # Avtomatik ravishda chop etildi deb belgilash (himoya uchun)
    box_ids = [t.box_id for t in tickets if t.box_id and t.box and t.box.status != Box.Status.CANCELLED]
    if box_ids:
        now = timezone.now()
        Box.objects.filter(id__in=box_ids, is_printed=False).exclude(status=Box.Status.CANCELLED).update(is_printed=True, printed_at=now)
        CuttingBatchItem.objects.filter(boxes__id__in=box_ids).update(status=CuttingBatchItem.Status.STICKERS_PRINTED)

    return render(request, 'production/box_stickers_print.html', {
        'box': {'box_number': box_display_title, 'order': order, 'quantity': order.all_models_quantity},
        'tickets': tickets,
        'order': order,
        'is_order_all': True,
        'articles_grouped_list': articles_grouped_list,
        'selected_article': selected_article,
        'selected_pastal': selected_pastal,
        'batch_id': batch_id,
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

    if not box.is_printed:
        box.is_printed = True
        box.printed_at = timezone.now()
        box.save(update_fields=['is_printed', 'printed_at'])
        if box.cutting_batch_item:
            box.cutting_batch_item.status = CuttingBatchItem.Status.STICKERS_PRINTED
            box.cutting_batch_item.save(update_fields=['status'])

    art = box.target_article
    art_code = "".join(c for c in (art.code if art else "ARTIKUL") if c.isalnum() or c in ('-', '_')).strip() or "ARTIKUL"
    clean_ord = "".join(c for c in (box.order.order_number if box.order else f"ORD_{box.order_id}") if c.isalnum() or c in ('-', '_')).strip()
    filename = f"{art_code}_QUTI_{box.box_number}_{box.box_code}_{clean_ord}.pdf"

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def order_download_all_stickers_100x60_pdf(request, order_id: int):
    """Buyurtmadagi barcha qutilar, tanlangan model yoki tanlangan pastal biletlarini 65x45 mm stiker PDF formatida yuklab olish"""
    from .sticker_generator import generate_box_stickers_100x60_pdf
    order = get_object_or_404(Order, id=order_id)
    article_id = request.GET.get('article_id')
    pastal_code = request.GET.get('pastal', '').strip()
    batch_id = request.GET.get('batch_id', '').strip()
    tickets_qs = Ticket.objects.filter(
        box__order=order
    ).exclude(
        box__status=Box.Status.CANCELLED
    ).exclude(
        status=Ticket.Status.CANCELLED
    )
    clean_ord = "".join(c for c in order.order_number if c.isalnum() or c in ('-', '_')).strip() or f"ORDER_{order.id}"

    if pastal_code or batch_id:
        if pastal_code:
            tickets_qs = tickets_qs.filter(
                Q(box__pastal_number=pastal_code) |
                Q(box__cutting_batch_item__batch__pastal_code=pastal_code)
            )
            clean_pastal = "".join(c for c in pastal_code if c.isalnum() or c in ('-', '_')).strip()
        else:
            clean_pastal = f"BATCH_{batch_id}"
        if batch_id:
            try:
                tickets_qs = tickets_qs.filter(box__cutting_batch_item__batch_id=int(batch_id))
            except (ValueError, TypeError):
                pass
        filename = f"PASTAL_{clean_pastal}_{clean_ord}_STIKERLAR_65x45.pdf"
    elif article_id:
        try:
            art = Article.objects.get(id=int(article_id))
            tickets_qs = tickets_qs.filter(Q(box__article=art) | Q(article_operation__article=art))
            clean_art = "".join(c for c in art.code if c.isalnum() or c in ('-', '_')).strip() or "ARTIKUL"
            filename = f"{clean_art}_BARCHA_QUTILAR_{clean_ord}_STIKERLAR_65x45.pdf"
        except (Article.DoesNotExist, ValueError):
            filename = f"BUYURTMA_{clean_ord}_BARCHA_STIKERLAR_65x45.pdf"
    else:
        filename = f"BUYURTMA_{clean_ord}_BARCHA_STIKERLAR_65x45.pdf"

    # Avtomatik ravishda chop etildi deb belgilash (himoya uchun)
    box_ids = list(tickets_qs.values_list('box_id', flat=True).distinct())
    if box_ids:
        now = timezone.now()
        Box.objects.filter(id__in=box_ids, is_printed=False).exclude(status=Box.Status.CANCELLED).update(is_printed=True, printed_at=now)
        CuttingBatchItem.objects.filter(boxes__id__in=box_ids).update(status=CuttingBatchItem.Status.STICKERS_PRINTED)

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
            name = request.POST.get('name', '').strip()
            code = request.POST.get('code', '').strip().upper()
            if not code and name:
                code = name.upper()[:50]
            if name:
                op = Operation.objects.create(code=code, name=name)
                messages.success(request, f"Operatsiya {op.code} - {op.name} katalogga qo'shildi.")
        elif action == 'delete_operation':
            op_id = request.POST.get('operation_id')
            op = get_object_or_404(Operation, id=op_id)
            op_name = op.name
            tickets_count = Ticket.objects.filter(article_operation__operation=op).count()
            if tickets_count > 0:
                messages.error(request, f"'{op_name}' operatsiyasini o'chirib bo'lmaydi! Unga bog'langan {tickets_count} ta bilet mavjud.")
            else:
                try:
                    op.delete()
                    messages.success(request, f"'{op_name}' operatsiyasi muvaffaqiyatli o'chirildi.")
                except Exception as e:
                    messages.error(request, f"Xatolik yuz berdi: {str(e)}")
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


@login_required
def box_pipeline_statistics_view(request):
    """
    Qutilar vizual statistikasi sahifasi.
    Ketma-ket operatsiya dumaloqlari (BLUE = Scanned, RED = Pending).
    Har bir ko'k dumaloq ustiga bosilganda stiker, tikuvchi, master va vaqt tafsilotlari ko'rinadi.
    """
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'ALL').strip().upper()
    clean_q = q.lstrip('#').strip()

    ordered_tickets = Ticket.objects.select_related(
        'article_operation__article',
        'article_operation__operation',
        'worker__user',
        'scanned_by'
    ).order_by('article_operation__sequence', 'split_index', 'id')

    boxes_qs = Box.objects.annotate(
        annotated_total_tickets=Count('tickets', distinct=True),
        annotated_scanned_tickets=Count('tickets', filter=Q(tickets__status=Ticket.Status.SCANNED), distinct=True)
    ).select_related(
        'order__customer',
        'order__article',
        'article__model'
    ).prefetch_related(
        'order__items__article',
        Prefetch('tickets', queryset=ordered_tickets, to_attr='prefetched_tickets')
    ).order_by('-created_at', 'order', 'box_number')

    highlighted_ticket_id = None
    highlighted_box_id = None

    if clean_q:
        # Avvalo tezkor indeksli bilet qidiramiz (<1ms)
        from .terminal_views import find_ticket_fast
        fast_ticket_id = find_ticket_fast(clean_q)
        if fast_ticket_id:
            ticket_match = Ticket.objects.only('id', 'box_id').filter(id=fast_ticket_id).first()
        else:
            ticket_match = Ticket.objects.filter(
                Q(stiker_code__iexact=clean_q) |
                Q(ticket_code__iexact=clean_q) |
                Q(id=int(clean_q) if clean_q.isdigit() else -1)
            ).first()

        if ticket_match:
            boxes_qs = boxes_qs.filter(id=ticket_match.box_id)
            highlighted_ticket_id = ticket_match.id
            highlighted_box_id = ticket_match.box_id
        else:
            box_filter = (
                Q(box_code__iexact=clean_q) |
                Q(order__order_number__icontains=clean_q) |
                Q(order__customer__name__icontains=clean_q) |
                Q(order__client_name__icontains=clean_q) |
                Q(article__code__icontains=clean_q) |
                Q(article__name__icontains=clean_q) |
                Q(article__model__name__icontains=clean_q)
            )
            if clean_q.isdigit():
                box_filter |= Q(box_number=int(clean_q))
            boxes_qs = boxes_qs.filter(box_filter)

    if status_filter == Box.Status.COMPLETED:
        boxes_qs = boxes_qs.filter(
            Q(status=Box.Status.COMPLETED) | Q(annotated_total_tickets__gt=0, annotated_scanned_tickets=F('annotated_total_tickets'))
        )
    elif status_filter == Box.Status.IN_PROGRESS:
        boxes_qs = boxes_qs.filter(
            Q(status=Box.Status.IN_PROGRESS) | Q(annotated_scanned_tickets__gt=0, annotated_scanned_tickets__lt=F('annotated_total_tickets'))
        )
    elif status_filter == Box.Status.CREATED:
        boxes_qs = boxes_qs.filter(
            Q(status=Box.Status.CREATED, annotated_scanned_tickets=0) | Q(annotated_scanned_tickets=0)
        )

    # Tezkor bitta agregatsiya so'rovi (1ms)
    counts = Box.objects.aggregate(
        total=Count('id'),
        in_prog=Count('id', filter=Q(status=Box.Status.IN_PROGRESS)),
        comp=Count('id', filter=Q(status=Box.Status.COMPLETED))
    )
    total_boxes_count = counts['total'] or 0
    in_progress_boxes_count = counts['in_prog'] or 0
    completed_boxes_count = counts['comp'] or 0

    paginator = Paginator(boxes_qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    boxes_pipeline_data = []
    current_tz = timezone.get_current_timezone()

    for b in page_obj:
        b_tickets = getattr(b, 'prefetched_tickets', None)
        if b_tickets is None:
            b_tickets = list(b.tickets.all().order_by('article_operation__sequence', 'split_index', 'id'))

        tickets_info = []
        scanned_cnt = 0

        for t in b_tickets:
            is_scanned = (t.status == Ticket.Status.SCANNED)
            if is_scanned:
                scanned_cnt += 1

            is_hl = (t.id == highlighted_ticket_id or (clean_q and t.stiker_code and t.stiker_code.upper() == clean_q.upper()))

            tickets_info.append({
                'id': t.id,
                'stiker_code': t.stiker_code or f"ID{t.id}",
                'stiker_id': t.stiker_id,
                'ticket_code': t.ticket_code,
                'sequence': t.article_operation.sequence if t.article_operation else 1,
                'op_name': t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else "Operatsiya",
                'op_code': t.article_operation.operation.code if (t.article_operation and t.article_operation.operation) else "",
                'status': t.status,
                'is_scanned': is_scanned,
                'worker_name': t.worker.full_name if t.worker else "—",
                'worker_id': t.worker.worker_id if t.worker else "",
                'worker_uid': t.worker.user.uid if (t.worker and t.worker.user and t.worker.user.uid) else "",
                'scanned_at_formatted': t.scanned_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M:%S") if t.scanned_at else "—",
                'scanned_by_name': (t.scanned_by.get_full_name() or t.scanned_by.username) if t.scanned_by else "—",
                'screen_number': t.screen_number or "—",
                'quantity': t.quantity,
                'total_amount': int(t.total_amount) if t.total_amount else 0,
                'total_amount_formatted': f"{int(t.total_amount):,} UZS".replace(",", " ") if t.total_amount else "0 UZS",
                'is_highlighted': is_hl,
            })

        total_cnt = getattr(b, 'annotated_total_tickets', len(b_tickets)) or len(b_tickets)
        progress_pct = int((scanned_cnt / total_cnt) * 100) if total_cnt > 0 else 0

        boxes_pipeline_data.append({
            'box': b,
            'tickets': tickets_info,
            'total_tickets': total_cnt,
            'scanned_tickets': scanned_cnt,
            'progress_pct': progress_pct,
            'target_article': b.target_article,
        })

    context = {
        'boxes_data': boxes_pipeline_data,
        'page_obj': page_obj,
        'q': q,
        'status_filter': status_filter,
        'total_boxes_count': total_boxes_count,
        'in_progress_boxes_count': in_progress_boxes_count,
        'completed_boxes_count': completed_boxes_count,
        'highlighted_ticket_id': highlighted_ticket_id,
        'highlighted_box_id': highlighted_box_id,
    }
    return render(request, 'production/statistics_pipeline.html', context)


@login_required
def api_ticket_scan_detail(request, code_or_id):
    current_tz = timezone.get_current_timezone()
    from .terminal_views import find_ticket_fast
    fast_ticket_id = find_ticket_fast(code_or_id)
    if fast_ticket_id:
        ticket = Ticket.objects.filter(id=fast_ticket_id).select_related(
            'box__order',
            'box__article',
            'article_operation__article',
            'article_operation__operation',
            'worker__user',
            'scanned_by'
        ).first()
    else:
        clean_val = code_or_id.lstrip('#').strip()
        ticket = Ticket.objects.filter(
            Q(stiker_code__iexact=clean_val) |
            Q(ticket_code__iexact=clean_val) |
            Q(id=int(clean_val) if clean_val.isdigit() else -1)
        ).select_related(
            'box__order',
            'box__article',
            'article_operation__article',
            'article_operation__operation',
            'worker__user',
            'scanned_by'
        ).first()

    if not ticket:
        return JsonResponse({'success': False, 'error': 'Stiker topilmadi'}, status=404)

    is_scanned = (ticket.status == Ticket.Status.SCANNED)

    data = {
        'success': True,
        'id': ticket.id,
        'stiker_id': ticket.stiker_id,
        'stiker_code': ticket.stiker_code,
        'ticket_code': ticket.ticket_code,
        'box_number': ticket.box.box_number if ticket.box else "—",
        'box_code': ticket.box.box_code if ticket.box else "—",
        'order_number': ticket.box.order.order_number if (ticket.box and ticket.box.order) else "—",
        'client_name': ticket.box.order.client_name if (ticket.box and ticket.box.order) else "—",
        'article_name': ticket.article_operation.article.name if (ticket.article_operation and ticket.article_operation.article) else "—",
        'article_code': ticket.article_operation.article.code if (ticket.article_operation and ticket.article_operation.article) else "—",
        'operation_name': ticket.article_operation.operation.name if (ticket.article_operation and ticket.article_operation.operation) else "—",
        'sequence': ticket.article_operation.sequence if ticket.article_operation else 1,
        'status': ticket.status,
        'is_scanned': is_scanned,
        'worker_name': ticket.worker.full_name if ticket.worker else "—",
        'worker_id': ticket.worker.worker_id if ticket.worker else "—",
        'worker_uid': ticket.worker.user.uid if (ticket.worker and ticket.worker.user and ticket.worker.user.uid) else "—",
        'scanned_at': ticket.scanned_at.astimezone(current_tz).strftime("%d.%m.%Y %H:%M:%S") if ticket.scanned_at else "—",
        'scanned_by': (ticket.scanned_by.get_full_name() or ticket.scanned_by.username) if ticket.scanned_by else "—",
        'screen_number': ticket.screen_number or "—",
        'quantity': ticket.quantity,
        'total_amount': int(ticket.total_amount) if ticket.total_amount else 0,
        'total_amount_formatted': f"{int(ticket.total_amount):,} UZS".replace(",", " ") if ticket.total_amount else "0 UZS",
    }
    return JsonResponse(data)


def pastal_passport_view(request, batch_id: int):
    """
    Pastal Pasporti (A4 Marshrut Varaqasi) Chop Etish Oynasi:
    - Model rasmi, Zakaz ma'lumotlari, Pastal kodi, Artikul, Model nomi, Bichuvchi, Gazlama
    - Razmerlar, Soni, Meto oralig'i, Quti ID lari va 3 ta checkbox ustunlari
    - Jami hisob-kitob va mas'ullar imzosi qatori
    """
    if not request.user.is_authenticated:
        return redirect('root_login')

    batch = get_object_or_404(
        CuttingBatch.objects.select_related(
            'order_item__order__customer',
            'order_item__article__model'
        ).prefetch_related(
            'items__order_item_size',
            'items__boxes'
        ),
        id=batch_id
    )
    order = batch.order_item.order
    article = batch.order_item.article

    batch_items_data = []
    total_boxes_count = 0
    total_qty = 0

    for b_it in batch.items.all().order_by('order_item_size__id'):
        boxes = list(b_it.boxes.exclude(status=Box.Status.CANCELLED).order_by('box_number'))
        total_boxes_count += len(boxes)
        qty = b_it.effective_quantity
        total_qty += qty

        # Meto oralig'i
        meto_range = "—"
        if b_it.meto_number_start and b_it.meto_number_end:
            meto_range = f"#{b_it.meto_number_start} — #{b_it.meto_number_end}"
        elif boxes and any(b.meto_range for b in boxes):
            ranges = [b.meto_range for b in boxes if b.meto_range]
            meto_range = ranges[0]

        # Qutilar ro'yxati (masalan: #161 (44), #162 (43), #163 (43))
        boxes_display = [f"#{b.box_number} ({b.quantity})" for b in boxes]
        boxes_text = ", ".join(boxes_display) if boxes_display else "—"

        batch_items_data.append({
            'size_name': b_it.order_item_size.size_name,
            'quantity': qty,
            'meto_range': meto_range,
            'boxes': boxes,
            'boxes_display': boxes_display,
            'boxes_text': boxes_text,
            'boxes_count': len(boxes),
        })

    # Model rasmi borligini tekshirish
    article_image_url = None
    if article and article.image:
        try:
            article_image_url = article.image.url
        except Exception:
            article_image_url = None

    return render(request, 'production/pastal_passport.html', {
        'batch': batch,
        'order': order,
        'article': article,
        'article_image_url': article_image_url,
        'batch_items': batch_items_data,
        'total_sizes_count': len(batch_items_data),
        'total_boxes_count': total_boxes_count,
        'total_qty': total_qty,
    })


