import os
import re
import csv
import json
import datetime
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Count, Q, F, FloatField, ExpressionWrapper, Value, Max, ProtectedError
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.conf import settings
from django.urls import reverse
from .models import User, Worker, WorkerPayout, DailyWorkerClosing, generate_unique_user_uid
from production.models import Customer, ProductModel, ProductModelOperation, Order, Ticket, Article, Operation, ArticleOperation, OrderItem, OperationGroup, OperationGroupItem
from production.excel_reports import compact_ticket_ids


def superadmin_required(view_func):
    """
    Ruxsat tekshiruvi: faqat superuser yoki SUPER_ADMIN roliga ega foydalanuvchilar
    """
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Login sahifasiga yo'naltirish
            return redirect(f'/login/?next={request.path}')
        if not (request.user.is_superuser or request.user.role == User.Role.SUPER_ADMIN):
            messages.error(request, "Ushbu sahifaga kirish uchun Super Admin huquqi talab qilinadi!")
            return redirect('production:dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def payroll_admin_required(view_func):
    """
    Ruxsat tekshiruvi: Super Admin (HQ) yoki Filial Admini (BRANCH_ADMIN)
    """
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f'/login/?next={request.path}')
        if not (request.user.is_superuser or request.user.role in [User.Role.SUPER_ADMIN, User.Role.BRANCH_ADMIN]):
            messages.error(request, "Ushbu sahifaga kirish huquqingiz yo'q!")
            return redirect('root_login')
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

    # 4. Masterlar faoliyati (bugungi) - Batch SQL agregatsiya
    masters = list(User.objects.filter(role=User.Role.MASTER))
    master_ticket_qs = Ticket.objects.filter(
        scanned_by__in=masters,
        scanned_at__date=today,
        status=Ticket.Status.SCANNED
    ).values('scanned_by_id').annotate(
        tickets_count=Count('id'),
        total_amount=Sum('total_amount')
    )
    master_map = {item['scanned_by_id']: item for item in master_ticket_qs}
    master_stats = []
    for m in masters:
        stat = master_map.get(m.id, {})
        master_stats.append({
            'master': m,
            'tickets_count': stat.get('tickets_count', 0),
            'total_amount': stat.get('total_amount') or Decimal('0'),
            'telegram_id': m.telegram_user_id or "Bog'lanmagan",
        })

    # 5. Sex ekranlari 1-10 bugungi yuklamasi - Batch SQL agregatsiya
    screen_ticket_qs = Ticket.objects.filter(
        screen_number__gte=1,
        screen_number__lte=10,
        scanned_at__date=today,
        status=Ticket.Status.SCANNED
    ).values('screen_number').annotate(
        workers_count=Count('worker_id', distinct=True),
        units_count=Sum('quantity'),
        total_amount=Sum('total_amount')
    )
    screen_map = {item['screen_number']: item for item in screen_ticket_qs}
    screen_stats = []
    for s_num in range(1, 11):
        stat = screen_map.get(s_num, {})
        screen_stats.append({
            'screen_number': s_num,
            'workers_count': stat.get('workers_count', 0),
            'units_count': stat.get('units_count', 0),
            'total_amount': stat.get('total_amount') or Decimal('0'),
        })

    # 6. Tikuvchilar Har Bir Model Normasi va KPI Samaradorligi
    points_expr = ExpressionWrapper(
        F('quantity') * Coalesce(F('article_operation__difficulty'), Value(1.0)),
        output_field=FloatField()
    )

    # OrderItem lardan har bir (order_id, article_id) ning o'ziga xos normasini olish
    order_item_norms = {
        (oi['order_id'], oi['article_id']): oi['norm']
        for oi in OrderItem.objects.values('order_id', 'article_id', 'norm')
    }

    # Biletlarni modellar bo'yicha guruhlash
    worker_model_qs = filtered_tickets.values(
        'worker_id',
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
        earned_amount=Sum('total_amount'),
    )

    worker_models_dict = {}
    for item in worker_model_qs:
        w_id = item['worker_id']
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
        earned_money = item['earned_amount'] or Decimal('0.00')
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
                'earned_amount': Decimal('0.00'),
                'orders': set(),
            }

        worker_models_dict[w_id][m_key]['units'] += units
        worker_models_dict[w_id][m_key]['points'] += pts
        worker_models_dict[w_id][m_key]['earned_amount'] += earned_money
        if ord_num:
            worker_models_dict[w_id][m_key]['orders'].add(ord_num)

    worker_models_map = {}
    for w_id, m_dict in worker_models_dict.items():
        worker_models_map[w_id] = []
        for m_key, m_info in m_dict.items():
            norm = m_info['norm']
            pts = round(m_info['points'], 1)
            pct = round((pts / norm) * 100, 1) if norm > 0 else 0.0
            orders_str = ", ".join(sorted(m_info['orders']))

            worker_models_map[w_id].append({
                'order_number': orders_str,
                'model_code': m_info['model_code'],
                'model_name': m_info['model_name'],
                'units': m_info['units'],
                'points': pts,
                'norm': norm,
                'percentage': pct,
                'earned_amount': m_info['earned_amount'],
            })

    all_workers = list(Worker.objects.filter(is_active=True).select_related('user').order_by('worker_id'))

    worker_kpi_list = []
    above_norm_count = 0
    below_norm_count = 0
    total_pct_sum = 0
    active_norm_workers_count = 0

    for w in all_workers:
        models_list = worker_models_map.get(w.id, [])

        if models_list:
            actual_units = sum(m['units'] for m in models_list)
            earned_points = round(sum(m['points'] for m in models_list), 1)
            earned_sum = sum((m['earned_amount'] for m in models_list), Decimal('0.00'))
            # Umumiy yig'indi foizi: har bir model bo'yicha foizlarning yig'indisi
            percentage = round(sum(m['percentage'] for m in models_list), 1)
        else:
            actual_units = 0
            earned_points = 0.0
            earned_sum = Decimal('0.00')
            percentage = 0.0

        diff_pct = round(percentage - 100.0, 1)

        if actual_units > 0 or earned_points > 0:
            active_norm_workers_count += 1
            total_pct_sum += percentage
            if percentage >= 100.0:
                above_norm_count += 1
            else:
                below_norm_count += 1

        worker_kpi_list.append({
            'worker': w,
            'actual_units': actual_units,
            'earned_points': earned_points,
            'earned_sum': earned_sum,
            'percentage': percentage,
            'diff_pct': diff_pct,
            'is_above': (percentage >= 100.0),
            'progress_width': min(100, int(percentage)),
            'models_list': models_list,
        })

    # Reyting: eng yuqori foiz va ball to'plaganlar bo'yicha saralash
    worker_kpi_list.sort(key=lambda x: (x['percentage'], x['earned_points']), reverse=True)
    avg_factory_kpi = round(total_pct_sum / active_norm_workers_count, 1) if active_norm_workers_count > 0 else 0.0

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
        'worker_kpi_list': worker_kpi_list,
        'above_norm_count': above_norm_count,
        'below_norm_count': below_norm_count,
        'active_norm_workers_count': active_norm_workers_count,
        'avg_factory_kpi': avg_factory_kpi,
    })


