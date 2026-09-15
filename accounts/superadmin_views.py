import csv
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from .models import User, Worker, WorkerPayout
from production.models import Order, Ticket, Article, Operation, ArticleOperation, OrderItem


def superadmin_required(view_func):
    """
    Ruxsat tekshiruvi: faqat superuser yoki SUPER_ADMIN roliga ega foydalanuvchilar
    """
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Login sahifasiga yoki admin loginga yo'naltirish
            return redirect('/admin/login/?next=' + request.path)
        if not (request.user.is_superuser or request.user.role == User.Role.SUPER_ADMIN):
            messages.error(request, "Ushbu sahifaga kirish uchun Super Admin huquqi talab qilinadi!")
            return redirect('production:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


@superadmin_required
def superadmin_dashboard(request):
    today = timezone.localdate()
    period = request.GET.get('period', 'today')

    ticket_filter = Q(status=Ticket.Status.SCANNED)
    if period == 'today':
        ticket_filter &= Q(scanned_at__date=today)
    elif period == 'week':
        week_start = today - timezone.timedelta(days=today.weekday())
        ticket_filter &= Q(scanned_at__date__gte=week_start)
    elif period == 'month':
        ticket_filter &= Q(scanned_at__year=today.year, scanned_at__month=today.month)

    filtered_tickets = Ticket.objects.filter(ticket_filter)

    # 1. Moliyaviy KPI lar
    total_earned = filtered_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0')
    total_units = filtered_tickets.aggregate(s=Sum('quantity'))['s'] or 0

    # Barcha vaqt bo'yicha to'lovlar va qoldiqlar
    all_time_earned = Ticket.objects.filter(status=Ticket.Status.SCANNED).aggregate(s=Sum('total_amount'))['s'] or Decimal('0')
    all_time_paid = WorkerPayout.objects.aggregate(s=Sum('amount'))['s'] or Decimal('0')
    all_time_balance = all_time_earned - all_time_paid

    # 2. Ishlab chiqarish buyurtmalari
    orders_count = Order.objects.count()
    active_orders = Order.objects.filter(status=Order.Status.IN_PROGRESS).count()
    completed_orders = Order.objects.filter(status=Order.Status.COMPLETED).count()

    # 3. Foydalanuvchilar soni
    masters_count = User.objects.filter(role=User.Role.MASTER).count()
    admins_count = User.objects.filter(role=User.Role.ADMIN).count()
    workers_count = Worker.objects.filter(is_active=True).count()

    # 4. Masterlar faoliyati (bugungi)
    masters = User.objects.filter(role=User.Role.MASTER)
    master_stats = []
    for m in masters:
        m_tickets = Ticket.objects.filter(scanned_by=m, scanned_at__date=today, status=Ticket.Status.SCANNED)
        t_count = m_tickets.count()
        t_amount = m_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0')
        master_stats.append({
            'master': m,
            'tickets_count': t_count,
            'total_amount': t_amount,
            'telegram_id': m.telegram_user_id or "Bog'lanmagan",
        })

    # 5. Sex ekranlari 1-10 bugungi yuklamasi
    screen_stats = []
    for s_num in range(1, 11):
        s_tickets = Ticket.objects.filter(screen_number=s_num, scanned_at__date=today, status=Ticket.Status.SCANNED)
        w_count = s_tickets.values('worker').distinct().count()
        u_count = s_tickets.aggregate(s=Sum('quantity'))['s'] or 0
        a_sum = s_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0')
        screen_stats.append({
            'screen_number': s_num,
            'workers_count': w_count,
            'units_count': u_count,
            'total_amount': a_sum,
        })

    return render(request, 'superadmin/dashboard.html', {
        'period': period,
        'today_str': today.strftime("%d.%m.%Y"),
        'total_earned': total_earned,
        'total_units': total_units,
        'all_time_earned': all_time_earned,
        'all_time_paid': all_time_paid,
        'all_time_balance': all_time_balance,
        'orders_count': orders_count,
        'active_orders': active_orders,
        'completed_orders': completed_orders,
        'masters_count': masters_count,
        'admins_count': admins_count,
        'workers_count': workers_count,
        'master_stats': master_stats,
        'screen_stats': screen_stats,
    })


@superadmin_required
def superadmin_users(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_user':
            username = request.POST.get('username', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            password = request.POST.get('password', '').strip()
            role = request.POST.get('role', User.Role.MASTER)
            phone_number = request.POST.get('phone_number', '').strip()
            telegram_user_id = request.POST.get('telegram_user_id', '').strip() or None

            if not username or not password:
                messages.error(request, "Login va parol kiritilishi shart!")
            elif User.objects.filter(username=username).exists():
                messages.error(request, f"'{username}' nomli foydalanuvchi allaqachon mavjud!")
            else:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    role=role,
                    phone_number=phone_number,
                    telegram_user_id=int(telegram_user_id) if telegram_user_id else None,
                    is_staff=(role in [User.Role.SUPER_ADMIN, User.Role.ADMIN]),
                    is_superuser=(role == User.Role.SUPER_ADMIN)
                )
                messages.success(request, f"Foydalanuvchi {user.username} ({user.get_role_display()}) muvaffaqiyatli yaratildi!")

        elif action == 'update_user':
            user_id = request.POST.get('user_id')
            user = get_object_or_404(User, id=user_id)
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            user.phone_number = request.POST.get('phone_number', '').strip()
            user.role = request.POST.get('role', user.role)
            
            tg_id = request.POST.get('telegram_user_id', '').strip()
            user.telegram_user_id = int(tg_id) if tg_id else None

            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                user.set_password(new_password)

            user.is_staff = (user.role in [User.Role.SUPER_ADMIN, User.Role.ADMIN])
            user.is_superuser = (user.role == User.Role.SUPER_ADMIN)
            user.save()
            messages.success(request, f"{user.username} ma'lumotlari yangilandi.")

        elif action == 'toggle_active':
            user_id = request.POST.get('user_id')
            user = get_object_or_404(User, id=user_id)
            if user == request.user:
                messages.error(request, "O'zingizning hisobingizni bloklay olmaysiz!")
            else:
                user.is_active = not user.is_active
                user.save()
                messages.success(request, f"{user.username} holati o'zgartirildi ({'Faol' if user.is_active else 'Nofaol'}).")

        return redirect('superadmin_users')

    users = User.objects.all().order_by('-date_joined')
    return render(request, 'superadmin/users.html', {'users': users})


@superadmin_required
def superadmin_payroll(request):
    workers = Worker.objects.filter(is_active=True).prefetch_related('tickets', 'payouts').order_by('worker_id')
    
    payroll_data = []
    grand_earned = Decimal('0')
    grand_paid = Decimal('0')
    grand_balance = Decimal('0')

    for w in workers:
        earned = Decimal(str(w.total_earned))
        paid = Decimal(str(w.total_paid))
        bal = earned - paid
        grand_earned += earned
        grand_paid += paid
        grand_balance += bal

        payroll_data.append({
            'worker': w,
            'total_earned': earned,
            'total_paid': paid,
            'balance': bal,
        })

    recent_payouts = WorkerPayout.objects.select_related('worker', 'created_by').order_by('-created_at')[:20]

    return render(request, 'superadmin/payroll.html', {
        'payroll_data': payroll_data,
        'grand_earned': grand_earned,
        'grand_paid': grand_paid,
        'grand_balance': grand_balance,
        'recent_payouts': recent_payouts,
        'workers': workers
    })


@superadmin_required
def superadmin_payout_create(request):
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id')
        amount = request.POST.get('amount')
        payout_type = request.POST.get('payout_type', WorkerPayout.PayoutType.ADVANCE)
        payout_date = request.POST.get('payout_date') or timezone.localdate()
        note = request.POST.get('note', '').strip()

        if not worker_id or not amount:
            messages.error(request, "Xodim va to'lov summasi kiritilishi shart!")
        else:
            worker = get_object_or_404(Worker, id=worker_id)
            payout = WorkerPayout.objects.create(
                worker=worker,
                amount=Decimal(amount),
                payout_type=payout_type,
                payout_date=payout_date,
                note=note,
                created_by=request.user
            )
            messages.success(request, f"{worker.full_name} ga {int(payout.amount):,} UZS ({payout.get_payout_type_display()}) muvaffaqiyatli to'landi!")

    return redirect('superadmin_payroll')


@superadmin_required
def superadmin_payroll_export_csv(request):
    """
    Buxgalteriya uchun butun sdelshina ish haqi hisobotini Excel/CSV formatida yuklab olish.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    filename = f"ish_haqi_tabel_{timezone.localdate().strftime('%Y_%m_%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Xodim ID",
        "F.I.SH",
        "Telefon",
        "Jami Ishlangan Summa (UZS)",
        "To'langan Summa (Avans/Oylik) (UZS)",
        "Qoldiq Balans (Qarz) (UZS)",
        "Holati"
    ])

    workers = Worker.objects.all().order_by('worker_id')
    for w in workers:
        earned = int(w.total_earned)
        paid = int(w.total_paid)
        bal = earned - paid
        writer.writerow([
            w.worker_id,
            w.full_name,
            w.phone_number or "—",
            earned,
            paid,
            bal,
            "Faol" if w.is_active else "Nofaol"
        ])

    return response


@superadmin_required
def superadmin_pricing(request):
    if request.method == 'POST':
        art_op_id = request.POST.get('article_operation_id')
        price = request.POST.get('price_per_unit')
        sequence = request.POST.get('sequence')

        art_op = get_object_or_404(ArticleOperation, id=art_op_id)
        if price:
            art_op.price_per_unit = Decimal(price)
        if sequence:
            art_op.sequence = int(sequence)
        art_op.save()
        messages.success(request, f"{art_op.article.code} -> '{art_op.operation.name}' narxi {price} UZS ga yangilandi.")
        return redirect('superadmin_pricing')

    articles = Article.objects.all().prefetch_related('article_operations__operation').order_by('code')
    return render(request, 'superadmin/pricing.html', {'articles': articles})


@superadmin_required
def superadmin_orders_list(request):
    """
    Zakazlar va Modellar bosh sahifasi:
    - Barcha oldin yaratilgan zakazlar ro'yxati.
    - Zakaz raqami, mijoz, ichidagi modellar soni, kiyim donalari, holati.
    - Yuqori o'ngda Yangi Zakaz va Model qo'shish formasi (modal):
      - Soni, ptichka bilan operatsiyalar, har biriga narx kiritish.
    """
    if request.method == 'POST':
        order_number = request.POST.get('order_number', '').strip()
        client_name = request.POST.get('client_name', '').strip()
        deadline = request.POST.get('deadline') or None

        model_name = request.POST.get('model_name', '').strip()
        model_code = request.POST.get('model_code', '').strip().upper()
        quantity_str = request.POST.get('quantity', '0').strip()

        checked_operations = request.POST.getlist('selected_operations')

        if not order_number or not model_name or not model_code or not quantity_str:
            messages.error(request, "Iltimos, Zakaz raqami, Model nomi, kodi va sonini kiriting!")
        elif Order.objects.filter(order_number=order_number).exists():
            messages.error(request, f"'{order_number}' raqamli zakaz allaqachon mavjud!")
        else:
            try:
                quantity = int(quantity_str)
                with transaction.atomic():
                    article, _ = Article.objects.get_or_create(
                        code=model_code,
                        defaults={'name': model_name}
                    )
                    order = Order.objects.create(
                        order_number=order_number,
                        client_name=client_name,
                        deadline=deadline,
                        article=article,
                        total_quantity=quantity,
                        status=Order.Status.IN_PROGRESS
                    )
                    OrderItem.objects.create(
                        order=order,
                        article=article,
                        quantity=quantity
                    )
                    seq_counter = 1
                    for op_id in checked_operations:
                        price_val = request.POST.get(f"price_{op_id}", "0").strip()
                        try:
                            price = Decimal(price_val)
                        except Exception:
                            price = Decimal("0.00")
                        
                        op_obj = Operation.objects.filter(id=op_id).first()
                        if op_obj:
                            ArticleOperation.objects.update_or_create(
                                article=article,
                                operation=op_obj,
                                defaults={'price_per_unit': price, 'sequence': seq_counter}
                            )
                            seq_counter += 1

                    messages.success(request, f"'{order.order_number}' zakazi va '{article.name}' modeli muvaffaqiyatli yaratildi!")
                    return redirect('superadmin_order_detail', order_id=order.id)
            except Exception as e:
                messages.error(request, f"Xatolik yuz berdi: {str(e)}")

    orders = Order.objects.all().prefetch_related('items__article__article_operations', 'boxes').order_by('-created_at')
    all_operations = Operation.objects.all().order_by('code')

    return render(request, 'superadmin/orders_list.html', {
        'orders': orders,
        'all_operations': all_operations
    })


@superadmin_required
def superadmin_order_detail(request, order_id: int):
    """
    Zakaz ustiga bosganda:
    - Ichidagi modellar va ularga biriktirilgan barcha operatsiyalar va narxlari jadvali.
    - 1 dona mahsulot uchun umumiy sdelshina haqi va butun partiya uchun hisoblangan sdelshina fondi.
    - Yangi model qo'shish imkoniyati.
    """
    order = get_object_or_404(Order.objects.prefetch_related(
        'items__article__article_operations__operation',
        'boxes__tickets'
    ), id=order_id)

    if not order.items.exists() and order.article:
        OrderItem.objects.get_or_create(
            order=order,
            article=order.article,
            defaults={'quantity': order.total_quantity}
        )
        order.refresh_from_db()

    items = order.items.all().select_related('article').prefetch_related('article__article_operations__operation')
    all_operations = Operation.objects.all().order_by('code')

    return render(request, 'superadmin/order_detail.html', {
        'order': order,
        'items': items,
        'all_operations': all_operations
    })


@superadmin_required
def superadmin_order_add_model(request, order_id: int):
    """
    Mavjud Zakaz ichiga yana qo'shimcha Model qo'shish.
    """
    order = get_object_or_404(Order, id=order_id)
    if request.method == 'POST':
        model_name = request.POST.get('model_name', '').strip()
        model_code = request.POST.get('model_code', '').strip().upper()
        quantity_str = request.POST.get('quantity', '0').strip()
        checked_operations = request.POST.getlist('selected_operations')

        if not model_name or not model_code or not quantity_str:
            messages.error(request, "Model nomi, kodi va soni to'ldirilishi shart!")
        else:
            try:
                quantity = int(quantity_str)
                with transaction.atomic():
                    article, _ = Article.objects.get_or_create(
                        code=model_code,
                        defaults={'name': model_name}
                    )
                    item, created = OrderItem.objects.update_or_create(
                        order=order,
                        article=article,
                        defaults={'quantity': quantity}
                    )
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
                                article=article,
                                operation=op_obj,
                                defaults={'price_per_unit': price, 'sequence': seq}
                            )
                            seq += 1

                    messages.success(request, f"Zakazga yangi '{article.name}' ({quantity} dona) modeli qo'shildi!")
            except Exception as e:
                messages.error(request, f"Xatolik: {str(e)}")

    return redirect('superadmin_order_detail', order_id=order.id)

