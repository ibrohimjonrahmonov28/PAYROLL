"""
Patok taqdirlashlari (egasi talabi 2026-11-09):
- /gifts/boshqaruv/            — faqat superadmin kiritadi: patok, oy, ism-familiya (ro'yxatdan tanlab yoki qo'lda),
                                  xodim rasmi, sovg'a rasmi, nominatsiya nomi va sababi;
- /gifts/<patok>/              — TV sahifasi (k1, K1, 1, u3 ...). Shu patokda taqdirlash kiritilgan eng oxirgi oy
                                  ochiladi, bir nechta bo'lsa har 5 soniyada almashadi, ◀ ▶ bilan boshqa oylar.
                                  TV ekrandagi (/screens/<n>/) sovg'a belgisi shu yerga olib keladi;
- /gifts/api/<patok>/version/  — TV sahifasi yangi kiritilgan taqdirlashni o'zi ko'rsatishi uchun.
"""
import datetime
import io
from collections import defaultdict

from django.contrib import messages
from django.core.files.base import ContentFile
from django.db.models import Count, ExpressionWrapper, F, FloatField, Max, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from PIL import Image, ImageOps

from accounts.models import User, Worker
from accounts.superadmin_views import superadmin_required
from production.models import Article, OrderItem, PatokAward, Ticket
from production.services import get_patok_code, get_patok_name, parse_patok_number

MAX_SCREENS = 40
SLIDE_SECONDS = 5   # egasi talabi 2026-11-11 (avval 15 edi)
IMAGE_MAX_SIDE = 1600
MONTH_NAMES_UZ = (
    'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun',
    'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr',
)
NOMINATION_SUGGESTIONS = (
    "Oyning eng yaxshi tikuvchisi",
    "Eng sifatli ish",
    "Normani eng ko'p bajargan",
    "Intizom va mas'uliyat uchun",
)


def _resolve_screen(patok):
    sn = parse_patok_number(patok)
    if not sn or not 1 <= sn <= MAX_SCREENS:
        raise Http404("Patok topilmadi")
    return sn


def _parse_month(value):
    """'2026-11' -> date(2026, 11, 1); noto'g'ri bo'lsa None."""
    try:
        year, month = (int(x) for x in str(value or '').split('-')[:2])
        if not 2000 <= year <= 2100:   # oy siljitish (◀ ▶) sana chegarasidan chiqmasin
            return None
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        return None


def _shift_month(month, delta):
    idx = month.year * 12 + (month.month - 1) + delta
    return datetime.date(idx // 12, idx % 12 + 1, 1)


def _month_label(month):
    return f"{MONTH_NAMES_UZ[month.month - 1]} {month.year}"


def _month_window(month):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(month, datetime.time.min), tz)
    end = timezone.make_aware(datetime.datetime.combine(_shift_month(month, 1), datetime.time.min), tz)
    return start, end


def _prepare_award_image(uploaded):
    """Telefon rasmini TV uchun tayyorlash: EXIF bo'yicha to'g'rilanadi, eng uzun tomoni 1600 px, JPEG."""
    try:
        img = Image.open(uploaded)
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        raise ValueError("Yuklangan fayl rasm emas")
    if img.mode in ('RGBA', 'LA', 'P'):
        rgba = img.convert('RGBA')
        background = Image.new('RGB', rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, 'JPEG', quality=85, optimize=True)
    return ContentFile(buffer.getvalue())


def _awards_version(sn):
    """Patok taqdirlashlari o'zgarganini bildiruvchi qisqa satr (soni + oxirgi o'zgarish vaqti)."""
    agg = PatokAward.objects.filter(screen_number=sn).aggregate(n=Count('id'), last=Max('updated_at'))
    return f"{agg['n']}-{agg['last'].timestamp() if agg['last'] else 0}"


