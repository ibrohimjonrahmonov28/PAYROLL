"""
OTK chek printerlari (egasi talabi 2026-11-11).
- /printers/ — superadmin: har patok OTK printeri (IP yoki usb:<nomi>), sinov cheki, oxirgi cheklar va qayta chiqarish.
- /printers/agent/... — pechat agentlari uchun API, X-Agent-Token bilan (token SECRET_KEY'dan, /printers/ da ko'rinadi):
  * kompyuterdagi agent (print_agent/) — IP va usb:<nomi> printerlar;
  * patok OTK planshetidagi Terry Jar Print ilovasi (android_print/, egasi talabi 2026-11-14) — ?patok=<n> bilan faqat
    o'z patokining "tablet" printeri cheklarini oladi va printer planshetga USB bilan ulangan.
"""
import base64
import datetime
import hashlib
import hmac
import ipaddress
import json
import re

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.superadmin_views import superadmin_required
from production.models import PrintJob, ReceiptPrinter
from production.receipt_printing import build_test_receipt
from production.services import get_patok_code, parse_patok_number

PATOK_NUMBERS = range(1, 41)
CLAIM_TIMEOUT = datetime.timedelta(seconds=60)   # agent javob bermasa chek qayta beriladi
MAX_ATTEMPTS = 5
USB_NAME = re.compile(r'^usb:[A-Za-z0-9_.-]{1,60}$')
TABLET = 'tablet'
SEEN_EVERY = datetime.timedelta(seconds=20)      # planshet har 3 s so'raydi — bazaga 20 s da bir yoziladi
ONLINE_WINDOW = datetime.timedelta(minutes=1)


def agent_token():
    return hmac.new(settings.SECRET_KEY.encode(), b'terryjar-print-agent', hashlib.sha256).hexdigest()[:32]


def _agent_ok(request):
    return hmac.compare_digest(request.headers.get('X-Agent-Token', ''), agent_token())


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _fmt_dt(dt, fmt='%d.%m %H:%M:%S'):
    return timezone.localtime(dt).strftime(fmt) if dt else ''


def _truncate(text, limit=60):
    val = text or ''
    return (val[:limit - 1] + '…') if len(val) > limit else val


def _printers_context(request):
    """Printerlar jadvali, planshetlar tirikligi va oxirgi 50 ta chek holati (sahifa va ?partial=1 uchun yagona manba)."""
    now = timezone.now()
    online_since = now - ONLINE_WINDOW
    latest = PrintJob.objects.filter(printer=OuterRef('pk')).order_by('-created_at', '-id')
    with_last = ReceiptPrinter.objects.annotate(
        last_status=Subquery(latest.values('status')[:1]),
        last_at=Subquery(latest.values('created_at')[:1]),
        last_error=Subquery(latest.values('error')[:1]),
    )
    printers = {p.screen_number: p for p in with_last}
    status_labels = dict(PrintJob.Status.choices)

    rows = []
    tablet_total = 0
    tablet_online = 0
    for n in PATOK_NUMBERS:
        printer = printers.get(n)
        tablet_state = ''
        tablet_seen = ''
        if printer and printer.host == TABLET:
            tablet_total += 1
            if printer.last_seen_at and printer.last_seen_at >= online_since:
                tablet_state = 'online'
                tablet_online += 1
            elif printer.last_seen_at:
                tablet_state = 'offline'
                tablet_seen = _fmt_dt(printer.last_seen_at)
            else:
                tablet_state = 'never'

        last_status = (printer.last_status or '') if printer else ''
        last_error_full = (printer.last_error or '') if printer else ''
        rows.append({
            'n': n,
            'code': get_patok_code(n),
            'printer': printer,
            'has_printer': printer is not None,
            'host': printer.host if printer else '',
            'is_active': printer.is_active if printer else True,
            'tablet_state': tablet_state,
            'tablet_seen': tablet_seen,
            'last_status': last_status,
            'last_status_label': status_labels.get(last_status, last_status),
            'last_at': _fmt_dt(printer.last_at) if (printer and printer.last_at) else '',
            'last_error': _truncate(last_error_full, 60),
            'last_error_full': last_error_full,
        })

    jobs = list(PrintJob.objects.select_related('printer').order_by('-created_at', '-id')[:50])
    for job in jobs:
        job.patok_code = get_patok_code(job.printer.screen_number)

    jobs_sig = hashlib.md5(
        '|'.join(f"{j.id}:{j.status}:{j.attempts}:{j.error}" for j in jobs).encode('utf-8')
    ).hexdigest()[:16]

    pending_qs = PrintJob.objects.filter(status__in=[PrintJob.Status.PENDING, PrintJob.Status.SENDING])
    pending = pending_qs.count()
    sending = pending_qs.filter(status=PrintJob.Status.SENDING).count()

    return {
        'rows': rows,
        'jobs': jobs,
        'jobs_sig': jobs_sig,
        'token': agent_token(),
        'server_url': request.build_absolute_uri('/').rstrip('/'),
        'online_since': online_since,
        'pending': pending,
        'sending': sending,
        'tablet_total': tablet_total,
        'tablet_online': tablet_online,
        'server_time': _fmt_dt(now, '%H:%M:%S'),
    }


