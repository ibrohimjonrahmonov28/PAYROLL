"""
Managerlar NEW (/managers-new/, egasi talabi 2026-11-11): menejer zakaz list va mato zakazini kiritadi.
- Menejer faqat o'zi ochgan zakazni ko'radi, tahrirlaydi va statusini qo'yadi. Superadmin hammasini qiladi:
  zakazni boshqa menejerga o'tkazadi, arxivlaydi.
- Har artikul yangi yaratiladi: Article.code = "<menejer yozgan kod> · <zakaz raqami>" (kod butun tizimda takrorlanmas,
  eski artikullar menejerga ko'rsatilmaydi).
- Razmerlar har artikulda alohida (OrderItem.size_labels): bir zakazda kattalar va bolalar artikuli bo'lishi mumkin.
- Kroy boshlangach (Kroyda, Tikuvda, Tugadi, Arxiv) tahrirni faqat superadmin qiladi; o'zgarishlar OrderChangeLog'da.
- Test davrida bu zakazlar (Order.is_new_flow) eski sahifalarda ko'rinmaydi. Sklad va kroy keyingi bosqichlarda.
"""
import datetime
import functools
import uuid
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.models import User
from production.gift_views import _prepare_award_image as prepare_image  # rasmni tekshiradi, 1600 px JPEG qiladi
from production.manager_new_excel import build_fabric_order_xlsx, build_order_list_xlsx
from production.models import (
    Article, Box, CuttingBatch, Order, OrderChangeLog, OrderItem, OrderItemFabric, OrderItemSize,
)

SIZE_PRESETS = (
    ("Kattalar: S (46) – 3XL (56)", "S (46), M (48), L (50), XL (52), 2XL (54), 3XL (56)"),
    ("Bolalar: 110 – 146", "110, 116, 122, 128, 134, 140, 146"),
)
STATUS_LABELS = OrderedDict([
    (Order.Status.DRAFT, "Qoralama"),
    (Order.Status.CONFIRMED, "Tasdiqlandi"),
    (Order.Status.IN_CUTTING, "Kroyda"),
    (Order.Status.IN_SEWING, "Tikuvda"),
    (Order.Status.COMPLETED, "Tugadi"),
    (Order.Status.CANCELLED, "Bekor qilindi"),
    (Order.Status.ARCHIVED, "Arxiv"),
])
LOCKED_STATUSES = {Order.Status.IN_CUTTING, Order.Status.IN_SEWING, Order.Status.COMPLETED, Order.Status.ARCHIVED}
FABRIC_FIELDS = ('id', 'name', 'composition', 'width_cm', 'density_gsm', 'color', 'factory_color_code',
                 'kg_per_piece', 'prosjyot_percent', 'raw_percent', 'export_date')


def manager_new_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('root_login')}?next={request.path}")
        if not request.user.is_manager():
            messages.error(request, "Bu bo'lim faqat menejer va superadmin uchun.")
            return redirect('root_login')
        return view(request, *args, **kwargs)
    return wrapper


def _get_order(request, order_id):
    """Menejer faqat o'z zakazini ochadi (boshqasiniki 404), superadmin hammasini."""
    order = get_object_or_404(Order.objects.select_related('manager'), id=order_id, is_new_flow=True)
    if not (request.user.is_superadmin() or order.manager_id == request.user.id):
        raise Http404("Zakaz topilmadi")
    return order


def _can_edit(user, order):
    return user.is_superadmin() or order.status not in LOCKED_STATUSES


def _log(order, user, message):
    OrderChangeLog.objects.create(order=order, user=user, message=message[:300])


def _parse_sizes(text):
    sizes = []
    for part in (text or '').split(','):
        size = part.strip()
        if size and size not in sizes:
            sizes.append(size)
    return sizes


def _parse_int(value, label, errors, required=False):
    text = str(value or '').strip().replace(' ', '')
    if not text:
        if required:
            errors.append(f"{label}ni kiriting")
        return None
    if not text.isdigit():
        errors.append(f"{label} butun son bo'lishi kerak")
        return None
    return int(text)