def worker_candidates(sn, month):
    """
    Ism tanlash ro'yxati: tanlangan oyda shu patok terminalida stiker urgan tikuvchilar, o'rtacha kunlik norma foizi
    bo'yicha (TV ekrandagi "Umumiy norma foizi" ning oy davomidagi o'rtachasi): sum(ball / model normasi) / ishlagan kunlar.
    """
    start, end = _month_window(month)
    points_expr = ExpressionWrapper(
        F('quantity') * Coalesce(F('article_operation__difficulty'), Value(1.0)),
        output_field=FloatField(),
    )
    rows = list(Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        screen_number=sn,
        worker__isnull=False,
        scanned_at__gte=start,
        scanned_at__lt=end,
    ).annotate(
        day=TruncDate('scanned_at', tzinfo=timezone.get_current_timezone())
    ).values(
        'worker_id', 'day', 'article_operation__article_id', 'box__order_id'
    ).annotate(
        points=Sum(points_expr),
        earned=Sum('total_amount'),
    ).order_by())
    if not rows:
        return []

    articles = {a.id: a for a in Article.objects.filter(
        id__in={r['article_operation__article_id'] for r in rows}
    ).select_related('model')}
    order_item_norms = {
        (oi['order_id'], oi['article_id']): oi['norm']
        for oi in OrderItem.objects.filter(order_id__in={r['box__order_id'] for r in rows}).values('order_id', 'article_id', 'norm')
    }

    def norm_for(article_id, order_id):
        # TV ekrandagi (screens.views.get_screen_data) model normasi qoidasi
        art = articles.get(article_id)
        if art and art.model_id:
            return art.model.daily_norm or art.daily_norm or 1000
        return order_item_norms.get((order_id, article_id)) or (art.daily_norm if art else 0) or 1000

    stats = defaultdict(lambda: {'pct': 0.0, 'days': set(), 'earned': 0})
    for r in rows:
        s = stats[r['worker_id']]
        s['pct'] += (r['points'] or 0.0) * 100 / norm_for(r['article_operation__article_id'], r['box__order_id'])
        s['days'].add(r['day'])
        s['earned'] += int(r['earned'] or 0)

    workers = {w.id: w for w in Worker.objects.filter(id__in=stats.keys())}
    candidates = [
        {
            'id': wid,
            'full_name': workers[wid].full_name,
            'worker_code': workers[wid].worker_id,
            'avg_pct': round(s['pct'] / len(s['days']), 1),
            'days': len(s['days']),
            'earned_display': f"{s['earned']:,}".replace(',', ' '),
        }
        for wid, s in stats.items() if wid in workers
    ]
    candidates.sort(key=lambda c: (-c['avg_pct'], c['full_name']))
    return candidates


@require_GET
def gifts_tv_view(request, patok):
    sn = _resolve_screen(patok)
    user = request.user
    if (user.is_authenticated and getattr(user, 'role', None) == User.Role.BRANCH_ADMIN
            and getattr(user, 'branch', None) == 'UYCHI' and sn < 14):
        sn = 14   # screens.views.screen_view bilan bir xil: Uychi admini faqat U-patoklar

    months = list(PatokAward.objects.filter(screen_number=sn).values_list('month', flat=True).distinct().order_by('month'))
    month = _parse_month(request.GET.get('month')) or (months[-1] if months else timezone.localdate().replace(day=1))
    older = [m for m in months if m < month]
    newer = [m for m in months if m > month]
    code = get_patok_code(sn)

    return render(request, 'gifts/tv.html', {
        'screen_number': sn,
        'patok_code': code,
        'patok_name': get_patok_name(sn),
        'month_label': _month_label(month),
        'prev_month': older[-1].strftime('%Y-%m') if older else '',
        'prev_label': _month_label(older[-1]) if older else '',
        'next_month': newer[0].strftime('%Y-%m') if newer else '',
        'next_label': _month_label(newer[0]) if newer else '',
        'awards': PatokAward.objects.filter(screen_number=sn, month=month).order_by('id'),
        'slide_seconds': SLIDE_SECONDS,
        'version': _awards_version(sn),
        'version_url': reverse('gifts:version', args=[code.lower()]),
        'screen_url': reverse('screens:screen_view', args=[sn]),
    })


