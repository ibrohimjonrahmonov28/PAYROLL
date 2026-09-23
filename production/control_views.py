import json
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from accounts.models import User
from production.models import Box, BoxQualityInspectionLog


def _is_control_authorized(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role in [User.Role.CONTROL, User.Role.SUPER_ADMIN, User.Role.ADMIN]


@login_required
def control_home_view(request):
    """
    Sifat Nazorati (OTK / Control) planshet interfeysi.
    Faqat CONTROL, SUPER_ADMIN va ADMIN foydalanuvchilariga ruxsat beriladi.
    """
    if not _is_control_authorized(request.user):
        return redirect('root_login')

    today = timezone.localdate()
    today_inspections = BoxQualityInspectionLog.objects.filter(
        created_at__date=today
    ).select_related('box', 'box__order', 'box__article', 'inspector').order_by('-created_at')[:15]

    today_count = BoxQualityInspectionLog.objects.filter(created_at__date=today).count()

    return render(request, 'production/control_home.html', {
        'inspector': request.user,
        'today_inspections': today_inspections,
        'today_count': today_count,
        'today_date': today,
    })


@require_http_methods(["GET", "POST"])
def control_box_lookup_api(request):
    """
    Skanerlangan QR/shtrix-kod bo'yicha qutini qidirib topish API.
    Kiritilgan kod CONTROL:BOX_CODE, BOX:BOX_CODE, yoki shunchaki BOX_CODE bo'lishi mumkin.
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            raw_code = data.get('code', '')
        except Exception:
            raw_code = request.POST.get('code', '')
    else:
        raw_code = request.GET.get('code', '')

    raw_code = str(raw_code).strip()
    if not raw_code:
        return JsonResponse({'status': 'ERROR', 'message': "Iltimos, quti kodini kiriting yoki skaner qiling!"}, status=400)

    # Prefikslarni tozalash (CONTROL:, BOX:, TICKET:, QUTI #, #)
    cleaned = raw_code.upper()
    for prefix in ['CONTROL:', 'BOX:', 'TICKET:', 'QUTI #', 'QUTI#', 'QUTI:', '#']:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Bazadan qidirish
    box = Box.objects.filter(box_code__iexact=cleaned).select_related('order', 'article').first()

    if not box and cleaned.isdigit():
        num = int(cleaned)
        box = Box.objects.filter(id=num).select_related('order', 'article').first()
        if not box:
            box = Box.objects.filter(box_number=num).select_related('order', 'article').first()

    if not box:
        return JsonResponse({
            'status': 'NOT_FOUND',
            'message': f"'{raw_code}' kodi bo'yicha quti topilmadi! Qaytadan tekshirib ko'ring."
        }, status=404)

    art = box.target_article
    image_url = None
    if art and art.image:
        try:
            image_url = art.image.url
        except Exception:
            image_url = None

    is_repair_mode = (box.controlled_repair_qty > 0)

    return JsonResponse({
        'status': 'OK',
        'box': {
            'id': box.id,
            'box_code': box.box_code,
            'box_number': box.box_number,
            'order_number': box.order.order_number if box.order else "—",
            'article_code': art.code if art else "N/A",
            'article_name': art.name if art else "Model",
            'image_url': image_url,
            'razmer': box.razmer or "—",
            'pastal_code': box.pastal_code or "—",
            'quantity': box.quantity,
            'is_controlled': box.is_controlled,
            'controlled_first_sort_qty': box.controlled_first_sort_qty,
            'controlled_second_sort_qty': box.controlled_second_sort_qty,
            'controlled_repair_qty': box.controlled_repair_qty,
            'controlled_defect_qty': box.controlled_defect_qty,
            'mode': 'REPAIR_RETURN' if is_repair_mode else 'INITIAL',
        }
    })


@require_http_methods(["POST"])
def control_submit_inspection_api(request):
    """
    Sifat nazorati natijasini saqlash va tasdiqlash API.
    Birlamchi tekshiruv (INITIAL) yoki Ta'mirdan qaytish (REPAIR_RETURN).
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    box_id = data.get('box_id')
    mode = data.get('mode', 'INITIAL')

    box = get_object_or_404(Box, id=box_id)

    if mode == 'INITIAL':
        # Birlamchi tekshiruv
        try:
            total_qty = int(data.get('total_qty', box.quantity))
            second_sort = int(data.get('second_sort_qty', 0))
            repair_qty = int(data.get('repair_qty', 0))
        except (ValueError, TypeError):
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar to'g'ri butun son formatida bo'lishi kerak!"}, status=400)

        if total_qty < 1:
            return JsonResponse({'status': 'ERROR', 'message': "Qutidagi umumiy son kamida 1 dona bo'lishi shart!"}, status=400)
        if second_sort < 0 or repair_qty < 0:
            return JsonResponse({'status': 'ERROR', 'message': "Sort yoki ta'mir soni manfiy bo'lishi mumkin emas!"}, status=400)
        if second_sort + repair_qty > total_qty:
            return JsonResponse({
                'status': 'ERROR', 
                'message': f"2-sort ({second_sort}) va Ta'mir ({repair_qty}) yig'indisi jami sondan ({total_qty}) oshib ketishi mumkin emas!"
            }, status=400)

        first_sort = total_qty - second_sort - repair_qty
        is_closed = (repair_qty == 0)

        # Qutini yangilash
        box.quantity = total_qty
        box.controlled_first_sort_qty = first_sort
        box.controlled_second_sort_qty = second_sort
        box.controlled_repair_qty = repair_qty
        box.controlled_defect_qty = 0
        box.is_controlled = is_closed
        box.status = Box.Status.COMPLETED if is_closed else Box.Status.IN_PROGRESS
        box.controlled_at = timezone.now()
        box.controlled_by = request.user
        box.save(update_fields=[
            'quantity', 'controlled_first_sort_qty', 'controlled_second_sort_qty',
            'controlled_repair_qty', 'controlled_defect_qty', 'is_controlled',
            'status', 'controlled_at', 'controlled_by'
        ])

        # Jurnalga yozish
        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=request.user,
            action_type=BoxQualityInspectionLog.ActionType.INITIAL,
            inspected_qty=total_qty,
            first_sort_qty=first_sort,
            second_sort_qty=second_sort,
            repair_qty=repair_qty,
            defect_qty=0,
            notes=data.get('notes', '')
        )

        if is_closed:
            msg = f"Quti #{box.box_number} yopildi: {first_sort} ta 1-sort, {second_sort} ta 2-sort. Mahsulot qabul qilindi va Upakovkaga topshiriladi!"
        else:
            msg = f"Quti #{box.box_number} ta'mirga yuborildi: {repair_qty} ta ta'mirga ketdi. Quti qutisi bilan ta'mir bo'limiga qaytariladi."

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': repair_qty,
            'box': {
                'box_code': box.box_code,
                'box_number': box.box_number,
                'first_sort': box.controlled_first_sort_qty,
                'second_sort': box.controlled_second_sort_qty,
                'repair': box.controlled_repair_qty,
                'defect': box.controlled_defect_qty,
                'is_repair_active': (box.controlled_repair_qty > 0),
                'is_closed': is_closed,
            }
        })

    elif mode == 'REPAIR_RETURN':
        # Ta'mirdan qaytib kelgan
        repair_in_hand = box.controlled_repair_qty
        if repair_in_hand <= 0:
            return JsonResponse({'status': 'ERROR', 'message': "Ushbu qutida ta'mirga ketgan ishlar mavjud emas!"}, status=400)

        try:
            defect_qty = int(data.get('defect_qty', 0))
            re_repair_qty = int(data.get('re_repair_qty', 0))
        except (ValueError, TypeError):
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar to'g'ri formatda kiritilishi shart!"}, status=400)

        if defect_qty < 0 or re_repair_qty < 0:
            return JsonResponse({'status': 'ERROR', 'message': "Sonlar manfiy bo'lishi mumkin emas!"}, status=400)
        if defect_qty + re_repair_qty > repair_in_hand:
            return JsonResponse({
                'status': 'ERROR', 
                'message': f"Brak ({defect_qty}) va Qayta ta'mir ({re_repair_qty}) yig'indisi ta'mirdagi sondan ({repair_in_hand}) ko'p bo'lishi mumkin emas!"
            }, status=400)

        fixed_qty = repair_in_hand - defect_qty - re_repair_qty
        is_closed = (re_repair_qty == 0)

        # Qutini yangilash
        box.controlled_first_sort_qty += fixed_qty
        box.controlled_defect_qty += defect_qty
        box.controlled_repair_qty = re_repair_qty
        box.is_controlled = is_closed
        box.status = Box.Status.COMPLETED if is_closed else Box.Status.IN_PROGRESS
        box.controlled_at = timezone.now()
        box.controlled_by = request.user
        box.save(update_fields=[
            'controlled_first_sort_qty', 'controlled_defect_qty',
            'controlled_repair_qty', 'is_controlled', 'status',
            'controlled_at', 'controlled_by'
        ])

        # Jurnalga yozish
        log = BoxQualityInspectionLog.objects.create(
            box=box,
            inspector=request.user,
            action_type=BoxQualityInspectionLog.ActionType.REPAIR_RETURN,
            inspected_qty=repair_in_hand,
            first_sort_qty=fixed_qty,
            second_sort_qty=0,
            repair_qty=re_repair_qty,
            defect_qty=defect_qty,
            notes=data.get('notes', '')
        )

        if is_closed:
            msg = f"Quti #{box.box_number} ta'miri yakunlandi va quti to'liq yopildi: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta brak. Mahsulot Upakovkaga topshiriladi!"
        else:
            msg = f"Quti #{box.box_number}: {fixed_qty} ta 1-sortga qo'shildi, {defect_qty} ta brak, {re_repair_qty} ta qayta ta'mirda qoldi."

        return JsonResponse({
            'status': 'OK',
            'message': msg,
            'is_closed': is_closed,
            'repair_qty': re_repair_qty,
            'box': {
                'box_code': box.box_code,
                'box_number': box.box_number,
                'first_sort': box.controlled_first_sort_qty,
                'second_sort': box.controlled_second_sort_qty,
                'repair': box.controlled_repair_qty,
                'defect': box.controlled_defect_qty,
                'is_repair_active': (box.controlled_repair_qty > 0),
                'is_closed': is_closed,
            }
        })

    else:
        return JsonResponse({'status': 'ERROR', 'message': "Noma'lum tekshiruv rejimi!"}, status=400)


@require_http_methods(["GET"])
def control_recent_inspections_api(request):
    """
    Bugungi oxirgi tekshirilgan qutilar jurnali API.
    """
    if not _is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)

    today = timezone.localdate()
    logs = BoxQualityInspectionLog.objects.filter(
        created_at__date=today
    ).select_related('box', 'box__order', 'box__article', 'inspector').order_by('-created_at')[:20]

    data = []
    for l in logs:
        data.append({
            'id': l.id,
            'box_number': l.box.box_number,
            'box_code': l.box.box_code,
            'order_number': l.box.order.order_number if l.box.order else "—",
            'article_code': l.box.target_article.code if l.box.target_article else "—",
            'action_type': l.action_type,
            'action_type_display': l.get_action_type_display(),
            'first_sort': l.first_sort_qty,
            'second_sort': l.second_sort_qty,
            'repair': l.repair_qty,
            'defect': l.defect_qty,
            'inspector': l.inspector.get_full_name() or l.inspector.username if l.inspector else "—",
            'time': timezone.localtime(l.created_at).strftime("%H:%M:%S"),
        })

    return JsonResponse({
        'status': 'OK',
        'count': len(data),
        'logs': data
    })