@superadmin_required
def superadmin_users(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_user':
            username = request.POST.get('username', '').strip() or None
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            password = request.POST.get('password', '').strip()
            role = request.POST.get('role', User.Role.USER)
            branch = request.POST.get('branch', User.Branch.HQ)
            if branch not in User.Branch.values:
                branch = User.Branch.HQ
            phone_number = request.POST.get('phone_number', '').strip()
            telegram_user_id = request.POST.get('telegram_user_id', '').strip() or None

            if not first_name or not password:
                messages.error(request, "Ism va parol kiritilishi shart!")
            elif username and User.objects.filter(username=username).exists():
                messages.error(request, f"'{username}' nomli foydalanuvchi allaqachon mavjud!")
            else:
                user = User(
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    role=role,
                    branch=branch,
                    phone_number=phone_number,
                    telegram_user_id=int(telegram_user_id) if telegram_user_id else None,
                    is_staff=(role in [User.Role.SUPER_ADMIN, User.Role.ADMIN]),
                    is_superuser=(role == User.Role.SUPER_ADMIN)
                )
                user.set_password(password)
                user.save()

                # Agar Oddiy User sifatida yaratilsa, unga avtomatik Worker profili ham ochiladi
                if role == User.Role.USER and not hasattr(user, 'worker_profile'):
                    next_id = Worker.objects.count() + 1
                    worker_code = f"W-{next_id:03d}"
                    while Worker.objects.filter(worker_id=worker_code).exists():
                        next_id += 1
                        worker_code = f"W-{next_id:03d}"
                    Worker.objects.create(
                        user=user,
                        worker_id=worker_code,
                        first_name=first_name,
                        last_name=last_name,
                        phone_number=phone_number,
                        branch=branch,
                        is_active=True
                    )

                messages.success(request, f"Foydalanuvchi {user.get_full_name() or user.username or 'Yangi foydalanuvchi'} (UID: {user.uid}, {user.get_role_display()}, {user.get_branch_display()}) muvaffaqiyatli yaratildi va QR kodi generatsiya qilindi!")

        elif action == 'update_user':
            user_id = request.POST.get('user_id')
            user = get_object_or_404(User, id=user_id)
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            user.email = request.POST.get('email', '').strip()
            user.phone_number = request.POST.get('phone_number', '').strip()

            new_branch = request.POST.get('branch')
            if new_branch in User.Branch.values:
                user.branch = new_branch

            # Username o'zgartirish (faqat agar formda aniq mavjud va to'ldirilgan bo'lsa)
            if 'username' in request.POST and request.POST.get('username', '').strip():
                new_username = request.POST.get('username', '').strip()
                if new_username != user.username:
                    if User.objects.filter(username=new_username).exclude(id=user.id).exists():
                        messages.error(request, f"'{new_username}' logini allaqachon boshqa foydalanuvchi tomonidan band qilingan!")
                    else:
                        user.username = new_username

            if not user.username:
                user.username = f"user_{user.uid or generate_unique_user_uid()}"
            
            old_role = user.role
            new_role = request.POST.get('role', user.role)
            if user == request.user and new_role != User.Role.SUPER_ADMIN:
                messages.warning(request, "O'zingizning Super Admin rolingizni tushira olmaysiz!")
            else:
                user.role = new_role
            
            tg_id = request.POST.get('telegram_user_id', '').strip()
            if tg_id:
                try:
                    parsed_tg = int(tg_id)
                    existing_tg = User.objects.filter(telegram_user_id=parsed_tg).exclude(id=user.id).first()
                    if existing_tg:
                        messages.warning(request, f"Telegram ID {parsed_tg} allaqachon '{existing_tg.username or existing_tg.get_full_name()}' foydalanuvchisiga biriktirilgan, shuning uchun Telegram ID saqlanmadi.")
                    else:
                        user.telegram_user_id = parsed_tg
                except ValueError:
                    messages.warning(request, "Telegram ID faqat sonlardan iborat bo'lishi kerak.")
            else:
                user.telegram_user_id = None

            new_password = request.POST.get('new_password', '').strip()
            if new_password:
                user.set_password(new_password)

            user.is_staff = (user.role in [User.Role.SUPER_ADMIN, User.Role.ADMIN])
            user.is_superuser = (user.role == User.Role.SUPER_ADMIN)
            user.save()

            # Agar Worker profili bog'langan bo'lsa, ismi, familiyasi, telefoni va filialini ham sinxronlashtirish
            worker = getattr(user, 'worker_profile', None) or Worker.objects.filter(user=user).first()
            if worker:
                worker.first_name = user.first_name
                worker.last_name = user.last_name
                worker.phone_number = user.phone_number
                worker.branch = user.branch
                worker.save(update_fields=['first_name', 'last_name', 'phone_number', 'branch'])
            elif user.role == User.Role.USER:
                # Agar user_id bog'lanmagan worker bo'lsa, uni bog'lashga harakat qilamiz
                w_code = (user.username or '').replace('worker_', '').replace('_', '-').upper() if user.username else None
                unlinked_worker = None
                if w_code:
                    unlinked_worker = Worker.objects.filter(user__isnull=True, worker_id__iexact=w_code).first()
                if not unlinked_worker and user.phone_number:
                    unlinked_worker = Worker.objects.filter(user__isnull=True, phone_number=user.phone_number).first()
                if unlinked_worker:
                    unlinked_worker.user = user
                    unlinked_worker.first_name = user.first_name
                    unlinked_worker.last_name = user.last_name
                    unlinked_worker.phone_number = user.phone_number
                    unlinked_worker.branch = user.branch
                    unlinked_worker.save(update_fields=['user', 'first_name', 'last_name', 'phone_number', 'branch'])

            messages.success(request, f"{user.get_full_name() or user.username or 'Foydalanuvchi'} ma'lumotlari muvaffaqiyatli yangilandi.")

        elif action == 'change_role':
            user_id = request.POST.get('user_id')
            new_role = request.POST.get('role', '').strip()
            user = get_object_or_404(User, id=user_id)
            if user == request.user and new_role != User.Role.SUPER_ADMIN:
                messages.error(request, "Xatolik: O'zingizning Super Admin rolingizni o'zgartira olmaysiz!")
            elif new_role not in User.Role.values:
                messages.error(request, "Noto'g'ri rol tanlandi!")
            else:
                user.role = new_role
                user.is_staff = (new_role in [User.Role.SUPER_ADMIN, User.Role.ADMIN])
                user.is_superuser = (new_role == User.Role.SUPER_ADMIN)
                user.save(update_fields=['role', 'is_staff', 'is_superuser'])
                messages.success(request, f"'{user.get_full_name() or user.username or 'Foydalanuvchi'}' foydalanuvchisi roli '{user.get_role_display()}' ga o'zgartirildi.")

        elif action == 'delete_user':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=user_id)
            if target_user == request.user:
                messages.error(request, "Xatolik: O'zingizning profilingizni o'chira olmaysiz!")
            elif target_user.role == User.Role.SUPER_ADMIN and User.objects.filter(role=User.Role.SUPER_ADMIN).count() <= 1:
                messages.error(request, "Xatolik: Tizimdagi yagona Super Adminni o'chirib bo'lmaydi!")
            else:
                deleted_name = target_user.get_full_name() or target_user.username or "Foydalanuvchi"
                target_user.delete()
                messages.success(request, f"Foydalanuvchi '{deleted_name}' tizimdan butunlay o'chirildi.")

        elif action == 'toggle_active':
            user_id = request.POST.get('user_id')
            user = get_object_or_404(User, id=user_id)
            if user == request.user:
                messages.error(request, "O'zingizning hisobingizni bloklay olmaysiz!")
            else:
                user.is_active = not user.is_active
                user.save(update_fields=['is_active'])
                status_text = 'Faollashtirildi' if user.is_active else 'Bloklandi'
                messages.success(request, f"{user.get_full_name() or user.username or 'Foydalanuvchi'} hisobi {status_text}.")

        elif action == 'toggle_badge_printed':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=user_id)
            target_user.is_badge_printed = not target_user.is_badge_printed
            target_user.save(update_fields=['is_badge_printed'])
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.GET.get('format') == 'json':
                return JsonResponse({
                    'status': 'ok',
                    'user_id': target_user.id,
                    'is_badge_printed': target_user.is_badge_printed,
                    'badge_status_text': 'Berilgan' if target_user.is_badge_printed else 'Berilmagan'
                })
            messages.success(request, f"{target_user.get_full_name() or target_user.username} uchun birka holati '{'Berilgan' if target_user.is_badge_printed else 'Berilmagan'}' deb belgilandi.")
            return redirect(request.META.get('HTTP_REFERER') or 'superadmin_users')

        elif action == 'bulk_mark_badge_printed':
            user_ids_raw = request.POST.get('user_ids', '')
            val = request.POST.get('value', 'true').lower() == 'true'
            ids = [int(i.strip()) for i in user_ids_raw.split(',') if i.strip().isdigit()]
            if ids:
                updated_cnt = User.objects.filter(id__in=ids).update(is_badge_printed=val)
                state_lbl = "Berilgan" if val else "Berilmagan"
                messages.success(request, f"{updated_cnt} ta xodimning birkasi '{state_lbl}' deb belgilandi.")
            return redirect(request.META.get('HTTP_REFERER') or 'superadmin_users')

        elif action == 'generate_patok_users':
            try:
                count = max(1, min(100, int(request.POST.get('count', 40))))
            except (ValueError, TypeError):
                count = 40
            reset_passwords = request.POST.get('reset_passwords') == '1'
            created_c = 0
            updated_c = 0
            for i in range(1, count + 1):
                username = f"patok{i}"
                default_password = f"patok{i}"
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        'first_name': f"Patok {i}",
                        'last_name': "Kontrolchi",
                        'role': User.Role.CONTROL,
                        'is_active': True,
                        'is_staff': False,
                        'is_superuser': False,
                    }
                )
                if created:
                    user.set_password(default_password)
                    user.save()
                    created_c += 1
                else:
                    changed = False
                    if user.role != User.Role.CONTROL:
                        user.role = User.Role.CONTROL
                        changed = True
                    if reset_passwords:
                        user.set_password(default_password)
                        changed = True
                    if not user.is_active:
                        user.is_active = True
                        changed = True
                    if changed:
                        user.save()
                        updated_c += 1
            messages.success(
                request,
                f"1 dan {count} gacha bo'lgan Patok kontrolchi akkauntlari tayyor! "
                f"({created_c} ta yangi yaratildi, {updated_c} ta mavjudi yangilandi). "
                f"Login: patok1...patok{count}, Parol: patok1...patok{count}"
            )
            return redirect(reverse('superadmin_users') + '?role=CONTROL')

        return redirect('superadmin_users')

    search_q = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()
    badge_filter = request.GET.get('badge', '').strip()  # unprinted, printed, all

    users_qs = User.objects.all()
    if search_q:
        users_qs = users_qs.filter(
            Q(username__icontains=search_q) |
            Q(uid__icontains=search_q) |
            Q(first_name__icontains=search_q) |
            Q(last_name__icontains=search_q) |
            Q(phone_number__icontains=search_q) |
            Q(email__icontains=search_q)
        )
    if role_filter in User.Role.values:
        users_qs = users_qs.filter(role=role_filter)

    if badge_filter == 'unprinted':
        users_qs = users_qs.filter(is_badge_printed=False)
    elif badge_filter == 'printed':
        users_qs = users_qs.filter(is_badge_printed=True)

    users = users_qs.order_by('-date_joined')

    # Hisoblagichlar
    all_users_count = User.objects.count()
    regular_users_count = User.objects.filter(role=User.Role.USER).count()
    masters_count = User.objects.filter(role=User.Role.MASTER).count()
    controllers_count = User.objects.filter(role=User.Role.CONTROL).count()
    screens_count = User.objects.filter(role=User.Role.SCREEN).count()
    admins_count = User.objects.filter(role=User.Role.ADMIN).count()
    superadmins_count = User.objects.filter(role=User.Role.SUPER_ADMIN).count()
    unprinted_badges_count = User.objects.filter(is_badge_printed=False).count()
    printed_badges_count = User.objects.filter(is_badge_printed=True).count()

    return render(request, 'superadmin/users.html', {
        'users': users,
        'search_q': search_q,
        'role_filter': role_filter,
        'badge_filter': badge_filter,
        'all_users_count': all_users_count,
        'regular_users_count': regular_users_count,
        'masters_count': masters_count,
        'controllers_count': controllers_count,
        'screens_count': screens_count,
        'admins_count': admins_count,
        'superadmins_count': superadmins_count,
        'unprinted_badges_count': unprinted_badges_count,
        'printed_badges_count': printed_badges_count,
    })


@superadmin_required
def superadmin_user_badge(request, user_id):
    """
    Yagona foydalanuvchi uchun Universal Birka (ID Card / Beydjik) chop etish sahifasi.
    Ismi, familiyasi, 6 xonali unikal UID raqami va yuqori sifatli QR kod aks etadi.
    """
    user = get_object_or_404(User, id=user_id)
    
    # UID mavjudligini kafolatlash
    if not user.uid:
        user.uid = generate_unique_user_uid()
        user.save(update_fields=['uid'])

    # QR kod mavjudligini tekshirish
    needs_qr = False
    if not user.qr_code:
        needs_qr = True
    else:
        try:
            if not os.path.exists(user.qr_code.path):
                needs_qr = True
        except Exception:
            needs_qr = True

    if needs_qr:
        user.generate_qr_code()
        user.save(update_fields=['qr_code', 'uid'])

    return render(request, 'superadmin/user_badge_print.html', {
        'target_user': user,
    })


@superadmin_required
def superadmin_users_print_badges(request):
    """
    Barcha yoki filtrlangan foydalanuvchilarning Universal Birkalarini
    A4 qog'ozda (varaqqa 8 tadan) ommaviy chop etish sahifasi.
    """
    if request.method == 'POST':
        action = request.POST.get('action')
        is_ajax = (
            request.headers.get('x-requested-with') == 'XMLHttpRequest' or
            request.GET.get('format') == 'json'
        )

        if action in ['mark_printed', 'bulk_mark_badge_printed']:
            user_ids_raw = request.POST.get('user_ids', '')
            val = request.POST.get('value', 'true').lower() == 'true'
            ids = [int(i.strip()) for i in user_ids_raw.split(',') if i.strip().isdigit()]
            updated_cnt = 0
            if ids:
                updated_cnt = User.objects.filter(id__in=ids).update(is_badge_printed=val)
                state_lbl = "Berilgan" if val else "Berilmagan"
                messages.success(request, f"Jami {updated_cnt} ta xodimning birkasi '{state_lbl}' deb belgilandi!")

            if is_ajax:
                return JsonResponse({
                    'status': 'ok',
                    'updated_count': updated_cnt,
                    'is_badge_printed': val,
                    'message': f"Jami {updated_cnt} ta xodimning birkasi 'Berilgan' deb belgilandi!" if val else f"{updated_cnt} ta xodim birkasi 'Berilmagan' holatiga qaytarildi."
                })
            return redirect(request.get_full_path())

        elif action == 'toggle_badge_printed':
            user_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=user_id)
            target_user.is_badge_printed = not target_user.is_badge_printed
            target_user.save(update_fields=['is_badge_printed'])

            if is_ajax:
                return JsonResponse({
                    'status': 'ok',
                    'user_id': target_user.id,
                    'is_badge_printed': target_user.is_badge_printed,
                    'badge_status_text': 'Berilgan' if target_user.is_badge_printed else 'Berilmagan',
                    'message': f"{target_user.get_full_name() or target_user.username} uchun birka holati '{'Berilgan' if target_user.is_badge_printed else 'Berilmagan'}' deb belgilandi."
                })
            messages.success(request, f"{target_user.get_full_name() or target_user.username} uchun birka holati '{'Berilgan' if target_user.is_badge_printed else 'Berilmagan'}' deb belgilandi.")
            return redirect(request.get_full_path())

    search_q = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()
    ids_param = request.GET.get('ids', '').strip()
    batch = request.GET.get('batch', '').strip()
    badge_status = request.GET.get('badge_status', 'unprinted').strip()  # unprinted (default), printed, all

    users_qs = User.objects.all()
    if batch == 'new':
        # Faqat yangi qo'shilgan 20 ta ishchi (id >= 9)
        users_qs = users_qs.filter(id__gte=9, role=User.Role.USER)

    if badge_status == 'unprinted':
        users_qs = users_qs.filter(is_badge_printed=False)
    elif badge_status == 'printed':
        users_qs = users_qs.filter(is_badge_printed=True)

    if ids_param:
        try:
            ids_list = [int(i.strip()) for i in ids_param.split(',') if i.strip().isdigit()]
            if ids_list:
                users_qs = users_qs.filter(id__in=ids_list)
        except Exception:
            pass

    if search_q:
        users_qs = users_qs.filter(
            Q(username__icontains=search_q) |
            Q(uid__icontains=search_q) |
            Q(first_name__icontains=search_q) |
            Q(last_name__icontains=search_q) |
            Q(phone_number__icontains=search_q)
        )
    if role_filter in [User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MASTER, User.Role.CONTROL, User.Role.SCREEN, User.Role.USER]:
        users_qs = users_qs.filter(role=role_filter)

    users_list = list(users_qs.order_by('role', 'worker_profile__worker_id', 'id'))

    # Barcha foydalanuvchilarning UID va QR kodlarini tekshirish / to'ldirish
    for u in users_list:
        needs_save = False
        if not u.uid:
            u.uid = generate_unique_user_uid()
            needs_save = True
        if not u.qr_code:
            u.generate_qr_code()
            needs_save = True
        else:
            try:
                if not os.path.exists(u.qr_code.path):
                    u.generate_qr_code()
                    needs_save = True
            except Exception:
                u.generate_qr_code()
                needs_save = True
        if needs_save:
            u.save(update_fields=['uid', 'qr_code'])

    # A4 varaqlariga 8 tadan joylashtirish (2 ustun x 4 qator)
    CARDS_PER_A4 = 8
    pages_list = [users_list[i:i + CARDS_PER_A4] for i in range(0, len(users_list), CARDS_PER_A4)]

    base_counts_qs = User.objects.all()
    if role_filter in [User.Role.SUPER_ADMIN, User.Role.ADMIN, User.Role.MASTER, User.Role.CONTROL, User.Role.SCREEN, User.Role.USER]:
        base_counts_qs = base_counts_qs.filter(role=role_filter)
    unprinted_badges_count = base_counts_qs.filter(is_badge_printed=False).count()
    printed_badges_count = base_counts_qs.filter(is_badge_printed=True).count()
    total_all_badges_count = base_counts_qs.count()

    return render(request, 'superadmin/users_badges_batch_print.html', {
        'badge_users': users_list,
        'pages_list': pages_list,
        'search_q': search_q,
        'role_filter': role_filter,
        'badge_status': badge_status,
        'batch': batch,
        'total_badges': len(users_list),
        'total_pages': len(pages_list),
        'unprinted_badges_count': unprinted_badges_count,
        'printed_badges_count': printed_badges_count,
        'total_all_badges_count': total_all_badges_count,
        'user_ids_str': ','.join(str(u.id) for u in users_list),
    })


