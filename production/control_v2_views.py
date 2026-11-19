"""
OTK v2 (egasi talabi 2026-11-19): /control/ sahifasining 1:1 nusxasi (templates/control_v2/control_home.html), yangi meto
oqimining konteynerlari bilan. Yangi oqim qutisi ta'mirga ketsa bo'sh kichik konteyner skaner qilinadi (chek odatdagidek
patok printeridan chiqadi), qayta ta'mirda o'sha konteyner keyingi chekka o'tadi, 1-sort bo'lmasa katta konteyner darhol
bo'shaydi. Boshqa qutilarda /control/ bilan aynan bir xil ishlaydi (o'sha server funksiyalari).
Muzlatilgan /control/ sahifasi va uning API lari o'zgarmaydi — bu yerda faqat ularning ustidan o'ram.
"""
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from production import control_views as base
from production.meto_flow import (FlowError, after_initial_inspection, after_repair_return, check_repair_container,
                                  is_v2_box, link_repair_container)
from production.models import Box, BoxQualityInspectionLog, Container


def _open_receipt(box):
    return box.repair_receipts.filter(returned_at__isnull=True).order_by('-cycle').first()


@login_required
@ensure_csrf_cookie
def control_v2_home_view(request):
    if not base._is_control_authorized(request.user):
        return redirect('root_login')
    today = timezone.localdate()
    active_operator = base._get_active_operator(request)
    stats = base._get_today_stats(request, active_operator)
    today_logs_qs = base._get_scoped_inspection_logs_qs(request, active_operator, today)
    return render(request, 'control_v2/control_home.html', {
        'inspector': request.user,
        'active_operator': active_operator,
        'active_operator_json': json.dumps(active_operator) if active_operator else "null",
        'today_boxes_count': today_logs_qs.values('box_id').distinct().count(),
        'today_first_sort': stats['first_sort'],
        'today_repair': stats['repair'],
        'today_second_sort': stats['second_sort'],
        'today_inspections': today_logs_qs.select_related('box', 'box__order', 'box__article', 'inspector')
                                          .order_by('-created_at')[:25],
        'today_date': str(today),
    })


@require_http_methods(["GET", "POST"])
def control_v2_lookup_api(request):
    """/control/ qidiruvining o'zi + yangi oqim belgisi va qutining konteynerlari."""
    response = base.control_box_lookup_api(request)
    if response.status_code != 200:
        return response
    payload = json.loads(response.content)
    box = Box.objects.select_related('cutting_batch_item__batch__order_item__order').get(id=payload['box']['id'])
    flow = is_v2_box(box)
    big = Container.objects.filter(box=box).first() if flow else None
    receipt = _open_receipt(box) if flow else None
    small = Container.objects.filter(repair_receipt=receipt).first() if receipt else None
    payload['box'].update({
        'container_flow': flow,
        'big_container': big.code if big else None,
        'repair_container': small.code if small else None,
    })
    return JsonResponse(payload)


@require_http_methods(["GET"])
def control_v2_container_check_api(request):
    if not base._is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)
    try:
        container = check_repair_container(request.GET.get('code'))
    except FlowError as exc:
        return JsonResponse({'status': 'ERROR', 'message': str(exc)}, status=400)
    return JsonResponse({'status': 'OK', 'code': container.code})


@csrf_exempt   # /control/ saqlash API si kabi (planshetlarda eskirgan cookie)
@require_http_methods(["POST"])
def control_v2_submit_api(request):
    """/control/ saqlash funksiyasining o'zi; yangi oqim qutisida konteynerlar ham yangilanadi (bitta tranzaksiyada)."""
    if not base._is_control_authorized(request.user):
        return JsonResponse({'status': 'FORBIDDEN', 'message': "Ruxsat etilmagan!"}, status=403)
    try:
        data = json.loads(request.body)
    except ValueError:
        data = request.POST
    box = Box.objects.select_related('cutting_batch_item__batch__order_item__order').filter(
        id=data.get('box_id')).first()
    if box is None or not is_v2_box(box):
        return base.control_submit_inspection_api(request)

    mode = data.get('mode', 'INITIAL')
    try:
        repair_qty = int(data.get('repair_qty') or 0)
    except (TypeError, ValueError):
        repair_qty = 0
    code = (data.get('repair_container') or '').strip()
    try:
        if mode == 'INITIAL' and repair_qty > 0:
            check_repair_container(code)   # saqlashdan oldin: konteynersiz ta'mirga yuborilmaydi
        with transaction.atomic():
            open_before = _open_receipt(box)
            response = base.control_submit_inspection_api(request)
            payload = json.loads(response.content)
            if response.status_code != 200 or payload.get('status') != 'OK':
                return response
            box.refresh_from_db()
            log = box.quality_inspection_logs.order_by('-created_at', '-id').first()
            if mode == 'INITIAL':
                if repair_qty > 0:
                    link_repair_container(_open_receipt(box), code, request.user)
                after_initial_inspection(box, log.first_sort_qty, request.user)
            elif log and log.action_type == BoxQualityInspectionLog.ActionType.REPAIR_RETURN:
                after_repair_return(box, open_before, _open_receipt(box), log.first_sort_qty, request.user)
    except FlowError as exc:
        return JsonResponse({'status': 'CONTAINER', 'message': str(exc)}, status=400)
    return JsonResponse(payload)
