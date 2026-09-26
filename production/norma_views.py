import json
from decimal import Decimal
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.db import transaction
from django.db.models import Sum, Q, Count, ProtectedError

from .models import (
    ProductModel, ProductModelOperation, Article, ArticleOperation, Operation,
    DailyModelProgress, Order, OrderItem, Ticket, OperationGroup, OperationGroupItem
)
from .norma_services import (
    calculate_daily_model_progress,
    sync_all_models_for_date,
    get_today_norma_dashboard_data,
    get_workers_norma_breakdown
)


def norma_access_required(view_func):
    """Norma bo'limiga faqat Admin, Superadmin, Menejer va Masterlar kira oladi"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        user = request.user
        is_allowed = (
            user.is_superadmin() or 
            user.is_admin_user() or 
            getattr(user, 'is_manager', lambda: False)() or
            getattr(user, 'is_master', lambda: False)()
        )
        if not is_allowed:
            messages.error(request, "Ushbu bo'limga kirish uchun sizda ruxsat yo'q!")
            return redirect('production:order_list')
        return view_func(request, *args, **kwargs)
    return wrapper


@norma_access_required
def norma_dashboard(request):
    """
    Norma Boshqaruv Markazi (Ishlab Chiqarish Miyasi):
    - Bugungi kunlik norma holati (jonli monitoring)
    - Modellar bo'yicha foizlar, bajarilgan dona va qoldiqlar
    - Erkin (modelga birikmagan) artikullar
    """
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    data = get_today_norma_dashboard_data(target_date)
    workers_data = get_workers_norma_breakdown(target_date)

    return render(request, 'norma/dashboard.html', {
        **data,
        'workers_data': workers_data[:10], # Top 10 workers on dashboard
        'total_active_workers': len(workers_data),
        'daily_bonus_amount': getattr(settings, 'DAILY_BONUS_AMOUNT', 0),
    })


@norma_access_required
def norma_models_list(request):
    """
    Barcha Modellar Katalogi va Normasi:
    - Model kodi, nomi, kunlik normasi
    - Unga bog'langan artikullar
    - Yangi model qo'shish va tahrirlash
    """
    models = ProductModel.objects.all().prefetch_related('articles').order_by('code')
    unassigned_articles = Article.objects.filter(model__isnull=True).order_by('code')

    return render(request, 'norma/models_list.html', {
        'models': models,
        'unassigned_articles': unassigned_articles,
    })


@norma_access_required
def norma_model_create(request):
    """Yangi Model yaratish va unga artikullarni biriktirish (POST)"""
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        daily_norm_str = request.POST.get('daily_norm', '1000').strip()
        article_ids = request.POST.getlist('article_ids')

        try:
            daily_norm = int(daily_norm_str)
            if daily_norm <= 0:
                daily_norm = 1000
        except ValueError:
            daily_norm = 1000

        if not code or not name:
            messages.error(request, "Model kodi va nomi to'ldirilishi shart!")
            return redirect('norma_models_list')

        if ProductModel.objects.filter(code=code).exists():
            messages.error(request, f"'{code}' kodli model allaqachon mavjud!")
            return redirect('norma_models_list')

        with transaction.atomic():
            pm = ProductModel.objects.create(
                code=code,
                name=name,
                description=description,
                daily_norm=daily_norm
            )
            if article_ids:
                Article.objects.filter(id__in=article_ids).update(model=pm, daily_norm=daily_norm)

        messages.success(request, f"Yangi '{pm.name}' ({pm.code}) modeli yaratildi! Kunlik norma: {pm.daily_norm} dona.")
        return redirect('norma_models_list')

    return redirect('norma_models_list')


@norma_access_required
def norma_model_edit(request, model_id: int):
    """Mavjud Modelni tahrirlash (Kunlik norma, nom, bog'langan artikullar)"""
    pm = get_object_or_404(ProductModel, id=model_id)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        daily_norm_str = request.POST.get('daily_norm', '').strip()
        description = request.POST.get('description', '').strip()
        article_ids = request.POST.getlist('article_ids')

        try:
            daily_norm = int(daily_norm_str)
            if daily_norm > 0:
                pm.daily_norm = daily_norm
        except ValueError:
            pass

        if name:
            pm.name = name
        pm.description = description
        pm.save()

        # Artikullarni yangilash
        with transaction.atomic():
            # Eski bog'langanlarni tozalash (agar formda olib tashlangan bo'lsa)
            Article.objects.filter(model=pm).exclude(id__in=article_ids).update(model=None)
            # Tanlanganlarni yangilash
            if article_ids:
                Article.objects.filter(id__in=article_ids).update(model=pm, daily_norm=pm.daily_norm)

        # Bugungi kun normasini yangilash
        calculate_daily_model_progress(pm)

        messages.success(request, f"'{pm.name}' modeli muvaffaqiyatli yangilandi! Kunlik norma: {pm.daily_norm} dona.")
        return redirect('norma_models_list')

    all_articles = Article.objects.all().order_by('code')
    return render(request, 'norma/model_edit.html', {
        'model': pm,
        'all_articles': all_articles,
    })


@norma_access_required
def norma_assign_articles(request, model_id: int):
    """Modelga erkin artikullarni biriktirish (POST)"""
    if request.method == 'POST':
        pm = get_object_or_404(ProductModel, id=model_id)
        article_ids = request.POST.getlist('article_ids')

        if article_ids:
            Article.objects.filter(id__in=article_ids).update(model=pm, daily_norm=pm.daily_norm)
            calculate_daily_model_progress(pm)
            messages.success(request, f"{len(article_ids)} ta artikul '{pm.name}' modeliga biriktirildi!")
        else:
            messages.warning(request, "Hech qanday artikul tanlanmadi.")

    return redirect('norma_models_list')


@norma_access_required
def norma_history(request):
    """
    Sanalar Bo'yicha Norma Arxivi:
    - O'tgan kunlar bo'yicha modellar statistikasi
    - Sana, Model, Status bo'yicha filter
    """
    date_from_str = request.GET.get('date_from', '')
    date_to_str = request.GET.get('date_to', '')
    model_id = request.GET.get('model_id', '')

    today = timezone.localdate()
    default_from = today - datetime.timedelta(days=30)

    try:
        date_from = datetime.datetime.strptime(date_from_str, "%Y-%m-%d").date() if date_from_str else default_from
    except ValueError:
        date_from = default_from

    try:
        date_to = datetime.datetime.strptime(date_to_str, "%Y-%m-%d").date() if date_to_str else today
    except ValueError:
        date_to = today

    qs = DailyModelProgress.objects.filter(
        date__gte=date_from,
        date__lte=date_to
    ).select_related('product_model')

    if model_id:
        try:
            qs = qs.filter(product_model_id=int(model_id))
        except ValueError:
            pass

    history_records = qs.order_by('-date', 'product_model__code')
    all_models = ProductModel.objects.all().order_by('name')

    return render(request, 'norma/history.html', {
        'history_records': history_records,
        'all_models': all_models,
        'date_from': date_from.strftime('%Y-%m-%d'),
        'date_to': date_to.strftime('%Y-%m-%d'),
        'selected_model_id': model_id,
    })


@norma_access_required
def norma_workers(request):
    """
    Xodimlar Kesimida Kunlik Norma Monitoringi:
    - Tikuvchilar qaysi modellarni tikkanligi
    - Bajarilish foizi va bonus holati
    """
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    workers_data = get_workers_norma_breakdown(target_date)

    return render(request, 'norma/workers.html', {
        'target_date': target_date,
        'workers_data': workers_data,
        'total_workers': len(workers_data),
        'bonus_workers_count': sum(1 for w in workers_data if w['has_bonus']),
        'daily_bonus_amount': getattr(settings, 'DAILY_BONUS_AMOUNT', 0),
    })


@norma_access_required
def norma_sync(request):
    """Kunlik hisobni qayta sinxronlash va hisoblash"""
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    sync_all_models_for_date(target_date)
    messages.success(request, f"{target_date.strftime('%d.%m.%Y')} sanasi uchun barcha modellar normasi qayta hisoblandi!")
    return redirect(f"/norma/?date={target_date.strftime('%Y-%m-%d')}")


@norma_access_required
def norma_model_operations(request, model_id: int):
    """Model operatsiyalari va ularning ketma-ketlik tartib raqamlarini (1, 2, 3...) boshqarish"""
    pm = get_object_or_404(ProductModel.objects.prefetch_related('articles'), id=model_id)
    operations = pm.model_operations.select_related('operation').order_by('sequence', 'id')
    all_operations = Operation.objects.all().order_by('order_number', 'name')

    return render(request, 'norma/model_operations.html', {
        'model': pm,
        'operations': operations,
        'all_operations': all_operations,
        'next_sequence': (operations.last().sequence + 1) if operations.exists() else 1,
    })


@norma_access_required
def norma_model_add_operation(request, model_id: int):
    """Modelga yangi operatsiyani tartib raqami bilan biriktirish"""
    pm = get_object_or_404(ProductModel, id=model_id)
    if request.method == 'POST':
        operation_id = request.POST.get('operation_id')
        new_op_name = request.POST.get('new_operation_name', '').strip()
        new_op_code = request.POST.get('new_operation_code', '').strip()
        sequence_val = request.POST.get('sequence', '').strip()
        price_val = request.POST.get('price_per_unit', '0').strip()
        diff_val = request.POST.get('difficulty', '1.0').strip()

        op = None
        if new_op_name:
            if not new_op_code:
                import re
                new_op_code = re.sub(r'[^A-Za-z0-9]', '', new_op_name.upper())[:20]
                base_code = new_op_code or "OP"
                counter = 1
                while Operation.objects.filter(code=new_op_code).exists():
                    new_op_code = f"{base_code}_{counter}"
                    counter += 1
            op, _ = Operation.objects.get_or_create(
                code=new_op_code,
                defaults={'name': new_op_name, 'default_difficulty': float(diff_val) if diff_val else 1.0}
            )
        elif operation_id:
            op = get_object_or_404(Operation, id=operation_id)

        if not op:
            messages.error(request, "Operatsiya tanlanmadi yoki nomi kiritilmadi!")
            return redirect('norma_model_operations', model_id=pm.id)

        try:
            seq = int(sequence_val) if sequence_val else (pm.model_operations.count() + 1)
            price = Decimal(price_val) if price_val else Decimal('0.00')
            diff = float(diff_val) if diff_val else getattr(op, 'default_difficulty', 1.0)

            with transaction.atomic():
                ProductModelOperation.objects.update_or_create(
                    model=pm,
                    operation=op,
                    defaults={
                        'sequence': seq,
                        'price_per_unit': price,
                        'difficulty': diff,
                    }
                )
                pm.sync_operations_to_articles()

            messages.success(request, f"'{op.name}' operatsiyasi #{seq} tartib raqami bilan biriktirildi!")
        except Exception as e:
            messages.error(request, f"Xatolik yuz berdi: {str(e)}")

    return redirect('norma_model_operations', model_id=pm.id)


@norma_access_required
def norma_model_update_operations(request, model_id: int):
    """Model operatsiyalarining tartib raqamlari va narxlarini ommaviy yangilash"""
    pm = get_object_or_404(ProductModel, id=model_id)
    if request.method == 'POST':
        mo_ids = request.POST.getlist('mo_id')
        with transaction.atomic():
            for mo_id in mo_ids:
                seq_val = request.POST.get(f'sequence_{mo_id}')
                price_val = request.POST.get(f'price_{mo_id}')
                diff_val = request.POST.get(f'difficulty_{mo_id}')

                mo = ProductModelOperation.objects.filter(id=mo_id, model=pm).first()
                if mo:
                    if seq_val is not None and seq_val.isdigit():
                        mo.sequence = int(seq_val)
                    if price_val:
                        try:
                            mo.price_per_unit = Decimal(price_val)
                        except Exception:
                            pass
                    if diff_val:
                        try:
                            mo.difficulty = float(diff_val)
                        except Exception:
                            pass
                    mo.save()

            pm.sync_operations_to_articles()

        messages.success(request, f"'{pm.name}' modelining operatsiyalar tartib raqamlari va ma'lumotlari muvaffaqiyatli saqlandi!")
    return redirect('norma_model_operations', model_id=pm.id)


@norma_access_required
def norma_model_delete_operation(request, model_id: int, mo_id: int):
    """Modeldan operatsiyani olib tashlash"""
    pm = get_object_or_404(ProductModel, id=model_id)
    if request.method == 'POST':
        mo = get_object_or_404(ProductModelOperation, id=mo_id, model=pm)
        op_name = mo.operation.name
        with transaction.atomic():
            ArticleOperation.objects.filter(article__model=pm, operation=mo.operation).delete()
            mo.delete()
        messages.success(request, f"'{op_name}' operatsiyasi modeldan olib tashlandi.")
    return redirect('norma_model_operations', model_id=pm.id)


@norma_access_required
def norma_operations_catalog(request):
    """Barcha mavjud operatsiyalar katalogi"""
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete_operation':
            op_id = request.POST.get('operation_id')
            op = get_object_or_404(Operation, id=op_id)
            op_name = op.name

            tickets_count = Ticket.objects.filter(article_operation__operation=op).count()
            if tickets_count > 0:
                messages.error(
                    request,
                    f"'{op_name}' operatsiyasini o'chirib bo'lmaydi! Ushbu operatsiya bo'yicha {tickets_count} ta ishchi bileti mavjud."
                )
                return redirect('norma_operations_catalog')

            try:
                op.delete()
                messages.success(request, f"'{op_name}' operatsiyasi katalogdan muvaffaqiyatli o'chirildi.")
            except ProtectedError:
                messages.error(request, f"'{op_name}' operatsiyasini o'chirib bo'lmaydi, chunki unga bog'langan ma'lumotlar mavjud.")
            except Exception as e:
                messages.error(request, f"Xatolik yuz berdi: {str(e)}")

            return redirect('norma_operations_catalog')

        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip().upper()
        if not code and name:
            code = name.upper()[:50]
        order_number_str = request.POST.get('order_number', '1').strip()
        difficulty_str = request.POST.get('default_difficulty', '1.0').strip()
        description = request.POST.get('description', '').strip()

        try:
            order_number = int(order_number_str) if order_number_str else 1
            difficulty = float(difficulty_str) if difficulty_str else 1.0
        except ValueError:
            order_number = 1
            difficulty = 1.0

        if not name:
            messages.error(request, "Operatsiya nomi kiritilishi shart!")
        elif Operation.objects.filter(code=code).exists():
            messages.error(request, f"'{code}' kodli operatsiya allaqachon mavjud!")
        else:
            Operation.objects.create(
                code=code,
                name=name,
                order_number=order_number,
                default_difficulty=difficulty,
                description=description
            )
            messages.success(request, f"'{name}' operatsiyasi (#{order_number}) katalogga qo'shildi!")
        return redirect('norma_operations_catalog')

    operations = Operation.objects.all().order_by('order_number', 'name')
    return render(request, 'norma/operations_catalog.html', {
        'operations': operations,
        'next_order_number': (operations.last().order_number + 1) if operations.exists() else 1,
    })


@norma_access_required
def norma_canvas_view(request):
    """
    Norma DB-Design Interaktiv Doskasi (Canvas):
    - Barcha aktiv zakazlar
    - Har bir zakaz ichida uning artikullari va modeli
    - Har bir artikulning statusi (Yashil: Norma + Operatsiya bor; Kulrang/Kutilmoqda: yo'q)
    """
    orders_qs = Order.objects.filter(
        status__in=[Order.Status.IN_PROGRESS, Order.Status.DRAFT]
    ).prefetch_related(
        'items__article__model__model_operations__operation',
        'items__article__article_operations__operation',
        'items__article__operation_group',
        'customer'
    )
    orders = list(orders_qs)

    # Zakazlar tartibi: Mavjud zakazlar yuqorida (1001, Детский сарафан), yangi qo'shilgan zakazlar (1109 va boshqalar) pastda
    def get_order_sort_key(o):
        if o.order_number == '1001':
            return (0, o.id)
        if 'сарафан' in (o.order_number or '').lower():
            return (1, o.id)
        return (2, o.id)

    orders.sort(key=get_order_sort_key)

    # Tikuvga kirmaganligini (skanerlanmaganligini) aniqlash uchun barcha skanerlangan biletlar artikullarini olish:
    scanned_article_ids = set(Ticket.objects.filter(
        status=Ticket.Status.SCANNED
    ).values_list('article_operation__article_id', flat=True))

    orders_data = []
    total_articles_count = 0
    ready_articles_count = 0

    for order in orders:
        articles_map = {}
        for item in order.items.all():
            art = item.article
            if not art or art.id in articles_map:
                continue

            pmodel = art.model
            daily_norm = item.norm or (pmodel.daily_norm if pmodel else art.daily_norm) or 0

            art_ops = list(art.article_operations.all().order_by('sequence', 'id'))

            ops_data = []
            for ao in art_ops:
                ops_data.append({
                    'sequence': ao.sequence,
                    'name': ao.operation.name,
                    'code': ao.operation.code,
                    'price': float(ao.price_per_unit),
                    'difficulty': ao.difficulty_display,
                })

            # is_ready faqat artikulning O'ZIGA operatsiya bog'langan bo'lsa True
            is_ready = bool(daily_norm > 0 and len(art_ops) > 0)
            if is_ready:
                ready_articles_count += 1
            total_articles_count += 1

            is_in_sewing = art.id in scanned_article_ids

            articles_map[art.id] = {
                'id': art.id,
                'code': art.code,
                'name': art.name,
                'model_id': pmodel.id if pmodel else None,
                'model_code': pmodel.code if pmodel else None,
                'model_name': pmodel.name if pmodel else None,
                'operation_group_id': art.operation_group_id,
                'operation_group_name': art.operation_group.name if art.operation_group else None,
                'daily_norm': daily_norm,
                'quantity': item.quantity or 0,
                'operations_count': len(ops_data),
                'operations': ops_data,
                'unit_total_rate': sum(op['price'] for op in ops_data),
                'is_ready': is_ready,
                'is_in_sewing': is_in_sewing,
            }

        if order.article and order.article.id not in articles_map:
            art = order.article
            pmodel = art.model
            daily_norm = (pmodel.daily_norm if pmodel else art.daily_norm) or 0
            art_ops = list(art.article_operations.all().order_by('sequence', 'id'))
            ops_data = [{
                'sequence': ao.sequence,
                'name': ao.operation.name,
                'code': ao.operation.code,
                'price': float(ao.price_per_unit),
                'difficulty': ao.difficulty_display,
            } for ao in art_ops]

            # is_ready faqat artikulning O'ZIGA operatsiya bog'langan bo'lsa True
            is_ready = bool(daily_norm > 0 and len(art_ops) > 0)
            if is_ready:
                ready_articles_count += 1
            total_articles_count += 1

            is_in_sewing = art.id in scanned_article_ids

            articles_map[art.id] = {
                'id': art.id,
                'code': art.code,
                'name': art.name,
                'model_id': pmodel.id if pmodel else None,
                'model_code': pmodel.code if pmodel else None,
                'model_name': pmodel.name if pmodel else 'Model',
                'daily_norm': daily_norm,
                'quantity': order.total_quantity or 0,
                'operations_count': len(ops_data),
                'operations': ops_data,
                'unit_total_rate': sum(op['price'] for op in ops_data),
                'is_ready': is_ready,
                'is_in_sewing': is_in_sewing,
            }

        # 1. Modellar va operatsiyalar/norma bo'yicha guruhlash
        # Foydalanuvchi bir xil modeldagi artikullarga turlicha operatsiya yoki norma belgilashi mumkin.
        # Shuning uchun guruhlash: (model_id, operations_signature, daily_norm)
        import hashlib
        model_groups_dict = {}
        for art_data in articles_map.values():
            m_id = str(art_data['model_id'] or art_data['model_name'] or 'boshqa')
            ops = art_data.get('operations') or []
            daily_norm = art_data.get('daily_norm') or 0
            if ops:
                ops_sig = "|".join(f"{op.get('code')}:{op.get('sequence')}:{float(op.get('price', 0))}" for op in ops)
                ops_hash = hashlib.md5(ops_sig.encode('utf-8')).hexdigest()[:8]
                m_key = f"{m_id}_{ops_hash}_{daily_norm}"
            else:
                m_key = f"{m_id}_no_ops_{daily_norm}"

            if m_key not in model_groups_dict:
                model_groups_dict[m_key] = {
                    'key': m_key,
                    'model_id': art_data['model_id'],
                    'model_name': art_data['model_name'] or 'Model',
                    'model_code': art_data['model_code'] or '',
                    'daily_norm': art_data['daily_norm'],
                    'operations': art_data['operations'],
                    'operations_count': art_data['operations_count'],
                    'unit_total_rate': art_data['unit_total_rate'],
                    'is_ready': art_data['is_ready'],
                    'is_in_sewing': art_data['is_in_sewing'],
                    'articles': [],
                }
            if not model_groups_dict[m_key]['is_ready'] and art_data['is_ready']:
                model_groups_dict[m_key]['daily_norm'] = art_data['daily_norm']
                model_groups_dict[m_key]['operations'] = art_data['operations']
                model_groups_dict[m_key]['operations_count'] = art_data['operations_count']
                model_groups_dict[m_key]['unit_total_rate'] = art_data['unit_total_rate']
                model_groups_dict[m_key]['is_ready'] = True

            if art_data['is_in_sewing']:
                model_groups_dict[m_key]['is_in_sewing'] = True

            model_groups_dict[m_key]['articles'].append(art_data)

        model_groups = list(model_groups_dict.values())
        # Modellar bo'yicha ketma-ket, bir model ichida operatsiyalari borlar oldin, so'ng yo'qlar
        model_groups.sort(key=lambda mg: (
            mg['model_name'],
            0 if (mg['operations'] and len(mg['operations']) > 0) else 1,
            mg['key']
        ))
        for mg in model_groups:
            mg['articles'].sort(key=lambda a: a['code'])

        flat_sorted_articles = []
        for mg in model_groups:
            flat_sorted_articles.extend(mg['articles'])

        orders_data.append({
            'id': order.id,
            'order_number': order.order_number,
            'client_name': order.client_name or (order.customer.name if order.customer else 'Buyurtma'),
            'total_quantity': order.all_models_quantity,
            'status': order.status,
            'articles': flat_sorted_articles,
            'model_groups': model_groups,
        })

    all_operations = Operation.objects.all().order_by('order_number', 'name')
    all_operations_data = [{
        'id': op.id,
        'code': op.code,
        'name': op.name,
        'difficulty': op.default_difficulty_display,
        'sequence': op.order_number,
    } for op in all_operations]

    all_models = ProductModel.objects.all().prefetch_related('model_operations__operation').order_by('code')

    models_lookup = {}
    for pm in all_models:
        models_lookup[pm.id] = {
            'id': pm.id,
            'code': pm.code,
            'name': pm.name,
            'daily_norm': pm.daily_norm,
            'operations': [{
                'sequence': mo.sequence,
                'name': mo.operation.name,
                'code': mo.operation.code,
                'price': float(mo.price_per_unit),
                'difficulty': mo.difficulty_display,
            } for mo in pm.model_operations.all().order_by('sequence', 'id')]
        }

    all_operation_groups = OperationGroup.objects.prefetch_related('items__operation').all().order_by('name')
    operation_groups_lookup = {}
    for grp in all_operation_groups:
        operation_groups_lookup[grp.id] = {
            'id': grp.id,
            'name': grp.name,
            'description': grp.description,
            'total_rate': float(grp.total_unit_rate),
            'operations': [{
                'sequence': item.sequence,
                'name': item.operation.name,
                'code': item.operation.code,
                'price': float(item.price_per_unit),
                'difficulty': item.difficulty_display,
            } for item in grp.items.all().order_by('sequence', 'id')]
        }

    return render(request, 'norma/canvas.html', {
        'orders_data': orders_data,
        'orders_data_json': json.dumps(orders_data),
        'all_operations': all_operations,
        'all_operations_json': json.dumps(all_operations_data),
        'all_models': all_models,
        'models_lookup_json': json.dumps(models_lookup),
        'all_operation_groups': all_operation_groups,
        'operation_groups_json': json.dumps(operation_groups_lookup),
        'total_orders_count': len(orders_data),
        'total_articles_count': total_articles_count,
        'ready_articles_count': ready_articles_count,
    })


@norma_access_required
def norma_canvas_save(request):
    """
    2 Bosqichli saqlash:
    1. 'save_operations': Bazadagi katalogdan tanlangan operatsiyalarni bog'laydi.
    2. 'save_norma': Kunlik normani saqlaydi va tasdiqlaydi.
    3. 'save_all': Ham norma, ham operatsiyalarni bir vaqtda saqlaydi.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': "Faqat POST so'rov qabul qilinadi."}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST

    article_ids = data.get('article_ids', [])
    action = data.get('action', 'save_all')
    daily_norm_raw = data.get('daily_norm')
    operations_list = data.get('operations', [])

    if not article_ids:
        return JsonResponse({'success': False, 'error': "Hech qanday artikul tanlanmadi!"}, status=400)

    articles = Article.objects.filter(id__in=article_ids).select_related('model', 'operation_group')
    if not articles.exists():
        return JsonResponse({'success': False, 'error': "Tanlangan artikullar topilmadi!"}, status=404)

    # 0-BOSQICH: TAYYOR GURUHNI BIRIKTIRISH
    if action == 'assign_group':
        group_id = data.get('group_id')
        with transaction.atomic():
            if not group_id:
                for art in articles:
                    art.operation_group = None
                    art.save(update_fields=['operation_group'])
                return JsonResponse({'success': True, 'action': 'assign_group', 'message': "Guruh biriktiruvi olib tashlandi."})

            group = get_object_or_404(OperationGroup, id=group_id)
            for art in articles:
                art.operation_group = group
                art.save()  # syncs operations from group!
        return JsonResponse({
            'success': True,
            'action': 'assign_group',
            'group_id': group.id,
            'group_name': group.name,
            'message': f"'{group.name}' guruhi muvaffaqiyatli biriktirildi va operatsiyalar sinxronlandi."
        })

    # 1-BOSQICH: FAQAT OPERATSIYALARNI SAQLASH
    if action == 'save_operations':
        if not operations_list or len(operations_list) == 0:
            return JsonResponse({'success': False, 'error': "Kamida 1 ta operatsiya tanlanishi shart!"}, status=400)

        cleaned_operations = []
        for idx, op_item in enumerate(operations_list, start=1):
            name = str(op_item.get('name', '')).strip()
            if not name:
                return JsonResponse({'success': False, 'error': f"{idx}-operatsiyaning nomi kiritilishi shart!"}, status=400)

            code = str(op_item.get('code', '')).strip().upper()
            if not code:
                code = name.upper()[:50]

            try:
                seq = int(op_item.get('sequence', idx))
            except (TypeError, ValueError):
                seq = idx

            try:
                price = Decimal(str(op_item.get('price', '0')).strip() or '0')
            except Exception:
                price = Decimal('0.00')

            if price <= Decimal('0.00'):
                return JsonResponse({'success': False, 'error': f"'{name}' operatsiyasining dona narxi 0 dan katta bo'lishi shart!"}, status=400)

            try:
                difficulty = float(op_item.get('difficulty', 1.0))
            except (TypeError, ValueError):
                difficulty = 1.0

            cleaned_operations.append({
                'sequence': seq,
                'name': name,
                'code': code,
                'price': price,
                'difficulty': difficulty,
            })

        # Tartib raqamlari takrorlanmasligini ta'minlash: agar takrorlangan bo'lsa, avtomatik 1, 2, 3... qilib tekislash
        seq_list = [c_op['sequence'] for c_op in cleaned_operations]
        if len(seq_list) != len(set(seq_list)):
            for idx, c_op in enumerate(cleaned_operations, start=1):
                c_op['sequence'] = idx

        with transaction.atomic():
            op_objs = []
            for c_op in cleaned_operations:
                op_obj, _ = Operation.objects.get_or_create(
                    name__iexact=c_op['name'],
                    defaults={
                        'code': c_op['code'],
                        'name': c_op['name'],
                        'default_difficulty': c_op['difficulty'],
                        'order_number': c_op['sequence'],
                    }
                )
                op_objs.append((op_obj, c_op))

            keep_op_ids = [op_obj.id for op_obj, _ in op_objs]
            models_to_sync = set()

            for art in articles:
                for op_obj, c_op in op_objs:
                    ArticleOperation.objects.update_or_create(
                        article=art,
                        operation=op_obj,
                        defaults={
                            'sequence': c_op['sequence'],
                            'price_per_unit': c_op['price'],
                            'difficulty': c_op['difficulty'],
                        }
                    )
                # Faqat biletlarga bog'lanmagan ortiqcha operatsiyalarni o'chirish
                to_delete = art.article_operations.exclude(operation_id__in=keep_op_ids)
                for ao in to_delete:
                    if not ao.tickets.exists():
                        ao.delete()

                if art.model:
                    models_to_sync.add(art.model)

            for pm in models_to_sync:
                for op_obj, c_op in op_objs:
                    ProductModelOperation.objects.update_or_create(
                        model=pm,
                        operation=op_obj,
                        defaults={
                            'sequence': c_op['sequence'],
                            'price_per_unit': c_op['price'],
                            'difficulty': c_op['difficulty'],
                        }
                    )
                pm.model_operations.exclude(operation_id__in=keep_op_ids).delete()

            assigned_group = None
            if data.get('group_id'):
                assigned_group = OperationGroup.objects.filter(id=data.get('group_id')).first()
                if assigned_group:
                    for art in articles:
                        art.operation_group = assigned_group
                        art.save(update_fields=['operation_group'])

        # Nechta bilet narxi yangilanganini hisoblash
        total_synced_tickets = Ticket.objects.filter(
            article_operation__article__in=articles,
            article_operation__operation_id__in=keep_op_ids
        ).count()

        return JsonResponse({
            'success': True,
            'action': 'save_operations',
            'updated_article_ids': list(articles.values_list('id', flat=True)),
            'operations_count': len(cleaned_operations),
            'operations': [{
                'sequence': o['sequence'],
                'name': o['name'],
                'code': o['code'],
                'price': float(o['price']),
                'difficulty': o['difficulty'],
            } for o in cleaned_operations],
            'unit_total_rate': float(sum(o['price'] for o in cleaned_operations)),
            'group_id': assigned_group.id if assigned_group else None,
            'group_name': assigned_group.name if assigned_group else None,
            'synced_tickets_count': total_synced_tickets,
            'message': f"Operatsiyalar va narxlar saqlandi! {total_synced_tickets} ta bilet yangi narxga qayta hisoblandi.",
        })

    # 2-BOSQICH: FAQAT KUNLIK NORMANI SAQLASH
    elif action == 'save_norma':
        try:
            daily_norm = int(daily_norm_raw)
            if daily_norm <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': "Kunlik norma soni kiritilishi shart (masalan: 1000 dona)!"}, status=400)

        with transaction.atomic():
            models_to_sync = set(art.model for art in articles if art.model)
            for pm in models_to_sync:
                pm.daily_norm = daily_norm
                pm.save()

            articles.update(daily_norm=daily_norm)
            OrderItem.objects.filter(article__in=articles).update(norm=daily_norm)

        return JsonResponse({
            'success': True,
            'action': 'save_norma',
            'updated_article_ids': list(articles.values_list('id', flat=True)),
            'daily_norm': daily_norm,
            'message': "Kunlik norma muvaffaqiyatli saqlandi va bog'landi!",
        })

    # 3-BOSQICH: OPERATSIYALARNI O'CHIRISH (AGAR TIKUVGA KIRMAGAN BO'LSA)
    elif action == 'delete_operations':
        article_ops = ArticleOperation.objects.filter(article__in=articles)

        # Tikuvga kirmaganligini qat'iy tekshirish: agar skanerlangan biletlar bo'lsa, o'chirib bo'lmaydi
        scanned_tickets = Ticket.objects.filter(
            article_operation__in=article_ops,
            status=Ticket.Status.SCANNED
        )
        if scanned_tickets.exists():
            return JsonResponse({
                'success': False,
                'error': "Ushbu operatsiyalar allaqachon tikuv jarayoniga kirgan (skanerlangan biletlar mavjud) va ularni o'chirib bo'lmaydi!"
            }, status=400)

        with transaction.atomic():
            # Hali skanerlanmagan (PENDING) biletlar bo'lsa, ularni tozalash
            Ticket.objects.filter(article_operation__in=article_ops).delete()

            # Artikul operatsiyalarini o'chirish
            article_ops.delete()

            # Operatsiyalar guruhini ham uzish
            articles.update(operation_group=None)

            # Modellar bilan sinxronizatsiya: agar modelning boshqa artikullarida operatsiyalar qolmagan bo'lsa
            models_to_check = set(art.model for art in articles if art.model)
            for pm in models_to_check:
                other_has_ops = ArticleOperation.objects.filter(
                    article__model=pm
                ).exclude(article__in=articles).exists()
                if not other_has_ops:
                    pm.model_operations.all().delete()

        return JsonResponse({
            'success': True,
            'action': 'delete_operations',
            'updated_article_ids': list(articles.values_list('id', flat=True)),
            'operations': [],
            'operations_count': 0,
            'unit_total_rate': 0,
            'message': "Operatsiyalar muvaffaqiyatli o'chirildi!",
        })

    # 4-BOSQICH: KUNLIK NORMANI O'CHIRISH
    elif action == 'delete_norma':
        with transaction.atomic():
            models_to_sync = set(art.model for art in articles if art.model)
            for pm in models_to_sync:
                pm.daily_norm = 0
                pm.save()

            articles.update(daily_norm=0)
            OrderItem.objects.filter(article__in=articles).update(norm=0)

        return JsonResponse({
            'success': True,
            'action': 'delete_norma',
            'updated_article_ids': list(articles.values_list('id', flat=True)),
            'daily_norm': 0,
            'message': "Kunlik norma muvaffaqiyatli o'chirildi!",
        })

    # 5-VARIANT: IKKALASINI BIR VAQTDA SAQLASH (FALLBACK)
    else:
        try:
            daily_norm = int(daily_norm_raw)
            if daily_norm <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': "Kunlik norma soni kiritilishi shart!"}, status=400)

        if not operations_list or len(operations_list) == 0:
            return JsonResponse({'success': False, 'error': "Kamida 1 ta operatsiya kiritilishi shart!"}, status=400)

        cleaned_operations = []
        for idx, op_item in enumerate(operations_list, start=1):
            name = str(op_item.get('name', '')).strip()
            if not name:
                return JsonResponse({'success': False, 'error': f"{idx}-operatsiyaning nomi kiritilishi shart!"}, status=400)
            code = str(op_item.get('code', '')).strip().upper()
            if not code:
                code = name.upper()[:50]

            try:
                seq = int(op_item.get('sequence', idx))
            except (TypeError, ValueError):
                seq = idx

            try:
                price = Decimal(str(op_item.get('price', '0')).strip() or '0')
            except Exception:
                price = Decimal('0.00')

            if price <= Decimal('0.00'):
                return JsonResponse({'success': False, 'error': f"'{name}' operatsiyasining dona narxi 0 dan katta bo'lishi shart!"}, status=400)

            try:
                difficulty = float(op_item.get('difficulty', 1.0))
            except (TypeError, ValueError):
                difficulty = 1.0

            cleaned_operations.append({
                'sequence': seq,
                'name': name,
                'code': code,
                'price': price,
                'difficulty': difficulty,
            })

        # Tartib raqamlari takrorlanmasligini ta'minlash: agar takrorlangan bo'lsa, avtomatik 1, 2, 3... qilib tekislash
        seq_list = [c_op['sequence'] for c_op in cleaned_operations]
        if len(seq_list) != len(set(seq_list)):
            for idx, c_op in enumerate(cleaned_operations, start=1):
                c_op['sequence'] = idx

        with transaction.atomic():
            op_objs = []
            for c_op in cleaned_operations:
                op_obj, _ = Operation.objects.get_or_create(
                    name__iexact=c_op['name'],
                    defaults={
                        'code': c_op['code'],
                        'name': c_op['name'],
                        'default_difficulty': c_op['difficulty'],
                        'order_number': c_op['sequence'],
                    }
                )
                op_objs.append((op_obj, c_op))

            keep_op_ids = [op_obj.id for op_obj, _ in op_objs]
            models_to_sync = set()

            for art in articles:
                art.daily_norm = daily_norm
                art.save()
                OrderItem.objects.filter(article=art).update(norm=daily_norm)
                if art.model:
                    art.model.daily_norm = daily_norm
                    art.model.save()
                    models_to_sync.add(art.model)

                for op_obj, c_op in op_objs:
                    ArticleOperation.objects.update_or_create(
                        article=art,
                        operation=op_obj,
                        defaults={
                            'sequence': c_op['sequence'],
                            'price_per_unit': c_op['price'],
                            'difficulty': c_op['difficulty'],
                        }
                    )
                # Faqat biletlarga bog'lanmagan ortiqcha operatsiyalarni o'chirish
                to_delete = art.article_operations.exclude(operation_id__in=keep_op_ids)
                for ao in to_delete:
                    if not ao.tickets.exists():
                        ao.delete()

            for pm in models_to_sync:
                for op_obj, c_op in op_objs:
                    ProductModelOperation.objects.update_or_create(
                        model=pm,
                        operation=op_obj,
                        defaults={
                            'sequence': c_op['sequence'],
                            'price_per_unit': c_op['price'],
                            'difficulty': c_op['difficulty'],
                        }
                    )
                pm.model_operations.exclude(operation_id__in=keep_op_ids).delete()

        return JsonResponse({
            'success': True,
            'action': 'save_all',
            'updated_article_ids': list(articles.values_list('id', flat=True)),
            'daily_norm': daily_norm,
            'operations_count': len(cleaned_operations),
            'operations': [{
                'sequence': o['sequence'],
                'name': o['name'],
                'code': o['code'],
                'price': float(o['price']),
                'difficulty': o['difficulty'],
            } for o in cleaned_operations],
            'unit_total_rate': float(sum(o['price'] for o in cleaned_operations)),
            'message': "Norma va operatsiyalar muvaffaqiyatli saqlandi!",
        })
