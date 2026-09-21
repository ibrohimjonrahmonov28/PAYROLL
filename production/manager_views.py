from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Max
from .models import Customer, ProductModel, Article, Order, OrderItem, OrderItemSize, CuttingBatch, CuttingBatchItem


def manager_required(view_func):
    """Menejer yoki Superadmin uchun ruxsat tekshiruvi"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('root_login')
        if not (getattr(request.user, 'is_manager', lambda: False)() or request.user.is_superadmin()):
            messages.error(request, "Ushbu bo'limga faqat menejerlar va superadminlar kira oladi!")
            return redirect('production:order_list')
        return view_func(request, *args, **kwargs)
    return wrapper


@manager_required
def manager_dashboard(request):
    """
    Menejerlar Bosh Sahifasi:
    - Barcha faol buyurtmalar ro'yxati.
    - Har bir buyurtmada: Mijoz, Model, Reja soni, Kesilish foizi va holati.
    """
    search_q = request.GET.get('q', '').strip()
    orders_qs = Order.objects.all().select_related('customer', 'article').prefetch_related(
        'items__article__model',
        'items__sizes__cutting_items',
        'items__cutting_batches__items'
    ).order_by('-created_at')

    if search_q:
        orders_qs = orders_qs.filter(
            Q(order_number__icontains=search_q) |
            Q(client_name__icontains=search_q) |
            Q(customer__name__icontains=search_q) |
            Q(items__article__name__icontains=search_q) |
            Q(items__article__code__icontains=search_q)
        ).distinct()

    orders_data = []
    for ord_obj in orders_qs:
        total_planned = 0
        total_cut = 0
        sizes_set = set()

        for item in ord_obj.items.all():
            total_planned += item.total_planned_quantity
            total_cut += item.total_cut_quantity
            for s in item.sizes.all():
                sizes_set.add(s.size_name)

        overall_pct = round((total_cut / total_planned) * 100, 1) if total_planned > 0 else 0.0

        orders_data.append({
            'order': ord_obj,
            'total_planned': total_planned,
            'total_cut': total_cut,
            'overall_cut_percentage': overall_pct,
            'sizes_str': ", ".join(sorted(sizes_set)) if sizes_set else "—",
            'is_cut_complete': (total_planned > 0 and total_cut >= total_planned),
        })

    return render(request, 'managers/dashboard.html', {
        'orders_data': orders_data,
        'search_q': search_q,
    })


@manager_required
def manager_order_create(request):
    """
    Menejer Yangi Zakaz Yaratish Sahifasi:
    - Zakazchi (Mijoz)
    - Zakaz raqami va topshirish muddati
    - Model (ProductModel) tanlash
    - Artikul(lar) va har bir artikul uchun kiyim razmerlari (S, M, L, XL...) hamda ularning reja soni.
    - Menejer buni kiritib saqlashi bilan uning ishi tugaydi va buyurtma Kesim bo'limiga uzatiladi.
    """
    customers = Customer.objects.all().order_by('name')
    product_models = ProductModel.objects.all().prefetch_related('articles').order_by('name')

    if request.method == 'POST':
        order_number = request.POST.get('order_number', '').strip()
        client_name = request.POST.get('client_name', '').strip()
        customer_id = request.POST.get('customer_id')
        deadline = request.POST.get('deadline') or None
        product_model_id = request.POST.get('product_model_id')

        # Artikul va Razmerlar ma'lumotlari
        article_codes = request.POST.getlist('article_code[]')
        article_names = request.POST.getlist('article_name[]')

        if not order_number or not client_name:
            messages.error(request, "Zakaz raqami va buyurtmachi nomini kiritish majburiy!")
            return render(request, 'managers/order_create.html', {
                'customers': customers,
                'product_models': product_models,
            })

        if Order.objects.filter(order_number=order_number).exists():
            messages.error(request, f"'{order_number}' raqamli zakaz allaqachon mavjud!")
            return render(request, 'managers/order_create.html', {
                'customers': customers,
                'product_models': product_models,
            })

        customer = None
        if customer_id:
            customer = Customer.objects.filter(id=customer_id).first()
        if not customer and client_name:
            customer, _ = Customer.objects.get_or_create(name=client_name)

        product_model = None
        if product_model_id:
            product_model = ProductModel.objects.filter(id=product_model_id).first()

        try:
            with transaction.atomic():
                order = Order.objects.create(
                    order_number=order_number,
                    customer=customer,
                    client_name=customer.name if customer else client_name,
                    deadline=deadline,
                    status=Order.Status.IN_PROGRESS
                )

                total_order_qty = 0

                # Har bir artikulni qo'shish
                for i, art_code in enumerate(article_codes):
                    art_code = art_code.strip().upper()
                    if not art_code:
                        continue
                    art_name = article_names[i].strip() if i < len(article_names) else art_code

                    norm_val = product_model.daily_norm if (product_model and product_model.daily_norm) else 1000

                    article_image = request.FILES.get(f'article_image_{i}')

                    article, created = Article.objects.get_or_create(
                        code=art_code,
                        defaults={
                            'name': art_name,
                            'daily_norm': norm_val,
                            'model': product_model,
                            'image': article_image,
                        }
                    )
                    update_fields = []
                    if product_model and article.model != product_model:
                        article.model = product_model
                        update_fields.append('model')
                    if art_name and article.name != art_name:
                        article.name = art_name
                        update_fields.append('name')
                    if article_image:
                        article.image = article_image
                        update_fields.append('image')
                    if update_fields:
                        article.save(update_fields=update_fields)

                    # Ushbu artikul uchun razmerlar
                    # Parametrlar: size_name_0[], size_qty_0[]
                    size_names = request.POST.getlist(f'size_name_{i}[]')
                    size_qtys = request.POST.getlist(f'size_qty_{i}[]')

                    art_total_qty = 0
                    parsed_sizes = []
                    for s_idx, s_name in enumerate(size_names):
                        s_name = s_name.strip().upper()
                        if not s_name:
                            continue
                        try:
                            s_qty = max(0, int(size_qtys[s_idx])) if s_idx < len(size_qtys) else 0
                        except (ValueError, TypeError):
                            s_qty = 0

                        if s_qty > 0:
                            parsed_sizes.append((s_name, s_qty))
                            art_total_qty += s_qty

                    # Agar razmer kiritilmagan bo'lsa, umumiy artikul soni fallback
                    if art_total_qty == 0:
                        fallback_qty = request.POST.get(f'article_qty_{i}', '0').strip()
                        try:
                            art_total_qty = max(0, int(fallback_qty))
                        except (ValueError, TypeError):
                            art_total_qty = 0

                    order_item = OrderItem.objects.create(
                        order=order,
                        article=article,
                        quantity=art_total_qty,
                        norm=norm_val
                    )

                    for s_name, s_qty in parsed_sizes:
                        OrderItemSize.objects.create(
                            order_item=order_item,
                            size_name=s_name,
                            planned_quantity=s_qty
                        )

                    total_order_qty += art_total_qty

                # Buyurtmaning umumiy soni va birinchi artikulini biriktirish
                order.total_quantity = total_order_qty
                first_item = order.items.first()
                if first_item:
                    order.article = first_item.article
                order.save()

            messages.success(
                request,
                f"'{order.order_number}' zakazi muvaffaqiyatli yaratildi! Jami reja: {total_order_qty} dona. Kesim bo'limiga uzatildi."
            )
            return redirect('manager_order_detail', order_id=order.id)

        except Exception as e:
            messages.error(request, f"Zakaz yaratishda xatolik yuz berdi: {str(e)}")
            return render(request, 'managers/order_create.html', {
                'customers': customers,
                'product_models': product_models,
            })

    return render(request, 'managers/order_create.html', {
        'customers': customers,
        'product_models': product_models,
    })


@manager_required
def manager_order_detail(request, order_id: int):
    """
    Menejer uchun Zakaz Tafsilotlari va Razmerlar Monitoringi:
    - Har bir artikul va uning razmerlari
    - Rejalashtirilgan soni
    - Kesim bo'limida qancha kesilgani va kesilish foizi
    - Kesim partiyalari tarixi (Kesim 1, Kesim 2...)
    """
    order = get_object_or_404(
        Order.objects.select_related('customer', 'article').prefetch_related(
            'items__article__model',
            'items__sizes__cutting_items',
            'items__cutting_batches__items__order_item_size'
        ),
        id=order_id
    )

    items_data = []
    total_planned_order = 0
    total_cut_order = 0

    for item in order.items.all():
        sizes_data = []
        for s in item.sizes.all():
            sizes_data.append({
                'size': s,
                'planned': s.planned_quantity,
                'cut': s.total_cut_quantity,
                'real': s.total_real_quantity,
                'percentage': s.cut_percentage,
                'remaining': s.remaining_to_cut_quantity,
                'excess': s.excess_cut_quantity,
            })
            total_planned_order += s.planned_quantity
            total_cut_order += s.total_cut_quantity

        batches_data = []
        for batch in item.cutting_batches.all():
            batches_data.append({
                'batch': batch,
                'name': batch.name,
                'batch_number': batch.batch_number,
                'cutter_name': batch.cutter_name,
                'created_at': batch.created_at,
                'total_quantity': batch.total_quantity,
                'items': batch.items.all(),
            })

        items_data.append({
            'item': item,
            'sizes': sizes_data,
            'batches': batches_data,
            'planned_qty': item.total_planned_quantity,
            'cut_qty': item.total_cut_quantity,
            'cut_percentage': item.overall_cut_percentage,
        })

    overall_order_pct = round((total_cut_order / total_planned_order) * 100, 1) if total_planned_order > 0 else 0.0

    return render(request, 'managers/order_detail.html', {
        'order': order,
        'items_data': items_data,
        'total_planned_order': total_planned_order,
        'total_cut_order': total_cut_order,
        'overall_order_pct': overall_order_pct,
    })

