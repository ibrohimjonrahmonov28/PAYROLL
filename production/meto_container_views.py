"""
Meto Markazi → "Konteynerlar" tabi (egasi talabi 2026-11-19, yangi meto oqimining 1-bosqichi):
- yangi yaratish: ketma-ket ID (KONT-000123), katta yoki kichik (ta'mir) turi;
- yorliq: 100×80 mm (Citizen CL-S703), har konteynerga 2 ta (ikki tomoniga), bir sahifada eng ko'pi MAX_PRINT ta konteyner;
  yo'qolgan yorliq o'sha ID bilan qayta;
- kuzatish: holat, tur, filial bo'yicha ro'yxat; QR skaner qilinsa konteyner kartochkasi (tarixi bilan) ochiladi.
Mavjud Meto Markazi oqimiga tegmaydi.
"""
import functools

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from production.container_services import (MAX_CREATE, MAX_PRINT, container_summary, create_containers, mark_printed,
                                           parse_container_code)
from accounts.models import User
from production.meto_flow import container_state
from production.meto_hub_views import meto_hub_access_required
from production.models import Container, _generate_qr_data_uri

PAGE_SIZE = 100
LABEL_COPIES = 2              # har konteynerga 2 ta yorliq — ikki tomoniga (egasi 2026-11-22)
LABEL_NUMBER_LENGTH_MM = 72   # o'ng ustunda raqam egallaydigan uzunlik (yorliq balandligi 80 mm, chetlari bilan)
LABEL_NUMBER_THICK_MM = 33    # o'ng ustun kengligi = raqam balandligi chegarasi


def container_access_required(*extra_roles):
    """Meto Markazi rollari + yangi oqim skladlari (extra_roles)."""
    def decorator(view):
        hub_view = meto_hub_access_required(view)

        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated and request.user.role in extra_roles:
                return view(request, *args, **kwargs)
            return hub_view(request, *args, **kwargs)
        return wrapper
    return decorator


SKLAD_ROLES = (User.Role.METO_SKLAD, User.Role.UYCHI_SKLAD)


def _number(value):
    """Konteyner raqami: '123' yoki 'KONT-000123' -> 123; noto'g'ri -> None."""
    text = str(value or '').strip()
    if text.upper().startswith('KONT'):
        return parse_container_code(text)
    return int(text) if text.isdigit() else None


def label_number_font_mm(number):
    """Yorliqdagi nolsiz raqam shrifti (mm): ko'p xonali raqam ustun uzunligiga sig'sin, kam xonalisi esa ustun
    kengligini to'ldirsin (Arial: raqam kengligi 0.556em, bosh harf balandligi ~0.72em). CSS uchun nuqtali satr."""
    digits = len(str(number))
    size = min(LABEL_NUMBER_THICK_MM / 0.77, LABEL_NUMBER_LENGTH_MM / (digits * 0.556))
    return f"{size:.1f}"


def _containers_url(**params):
    query = '&'.join(f"{key}={value}" for key, value in params.items() if value not in (None, ''))
    return f"{reverse('meto_hub_containers')}?{query}" if query else reverse('meto_hub_containers')


