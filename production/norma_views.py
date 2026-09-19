from decimal import Decimal
import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Q, Count

from .models import ProductModel, ProductModelOperation, Article, ArticleOperation, Operation, DailyModelProgress
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
        code = request.POST.get('code', '').strip().upper()
        name = request.POST.get('name', '').strip()
        order_number_str = request.POST.get('order_number', '1').strip()
        difficulty_str = request.POST.get('default_difficulty', '1.0').strip()
        description = request.POST.get('description', '').strip()

        try:
            order_number = int(order_number_str) if order_number_str else 1
            difficulty = float(difficulty_str) if difficulty_str else 1.0
        except ValueError:
            order_number = 1
            difficulty = 1.0

        if not code or not name:
            messages.error(request, "Operatsiya kodi va nomi to'ldirilishi shart!")
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