def _live_payload(request, client_sig=None):
    ctx = _printers_context(request)
    include_jobs = (client_sig is None or client_sig != ctx['jobs_sig'])
    return {
        'status': 'OK',
        'server_time': ctx['server_time'],
        'pending': ctx['pending'],
        'sending': ctx['sending'],
        'tablet_total': ctx['tablet_total'],
        'tablet_online': ctx['tablet_online'],
        'rows': [
            {
                'n': r['n'],
                'has_printer': r['has_printer'],
                'host': r['host'],
                'is_active': r['is_active'],
                'tablet_state': r['tablet_state'],
                'tablet_seen': r['tablet_seen'],
                'last_status': r['last_status'],
                'last_status_label': r['last_status_label'],
                'last_at': r['last_at'],
                'last_error': r['last_error'],
                'last_error_full': r['last_error_full'],
                'test_url': reverse('printers:test', args=[r['n']]),
            }
            for r in ctx['rows']
        ],
        'jobs_sig': ctx['jobs_sig'],
        'jobs_html': render_to_string('superadmin/_printers_jobs_rows.html', {'jobs': ctx['jobs']}, request=request) if include_jobs else None,
    }


def _no_store_json(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(["GET"])
def agent_jobs(request):
    """Navbatdagi cheklarni beradi va ularni SENDING qiladi (bir chek ikki marta chiqmasligi uchun)."""
    if not _agent_ok(request):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)
    now = timezone.now()
    jobs_qs = PrintJob.objects.select_for_update().select_related('printer').filter(
        Q(status=PrintJob.Status.PENDING) | Q(status=PrintJob.Status.SENDING, sent_at__lt=now - CLAIM_TIMEOUT),
        printer__is_active=True,
    )
    patok = parse_patok_number(request.GET.get('patok'))
    if request.GET.get('patok') is not None:
        # planshet ilovasi: faqat o'z patokining "tablet" printeri; ilova tirikligi /printers/ da ko'rinadi
        printer = ReceiptPrinter.objects.filter(screen_number=patok, host=TABLET).first() if patok else None
        if printer is None:
            return JsonResponse({'status': 'NO_PRINTER', 'jobs': [],
                                 'message': "Bu patokka /printers/ da \"tablet\" printer belgilanmagan"})
        if not printer.last_seen_at or printer.last_seen_at < now - SEEN_EVERY:
            ReceiptPrinter.objects.filter(pk=printer.pk).update(last_seen_at=now)
        jobs_qs = jobs_qs.filter(printer=printer)
    else:
        jobs_qs = jobs_qs.exclude(printer__host=TABLET)    # kompyuter agenti planshet printerlariga tegmaydi
    with transaction.atomic():
        jobs = list(jobs_qs.order_by('created_at')[:20])
        for job in jobs:
            job.status = PrintJob.Status.SENDING
            job.sent_at = now
            job.attempts += 1
            job.save(update_fields=['status', 'sent_at', 'attempts'])
    return JsonResponse({'status': 'OK', 'jobs': [
        {'id': j.id, 'host': j.printer.host, 'port': j.printer.port, 'data': j.payload, 'title': j.title} for j in jobs
    ]})