@superadmin_required
def superadmin_users_download_badges_pdf(request):
    """20 ta yangi ishchining tayyor A4 PDF faylini yuklab olish"""
    from django.conf import settings
    from django.http import FileResponse
    pdf_path = os.path.join(settings.BASE_DIR, 'A4_YANGI_ISHCHILAR_BIRKALARI.pdf')
    if not os.path.exists(pdf_path):
        import scratch_badges_pdf
        scratch_badges_pdf.create_a4_pdf()
    return FileResponse(open(pdf_path, 'rb'), as_attachment=True, filename='A4_YANGI_ISHCHILAR_BIRKALARI.pdf')



MONTHS_LIST = [
    (1, 'Yanvar'), (2, 'Fevral'), (3, 'Mart'), (4, 'Aprel'),
    (5, 'May'), (6, 'Iyun'), (7, 'Iyul'), (8, 'Avgust'),
    (9, 'Sentyabr'), (10, 'Oktyabr'), (11, 'Noyabr'), (12, 'Dekabr')
]


@payroll_admin_required
def superadmin_payroll(request):
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        selected_month = today.month

    # 7 kunlik taymerlarni avtomatik tekshirib muzlatish va tanlangan oy holatini olish
    from accounts.freeze_services import check_and_apply_freeze_timers, get_monthly_closing_status
    check_and_apply_freeze_timers()
    closing_info = get_monthly_closing_status(selected_year, selected_month)

    # Yillar ro'yxati (oxirgi 2 yil va kelgusi 1 yil)
    years_list = list(range(today.year - 2, today.year + 2))

    search_q = request.GET.get('q', '').strip()
    workers = Worker.objects.all().select_related('user').order_by('worker_id')

    # Filial (Branch) filtri: Filial admini faqat o'z filialini ko'radi, Superadmin barchasini
    if request.user.role == User.Role.BRANCH_ADMIN:
        user_branch = request.user.branch or User.Branch.UYCHI
        workers = workers.filter(branch=user_branch)
        selected_branch = user_branch
        is_branch_admin = True
    else:
        is_branch_admin = False
        branch_filter = request.GET.get('branch', 'ALL').strip().upper()
        if branch_filter and branch_filter in User.Branch.values:
            workers = workers.filter(branch=branch_filter)
            selected_branch = branch_filter
        else:
            selected_branch = 'ALL'

    if search_q:
        workers = workers.filter(
            Q(worker_id__icontains=search_q) |
            Q(first_name__icontains=search_q) |
            Q(last_name__icontains=search_q) |
            Q(user__uid__icontains=search_q) |
            Q(phone_number__icontains=search_q)
        )

    worker_ids = list(workers.values_list('id', flat=True))

    payroll_data = []
    grand_gross_month = Decimal('0.00')
    grand_advances_month = Decimal('0.00')
    grand_salary_month = Decimal('0.00')
    grand_net_month = Decimal('0.00')
    grand_units_month = 0
    grand_lifetime_balance = Decimal('0.00')
    active_workers_count = 0

    # 1. Tanlangan oy uchun xodimlarning biletlarini bitta so'rovda agregatsiya qilish (faqat tanlangan filial)
    month_ticket_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=selected_year,
        scanned_at__month=selected_month,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(
        units=Sum('quantity'),
        gross=Sum('total_amount'),
        days_worked=Count('scanned_at__date', distinct=True),
        total_tickets=Count('id'),
        total_boxes=Count('box_id', distinct=True),
    )
    month_ticket_map = {item['worker_id']: item for item in month_ticket_qs}

    # 2. Tanlangan oy uchun xodimlarning to'lovlarini bitta so'rovda agregatsiya qilish
    month_payout_qs = WorkerPayout.objects.filter(
        payout_date__year=selected_year,
        payout_date__month=selected_month,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY)),
        bonuses=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.BONUS)),
    )
    month_payout_map = {item['worker_id']: item for item in month_payout_qs}

    # 3. Barcha vaqt uchun doimiy balanslarni tezkor hisoblash
    lifetime_earnings_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(total=Sum('total_amount'))
    lifetime_earned_map = {item['worker_id']: (item['total'] or Decimal('0.00')) for item in lifetime_earnings_qs}

    lifetime_paid_qs = WorkerPayout.objects.filter(
        worker_id__in=worker_ids
    ).values('worker_id').annotate(total=Sum('amount'))
    lifetime_paid_map = {item['worker_id']: (item['total'] or Decimal('0.00')) for item in lifetime_paid_qs}

    for w in workers:
        t_stat = month_ticket_map.get(w.id, {})
        month_units = t_stat.get('units') or 0
        month_gross = t_stat.get('gross') or Decimal('0.00')
        days_worked = t_stat.get('days_worked') or 0
        total_tickets = t_stat.get('total_tickets') or 0
        total_boxes = t_stat.get('total_boxes') or 0

        p_stat = month_payout_map.get(w.id, {})
        advances = p_stat.get('advances') or Decimal('0.00')
        salaries_paid = p_stat.get('salaries') or Decimal('0.00')
        bonuses_paid = p_stat.get('bonuses') or Decimal('0.00')

        # Ushbu oy uchun to'lanishi kerak sof summa (Netto)
        net_payable_month = month_gross - advances - salaries_paid

        # Umumiy doimiy balans
        lifetime_earned = lifetime_earned_map.get(w.id, Decimal('0.00'))
        lifetime_paid = lifetime_paid_map.get(w.id, Decimal('0.00'))
        lifetime_balance = lifetime_earned - lifetime_paid
        grand_lifetime_balance += lifetime_balance

        if days_worked > 0 or month_gross > 0:
            active_workers_count += 1

        grand_gross_month += month_gross
        grand_advances_month += advances
        grand_salary_month += salaries_paid
        grand_net_month += max(Decimal('0.00'), net_payable_month)
        grand_units_month += month_units

        # Holatni belgilash
        if month_gross == Decimal('0.00') and advances == Decimal('0.00') and salaries_paid == Decimal('0.00'):
            status_text = "Ishlamagan"
            status_badge = "slate"
        elif net_payable_month <= Decimal('0.00'):
            status_text = "To'liq to'langan"
            status_badge = "emerald"
        elif advances > Decimal('0.00') or salaries_paid > Decimal('0.00'):
            status_text = "Qisman to'langan (Avans)"
            status_badge = "amber"
        else:
            status_text = "To'lov kutilmoqda"
            status_badge = "cyan"

        payroll_data.append({
            'worker': w,
            'days_worked': days_worked,
            'month_units': month_units,
            'month_gross': month_gross,
            'advances': advances,
            'salaries_paid': salaries_paid,
            'bonuses_paid': bonuses_paid,
            'net_payable_month': net_payable_month,
            'lifetime_balance': lifetime_balance,
            'total_tickets': total_tickets,
            'total_boxes': total_boxes,
            'status_text': status_text,
            'status_badge': status_badge,
        })

    # Oylik to'lovlar jurnali
    recent_payouts = WorkerPayout.objects.filter(
        payout_date__year=selected_year,
        payout_date__month=selected_month
    ).select_related('worker', 'created_by').order_by('-payout_date', '-created_at')[:30]

    # Bugungi sana yopilganmi?
    today_is_closed = DailyWorkerClosing.objects.filter(date=today).exists()

    selected_month_name = dict(MONTHS_LIST).get(selected_month, '')

    return render(request, 'superadmin/payroll.html', {
        'payroll_data': payroll_data,
        'selected_year': selected_year,
        'selected_month': selected_month,
        'selected_month_name': selected_month_name,
        'closing_info': closing_info,
        'years_list': years_list,
        'months_list': MONTHS_LIST,
        'grand_gross_month': grand_gross_month,
        'grand_advances_month': grand_advances_month,
        'grand_salary_month': grand_salary_month,
        'grand_net_month': grand_net_month,
        'grand_units_month': grand_units_month,
        'grand_lifetime_balance': grand_lifetime_balance,
        'active_workers_count': active_workers_count,
        'recent_payouts': recent_payouts,
        'workers': workers,
        'today_str': today.strftime("%Y-%m-%d"),
        'today_is_closed': today_is_closed,
        'search_q': search_q,
        'selected_branch': selected_branch,
        'is_branch_admin': is_branch_admin,
        'branches_list': User.Branch.choices,
    })


@payroll_admin_required
def superadmin_worker_daily_breakdown(request, worker_id):
    """
    Buxgalteriya uchun xodimning tanlangan oydagi har bir kunlik ishi va olgan avanslari tafsiloti (AJAX).
    """
    worker = get_object_or_404(Worker, id=worker_id)
    if request.user.role == User.Role.BRANCH_ADMIN and worker.branch != request.user.branch:
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat berilmagan!"}, status=403)

    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        year, month = today.year, today.month

    # Ushbu oyda skanerlangan biletlar
    tickets = worker.tickets.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=year,
        scanned_at__month=month
    ).select_related(
        'article_operation__operation',
        'box'
    ).order_by('scanned_at')

    # Barcha ish kunlari
    current_tz = timezone.get_current_timezone()
    ticket_dates = set(t.scanned_at.astimezone(current_tz).date() for t in tickets)

    payouts = worker.payouts.filter(
        payout_date__year=year,
        payout_date__month=month
    )
    payout_dates = set(p.payout_date for p in payouts)
    all_dates = sorted(list(ticket_dates | payout_dates))

    UZ_DAYS = {
        0: 'Dushanba',
        1: 'Seshanba',
        2: 'Chorshanba',
        3: 'Payshanba',
        4: 'Juma',
        5: 'Shanba',
        6: 'Yakshanba'
    }

    daily_rows = []
    total_units_sum = 0
    total_earned_sum = Decimal('0.00')
    total_advance_sum = Decimal('0.00')

    for d in all_dates:
        d_tickets = [t for t in tickets if t.scanned_at.astimezone(current_tz).date() == d]
        d_units = sum(t.quantity for t in d_tickets)
        d_earned = sum(t.total_amount for t in d_tickets)
        d_count = len(d_tickets)

        # Operatsiyalar xulosasi
        op_counts = {}
        for t in d_tickets:
            op_name = "Noma'lum"
            if t.article_operation and t.article_operation.operation:
                op_name = t.article_operation.operation.name
            op_counts[op_name] = op_counts.get(op_name, 0) + (t.quantity or 0)
        sorted_ops = sorted(op_counts.items(), key=lambda x: x[1], reverse=True)
        ops_summary = ", ".join(f"{name}: {qty} ta" for name, qty in sorted_ops)
        ops_list = [{'name': name, 'quantity': qty} for name, qty in sorted_ops]

        boxes_count = len(set(t.box_id for t in d_tickets if t.box_id))
        compact_stickers = compact_ticket_ids(d_tickets) if d_tickets else "—"
        ticket_ids = [t.stiker_id for t in d_tickets]

        d_payouts = [p for p in payouts if p.payout_date == d]
        d_advance = sum(p.amount for p in d_payouts if p.payout_type == WorkerPayout.PayoutType.ADVANCE)

        total_units_sum += d_units
        total_earned_sum += d_earned
        total_advance_sum += d_advance

        is_closed = DailyWorkerClosing.objects.filter(worker=worker, date=d).exists()

        daily_rows.append({
            'date_str': d.strftime("%d.%m.%Y"),
            'date_iso': d.strftime("%Y-%m-%d"),
            'day_name': UZ_DAYS.get(d.weekday(), ''),
            'units': d_units,
            'amount': float(d_earned),
            'amount_formatted': f"{int(d_earned):,} UZS".replace(",", " "),
            'ticket_count': d_count,
            'boxes_count': boxes_count,
            'operations_summary': ops_summary,
            'operations_list': ops_list,
            'compact_stickers': compact_stickers,
            'ticket_ids': ticket_ids,
            'advance_amount': float(d_advance),
            'advance_formatted': f"{int(d_advance):,} UZS".replace(",", " ") if d_advance > 0 else "—",
            'is_closed': is_closed,
        })

    net_sum = total_earned_sum - total_advance_sum

    return JsonResponse({
        'worker_id': worker.worker_id,
        'worker_pk': worker.id,
        'full_name': worker.full_name,
        'uid': worker.user.uid if worker.user else "—",
        'year': year,
        'month': month,
        'total_units': total_units_sum,
        'total_earned': float(total_earned_sum),
        'total_earned_formatted': f"{int(total_earned_sum):,} UZS".replace(",", " "),
        'total_advance': float(total_advance_sum),
        'total_advance_formatted': f"{int(total_advance_sum):,} UZS".replace(",", " "),
        'net_payable': float(net_sum),
        'net_payable_formatted': f"{int(net_sum):,} UZS".replace(",", " "),
        'days_count': len(all_dates),
        'rows': daily_rows
    })