def _parse_decimal(value, label, errors, required=False):
    text = str(value or '').strip().replace(' ', '').replace(',', '.')
    if not text:
        if required:
            errors.append(f"{label}ni kiriting")
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        errors.append(f"{label} son bo'lishi kerak")
        return None
    if number < 0:
        errors.append(f"{label} manfiy bo'lmaydi")
        return None
    return number


def _parse_date(value, label, errors, required=False):
    text = (value or '').strip()
    if not text:
        if required:
            errors.append(f"{label}ni tanlang")
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        errors.append(f"{label} noto'g'ri")
        return None


def _replace_image(field_file, content, prefix):
    """Eski faylni o'chirib, yangisini noyob nom bilan saqlaydi (nginx /media/ ni 7 kun keshlaydi)."""
    if field_file:
        field_file.delete(save=False)
    field_file.save(f"{prefix}_{uuid.uuid4().hex[:12]}.jpg", content, save=False)


def _delete_item_files(item):
    for image in [item.article.image, item.print_image] + [f.lab_card_image for f in item.fabrics.all()]:
        if image:
            image.delete(save=False)


def _full_article_code(code, order_number):
    return f"{code} · {order_number}"


def _has_production(order, item=None):
    """Kesilgan yoki qutisi bor artikul/zakaz o'chirilmaydi: Article o'chsa qutilar va stikerlar ham o'chadi (oltin qoida)."""
    if item is not None:
        return Box.objects.filter(article_id=item.article_id).exists() or item.cutting_batches.exists()
    return Box.objects.filter(order=order).exists() or CuttingBatch.objects.filter(order_item__order=order).exists()


def _recount_total(order):
    order.total_quantity = order.items.aggregate(s=Sum('quantity'))['s'] or 0
    order.save(update_fields=['total_quantity'])


@manager_new_required
def order_list(request):
    orders = Order.objects.filter(is_new_flow=True).select_related('manager').annotate(
        items_count=Count('items', distinct=True))
    if not request.user.is_superadmin():
        orders = orders.filter(manager=request.user)
    elif request.GET.get('manager', '').isdigit():
        orders = orders.filter(manager_id=int(request.GET['manager']))

    status = request.GET.get('status', '')
    if status in STATUS_LABELS:
        orders = orders.filter(status=status)
    else:
        orders = orders.exclude(status=Order.Status.ARCHIVED)
    search = request.GET.get('q', '').strip()
    if search:
        orders = orders.filter(
            Q(order_number__icontains=search) | Q(client_name__icontains=search) | Q(items__article_code__icontains=search)
        ).distinct()

    return render(request, 'managers_new/order_list.html', {
        'orders': orders.order_by('-created_at'),
        'status': status,
        'search': search,
        'status_labels': STATUS_LABELS,
        'managers': User.objects.filter(role=User.Role.MANAGER, is_active=True).order_by('first_name', 'username')
        if request.user.is_superadmin() else [],
        'manager_filter': request.GET.get('manager', ''),
    })


@manager_new_required
def order_form(request, order_id=None):
    order = _get_order(request, order_id) if order_id else None
    if order and not _can_edit(request.user, order):
        messages.error(request, "Kroy boshlangan zakazni faqat superadmin o'zgartira oladi.")
        return redirect('managers_new:order_detail', order_id=order.id)

    values = {
        'order_number': order.order_number if order else '',
        'client_name': order.client_name if order else '',
        'order_date': order.order_date.isoformat() if order and order.order_date else datetime.date.today().isoformat(),
        'export_dates': order.export_dates if order else '',
    }
    if request.method == 'POST':
        values = {key: (request.POST.get(key) or '').strip() for key in values}
        errors = []
        number = values['order_number']
        order_date = _parse_date(values['order_date'], "Buyurtma sanasi", errors, required=True)
        if not number:
            errors.append("Zakaz raqamini yozing")
        elif Order.objects.filter(order_number__iexact=number).exclude(pk=getattr(order, 'pk', None)).exists():
            errors.append(f"«{number}» raqamli zakaz tizimda allaqachon bor")
        if not values['client_name']:
            errors.append("Mijozni yozing")
        if order:
            long_codes = [i.article_code for i in order.items.all() if len(_full_article_code(i.article_code, number)) > 50]
            if long_codes:
                errors.append("Zakaz raqami juda uzun: artikul kodi bilan 50 belgidan oshadi")

        if errors:
            messages.error(request, '. '.join(errors) + '.')
        else:
            created = order is None
            with transaction.atomic():
                if created:
                    order = Order(is_new_flow=True, manager=request.user, status=Order.Status.DRAFT)
                old_number = order.order_number
                order.order_number = number
                order.client_name = values['client_name']
                order.order_date = order_date
                order.export_dates = values['export_dates']
                order.save()
                if not created:
                    if old_number != number:
                        for item in order.items.select_related('article'):
                            item.article.code = _full_article_code(item.article_code, number)
                            item.article.save(update_fields=['code'])
                _log(order, request.user, "Zakaz yaratildi" if created else "Zakaz ma'lumotlari o'zgartirildi")
            messages.success(request, f"«{order.order_number}» zakazi saqlandi.")
            return redirect('managers_new:order_detail', order_id=order.id)

    return render(request, 'managers_new/order_form.html', {
        'order': order,
        'values': values,
    })