@csrf_exempt
@require_POST
def agent_result(request, job_id):
    if not _agent_ok(request):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)
    job = get_object_or_404(PrintJob, id=job_id)
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        data = {}
    if data.get('ok'):
        job.status, job.printed_at, job.error = PrintJob.Status.PRINTED, timezone.now(), ''
    else:
        job.error = str(data.get('error') or "Noma'lum xato")[:300]
        # printer o'chiq bo'lsa keyinroq yana urinadi; ko'p urinishdan keyin to'xtaydi
        job.status = PrintJob.Status.FAILED if job.attempts >= MAX_ATTEMPTS else PrintJob.Status.PENDING
    job.save(update_fields=['status', 'printed_at', 'error'])
    return JsonResponse({'status': 'OK'})


def _valid_host(host):
    if host == TABLET or USB_NAME.match(host):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@superadmin_required
def printers_page(request):
    printers = {p.screen_number: p for p in ReceiptPrinter.objects.all()}
    if request.method == 'POST':
        errors, error_screens, saved = [], [], 0
        for n in PATOK_NUMBERS:
            host = (request.POST.get(f'host-{n}') or '').strip()
            if host.lower() == TABLET:
                host = TABLET
            active = request.POST.get(f'active-{n}') == '1'
            printer = printers.get(n)
            if not host:
                if printer:
                    printer.delete()
                    saved += 1
                continue
            if not _valid_host(host):
                errors.append(f"{get_patok_code(n)}: «{host}» — IP manzil, usb:nomi yoki tablet bo'lishi kerak")
                error_screens.append(n)
                continue
            if printer is None:
                ReceiptPrinter.objects.create(screen_number=n, host=host, is_active=active)
                saved += 1
            elif (printer.host, printer.is_active) != (host, active):
                printer.host, printer.is_active = host, active
                printer.save(update_fields=['host', 'is_active', 'updated_at'])
                saved += 1
        if _is_ajax(request):
            err_msg = '. '.join(errors) + '.' if errors else ''
            save_msg = f"Saqlandi: {saved} ta o'zgarish."
            if errors and not saved:
                return _no_store_json({
                    'status': 'ERROR',
                    'message': err_msg,
                    'errors': errors,
                    'error_screens': error_screens,
                    'saved': saved,
                    'live': _live_payload(request),
                }, status=400)
            return _no_store_json({
                'status': 'PARTIAL' if errors else 'OK',
                'message': f"{err_msg} {save_msg}".strip() if errors else save_msg,
                'errors': errors,
                'error_screens': error_screens,
                'saved': saved,
                'live': _live_payload(request),
            })
        if errors:
            messages.error(request, '. '.join(errors) + '.')
        messages.success(request, f"Saqlandi: {saved} ta o'zgarish.")
        return redirect('printers:page')

    if request.GET.get('partial') == '1':
        return _no_store_json(_live_payload(request, client_sig=request.GET.get('sig')))

    return render(request, 'superadmin/printers.html', _printers_context(request))


@superadmin_required
@require_POST
def printer_test(request, screen_number):
    printer = get_object_or_404(ReceiptPrinter, screen_number=screen_number)
    PrintJob.objects.create(printer=printer, title="Sinov cheki",
                            payload=base64.b64encode(build_test_receipt(printer)).decode('ascii'))
    msg = f"{get_patok_code(screen_number)} printeriga sinov cheki navbatga qo'yildi."
    if _is_ajax(request):
        return _no_store_json({'status': 'OK', 'message': msg, 'live': _live_payload(request)})
    messages.success(request, msg)
    return redirect('printers:page')


@superadmin_required
@require_POST
def job_reprint(request, job_id):
    job = get_object_or_404(PrintJob, id=job_id)
    PrintJob.objects.create(printer=job.printer, box=job.box, inspection_log=job.inspection_log,
                            title=f"Qayta: {job.title}"[:200], payload=job.payload)
    msg = "Chek qayta navbatga qo'yildi."
    if _is_ajax(request):
        return _no_store_json({'status': 'OK', 'message': msg, 'live': _live_payload(request)})
    messages.success(request, msg)
    return redirect('printers:page')