@payroll_admin_required
def superadmin_close_daily_now(request):
    """
    Superadmin yoki Buxgalteriya panelidan turib kunlik hisobotni qo'lda yopish va balansga muhrlash.
    """
    if request.method == 'POST':
        date_str = request.POST.get('date')
        if date_str:
            try:
                target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                target_date = timezone.localdate()
        else:
            target_date = timezone.localdate()

        from accounts.management.commands.close_daily_payroll import perform_daily_closing
        res = perform_daily_closing(target_date)

        messages.success(
            request,
            f"✓ {target_date.strftime('%d.%m.%Y')} sanasi bo'yicha kunlik hisob muvaffaqiyatli yopildi: "
            f"{res['workers_count']} nafar xodim, {res['total_units']:,} dona, {int(res['total_amount']):,} UZS umumiy balansga qo'shildi!"
        )
    return redirect('superadmin_payroll')


@payroll_admin_required
def superadmin_payout_create(request):
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id')
        amount = request.POST.get('amount')
        payout_type = request.POST.get('payout_type', WorkerPayout.PayoutType.ADVANCE)
        payout_date = request.POST.get('payout_date') or timezone.localdate()
        note = request.POST.get('note', '').strip()
        month = request.POST.get('selected_month')
        year = request.POST.get('selected_year')

        if not worker_id or not amount:
            messages.error(request, "Xodim va to'lov summasi kiritilishi shart!")
        else:
            worker = get_object_or_404(Worker, id=worker_id)
            if request.user.role == User.Role.BRANCH_ADMIN and worker.branch != request.user.branch:
                messages.error(request, "Siz faqat o'z filialingizdagi xodimlarga to'lov qila olasiz!")
                return redirect('superadmin_payroll')

            with transaction.atomic():
                payout = WorkerPayout.objects.create(
                    worker=worker,
                    amount=Decimal(amount),
                    payout_type=payout_type,
                    payout_date=payout_date,
                    note=note,
                    created_by=request.user
                )

            messages.success(request, f"{worker.full_name} ga {int(payout.amount):,} UZS ({payout.get_payout_type_display()}) muvaffaqiyatli to'landi!")

        if month and year:
            return redirect(f"/superadmin/payroll/?year={year}&month={month}")
        return redirect('superadmin_payroll')

    return redirect('superadmin_payroll')


@payroll_admin_required
def superadmin_payroll_export_csv(request):
    """
    Buxgalteriya uchun tanlangan oy bo'yicha sdelshina ish haqi hisobotini Excel/CSV formatida yuklab olish.
    """
    today = timezone.localdate()
    try:
        selected_year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        selected_year = today.year
    try:
        selected_month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        selected_month = today.month

    selected_month_name = dict(MONTHS_LIST).get(selected_month, str(selected_month))

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    filename = f"buxgalteriya_oylik_tabel_{selected_year}_{selected_month:02d}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "№",
        "Xodim ID",
        "6-Xonali UID",
        "F.I.SH",
        "Filial",
        "Telefon",
        f"Ishlagan Kunlari ({selected_month_name})",
        f"Tikilgan Dona ({selected_month_name})",
        f"Hisoblangan Maosh Brutto ({selected_month_name}, UZS)",
        f"Berilgan Avanslar ({selected_month_name}, UZS)",
        f"To'langan Oylik ({selected_month_name}, UZS)",
        f"Qo'lga Tegishi Kerak Sof Oylik ({selected_month_name}, UZS)",
        "Umumiy Balans Qoldiq (UZS)",
        "Holati"
    ])

    workers_qs = Worker.objects.all().select_related('user').order_by('worker_id')
    if request.user.role == User.Role.BRANCH_ADMIN:
        user_branch = request.user.branch or User.Branch.UYCHI
        workers_qs = workers_qs.filter(branch=user_branch)
    else:
        branch_filter = request.GET.get('branch', 'ALL').strip().upper()
        if branch_filter and branch_filter in User.Branch.values:
            workers_qs = workers_qs.filter(branch=branch_filter)

    workers = list(workers_qs)
    worker_ids = [w.id for w in workers]

    # Batch SQL agregatsiya (faqat tanlangan filial xodimlari)
    month_ticket_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__year=selected_year,
        scanned_at__month=selected_month,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(
        units=Sum('quantity'),
        gross=Sum('total_amount'),
        days_worked=Count('scanned_at__date', distinct=True)
    )
    month_ticket_map = {item['worker_id']: item for item in month_ticket_qs}

    month_payout_qs = WorkerPayout.objects.filter(
        payout_date__year=selected_year,
        payout_date__month=selected_month,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY)),
    )
    month_payout_map = {item['worker_id']: item for item in month_payout_qs}

    lifetime_earnings_qs = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        worker_id__in=worker_ids
    ).values('worker_id').annotate(total=Sum('total_amount'))
    lifetime_earned_map = {item['worker_id']: (item['total'] or Decimal('0.00')) for item in lifetime_earnings_qs}

    lifetime_paid_qs = WorkerPayout.objects.filter(
        worker_id__in=worker_ids
    ).values('worker_id').annotate(total=Sum('amount'))
    lifetime_paid_map = {item['worker_id']: (item['total'] or Decimal('0.00')) for item in lifetime_paid_qs}

    for idx, w in enumerate(workers, start=1):
        t_stat = month_ticket_map.get(w.id, {})
        month_units = t_stat.get('units') or 0
        month_gross = t_stat.get('gross') or Decimal('0.00')
        days_worked = t_stat.get('days_worked') or 0

        p_stat = month_payout_map.get(w.id, {})
        advances = p_stat.get('advances') or Decimal('0.00')
        salaries = p_stat.get('salaries') or Decimal('0.00')

        net_payable = month_gross - advances - salaries

        lifetime_balance = lifetime_earned_map.get(w.id, Decimal('0.00')) - lifetime_paid_map.get(w.id, Decimal('0.00'))

        if month_gross == Decimal('0.00') and advances == Decimal('0.00') and salaries == Decimal('0.00'):
            status_text = "Ishlamagan"
        elif net_payable <= Decimal('0.00'):
            status_text = "To'liq to'langan"
        elif advances > Decimal('0.00'):
            status_text = "Avans berilgan (Qisman)"
        else:
            status_text = "To'lov kutilmoqda"

        uid_val = w.user.uid if w.user else ""

        writer.writerow([
            idx,
            w.worker_id,
            uid_val,
            w.full_name,
            w.get_branch_display(),
            w.phone_number or "—",
            days_worked,
            month_units,
            int(month_gross),
            int(advances),
            int(salaries),
            int(net_payable),
            int(w.balance),
            status_text
        ])

    return response


@payroll_admin_required
def superadmin_send_telegram_report(request):
    """
    Superadmin panelidan turib xodimlar va stikerlar bo'yicha joriy oy boshidan hozirgacha
    bo'lgan to'liq oylik Excel hisoboti va To'liq baza zaxira nusxasini (DB Backup)
    Telegram guruhga yuborish.
    Gunicorn worker va veb sahifa qotib qolmasligi uchun jarayon orqa fonda (background thread) bajariladi.
    """
    import threading
    import logging
    from django.db import connection

    chat_id = (request.POST.get('chat_id') or request.GET.get('chat_id') or '').strip() or None
    bot_token = (request.POST.get('bot_token') or request.GET.get('bot_token') or '').strip() or None

    def _send_report_worker(c_id, b_token):
        logger = logging.getLogger('production.telegram_reports')
        try:
            from production.telegram_reports import send_month_to_date_telegram_report
            logger.info("Fonda oylik hisobot va DB backup yuborish boshlandi...")
            res = send_month_to_date_telegram_report(chat_id=c_id, bot_token=b_token)
            logger.info(f"Fonda hisobot natijasi: {res}")
        except Exception as exc:
            logger.error(f"Fonda hisobot yuborishda xatolik: {exc}", exc_info=True)
        finally:
            connection.close()

    bg_thread = threading.Thread(target=_send_report_worker, args=(chat_id, bot_token), daemon=True)
    bg_thread.start()

    messages.success(
        request,
        "🚀 Oylik hisobot (Excel) va to'liq baza zaxira nusxasi (Backup) orqa fonda Telegram guruhga jo'natilmoqda. "
        "Fayllar 10-30 soniya ichida Telegramga yetib boradi."
    )

    referer = request.META.get('HTTP_REFERER')
    if referer and 'telegram-report' not in referer:
        return redirect(referer)
    return redirect('superadmin_payroll')


@payroll_admin_required
def superadmin_download_daily_excel(request):
    """
    Superadmin uchun kunlik xodimlar va stikerlar hisobotini brauzerda to'g'ridan-to'g'ri Excel (.xlsx) sifatida yuklab olish.
    """
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    try:
        from production.excel_reports import generate_daily_excel_report
        excel_buffer = generate_daily_excel_report(target_date=target_date)
    except Exception as e:
        messages.error(request, f"Excel fayl yaratishda xatolik: {str(e)}")
        return redirect('superadmin_payroll')

    filename = f"Kunlik_Hisobot_{target_date.strftime('%Y_%m_%d')}.xlsx"
    response = HttpResponse(
        excel_buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


CYRILLIC_TO_LATIN = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h'
}


def get_or_create_operation_safely(name: str, code: str = None, difficulty: float = 1.0, description: str = "", order_number: int = None):
    """
    Yangi operatsiyani xavfsiz yaratish yoki mavjudini qaytarish:
    - Nomi bo'yicha takrorlanishdan saqlaydi.
    - Kod operatsiya nomi bilan bir xil va KATTA HARFLARDA bo'ladi (foydalanuvchi talabi).
    - Kod bazada mavjud bo'lsa, unga avtomatik 1, 2 sufiks qo'shadi.
    """
    name = (name or '').strip()
    if not name:
        return None

    existing = Operation.objects.filter(name__iexact=name).first()
    if existing:
        return existing

    raw_code = (code or '').strip()
    if raw_code:
        op_code = raw_code.upper()[:50]
    else:
        op_code = name.upper()[:50]

    if not op_code:
        op_code = "OP"

    final_code = op_code
    counter = 1
    while Operation.objects.filter(code=final_code).exists():
        suffix = f" {counter}"
        final_code = f"{op_code[:50-len(suffix)]}{suffix}"
        counter += 1

    if not order_number:
        max_order = Operation.objects.aggregate(m=Max('order_number'))['m'] or 0
        order_number = max_order + 1

    try:
        diff_val = float(difficulty) if difficulty else 1.0
    except (ValueError, TypeError):
        diff_val = 1.0

    return Operation.objects.create(
        code=final_code,
        name=name,
        description=description or "",
        default_difficulty=diff_val,
        order_number=order_number
    )