@require_GET
def gifts_version_api(request, patok):
    return JsonResponse({'version': _awards_version(_resolve_screen(patok))})


def _save_award_from_post(request, award):
    """Formadagi maydonlar va rasmlarni taqdirlashga yozadi. Xato bo'lsa ValueError (xabari foydalanuvchiga)."""
    full_name = (request.POST.get('full_name') or '').strip()
    nomination = (request.POST.get('nomination') or '').strip()
    if not full_name or not nomination:
        raise ValueError("Ism-familiya va nominatsiya nomi majburiy")

    worker_id = (request.POST.get('worker_id') or '').strip()
    award.worker = Worker.objects.filter(id=int(worker_id)).first() if worker_id.isdigit() else None
    award.full_name = full_name[:150]
    award.nomination = nomination[:200]
    award.reason = (request.POST.get('reason') or '').strip()

    prepared = {}
    for field in ('photo', 'gift_photo'):
        uploaded = request.FILES.get(field)
        if uploaded:
            prepared[field] = _prepare_award_image(uploaded)   # avval hammasini tekshiramiz, keyin saqlaymiz

    for field in ('photo', 'gift_photo'):
        current = getattr(award, field)
        if field in prepared:
            if current:
                current.delete(save=False)
            getattr(award, field).save(f"{field}.jpg", prepared[field], save=False)
        elif request.POST.get(f'remove_{field}') == '1' and current:
            current.delete(save=False)
    award.save()


@superadmin_required
def gifts_manage_view(request):
    raw_patok = request.POST.get('patok') or request.GET.get('patok')
    sn = parse_patok_number(raw_patok) or 1
    if not 1 <= sn <= MAX_SCREENS:
        sn = 1
    month = (_parse_month(request.POST.get('month') or request.GET.get('month'))
             or timezone.localdate().replace(day=1))
    code = get_patok_code(sn)
    back_url = f"{reverse('gifts:manage')}?patok={code.lower()}&month={month:%Y-%m}"

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'delete':
            award = get_object_or_404(PatokAward, id=request.POST.get('award_id'))
            for field in ('photo', 'gift_photo'):
                if getattr(award, field):
                    getattr(award, field).delete(save=False)
            name = award.full_name
            award.delete()
            messages.success(request, f"'{name}' taqdirlashi o'chirildi.")
            return redirect(back_url)

        if action == 'update':
            award = get_object_or_404(PatokAward, id=request.POST.get('award_id'))
        else:
            award = PatokAward(screen_number=sn, month=month, created_by=request.user)
        try:
            _save_award_from_post(request, award)
        except ValueError as exc:
            messages.error(request, f"{exc}. Iltimos, tekshirib qaytadan saqlang (rasm — JPG yoki PNG).")
            return redirect(back_url)
        verb = "yangilandi" if action == 'update' else "saqlandi"
        messages.success(request, f"{code}-patok, {_month_label(month)}: '{award.full_name}' taqdirlashi {verb}.")
        return redirect(back_url)

    counts = dict(PatokAward.objects.filter(month=month).order_by().values_list('screen_number').annotate(n=Count('id')))
    patoks = [
        {'sn': i, 'code': get_patok_code(i), 'count': counts.get(i, 0)}
        for i in range(1, MAX_SCREENS + 1)
    ]
    return render(request, 'gifts/manage.html', {
        'sn': sn,
        'patok_code': code,
        'month_param': month.strftime('%Y-%m'),
        'month_label': _month_label(month),
        'prev_month': _shift_month(month, -1).strftime('%Y-%m'),
        'next_month': _shift_month(month, 1).strftime('%Y-%m'),
        'hq_patoks': patoks[:13],
        'uychi_patoks': patoks[13:],
        'awards': PatokAward.objects.filter(screen_number=sn, month=month).select_related('worker').order_by('id'),
        'candidates': worker_candidates(sn, month),
        'nomination_suggestions': NOMINATION_SUGGESTIONS,
        'tv_url': f"{reverse('gifts:tv', args=[code.lower()])}?month={month:%Y-%m}",
    })