@manager_new_required
def order_detail(request, order_id):
    order = _get_order(request, order_id)
    rows = []
    by_model = OrderedDict()
    totals = {'qty': 0, 'prosjyot': 0, 'finished_kg': Decimal('0'), 'raw_kg': Decimal('0')}
    for item in order.items.select_related('article').prefetch_related('sizes', 'fabrics').order_by('id'):
        size_map = {s.size_name: s.planned_quantity for s in item.sizes.all()}
        fabrics = list(item.fabrics.all())
        finished = sum((f.finished_kg for f in fabrics), Decimal('0'))
        raw = sum((f.raw_kg for f in fabrics), Decimal('0'))
        rows.append({'item': item, 'sizes': [(s, size_map.get(s, 0)) for s in item.size_list], 'fabrics': fabrics,
                     'finished_kg': finished, 'raw_kg': raw})
        name = item.article.name
        by_model[name] = by_model.get(name, 0) + item.quantity
        totals['qty'] += item.quantity
        totals['prosjyot'] += item.prosjyot or 0
        totals['finished_kg'] += finished
        totals['raw_kg'] += raw

    is_super = request.user.is_superadmin()
    status_choices = [(k, v) for k, v in STATUS_LABELS.items() if is_super or k != Order.Status.ARCHIVED]
    return render(request, 'managers_new/order_detail.html', {
        'order': order,
        'rows': rows,
        'by_model': by_model,
        'totals': totals,
        'can_edit': _can_edit(request.user, order),
        'status_label': STATUS_LABELS.get(order.status, order.get_status_display()),
        'status_choices': status_choices,
        'managers': User.objects.filter(role=User.Role.MANAGER, is_active=True).order_by('first_name', 'username')
        if is_super else [],
        'logs': order.change_logs.select_related('user')[:30],
    })


@manager_new_required
def order_status(request, order_id):
    order = _get_order(request, order_id)
    if request.method != 'POST':
        return redirect('managers_new:order_detail', order_id=order.id)
    new_status = request.POST.get('status', '')
    is_super = request.user.is_superadmin()
    if new_status not in STATUS_LABELS or (new_status == Order.Status.ARCHIVED and not is_super):
        messages.error(request, "Bu statusni qo'yib bo'lmaydi.")
    elif order.status == Order.Status.ARCHIVED and not is_super:
        messages.error(request, "Arxivdagi zakazni faqat superadmin qaytaradi.")
    elif new_status != Order.Status.DRAFT and not order.items.exists():
        messages.error(request, "Avval kamida bitta artikul qo'shing.")
    elif new_status != order.status:
        old_label = STATUS_LABELS.get(order.status, order.status)
        order.status = new_status
        order.save(update_fields=['status'])
        _log(order, request.user, f"Status: {old_label} → {STATUS_LABELS[new_status]}")
        messages.success(request, f"Status: {STATUS_LABELS[new_status]}.")
    return redirect('managers_new:order_detail', order_id=order.id)