@superadmin_required
def superadmin_pricing(request):
    """
    Operatsiyalar Guruhi (Shablonlar) va Narxlar Matritsasi:
    - Guruhlar (shablonlar) ro'yxati (masalan: DC UZUN QOL KOYLAKCHA).
    - Har bir guruh ichidagi operatsiyalar, ularning tartibi, qiyinligi va narxi.
    - Guruhda narx o'zgarsa, unga ulangan barcha artikullar va barcha biletlar avtomatik yangilanadi.
    - Yangi guruh yaratish (katalogdagi barcha operatsiyalar checkbox bilan chiqadi).
    """
    if request.method == 'POST':
        action = request.POST.get('action')

        # 0. TEZKOR AJAX ORQALI YANGI OPERATSIYA YARATISH (Modal ichida sahifani yangilamasdan)
        if action == 'quick_create_operation':
            op_name = request.POST.get('name', '').strip()
            op_code = request.POST.get('code', '').strip()
            diff_str = request.POST.get('difficulty', '1.0').strip()
            if not op_name:
                return JsonResponse({'success': False, 'error': "Operatsiya nomini kiritish shart!"}, status=400)
            try:
                diff_val = float(diff_str) if diff_str else 1.0
            except Exception:
                diff_val = 1.0
            op = get_or_create_operation_safely(op_name, code=op_code, difficulty=diff_val)
            return JsonResponse({
                'success': True,
                'operation': {
                    'id': op.id,
                    'code': op.code,
                    'name': op.name,
                    'default_difficulty': op.default_difficulty_display,
                    'order_number': op.order_number,
                }
            })

        # 0.1 KATALOGGA MUSTAQIL YANGI OPERATSIYA QO'SHISH
        elif action == 'create_catalog_operation':
            op_name = request.POST.get('name', '').strip()
            op_code = request.POST.get('code', '').strip()
            diff_str = request.POST.get('difficulty', '1.0').strip()
            desc = request.POST.get('description', '').strip()
            ord_str = request.POST.get('order_number', '').strip()
            if not op_name:
                messages.error(request, "Operatsiya nomini kiritish shart!")
                return redirect('superadmin_pricing')

            try:
                ord_val = int(ord_str) if ord_str else None
            except Exception:
                ord_val = None
            try:
                diff_val = float(diff_str) if diff_str else 1.0
            except Exception:
                diff_val = 1.0

            op = get_or_create_operation_safely(
                op_name,
                code=op_code,
                difficulty=diff_val,
                description=desc,
                order_number=ord_val
            )
            messages.success(request, f"'{op.name}' ({op.code}) operatsiyasi katalogga muvaffaqiyatli qo'shildi!")
            return redirect('superadmin_pricing')

        # 0.2 KATALOGDAN OPERATSIYANI O'CHIRISH
        elif action == 'delete_catalog_operation':
            op_id = request.POST.get('operation_id')
            op = get_object_or_404(Operation, id=op_id)
            op_name = op.name
            is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.POST.get('ajax') == '1'

            # Biletlar mavjudligini tekshirish (Ticket -> ArticleOperation on_delete=PROTECT)
            tickets_count = Ticket.objects.filter(article_operation__operation=op).count()
            if tickets_count > 0:
                err_msg = f"'{op_name}' operatsiyasini o'chirib bo'lmaydi! Ushbu operatsiya bo'yicha ishlab chiqarishda {tickets_count} ta ishchi bileti mavjud."
                if is_ajax:
                    return JsonResponse({'success': False, 'error': err_msg}, status=400)
                messages.error(request, err_msg)
                return redirect('superadmin_pricing')

            try:
                op.delete()
                suc_msg = f"'{op_name}' operatsiyasi katalogdan muvaffaqiyatli o'chirildi."
                if is_ajax:
                    return JsonResponse({'success': True, 'message': suc_msg, 'operation_id': op_id})
                messages.success(request, suc_msg)
            except ProtectedError:
                err_msg = f"'{op_name}' operatsiyasini o'chirib bo'lmaydi, chunki unga bog'langan ma'lumotlar mavjud."
                if is_ajax:
                    return JsonResponse({'success': False, 'error': err_msg}, status=400)
                messages.error(request, err_msg)
            except Exception as e:
                err_msg = f"Xatolik yuz berdi: {str(e)}"
                if is_ajax:
                    return JsonResponse({'success': False, 'error': err_msg}, status=400)
                messages.error(request, err_msg)

            return redirect('superadmin_pricing')

        # 1. YANGI GURUH (SHABLON) YARATISH
        elif action == 'create_group':
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            selected_ops = request.POST.getlist('selected_operations')

            if not name:
                messages.error(request, "Guruh nomini kiritish shart!")
                return redirect('superadmin_pricing')

            if OperationGroup.objects.filter(name__iexact=name).exists():
                messages.error(request, f"'{name}' nomli guruh allaqachon mavjud!")
                return redirect('superadmin_pricing')

            group = OperationGroup.objects.create(name=name, description=description)

            added_count = 0
            # Mavjud katalog operatsiyalaridan tanlanganlar
            for idx, op_id in enumerate(selected_ops, start=1):
                try:
                    op = Operation.objects.get(id=op_id)
                    seq_raw = request.POST.get(f'sequence_{op_id}', idx)
                    try:
                        seq = int(seq_raw)
                    except Exception:
                        seq = idx

                    price_raw = request.POST.get(f'price_{op_id}', '0').strip().replace(' ', '').replace(',', '.')
                    try:
                        price = Decimal(price_raw or '0')
                    except Exception:
                        price = Decimal('0.00')

                    diff_raw = request.POST.get(f'difficulty_{op_id}', '1.0').strip().replace(',', '.')
                    try:
                        diff = float(diff_raw or 1.0)
                    except Exception:
                        diff = 1.0

                    OperationGroupItem.objects.update_or_create(
                        group=group,
                        operation=op,
                        defaults={
                            'sequence': seq,
                            'price_per_unit': price,
                            'difficulty': diff,
                        }
                    )
                    added_count += 1
                except Exception:
                    pass

            # Shu paytning o'zida kiritilgan yangi maxsus operatsiyalar (inline formadan)
            inline_name = request.POST.get('inline_new_op_name', '').strip()
            if inline_name:
                inline_code = request.POST.get('inline_new_op_code', '').strip()
                inline_diff_raw = request.POST.get('inline_new_op_diff', '1.0').strip().replace(',', '.')
                inline_price_raw = request.POST.get('inline_new_op_price', '0').strip().replace(' ', '').replace(',', '.')
                try:
                    i_diff = float(inline_diff_raw or 1.0)
                except Exception:
                    i_diff = 1.0
                try:
                    i_price = Decimal(inline_price_raw or '0')
                except Exception:
                    i_price = Decimal('0.00')

                inline_op = get_or_create_operation_safely(inline_name, code=inline_code, difficulty=i_diff)
                if inline_op:
                    next_s = group.items.count() + 1
                    OperationGroupItem.objects.update_or_create(
                        group=group,
                        operation=inline_op,
                        defaults={
                            'sequence': next_s,
                            'price_per_unit': i_price,
                            'difficulty': i_diff,
                        }
                    )
                    added_count += 1

            # Massiv sifatida yuborilgan new_op_name[] bo'lsa
            new_op_names = request.POST.getlist('new_op_name[]')
            new_op_codes = request.POST.getlist('new_op_code[]')
            new_op_prices = request.POST.getlist('new_op_price[]')
            new_op_diffs = request.POST.getlist('new_op_difficulty[]')
            new_op_seqs = request.POST.getlist('new_op_sequence[]')

            for idx, n_name in enumerate(new_op_names):
                n_name = n_name.strip()
                if not n_name:
                    continue
                n_code = new_op_codes[idx].strip() if idx < len(new_op_codes) else ''
                n_price_str = (new_op_prices[idx].strip().replace(' ', '').replace(',', '.')) if idx < len(new_op_prices) else '0'
                n_diff_str = (new_op_diffs[idx].strip().replace(',', '.')) if idx < len(new_op_diffs) else '1.0'
                n_seq_str = new_op_seqs[idx].strip() if idx < len(new_op_seqs) else str(group.items.count() + 1)

                try:
                    d_val = float(n_diff_str or 1.0)
                except Exception:
                    d_val = 1.0

                new_op_obj = get_or_create_operation_safely(n_name, code=n_code, difficulty=d_val)
                if new_op_obj:
                    try:
                        p_val = Decimal(n_price_str or '0')
                    except Exception:
                        p_val = Decimal('0.00')
                    try:
                        s_val = int(n_seq_str)
                    except Exception:
                        s_val = group.items.count() + 1

                    OperationGroupItem.objects.update_or_create(
                        group=group,
                        operation=new_op_obj,
                        defaults={
                            'price_per_unit': p_val,
                            'sequence': s_val,
                            'difficulty': d_val,
                        }
                    )
                    added_count += 1

            messages.success(request, f"'{group.name}' operatsiyalar guruhi muvaffaqiyatli yaratildi ({added_count} ta operatsiya bilan).")
            return redirect('superadmin_pricing')

        # 1.1 GURUH NOMINI VA TAVSIFINI TAHRIRLASH
        elif action == 'update_group':
            group_id = request.POST.get('group_id')
            group = get_object_or_404(OperationGroup, id=group_id)
            new_name = request.POST.get('name', '').strip()
            new_desc = request.POST.get('description', '').strip()

            if not new_name:
                messages.error(request, "Guruh nomini kiritish shart!")
                return redirect('superadmin_pricing')

            # Nom takrorlanmasligi kerak (boshqa guruhlarda)
            if OperationGroup.objects.filter(name__iexact=new_name).exclude(id=group.id).exists():
                messages.error(request, f"'{new_name}' nomli boshqa guruh allaqachon mavjud!")
                return redirect('superadmin_pricing')

            old_name = group.name
            group.name = new_name
            group.description = new_desc
            group.save(update_fields=['name', 'description'])

            messages.success(request, f"Guruh nomi muvaffaqiyatli yangilandi: '{old_name}' ➔ '{group.name}'")
            return redirect('superadmin_pricing')

        # 2. GURUH OPERATSIYASI NARXINI / TARTIBINI O'ZGARTIRISH
        elif action == 'update_group_item':
            item_id = request.POST.get('item_id')
            item = get_object_or_404(OperationGroupItem, id=item_id)
            price = request.POST.get('price_per_unit')
            seq = request.POST.get('sequence')
            diff = request.POST.get('difficulty')

            if price is not None:
                try:
                    item.price_per_unit = Decimal(price.strip().replace(' ', '').replace(',', '.') or '0')
                except Exception:
                    pass
            if seq:
                try:
                    item.sequence = int(seq)
                except Exception:
                    pass
            if diff:
                try:
                    item.difficulty = float(diff.strip().replace(',', '.'))
                except Exception:
                    pass

            item.save()  # Bu avtomatik ravishda barcha bog'liq artikullar va biletlarni yangilaydi
            messages.success(
                request, 
                f"'{item.group.name}' guruhi -> '{item.operation.name}' narxi {item.price_per_unit:,.0f} UZS ga o'zgartirildi va barcha joyda yangilandi!"
            )
            return redirect('superadmin_pricing')

        # 3. MAVJUD GURUHGA OPERATSIYA QO'SHISH (Mavjudini tanlash yoki yangisini yaratish)
        elif action == 'add_op_to_group':
            group_id = request.POST.get('group_id')
            group = get_object_or_404(OperationGroup, id=group_id)

            price_raw = request.POST.get('price_per_unit', '0').strip().replace(' ', '').replace(',', '.')
            try:
                price = Decimal(price_raw or '0')
            except Exception:
                price = Decimal('0.00')

            try:
                seq = int(request.POST.get('sequence', group.items.count() + 1))
            except Exception:
                seq = group.items.count() + 1

            diff_raw = request.POST.get('difficulty', '1.0').strip().replace(',', '.')
            try:
                diff = float(diff_raw or 1.0)
            except Exception:
                diff = 1.0

            new_op_name = request.POST.get('new_op_name', '').strip()
            if new_op_name:
                new_op_code = request.POST.get('new_op_code', '').strip()
                op = get_or_create_operation_safely(new_op_name, code=new_op_code, difficulty=diff)
            else:
                op_id = request.POST.get('operation_id')
                op = get_object_or_404(Operation, id=op_id)

            item, created = OperationGroupItem.objects.update_or_create(
                group=group,
                operation=op,
                defaults={
                    'price_per_unit': price,
                    'sequence': seq,
                    'difficulty': diff,
                }
            )
            if created:
                messages.success(request, f"'{op.name}' operatsiyasi '{group.name}' guruhiga qo'shildi ({price:,.0f} UZS).")
            else:
                messages.info(request, f"'{op.name}' operatsiyasi '{group.name}' guruhida mavjud edi — uning narxi {price:,.0f} UZS va tartibi yangilandi.")
            return redirect('superadmin_pricing')

        # 4. GURUHDAN OPERATSIYANI O'CHIRISH
        elif action == 'delete_group_item':
            item_id = request.POST.get('item_id')
            item = get_object_or_404(OperationGroupItem, id=item_id)
            group_name = item.group.name
            op_name = item.operation.name
            item.delete()
            messages.success(request, f"'{op_name}' operatsiyasi '{group_name}' guruhidan olib tashlandi.")
            return redirect('superadmin_pricing')

        # 5. GURUHNI TO'LIQ O'CHIRISH
        elif action == 'delete_group':
            group_id = request.POST.get('group_id')
            group = get_object_or_404(OperationGroup, id=group_id)
            group_name = group.name
            group.delete()
            messages.success(request, f"'{group_name}' guruhi o'chirildi.")
            return redirect('superadmin_pricing')

        # 6. ARTIKULGA GURUHNI BIRIKTIRISH
        elif action == 'assign_group_to_article':
            article_id = request.POST.get('article_id')
            group_id = request.POST.get('group_id')
            article = get_object_or_404(Article, id=article_id)

            if group_id:
                group = get_object_or_404(OperationGroup, id=group_id)
                article.operation_group = group
                article.save()
                messages.success(request, f"'{article.code}' artikuliga '{group.name}' guruhi biriktirildi va operatsiyalar sinxronlandi.")
            else:
                article.operation_group = None
                article.save()
                messages.success(request, f"'{article.code}' artikulidan guruh biriktiruvi olib tashlandi.")
            return redirect('superadmin_pricing')

        # 7. ESKI MOSLIK: ARTIKUL NORMASINI YANGILASH
        elif action == 'update_article_norm' or ('daily_norm' in request.POST and not request.POST.get('article_operation_id')):
            article_id = request.POST.get('article_id')
            article = get_object_or_404(Article, id=article_id)
            norm_val = request.POST.get('daily_norm')
            if norm_val:
                try:
                    article.daily_norm = max(1, int(norm_val))
                    article.save(update_fields=['daily_norm'])
                    messages.success(request, f"'{article.code}' uchun kunlik norma {article.daily_norm} ball qilib belgilandi.")
                except (ValueError, TypeError):
                    messages.error(request, "Norma butun musbat son bo'lishi kerak.")
            return redirect('superadmin_pricing')

        # 8. ESKI MOSLIK: BEVOSITA ARTIKUL OPERATSIYASI NARXINI YANGILASH
        elif request.POST.get('article_operation_id'):
            art_op_id = request.POST.get('article_operation_id')
            art_op = get_object_or_404(ArticleOperation, id=art_op_id)
            price = request.POST.get('price_per_unit')
            seq = request.POST.get('sequence')
            diff = request.POST.get('difficulty')
            if price:
                art_op.price_per_unit = Decimal(price)
            if seq:
                art_op.sequence = int(seq)
            if diff:
                try:
                    art_op.difficulty = float(diff)
                except (ValueError, TypeError):
                    pass
            art_op.save()
            messages.success(request, f"{art_op.article.code} -> '{art_op.operation.name}' yangilandi.")
            return redirect('superadmin_pricing')

    groups = OperationGroup.objects.prefetch_related('items__operation', 'articles').order_by('name')
    catalog_operations = Operation.objects.prefetch_related('group_items__group').order_by('order_number', 'code')
    articles = Article.objects.all().select_related('operation_group').prefetch_related('article_operations__operation').order_by('code')
    next_order_number = (Operation.objects.aggregate(m=Max('order_number'))['m'] or 0) + 1

    return render(request, 'superadmin/pricing.html', {
        'groups': groups,
        'catalog_operations': catalog_operations,
        'articles': articles,
        'next_order_number': next_order_number,
    })


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
        customer_id = request.POST.get('customer_id')
        deadline = request.POST.get('deadline') or None

        customer = None
        if customer_id:
            customer = Customer.objects.filter(id=customer_id).first()
        if not customer and client_name:
            customer, _ = Customer.objects.get_or_create(name=client_name)

        product_model_id = request.POST.get('product_model_id')
        product_model = None
        if product_model_id:
            product_model = ProductModel.objects.filter(id=product_model_id).first()

        model_name = request.POST.get('model_name', '').strip()
        model_code = request.POST.get('model_code', '').strip().upper()
        quantity_str = request.POST.get('quantity', '0').strip()
        norm_str = request.POST.get('norm', '').strip()
        try:
            norm = max(1, int(norm_str)) if norm_str else (product_model.daily_norm if product_model else 1000)
        except Exception:
            norm = product_model.daily_norm if product_model else 1000

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
                        defaults={'name': model_name, 'daily_norm': norm}
                    )
                    if product_model and article.model != product_model:
                        article.model = product_model
                        article.save()

                    order = Order.objects.create(
                        order_number=order_number,
                        customer=customer,
                        client_name=customer.name if customer else client_name,
                        deadline=deadline,
                        article=article,
                        total_quantity=quantity,
                        status=Order.Status.IN_PROGRESS
                    )
                    OrderItem.objects.create(
                        order=order,
                        article=article,
                        quantity=quantity,
                        norm=norm
                    )
                    seq_counter = 1
                    for op_id in checked_operations:
                        price_val = request.POST.get(f"price_{op_id}", "0").strip()
                        diff_val = request.POST.get(f"diff_{op_id}", "1").strip()
                        try:
                            price = Decimal(price_val)
                        except Exception:
                            price = Decimal("0.00")
                        try:
                            diff = float(diff_val) if diff_val else 1.0
                        except Exception:
                            diff = 1.0
                        
                        op_obj = Operation.objects.filter(id=op_id).first()
                        if op_obj:
                            ArticleOperation.objects.update_or_create(
                                article=article,
                                operation=op_obj,
                                defaults={'price_per_unit': price, 'sequence': seq_counter, 'difficulty': diff}
                            )
                            seq_counter += 1

                    messages.success(request, f"'{order.order_number}' zakazi va '{article.name}' modeli muvaffaqiyatli yaratildi!")
                    return redirect('superadmin_order_detail', order_id=order.id)
            except Exception as e:
                messages.error(request, f"Xatolik yuz berdi: {str(e)}")

    search_q = request.GET.get('q', '').strip()
    orders = Order.objects.annotate(
        annotated_completed_tickets=Count('boxes__tickets', filter=Q(boxes__tickets__status=Ticket.Status.SCANNED), distinct=True),
        annotated_total_tickets=Count('boxes__tickets', distinct=True),
        annotated_total_boxes_qty=Sum('boxes__quantity', distinct=True)
    ).select_related('customer', 'article', 'article__model').prefetch_related('items__article__article_operations__operation', 'boxes').order_by('-created_at')
    if search_q:
        orders = orders.filter(
            Q(order_number__icontains=search_q) |
            Q(customer__name__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(article__name__icontains=search_q) |
            Q(article__code__icontains=search_q) |
            Q(boxes__box_code__icontains=search_q)
        ).distinct()

    all_operations = Operation.objects.all().order_by('code')
    all_articles = Article.objects.all().select_related('model').prefetch_related('article_operations__operation').order_by('code')
    all_customers = Customer.objects.all().order_by('name')
    all_product_models = ProductModel.objects.all().prefetch_related('model_operations__operation').order_by('code')

    # Serialize articles with operations for client-side auto-fill
    articles_data = []
    for art in all_articles:
        ops = []
        for ao in art.article_operations.all():
            ops.append({
                'op_id': ao.operation.id,
                'code': ao.operation.code,
                'name': ao.operation.name,
                'price': float(ao.price_per_unit),
                'sequence': ao.sequence,
                'difficulty': ao.difficulty_display,
            })
        articles_data.append({
            'id': art.id,
            'code': art.code,
            'name': art.name,
            'model_id': art.model_id or '',
            'norm': art.daily_norm or (art.model.daily_norm if art.model else 1000),
            'description': art.description or '',
            'operations': ops
        })

    return render(request, 'superadmin/orders_list.html', {
        'orders': orders,
        'all_operations': all_operations,
        'all_articles': all_articles,
        'all_customers': all_customers,
        'all_product_models': all_product_models,
        'articles_json': json.dumps(articles_data),
        'search_q': search_q,
    })


@superadmin_required
def superadmin_order_detail(request, order_id: int):
    """
    Zakaz ustiga bosganda:
    - Zakazning umumiy KPI ko'rsatkichlari (Jami dona, jami fond, o'rtacha dona narxi, modellar soni).
    - Ichidagi modellar kartochkalari (reja soni, 1 dona narxi, jami fondi, foiz ulushi).
    - Model bosilganda operatsiyalar va narxlari to'liq hisoblangan matritsasi.
    """
    order = get_object_or_404(Order.objects.prefetch_related(
        'items__article__article_operations__operation',
        'boxes__tickets'
    ), id=order_id)

    items = list(order.items.all().select_related('article').prefetch_related('article__article_operations__operation'))
    for item in items:
        item.operations_list = item.get_operations_with_totals()

    all_operations = Operation.objects.all().order_by('code')
    all_articles = Article.objects.all().prefetch_related('article_operations__operation').order_by('code')

    articles_data = []
    for art in all_articles:
        ops = []
        for ao in art.article_operations.all():
            ops.append({
                'op_id': ao.operation.id,
                'code': ao.operation.code,
                'name': ao.operation.name,
                'price': float(ao.price_per_unit),
                'sequence': ao.sequence,
                'difficulty': ao.difficulty_display,
            })
        articles_data.append({
            'id': art.id,
            'code': art.code,
            'name': art.name,
            'description': art.description or '',
            'operations': ops
        })

    return render(request, 'superadmin/order_detail.html', {
        'order': order,
        'items': items,
        'all_operations': all_operations,
        'all_articles': all_articles,
        'articles_json': json.dumps(articles_data),
    })


@superadmin_required
def superadmin_order_edit(request, order_id: int):
    """Zakaz asosiy ma'lumotlarini (mijoz, muddat, holat) tahrirlash"""
    order = get_object_or_404(Order, id=order_id)
    if request.method == 'POST':
        client_name = request.POST.get('client_name', '').strip()
        customer_id = request.POST.get('customer_id')
        deadline = request.POST.get('deadline') or None
        status = request.POST.get('status', order.status)
        
        if customer_id:
            order.customer = Customer.objects.filter(id=customer_id).first()
            if order.customer:
                order.client_name = order.customer.name
        elif client_name:
            order.client_name = client_name
            cust, _ = Customer.objects.get_or_create(name=client_name)
            order.customer = cust

        order.deadline = deadline
        if status in dict(Order.Status.choices):
            order.status = status
        order.save()
        messages.success(request, f"'{order.order_number}' zakazi ma'lumotlari yangilandi.")
    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_add_model(request, order_id: int):
    """
    Mavjud Zakaz ichiga yana qo'shimcha Model qo'shish.
    """
    order = get_object_or_404(Order, id=order_id)
    if request.method == 'POST':
        product_model_id = request.POST.get('product_model_id')
        product_model = None
        if product_model_id:
            product_model = ProductModel.objects.filter(id=product_model_id).first()

        model_name = request.POST.get('model_name', '').strip()
        model_code = request.POST.get('model_code', '').strip().upper()
        quantity_str = request.POST.get('quantity', '0').strip()
        norm_str = request.POST.get('norm', '').strip()
        try:
            norm = max(1, int(norm_str)) if norm_str else (product_model.daily_norm if product_model else 1000)
        except Exception:
            norm = product_model.daily_norm if product_model else 1000
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
                        defaults={'quantity': quantity, 'norm': norm}
                    )
                    seq = 1
                    for op_id in checked_operations:
                        price_val = request.POST.get(f"price_{op_id}", "0").strip()
                        diff_val = request.POST.get(f"diff_{op_id}", "1").strip()
                        try:
                            price = Decimal(price_val)
                        except Exception:
                            price = Decimal("0.00")
                        try:
                            diff = float(diff_val) if diff_val else 1.0
                        except Exception:
                            diff = 1.0
                        op_obj = Operation.objects.filter(id=op_id).first()
                        if op_obj:
                            ArticleOperation.objects.update_or_create(
                                article=article,
                                operation=op_obj,
                                defaults={'price_per_unit': price, 'sequence': seq, 'difficulty': diff}
                            )
                            seq += 1

                    messages.success(request, f"Zakazga yangi '{article.name}' ({quantity} dona, norma: {norm} ball) modeli qo'shildi!")
            except Exception as e:
                messages.error(request, f"Xatolik: {str(e)}")

    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_model_edit(request, order_id: int, item_id: int):
    """Model soni (quantity), normasi va nomini tahrirlash"""
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(OrderItem, id=item_id, order=order)
    if request.method == 'POST':
        model_name = request.POST.get('model_name', '').strip()
        quantity_str = request.POST.get('quantity', '').strip()
        norm_str = request.POST.get('norm', '').strip()
        try:
            quantity = int(quantity_str)
            if quantity <= 0:
                raise ValueError("Soni musbat son bo'lishi kerak")
            item.quantity = quantity
            if norm_str:
                try:
                    item.norm = max(1, int(norm_str))
                except Exception:
                    pass
            item.save()
            if model_name:
                item.article.name = model_name
                item.article.save()
            messages.success(request, f"'{item.article.code}' modeli muvaffaqiyatli yangilandi ({item.quantity} dona, norma: {item.norm} ball).")
        except Exception as e:
            messages.error(request, f"Xatolik: {str(e)}")
    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_model_delete(request, order_id: int, item_id: int):
    """Modelni zakazdan to'liq o'chirish"""
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(OrderItem, id=item_id, order=order)
    if request.method == 'POST':
        art_code = item.article.code
        
        # 1. Skanerlangan biletlar borligini tekshirish
        scanned_tickets = Ticket.objects.filter(box__order=order, box__article=item.article, status=Ticket.Status.SCANNED)
        if scanned_tickets.exists():
            messages.error(request, f"'{art_code}' modeliga tegishli biletlar allaqachon skanerlangan! Uni o'chirib bo'lmaydi.")
            return redirect('superadmin_order_detail', order_id=order.id)

        # 2. Ushbu modelning skanerlanmagan qutilarini tozalash
        order.boxes.filter(article=item.article).delete()

        # 3. Zakazning asosiy article maydonini tozalash yoki keyingi modelga o'tkazish
        remaining_item = order.items.exclude(id=item.id).first()
        order.article = remaining_item.article if remaining_item else None

        # 4. OrderItem ni o'chirish
        item.delete()

        # 5. Jami zakaz miqdorini yangilash
        new_total = order.items.aggregate(s=Sum('quantity'))['s'] or 0
        order.total_quantity = new_total
        order.save(update_fields=['article', 'total_quantity'])

        messages.success(request, f"'{art_code}' modeli ushbu zakazdan to'liq o'chirildi.")
    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_delete(request, order_id: int):
    """Zakazni butunlay o'chirish"""
    order = get_object_or_404(Order, id=order_id)
    if request.method == 'POST':
        scanned_tickets = Ticket.objects.filter(box__order=order, status=Ticket.Status.SCANNED)
        if scanned_tickets.exists():
            messages.error(request, f"'{order.order_number}' zakazida allaqachon skanerlangan biletlar mavjud! Uni o'chirib bo'lmaydi.")
            return redirect('superadmin_order_detail', order_id=order.id)

        order_num = order.order_number
        order.delete()
        messages.success(request, f"'{order_num}' zakazi muvaffaqiyatli o'chirildi.")
        return redirect('superadmin_orders_list')
    return redirect('superadmin_order_detail', order_id=order.id)



