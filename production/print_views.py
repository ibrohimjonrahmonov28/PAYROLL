"""
OTK chek printerlari (egasi talabi 2026-11-11).
- /printers/ — superadmin: har patok OTK printeri (IP yoki usb:<nomi>), sinov cheki, oxirgi cheklar va qayta chiqarish.
- /printers/agent/... — fabrikadagi pechat agenti (print_agent/terryjar_print_agent.py) uchun API, X-Agent-Token bilan.
  Token SECRET_KEY'dan hosil qilinadi va superadmin sahifasida ko'rsatiladi (alohida sozlama shart emas).
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
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.superadmin_views import superadmin_required
from production.models import PrintJob, ReceiptPrinter
from production.receipt_printing import build_test_receipt
from production.services import get_patok_code

PATOK_NUMBERS = range(1, 41)
CLAIM_TIMEOUT = datetime.timedelta(seconds=60)   # agent javob bermasa chek qayta beriladi
MAX_ATTEMPTS = 5
USB_NAME = re.compile(r'^usb:[A-Za-z0-9_.-]{1,60}$')


def agent_token():
    return hmac.new(settings.SECRET_KEY.encode(), b'terryjar-print-agent', hashlib.sha256).hexdigest()[:32]


def _agent_ok(request):
    return hmac.compare_digest(request.headers.get('X-Agent-Token', ''), agent_token())


@require_http_methods(["GET"])
def agent_jobs(request):
    """Navbatdagi cheklarni beradi va ularni SENDING qiladi (bir chek ikki marta chiqmasligi uchun)."""
    if not _agent_ok(request):
        return JsonResponse({'status': 'FORBIDDEN'}, status=403)
    now = timezone.now()
    with transaction.atomic():
        jobs = list(PrintJob.objects.select_for_update().select_related('printer').filter(
            Q(status=PrintJob.Status.PENDING) | Q(status=PrintJob.Status.SENDING, sent_at__lt=now - CLAIM_TIMEOUT),
            printer__is_active=True,
        ).order_by('created_at')[:20])
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
    if USB_NAME.match(host):
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
        errors, saved = [], 0
        for n in PATOK_NUMBERS:
            host = (request.POST.get(f'host-{n}') or '').strip()
            active = request.POST.get(f'active-{n}') == '1'
            printer = printers.get(n)
            if not host:
                if printer:
                    printer.delete()
                    saved += 1
                continue
            if not _valid_host(host):
                errors.append(f"{get_patok_code(n)}: «{host}» — IP manzil yoki usb:nomi bo'lishi kerak")
                continue
            if printer is None:
                ReceiptPrinter.objects.create(screen_number=n, host=host, is_active=active)
                saved += 1
            elif (printer.host, printer.is_active) != (host, active):
                printer.host, printer.is_active = host, active
                printer.save(update_fields=['host', 'is_active', 'updated_at'])
                saved += 1
        if errors:
            messages.error(request, '. '.join(errors) + '.')
        messages.success(request, f"Saqlandi: {saved} ta o'zgarish.")
        return redirect('printers:page')

    latest = PrintJob.objects.filter(printer=OuterRef('pk')).order_by('-created_at')
    with_last = ReceiptPrinter.objects.annotate(
        last_status=Subquery(latest.values('status')[:1]), last_at=Subquery(latest.values('created_at')[:1]),
        last_error=Subquery(latest.values('error')[:1]))
    printers = {p.screen_number: p for p in with_last}
    rows = [{'n': n, 'code': get_patok_code(n), 'printer': printers.get(n)} for n in PATOK_NUMBERS]
    return render(request, 'superadmin/printers.html', {
        'rows': rows,
        'jobs': PrintJob.objects.select_related('printer')[:50],
        'token': agent_token(),
        'server_url': request.build_absolute_uri('/').rstrip('/'),
        'pending': PrintJob.objects.filter(status__in=[PrintJob.Status.PENDING, PrintJob.Status.SENDING]).count(),
    })


@superadmin_required
@require_POST
def printer_test(request, screen_number):
    printer = get_object_or_404(ReceiptPrinter, screen_number=screen_number)
    PrintJob.objects.create(printer=printer, title="Sinov cheki",
                            payload=base64.b64encode(build_test_receipt(printer)).decode('ascii'))
    messages.success(request, f"{get_patok_code(screen_number)} printeriga sinov cheki navbatga qo'yildi.")
    return redirect('printers:page')


@superadmin_required
@require_POST
def job_reprint(request, job_id):
    job = get_object_or_404(PrintJob, id=job_id)
    PrintJob.objects.create(printer=job.printer, box=job.box, inspection_log=job.inspection_log,
                            title=f"Qayta: {job.title}"[:200], payload=job.payload)
    messages.success(request, "Chek qayta navbatga qo'yildi.")
    return redirect('printers:page')