@manager_new_required
def order_reassign(request, order_id):
    order = _get_order(request, order_id)
    if request.method != 'POST' or not request.user.is_superadmin():
        return redirect('managers_new:order_detail', order_id=order.id)
    manager = get_object_or_404(User, id=request.POST.get('manager_id'), role=User.Role.MANAGER)
    if manager.id != order.manager_id:
        old_name = order.manager.get_full_name() or order.manager.username if order.manager else "—"
        order.manager = manager
        order.save(update_fields=['manager'])
        new_name = manager.get_full_name() or manager.username
        _log(order, request.user, f"Menejer: {old_name} → {new_name}")
        messages.success(request, f"Zakaz {new_name} ga o'tkazildi.")
    return redirect('managers_new:order_detail', order_id=order.id)


@manager_new_required
def order_delete(request, order_id):
    order = _get_order(request, order_id)
    if request.method != 'POST':
        return redirect('managers_new:order_detail', order_id=order.id)
    if order.status != Order.Status.DRAFT or _has_production(order):
        messages.error(request, "Faqat qoralama (kesilmagan) zakazni o'chirish mumkin.")
        return redirect('managers_new:order_detail', order_id=order.id)
    number = order.order_number
    with transaction.atomic():
        items = list(order.items.select_related('article').prefetch_related('fabrics'))
        for item in items:
            _delete_item_files(item)
        Article.objects.filter(id__in=[i.article_id for i in items]).delete()
        order.delete()
    messages.success(request, f"«{number}» zakazi o'chirildi.")
    return redirect('managers_new:order_list')


def _fabric_rows_from_post(request):
    rows = []
    for idx in request.POST.getlist('fabric_row'):
        row = {key: (request.POST.get(f'fabric-{idx}-{key}') or '').strip() for key in FABRIC_FIELDS}
        row['idx'] = idx
        row['lab_card'] = request.FILES.get(f'fabric-{idx}-lab_card')
        if any(row[key] for key in FABRIC_FIELDS if key not in ('id', 'raw_percent')) or row['lab_card']:
            rows.append(row)
    return rows


def _plain(value):
    """Formada ko'rsatish: Decimal oxiridagi nollarsiz (0.2550 -> 0.255, 10.00 -> 10)."""
    if value is None:
        return ''
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    return str(value)


def _fabric_rows_from_item(item):
    rows = []
    for fabric in item.fabrics.all():
        row = {key: _plain(getattr(fabric, key)) for key in FABRIC_FIELDS}
        row['export_date'] = fabric.export_date.isoformat() if fabric.export_date else ''
        row['idx'] = str(fabric.id)
        row['lab_card_url'] = fabric.lab_card_image.url if fabric.lab_card_image else ''
        rows.append(row)
    return rows