@superadmin_required
def superadmin_order_model_update_operation(request, order_id: int, item_id: int):
    """Modeldagi mavjud operatsiyaning dona narxi va ketma-ketligini yangilash"""
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(OrderItem, id=item_id, order=order)
    if request.method == 'POST':
        ao_id = request.POST.get('article_operation_id')
        price_val = request.POST.get('price_per_unit', '').strip()
        sequence_val = request.POST.get('sequence', '').strip()
        diff_val = request.POST.get('difficulty', '').strip()
        
        ao = get_object_or_404(ArticleOperation, id=ao_id, article=item.article)
        try:
            if price_val:
                ao.price_per_unit = Decimal(price_val)
            if sequence_val:
                ao.sequence = int(sequence_val)
            if diff_val:
                ao.difficulty = float(diff_val)
            ao.save()
            messages.success(request, f"'{ao.operation.name}' operatsiyasi ma'lumotlari (narxi: {ao.price_per_unit:,.0f} UZS, qiyinlik: {ao.difficulty_display}) yangilandi.")
        except Exception as e:
            messages.error(request, f"Xatolik: {str(e)}")
    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_model_add_operation(request, order_id: int, item_id: int):
    """Modelga qo'shimcha operatsiya biriktirish"""
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(OrderItem, id=item_id, order=order)
    if request.method == 'POST':
        operation_id = request.POST.get('operation_id')
        price_val = request.POST.get('price_per_unit', '0').strip()
        sequence_val = request.POST.get('sequence', '1').strip()
        diff_val = request.POST.get('difficulty', '').strip()
        
        op = get_object_or_404(Operation, id=operation_id)
        try:
            price = Decimal(price_val)
            seq = int(sequence_val) if sequence_val else (item.article.article_operations.count() + 1)
            diff = float(diff_val) if diff_val else getattr(op, 'default_difficulty', 1.0)
            ArticleOperation.objects.update_or_create(
                article=item.article,
                operation=op,
                defaults={'price_per_unit': price, 'sequence': seq, 'difficulty': diff}
            )
            messages.success(request, f"'{item.article.code}' modeliga '{op.name}' operatsiyasi qo'shildi ({price:,.0f} UZS, qiyinlik: {diff:g}).")
        except Exception as e:
            messages.error(request, f"Xatolik: {str(e)}")
    return redirect('superadmin_order_detail', order_id=order.id)