@container_access_required(*SKLAD_ROLES)
def containers_view(request):
    status = request.GET.get('holat', '')
    kind = request.GET.get('tur', '')
    branch = request.GET.get('filial', '')
    q = request.GET.get('q', '').strip()

    qs = Container.objects.select_related('created_by', 'box', 'repair_receipt__box')
    if status in Container.Status.values:
        qs = qs.filter(status=status)
    if kind in Container.Kind.values:
        qs = qs.filter(kind=kind)
    if branch in ('HQ', 'UYCHI'):
        qs = qs.filter(branch=branch)
    if q:
        number = _number(q)
        if number is not None and Container.objects.filter(number=number).exists():
            return redirect('meto_hub_container_detail', number=number)   # QR skaner qilindi — kartochka
        qs = qs.none()
        messages.error(request, f"«{q}» konteyneri topilmadi.")

    created_from = _number(request.GET.get('yaratildi_dan'))
    created_to = _number(request.GET.get('yaratildi_gacha'))
    print_chunks = []
    if created_from and created_to and created_to >= created_from:
        for start in range(created_from, created_to + 1, MAX_PRINT):
            print_chunks.append((start, min(start + MAX_PRINT - 1, created_to)))

    summary = container_summary()
    return render(request, 'meto_hub/containers.html', {
        'page': Paginator(qs.order_by('-number'), PAGE_SIZE).get_page(request.GET.get('page')),
        'summary': summary,
        'kpis': [
            ("Jami", summary['total'], 'text-white'), ("Bo'sh", summary['empty'], 'text-emerald-300'),
            ("Skladda", summary['filled'], 'text-amber-300'), ("Yo'lda", summary['in_transit'], 'text-sky-300'),
            ("Patokda", summary['at_patok'], 'text-orange-300'), ("Ta'mirda", summary['in_repair'], 'text-purple-300'),
            ("Katta", summary['big'], 'text-cyan-300'), ("Kichik (ta'mir)", summary['small'], 'text-purple-300'),
            ("Yorlig'i chiqmagan", summary['unprinted'], 'text-rose-300' if summary['unprinted'] else 'text-slate-300'),
        ],
        'filters': {'holat': status, 'tur': kind, 'filial': branch},
        'q': q,
        'kinds': Container.Kind.choices,
        'statuses': Container.Status.choices,
        'created_from': created_from,
        'created_to': created_to,
        'print_chunks': print_chunks,
        'max_create': MAX_CREATE,
        'max_print': MAX_PRINT,
        'max_labels': MAX_PRINT * LABEL_COPIES,
        'can_create': request.user.role != User.Role.UYCHI_SKLAD,
    })


@container_access_required(User.Role.METO_SKLAD)   # konteynerlar Kosonda yaratiladi
@require_POST
def container_create_view(request):
    kind = request.POST.get('tur', '')
    count = _number(request.POST.get('soni'))
    if kind not in Container.Kind.values:
        messages.error(request, "Konteyner turini tanlang.")
        return redirect('meto_hub_containers')
    if not count or count > MAX_CREATE:
        messages.error(request, f"Soni 1 dan {MAX_CREATE} gacha bo'lishi kerak.")
        return redirect('meto_hub_containers')
    first, last = create_containers(count, kind, request.user)
    first_code, last_code = Container(number=first).code, Container(number=last).code
    messages.success(request, f"{count} ta konteyner yaratildi: {first_code} — {last_code}. Endi yorliqlarini chop eting.")
    return redirect(_containers_url(yaratildi_dan=first, yaratildi_gacha=last))


@container_access_required(*SKLAD_ROLES)
def container_labels_view(request):
    first = _number(request.GET.get('dan'))
    last = _number(request.GET.get('gacha')) or first
    if first is None or last is None or last < first:
        messages.error(request, "Yorliq uchun konteyner raqamlarini to'g'ri kiriting (dan — gacha).")
        return redirect('meto_hub_containers')
    if last - first + 1 > MAX_PRINT:
        messages.error(request, f"Bir martada ko'pi bilan {MAX_PRINT} ta konteyner ({MAX_PRINT * LABEL_COPIES} ta yorliq) "
                                f"chop etiladi — oraliqni bo'ling.")
        return redirect('meto_hub_containers')
    containers = list(Container.objects.filter(number__gte=first, number__lte=last).order_by('number'))
    if not containers:
        messages.error(request, "Bu oraliqda konteyner yo'q.")
        return redirect('meto_hub_containers')

    mark_printed(containers, request.user)
    labels = []
    for c in containers:   # bir konteynerning ikkala yorlig'i ketma-ket (ikki tomoniga)
        label = {'container': c, 'qr': _generate_qr_data_uri(c.code, box_size=10, border=1),
                 'font_mm': label_number_font_mm(c.number)}
        labels.extend([label] * LABEL_COPIES)
    return render(request, 'meto_hub/container_labels_print.html', {
        'labels': labels,
        'container_count': len(containers),
        'first_code': containers[0].code,
        'last_code': containers[-1].code,
    })


@container_access_required(*SKLAD_ROLES)
def container_detail_view(request, number):
    container = get_object_or_404(Container.objects.select_related(
        'created_by', 'box__cutting_batch_item__batch', 'box__order', 'repair_receipt__box'), number=number)
    return render(request, 'meto_hub/container_detail.html', {
        'container': container,
        'state': container_state(container),
        'events': container.events.select_related('user', 'box')[:200],
    })