@manager_new_required
def article_form(request, order_id, item_id=None):
    order = _get_order(request, order_id)
    item = get_object_or_404(OrderItem.objects.select_related('article'), id=item_id, order=order) if item_id else None
    if not _can_edit(request.user, order):
        messages.error(request, "Kroy boshlangan zakazni faqat superadmin o'zgartira oladi.")
        return redirect('managers_new:order_detail', order_id=order.id)

    size_map = {s.size_name: s.planned_quantity for s in item.sizes.all()} if item else {}
    last_item = order.items.exclude(size_labels='').order_by('-id').first()
    values = {
        'article_code': item.article_code if item else '',
        'model_name': item.article.name if item else '',
        'prosjyot': str(item.prosjyot or '') if item else '',
        # yangi artikulga shu zakazning oxirgi artikuli razmerlari taklif qilinadi (o'zgartirsa bo'ladi)
        'size_labels': item.size_labels if item else (last_item.size_labels if last_item else SIZE_PRESETS[0][1]),
    }
    sizes = _parse_sizes(values['size_labels'])
    size_values = [str(size_map.get(s, '')) for s in sizes]
    fabric_rows = _fabric_rows_from_item(item) if item else []

    if request.method == 'POST':
        values = {key: (request.POST.get(key) or '').strip() for key in values}
        sizes = _parse_sizes(values['size_labels'])
        size_values = [(request.POST.get(f'size-{i}') or '').strip() for i in range(len(sizes))]
        fabric_rows = _fabric_rows_from_post(request)
        existing_fabrics = {str(f.id): f for f in item.fabrics.all()} if item else {}
        for row in fabric_rows:
            fabric = existing_fabrics.get(row['id'])
            row['lab_card_url'] = fabric.lab_card_image.url if fabric and fabric.lab_card_image else ''
        errors = []

        code = values['article_code']
        if not code:
            errors.append("Artikul kodini yozing")
        elif order.items.filter(article_code__iexact=code).exclude(pk=getattr(item, 'pk', None)).exists():
            errors.append(f"«{code}» artikuli bu zakazda allaqachon bor")
        elif len(_full_article_code(code, order.order_number)) > 50:
            errors.append("Artikul kodi juda uzun")
        elif Article.objects.filter(code=_full_article_code(code, order.order_number)).exclude(
                pk=getattr(item, 'article_id', None)).exists():
            errors.append(f"«{code}» artikuli tizimda allaqachon bor")
        if not values['model_name']:
            errors.append("Model nomini yozing")
        if not sizes:
            errors.append("Kamida bitta razmer kiriting")
        elif len(', '.join(sizes)) > 300 or any(len(s) > 50 for s in sizes):
            errors.append("Razmerlar juda uzun")
        elif item:
            cut = item.sizes.exclude(size_name__in=sizes).filter(cutting_items__isnull=False)
            removed = sorted(set(cut.values_list('size_name', flat=True)))
            if removed:
                errors.append(f"{', '.join(removed)} razmeri kesilgan — uni olib tashlab bo'lmaydi")
        quantities = [_parse_int(v, f"{s} soni", errors) or 0 for s, v in zip(sizes, size_values)]
        total = sum(quantities)
        if sizes and not total:
            errors.append("Kamida bitta razmerga son yozing")
        prosjyot = _parse_int(values['prosjyot'], "Просчет", errors)
        if prosjyot is None:
            prosjyot = total
        elif prosjyot < total:
            errors.append(f"Просчет ({prosjyot}) zakaz sonidan ({total}) kam bo'lmasligi kerak")

        model_upload = request.FILES.get('model_image')
        print_upload = request.FILES.get('print_image')
        if not model_upload and not (item and item.article.image):
            errors.append("Model rasmini yuklang")
        if not fabric_rows:
            errors.append("Kamida bitta mato qismini kiriting")
        parsed_fabrics = []
        for number, row in enumerate(fabric_rows, start=1):
            row_errors = []
            if not row['name']:
                row_errors.append("mato nomini yozing")
            kg = _parse_decimal(row['kg_per_piece'], "1 dona uchun kg", row_errors, required=True)
            if kg is not None and kg <= 0:
                row_errors.append("1 dona uchun kg 0 dan katta bo'lsin")
            percent = _parse_decimal(row['prosjyot_percent'], "Prosjyot %", row_errors, required=True)
            raw_percent = _parse_decimal(row['raw_percent'] or '10', "Xom %", row_errors)
            width = _parse_int(row['width_cm'], "Eni", row_errors)
            density = _parse_int(row['density_gsm'], "Gr/m2", row_errors)
            export_date = _parse_date(row['export_date'], "Eksport sanasi", row_errors)
            if row['id'] and row['id'] not in existing_fabrics:
                row_errors.append("mato qatori topilmadi")
            if row_errors:
                errors.append(f"{number}-mato: {', '.join(row_errors)}")
            parsed_fabrics.append((row, {
                'name': row['name'], 'composition': row['composition'], 'width_cm': width, 'density_gsm': density,
                'color': row['color'], 'factory_color_code': row['factory_color_code'], 'kg_per_piece': kg,
                'prosjyot_percent': percent, 'raw_percent': raw_percent if raw_percent is not None else Decimal('10'),
                'export_date': export_date,
            }))

        images = {}
        if not errors:
            try:
                if model_upload:
                    images['model'] = prepare_image(model_upload)
                if print_upload:
                    images['print'] = prepare_image(print_upload)
                for row, _ in parsed_fabrics:
                    if row['lab_card']:
                        images[f"lab-{row['idx']}"] = prepare_image(row['lab_card'])
            except ValueError as exc:
                errors.append(f"{exc} (JPG yoki PNG yuklang)")

        if errors:
            messages.error(request, '. '.join(errors) + '. Rasmlarni qaytadan tanlang.')
        else:
            created = item is None
            old_total = 0 if created else item.quantity
            with transaction.atomic():
                article = Article() if created else item.article
                article.code = _full_article_code(code, order.order_number)
                article.name = values['model_name']
                if 'model' in images:
                    _replace_image(article.image, images['model'], 'model')
                article.save()

                if created:
                    item = OrderItem(order=order, article=article)
                item.article_code = code
                item.quantity = total
                item.prosjyot = prosjyot
                item.size_labels = ', '.join(sizes)
                if 'print' in images:
                    _replace_image(item.print_image, images['print'], 'print')
                elif request.POST.get('remove_print') == '1' and item.print_image:
                    item.print_image.delete(save=False)
                item.save()

                for size, qty in zip(sizes, quantities):
                    OrderItemSize.objects.update_or_create(order_item=item, size_name=size, defaults={'planned_quantity': qty})
                item.sizes.exclude(size_name__in=sizes).filter(cutting_items__isnull=True).delete()

                keep_ids = []
                for row, data in parsed_fabrics:
                    fabric = existing_fabrics.get(row['id']) or OrderItemFabric(order_item=item)
                    for key, value in data.items():
                        setattr(fabric, key, value)
                    if f"lab-{row['idx']}" in images:
                        _replace_image(fabric.lab_card_image, images[f"lab-{row['idx']}"], 'lab')
                    fabric.save()
                    keep_ids.append(fabric.id)
                for fabric in item.fabrics.exclude(id__in=keep_ids):
                    if fabric.lab_card_image:
                        fabric.lab_card_image.delete(save=False)
                    fabric.delete()

                _recount_total(order)
                if created:
                    _log(order, request.user, f"Artikul qo'shildi: {code} ({total} dona)")
                elif old_total != total:
                    _log(order, request.user, f"Artikul o'zgartirildi: {code} ({old_total} → {total} dona)")
                else:
                    _log(order, request.user, f"Artikul o'zgartirildi: {code}")
            messages.success(request, f"«{code}» artikuli saqlandi.")
            return redirect(f"{reverse('managers_new:order_detail', args=[order.id])}#item-{item.id}")

    return render(request, 'managers_new/article_form.html', {
        'order': order,
        'item': item,
        'values': values,
        'size_rows': list(zip(range(len(sizes)), sizes, size_values)),
        'size_presets': SIZE_PRESETS,
        'fabric_rows': fabric_rows,
    })