@superadmin_required
def superadmin_order_model_delete_operation(request, order_id: int, item_id: int, ao_id: int):
    """Modeldan biror operatsiyani olib tashlash"""
    order = get_object_or_404(Order, id=order_id)
    item = get_object_or_404(OrderItem, id=item_id, order=order)
    ao = get_object_or_404(ArticleOperation, id=ao_id, article=item.article)
    if request.method == 'POST':
        op_name = ao.operation.name
        ao.delete()
        messages.success(request, f"'{op_name}' operatsiyasi modeldan olib tashlandi.")
    return redirect('superadmin_order_detail', order_id=order.id)


@payroll_admin_required
def superadmin_worker_history(request, worker_id: int):
    """
    Ishchining kunlik normasi, foizi, ishlab topgan sdelshina haqi, 
    100% dan oshganda +30000 bonus va o'sha kuni qilgan barcha operatsiyalari (stikerlari).
    """
    worker = get_object_or_404(Worker, id=worker_id)
    if request.user.role == User.Role.BRANCH_ADMIN and worker.branch != request.user.branch:
        messages.error(request, "Siz faqat o'z filialingizdagi xodimlarning tarixini ko'ra olasiz!")
        return redirect('superadmin_payroll')

    today = timezone.localdate()
    current_tz = timezone.get_current_timezone()

    date_range = request.GET.get('range', 'this_month')
    start_date_str = request.GET.get('start_date', '')
    end_date_str = request.GET.get('end_date', '')

    if date_range == 'today':
        start_date = today
        end_date = today
    elif date_range == 'last_month':
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
        start_date = last_day_prev_month.replace(day=1)
        end_date = last_day_prev_month
    elif date_range == 'custom' and start_date_str and end_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = today.replace(day=1)
            end_date = today
    else:  # 'this_month' (default)
        start_date = today.replace(day=1)
        end_date = today
        date_range = 'this_month'

    # Filter tickets
    tickets = worker.tickets.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date__gte=start_date,
        scanned_at__date__lte=end_date
    ).select_related(
        'article_operation__article__model',
        'article_operation__operation',
        'box__order',
        'scanned_by'
    ).order_by('-scanned_at')

    ticket_dates = sorted(
        list(set(t.scanned_at.astimezone(current_tz).date() for t in tickets)),
        reverse=True
    )

    daily_bonus_amount = getattr(settings, 'DAILY_BONUS_AMOUNT', 0)

    UZ_DAYS = {
        0: 'Dushanba',
        1: 'Seshanba',
        2: 'Chorshanba',
        3: 'Payshanba',
        4: 'Juma',
        5: 'Shanba',
        6: 'Yakshanba'
    }

    daily_history = []
    total_period_units = 0
    total_period_piece_rate = Decimal('0.00')
    total_period_bonus = Decimal('0.00')
    days_with_bonus_count = 0

    for d in ticket_dates:
        d_tickets = [t for t in tickets if t.scanned_at.astimezone(current_tz).date() == d]
        d_units = sum(t.quantity for t in d_tickets)
        d_earned = sum(t.total_amount for t in d_tickets)

        # Multi-model norma foizi: sum((points_model / norm_model) * 100)
        model_stats = {}
        for t in d_tickets:
            ao = t.article_operation
            art = ao.article if ao else None
            pmodel = art.model if art else None
            norm = (pmodel.daily_norm if (pmodel and pmodel.daily_norm) else (art.daily_norm if (art and art.daily_norm) else 1000)) or 1000
            diff = float(ao.difficulty) if (ao and ao.difficulty) else 1.0
            pts = t.quantity * diff
            model_key = f"m_{pmodel.id}" if pmodel else (f"art_{art.id}" if art else "0")
            model_name = pmodel.name if pmodel else (art.name if art else "Noma'lum")

            if model_key not in model_stats:
                model_stats[model_key] = {
                    'name': model_name,
                    'norm': norm,
                    'points': 0.0,
                }
            model_stats[model_key]['points'] += pts

        total_day_pct = Decimal('0.0')
        norms_list = []
        for mk, mdata in model_stats.items():
            if mdata['norm'] > 0:
                pct = (Decimal(str(mdata['points'])) / Decimal(str(mdata['norm']))) * Decimal('100.0')
                total_day_pct += pct
                norms_list.append(f"{mdata['name']} ({mdata['norm']} dona)")

        total_day_pct = round(total_day_pct, 1)

        # Bonus sharti: qat'iy > 100.0% va bonus miqdori > 0 bo'lgandagina (100% bo'lsa hisob emas!)
        has_bonus = (total_day_pct > Decimal('100.0')) and (daily_bonus_amount > 0)
        d_bonus = Decimal(str(daily_bonus_amount)) if has_bonus else Decimal('0.00')

        if has_bonus:
            days_with_bonus_count += 1
            total_period_bonus += d_bonus

        total_day_income = d_earned + d_bonus
        total_period_units += d_units
        total_period_piece_rate += d_earned

        # Operatsiyalar xulosasi
        op_counts = {}
        for t in d_tickets:
            op_name = "Noma'lum"
            if t.article_operation and t.article_operation.operation:
                op_name = t.article_operation.operation.name
            op_counts[op_name] = op_counts.get(op_name, 0) + (t.quantity or 0)
        sorted_ops = sorted(op_counts.items(), key=lambda x: x[1], reverse=True)
        ops_summary = ", ".join(f"{name}: {qty} ta" for name, qty in sorted_ops)
        ops_list = [{'name': name, 'quantity': qty} for name, qty in sorted_ops]

        boxes_count = len(set(t.box_id for t in d_tickets if t.box_id))
        compact_stickers = compact_ticket_ids(d_tickets) if d_tickets else "—"
        ticket_ids_list = [t.stiker_id for t in d_tickets]

        tickets_list = []
        for t in d_tickets:
            tickets_list.append({
                'id': t.id,
                'stiker_code': t.stiker_code or f"ID{t.id}",
                'stiker_id': t.stiker_id,
                'box_number': t.box.box_number if t.box else "—",
                'box_code': t.box.box_code if t.box else "—",
                'order_number': t.box.order.order_number if (t.box and t.box.order) else "—",
                'model_name': t.article_operation.article.model.name if (t.article_operation and t.article_operation.article and t.article_operation.article.model) else (t.article_operation.article.name if (t.article_operation and t.article_operation.article) else "—"),
                'article_name': t.article_operation.article.name if (t.article_operation and t.article_operation.article) else "—",
                'operation_name': t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else "—",
                'quantity': t.quantity,
                'total_amount': int(t.total_amount) if t.total_amount else 0,
                'total_amount_formatted': f"{int(t.total_amount):,} UZS".replace(",", " ") if t.total_amount else "0 UZS",
                'time': t.scanned_at.astimezone(current_tz).strftime("%H:%M:%S") if t.scanned_at else "—",
                'master': (t.scanned_by.get_full_name() or t.scanned_by.username) if t.scanned_by else "—",
            })

        daily_history.append({
            'date': d,
            'date_str': d.strftime("%d.%m.%Y"),
            'date_iso': d.strftime("%Y-%m-%d"),
            'day_name': UZ_DAYS.get(d.weekday(), ''),
            'units': d_units,
            'ticket_count': len(d_tickets),
            'boxes_count': boxes_count,
            'operations_summary': ops_summary,
            'operations_list': ops_list,
            'compact_stickers': compact_stickers,
            'ticket_ids_list': ticket_ids_list,
            'ticket_ids_str': ", ".join(ticket_ids_list),
            'norm_display': ", ".join(norms_list) if norms_list else "1 000 dona",
            'percentage': float(total_day_pct),
            'percentage_display': f"{total_day_pct:.1f}%",
            'piece_rate': d_earned,
            'piece_rate_formatted': f"{int(d_earned):,} UZS".replace(",", " "),
            'has_bonus': has_bonus,
            'bonus': d_bonus,
            'bonus_formatted': f"+{int(d_bonus):,} UZS".replace(",", " ") if has_bonus else "—",
            'total_income': total_day_income,
            'total_income_formatted': f"{int(total_day_income):,} UZS".replace(",", " "),
            'tickets': tickets_list,
        })

    grand_total_income = total_period_piece_rate + total_period_bonus

    context = {
        'worker': worker,
        'date_range': date_range,
        'start_date': start_date,
        'end_date': end_date,
        'start_date_str': start_date.strftime("%Y-%m-%d"),
        'end_date_str': end_date.strftime("%Y-%m-%d"),
        'daily_history': daily_history,
        'days_worked_count': len(daily_history),
        'total_period_units': total_period_units,
        'total_period_piece_rate': total_period_piece_rate,
        'total_period_bonus': total_period_bonus,
        'days_with_bonus_count': days_with_bonus_count,
        'grand_total_income': grand_total_income,
        'daily_bonus_amount': daily_bonus_amount,
    }
    return render(request, 'superadmin/worker_history.html', context)