@manager_new_required
def article_delete(request, order_id, item_id):
    order = _get_order(request, order_id)
    item = get_object_or_404(OrderItem.objects.select_related('article'), id=item_id, order=order)
    if request.method != 'POST':
        return redirect('managers_new:order_detail', order_id=order.id)
    if not _can_edit(request.user, order):
        messages.error(request, "Kroy boshlangan zakazni faqat superadmin o'zgartira oladi.")
        return redirect('managers_new:order_detail', order_id=order.id)
    if _has_production(order, item):
        messages.error(request, "Bu artikul kesilgan — uni o'chirib bo'lmaydi.")
        return redirect('managers_new:order_detail', order_id=order.id)
    code = item.article_code
    with transaction.atomic():
        _delete_item_files(item)
        item.article.delete()   # OrderItem, razmerlar va mato qismlari ham o'chadi
        _recount_total(order)
        _log(order, request.user, f"Artikul o'chirildi: {code}")
    messages.success(request, f"«{code}» artikuli o'chirildi.")
    return redirect('managers_new:order_detail', order_id=order.id)


def _xlsx_response(content, filename):
    response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _safe_name(order):
    return ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in order.order_number)


@manager_new_required
def export_order_list(request, order_id):
    order = _get_order(request, order_id)
    return _xlsx_response(build_order_list_xlsx(order), f"zakaz_list_{_safe_name(order)}.xlsx")


@manager_new_required
def export_fabric_order(request, order_id):
    order = _get_order(request, order_id)
    return _xlsx_response(build_fabric_order_xlsx(order), f"mato_zakazi_{_safe_name(order)}.xlsx")