@payroll_admin_required
def api_worker_tickets_by_date(request, worker_id: int):
    worker = get_object_or_404(Worker, id=worker_id)
    if request.user.role == User.Role.BRANCH_ADMIN and worker.branch != request.user.branch:
        return JsonResponse({'success': False, 'error': "Ruxsat berilmagan!"}, status=403)

    date_str = request.GET.get('date')
    if not date_str:
        return JsonResponse({'success': False, 'error': 'Sana kiritilmadi'}, status=400)
    try:
        target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({'success': False, 'error': "Sana formati noto'g'ri (YYYY-MM-DD)"}, status=400)

    current_tz = timezone.get_current_timezone()
    tickets = worker.tickets.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date
    ).select_related(
        'article_operation__article__model',
        'article_operation__operation',
        'box__order',
        'scanned_by'
    ).order_by('scanned_at')

    # Operatsiyalar xulosasi
    op_counts = {}
    for t in tickets:
        op_name = "Noma'lum"
        if t.article_operation and t.article_operation.operation:
            op_name = t.article_operation.operation.name
        op_counts[op_name] = op_counts.get(op_name, 0) + (t.quantity or 0)
    sorted_ops = sorted(op_counts.items(), key=lambda x: x[1], reverse=True)
    ops_summary = ", ".join(f"{name}: {qty} ta" for name, qty in sorted_ops)
    ops_list = [{'name': name, 'quantity': qty} for name, qty in sorted_ops]

    boxes_count = len(set(t.box_id for t in tickets if t.box_id))
    compact_stickers = compact_ticket_ids(tickets) if tickets else "—"
    ticket_ids = [t.stiker_id for t in tickets]

    items = []
    for t in tickets:
        items.append({
            'id': t.id,
            'stiker_code': t.stiker_code or f"ID{t.id}",
            'stiker_id': t.stiker_id,
            'ticket_code': t.ticket_code,
            'box_number': t.box.box_number if t.box else "—",
            'box_code': t.box.box_code if t.box else "—",
            'order_number': t.box.order.order_number if (t.box and t.box.order) else "—",
            'model_name': t.article_operation.article.model.name if (t.article_operation and t.article_operation.article and t.article_operation.article.model) else (t.article_operation.article.name if (t.article_operation and t.article_operation.article) else "—"),
            'article_name': t.article_operation.article.name if (t.article_operation and t.article_operation.article) else "—",
            'operation_name': t.article_operation.operation.name if (t.article_operation and t.article_operation.operation) else "—",
            'quantity': t.quantity,
            'total_amount': int(t.total_amount) if t.total_amount else 0,
            'total_amount_formatted': f"{int(t.total_amount):,} UZS".replace(",", " ") if t.total_amount else "0 UZS",
            'time': t.scanned_at.astimezone(current_tz).strftime("%H:%M:%S") if t.scanned_at else "—",
            'master': (t.scanned_by.get_full_name() or t.scanned_by.username) if t.scanned_by else "—",
        })

    return JsonResponse({
        'success': True,
        'worker_name': worker.full_name,
        'date': date_str,
        'total_units': sum(t.quantity for t in tickets),
        'total_amount': float(sum(t.total_amount for t in tickets)),
        'count': len(items),
        'boxes_count': boxes_count,
        'compact_stickers': compact_stickers,
        'ticket_ids': ticket_ids,
        'operations_summary': ops_summary,
        'operations_list': ops_list,
        'tickets': items
    })


@payroll_admin_required
def superadmin_payroll_bulk_pay(request):
    """
    Buxgalteriya tabelidan tanlangan bir nechta xodimlarga oylik maoshni guruhlab to'lash (yopish).
    To'langan xodimlarning shu oydagi barcha ochiq biletlari avtomatik muzlatiladi (freeze).
    """
    if request.method != 'POST':
        return redirect('superadmin_payroll')

    worker_ids = request.POST.getlist('selected_worker_ids')
    if request.user.role == User.Role.BRANCH_ADMIN:
        user_branch = request.user.branch or User.Branch.UYCHI
        worker_ids = list(Worker.objects.filter(id__in=worker_ids, branch=user_branch).values_list('id', flat=True))

    year = int(request.POST.get('selected_year', timezone.localdate().year))
    month = int(request.POST.get('selected_month', timezone.localdate().month))
    today = timezone.localdate()

    if not worker_ids:
        messages.warning(request, "Hech qanday xodim tanlanmadi!")
        return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")

    paid_count = 0
    total_paid_sum = Decimal('0.00')

    with transaction.atomic():
        for wid in worker_ids:
            try:
                worker = Worker.objects.get(id=wid)
            except Worker.DoesNotExist:
                continue

            month_gross = worker.tickets.filter(
                status=Ticket.Status.SCANNED,
                scanned_at__year=year,
                scanned_at__month=month
            ).aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')

            payouts = worker.payouts.filter(
                payout_date__year=year,
                payout_date__month=month
            )
            advances = payouts.filter(payout_type=WorkerPayout.PayoutType.ADVANCE).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            salaries_paid = payouts.filter(payout_type=WorkerPayout.PayoutType.SALARY).aggregate(s=Sum('amount'))['s'] or Decimal('0.00')

            net_payable = month_gross - advances - salaries_paid

            if net_payable > Decimal('0.00'):
                payout = WorkerPayout.objects.create(
                    worker=worker,
                    amount=net_payable,
                    payout_type=WorkerPayout.PayoutType.SALARY,
                    payout_date=today,
                    note=f"{year}-{month:02d} oylik maoshi to'liq yopildi",
                    created_by=request.user
                )
                paid_count += 1
                total_paid_sum += net_payable

    if paid_count > 0:
        # Ushbu oy uchun 7 kunlik muzlatish taymerini avtomatik ishga tushirish
        from accounts.freeze_services import start_monthly_freeze_timer
        closing, _ = start_monthly_freeze_timer(year=year, month=month, user=request.user)

        deadline_str = timezone.localtime(closing.freeze_deadline).strftime('%d.%m.%Y %H:%M') if closing.freeze_deadline else "7 kun"
        messages.success(
            request, 
            f"Muvaffaqiyatli! {paid_count} nafar xodimga jami {int(total_paid_sum):,} UZS oylik to'landi. "
            f"Ushbu oy uchun 7 kunlik muzlatish taymeri ishga tushdi (Muddati: {deadline_str}).".replace(",", " ")
        )
    else:
        messages.info(request, "Tanlangan xodimlarda to'lanishi kerak bo'lgan maosh qoldig'i yo'q.")

    return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")


@payroll_admin_required
def superadmin_start_month_freeze_timer(request):
    """
    Buxgalter qo'lda 'Ushbu oy uchun 7 kunlik taymerni boshlash' tugmasini bosganda chaqiriladi.
    """
    if request.method != 'POST':
        return redirect('superadmin_payroll')
    try:
        year = int(request.POST.get('year', timezone.localdate().year))
        month = int(request.POST.get('month', timezone.localdate().month))
    except (ValueError, TypeError):
        year, month = timezone.localdate().year, timezone.localdate().month

    from accounts.freeze_services import start_monthly_freeze_timer
    closing, started = start_monthly_freeze_timer(year=year, month=month, user=request.user)

    if started:
        deadline_str = timezone.localtime(closing.freeze_deadline).strftime('%d.%m.%Y %H:%M')
        messages.success(
            request,
            f"⏳ {year}-yil {month:02d}-oy uchun 7 kunlik muzlatish taymeri ishga tushirildi! "
            f"Muddati: {deadline_str}. 7 kundan so'ng barcha stikerlar avtomatik muzlatiladi."
        )
    else:
        messages.warning(request, f"Ushbu oy allaqachon muzlatilgan ({closing.get_status_display()})!")

    return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")


@payroll_admin_required
def superadmin_month_freeze_toggle(request):
    """
    Superadmin uchun oyni darhol muzlatish yoki muzdan chiqarish (Manual override).
    """
    if request.method != 'POST':
        return redirect('superadmin_payroll')
    try:
        year = int(request.POST.get('year', timezone.localdate().year))
        month = int(request.POST.get('month', timezone.localdate().month))
    except (ValueError, TypeError):
        year, month = timezone.localdate().year, timezone.localdate().month

    action = request.POST.get('action', 'freeze')  # 'freeze' or 'unfreeze'
    from accounts.models import MonthlyClosing
    closing, _ = MonthlyClosing.objects.get_or_create(year=year, month=month)

    now = timezone.now()
    if action == 'freeze':
        tickets_qs = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__year=year,
            scanned_at__month=month,
            is_frozen=False
        )
        cnt = tickets_qs.update(is_frozen=True, frozen_at=now)
        closing.status = MonthlyClosing.Status.FROZEN
        closing.frozen_at = now
        closing.save()
        messages.success(request, f"🔒 {year}-yil {month:02d}-oy to'liq muzlatildi! ({cnt} ta stiker qulflondi).")
    else:
        tickets_qs = Ticket.objects.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__year=year,
            scanned_at__month=month,
            is_frozen=True
        )
        cnt = tickets_qs.update(is_frozen=False, frozen_at=None)
        closing.status = MonthlyClosing.Status.OPEN
        closing.freeze_deadline = None
        closing.frozen_at = None
        closing.save()
        messages.warning(request, f"🔓 {year}-yil {month:02d}-oy muzdan chiqarildi! ({cnt} ta stiker qayta faollashdi).")

    return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")


@payroll_admin_required
def superadmin_ticket_freeze_toggle(request):
    """
    Xodimning tanlangan oy bo'yicha biletlarini qo'lda qulflash (freeze) yoki ochish (unfreeze).
    """
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id')
        year = int(request.POST.get('year', timezone.localdate().year))
        month = int(request.POST.get('month', timezone.localdate().month))
        action_type = request.POST.get('action_type', 'freeze')  # 'freeze' yoki 'unfreeze'

        worker = get_object_or_404(Worker, id=worker_id)
        if request.user.role == User.Role.BRANCH_ADMIN and worker.branch != request.user.branch:
            messages.error(request, "Siz faqat o'z filialingizdagi xodimlar stikerlarini boshqara olasiz!")
            return redirect('superadmin_payroll')

        tickets_qs = worker.tickets.filter(
            status=Ticket.Status.SCANNED,
            scanned_at__year=year,
            scanned_at__month=month
        )

        if action_type == 'freeze':
            cnt = tickets_qs.filter(is_frozen=False).update(
                is_frozen=True,
                frozen_at=timezone.now()
            )
            messages.success(request, f"🔒 {worker.full_name}: {cnt} ta bilet muzlatildi (qulflondi).")
        else:
            cnt = tickets_qs.filter(is_frozen=True).update(
                is_frozen=False,
                frozen_at=None,
                frozen_payout=None
            )
            messages.warning(request, f"🔓 {worker.full_name}: {cnt} ta bilet muzdan chiqarildi (ochildi).")

        return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")

    return redirect('superadmin_payroll')


@payroll_admin_required
def superadmin_recalculate_unfrozen_tickets(request):
    """
    Tizimdagi barcha biletlarni operatsiyalarning eng so'nggi narxlari bilan
    to'liq qayta hisoblash (re-sync).
    """
    if request.method == 'POST':
        total_updated = 0
        for ao in ArticleOperation.objects.all():
            cnt = ao.sync_price_to_tickets()
            total_updated += cnt

        messages.success(
            request,
            f"✓ Muvaffaqiyatli! {total_updated} ta bilet operatsiyalarning yangi narxlariga to'liq qayta hisoblandi."
        )

        year = request.POST.get('year')
        month = request.POST.get('month')
        if year and month:
            return redirect(f"{reverse('superadmin_payroll')}?year={year}&month={month}")
        return redirect('superadmin_payroll')

    return redirect('superadmin_payroll')


@superadmin_required
def superadmin_update_worker_branch(request):
    """
    Super Admin (HQ) xodimning ish joyini (Filialini) HQ yoki Uychiga o'zgartirishi uchun.
    """
    if request.method == 'POST':
        worker_id = request.POST.get('worker_id')
        new_branch = request.POST.get('branch', '').strip().upper()
        worker = get_object_or_404(Worker, id=worker_id)
        if new_branch in User.Branch.values:
            worker.branch = new_branch
            worker.save(update_fields=['branch'])
            if worker.user:
                worker.user.branch = new_branch
                worker.user.save(update_fields=['branch'])
            messages.success(request, f"✓ {worker.full_name} ({worker.worker_id}) ning filiali '{worker.get_branch_display()}' ga o'zgartirildi.")
        else:
            messages.error(request, "Noto'g'ri filial tanlandi!")
        return redirect(request.META.get('HTTP_REFERER') or 'superadmin_payroll')
    return redirect('superadmin_payroll')



