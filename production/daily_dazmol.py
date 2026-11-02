"""
Kunlik dazmol hisobi — yagona manba.

- Asosiy dazmol: patokka HAQIQATAN qabul qilingan pastal qutilarining yakuniy dazmoli. Qaysi patokda
  dazmollanganidan qat'i nazar, pastalni qabul qilgan patokka yoziladi. Haqiqiy qabul:
    * Telegram bot patokka yuborgan (DISPATCHED) pastal patok planshetida qabul qilinganda qutiga qo'yiladigan
      Box.is_received_by_patok — Koson ham, Uychi ham (pastal qaytarilsa False bo'ladi);
    * Uychi ombori (meto hub) yuk xati bilan qabul qilib (ACCEPTED) U-patokka topshirgan pastal — bu yo'lda
      planshet bosqichi yo'q, topshirish (Box.assigned_screen) qabul hisoblanadi.
  Faqat yuborilgan (DISPATCHED) yoki Uychi omboridan hali chiqmagan pastallar asosiyga kirmaydi.
- Patok dazmoli: shu patok terminalida (ticket.screen_number) skanerlangan yakuniy dazmol, pastal kimniki
  bo'lishidan qat'i nazar.
- Egasiz dazmol: hech bir patok qabul qilmagan pastallar dazmoli; hech kimga yozilmaydi.

/sewing-statistics/daily/ (asosiy dazmol) va /sewing-statistics/daily-report/ (shablonlar bo'yicha) shu modul
natijasidan foydalanadi, shuning uchun patokning asosiy dazmoli har doim shablonlari yig'indisiga teng.
"""
import datetime
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from production.models import Box, CuttingBatch, Ticket

# Qutisi belgilangan bo'lsa ham pastal hozir patokka tegishli bo'lmagan holatlar (qayta yuborilgan va h.k.)
NOT_OWNED_BATCH_STATUSES = (
    CuttingBatch.DispatchStatus.WAITING,
    CuttingBatch.DispatchStatus.DISPATCHED,
    CuttingBatch.DispatchStatus.REJECTED,
)


MAIN_DAZMOL_NAME = 'DAZMOL'


def _normalize_op_text(value):
    return ' '.join((value or '').split()).upper()


def is_main_dazmol_operation(name, code=None):
    """
    Yakuniy kiyim dazmoli — faqat aynan "DAZMOL" nomli operatsiya (egasi qoidasi, 2026-10-30). U modelning oxirgi
    operatsiyasi; PLANKA / TASMA / CHO'NTAK va boshqa "... DAZMOL" oraliq amallar tayyor ish emas va sanalmaydi.
    Nom bo'lmagan holda kod tekshiriladi.
    """
    normalized_name = _normalize_op_text(name)
    if normalized_name:
        return normalized_name == MAIN_DAZMOL_NAME
    return _normalize_op_text(code) == MAIN_DAZMOL_NAME


def main_dazmol_operation_ids():
    """Katalogdagi yakuniy dazmol operatsiyalari ID lari (is_main_dazmol_operation bo'yicha) — SQL filtrlar uchun."""
    from production.models import Operation
    candidates = Operation.objects.filter(Q(name__icontains=MAIN_DAZMOL_NAME) | Q(code__icontains=MAIN_DAZMOL_NAME))
    return [op.id for op in candidates.only('id', 'name', 'code') if is_main_dazmol_operation(op.name, op.code)]


def get_day_window(target_date, now=None):
    """Bugun: 00:00 dan hozirgacha (real-time). O'tgan kunlar: 00:00 – 23:59:59.999999 (Toshkent vaqti)."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.min), tz)
    if target_date == timezone.localdate():
        return start, (now or timezone.now())
    return start, timezone.make_aware(datetime.datetime.combine(target_date, datetime.time.max), tz)


UYCHI_FIRST_SCREEN = 14


def get_box_owner_screen(box):
    """Qutini qabul qilgan patok raqami (asosiy egasi) yoki None. owned_box_q() bilan bir xil qoida."""
    if not box or not box.assigned_screen:
        return None
    batch = box.cutting_batch_item.batch if box.cutting_batch_item_id else None
    if batch and batch.dispatch_status in NOT_OWNED_BATCH_STATUSES:
        return None
    if box.is_received_by_patok:
        return box.assigned_screen
    # Uychi ombori (meto hub) topshirgan pastal: bu yo'lda planshet bosqichi yo'q, topshirish qabul hisoblanadi
    if (batch and batch.assigned_branch == 'UYCHI'
            and batch.dispatch_status == CuttingBatch.DispatchStatus.ACCEPTED
            and box.assigned_screen >= UYCHI_FIRST_SCREEN):
        return box.assigned_screen
    return None


def owned_box_q():
    """Box queryset uchun get_box_owner_screen() qoidasi (NOT_OWNED_BATCH_STATUSES alohida chiqariladi)."""
    return Q(is_received_by_patok=True) | Q(
        cutting_batch_item__batch__assigned_branch='UYCHI',
        cutting_batch_item__batch__dispatch_status=CuttingBatch.DispatchStatus.ACCEPTED,
        assigned_screen__gte=UYCHI_FIRST_SCREEN,
    )


DEFAULT_DAILY_NORM = 1200


def get_article_norm(article):
    """Artikulning kunlik normasi (dona): artikul -> model -> 1200 (kunlik sahifadagi qoida bilan bir xil)."""
    if not article:
        return DEFAULT_DAILY_NORM
    model_norm = article.model.daily_norm if article.model_id and article.model else 0
    return article.daily_norm or model_norm or DEFAULT_DAILY_NORM


def _ticket_template_info(ticket):
    """(shablon nomi, OperationGroup yoki None, shablonni belgilagan artikul)."""
    ao = ticket.article_operation
    article = ao.article if ao else None
    group = article.operation_group if article else None
    source_article = article
    if not group:
        box = ticket.box
        batch = box.cutting_batch_item.batch if box and box.cutting_batch_item_id else None
        batch_article = batch.order_item.article if batch and batch.order_item_id else None
        if batch_article and batch_article.operation_group:
            group = batch_article.operation_group
            source_article = batch_article
    name = group.name if group else (article.name if article else "Noma'lum")
    return name, group, source_article


def get_ticket_template(ticket):
    """Biletning narxlar shabloni: (nomi, OperationGroup yoki None). Shablon bo'lmasa artikul nomi."""
    name, group, _ = _ticket_template_info(ticket)
    return name, group


@dataclass
class DailyDazmol:
    asosiy_by_patok: dict = field(default_factory=dict)          # {sn: dona}
    templates_by_patok: dict = field(default_factory=dict)       # {sn: {nomi: {'name', 'group', 'qty'}}}
    pastals_by_patok: dict = field(default_factory=dict)         # {sn: {pastal_code: dona}}
    patok_dazmol_by_screen: dict = field(default_factory=dict)   # {sn: dona} terminal bo'yicha
    egasiz_by_screen: dict = field(default_factory=dict)         # {sn: dona} egasiz, dazmollangan terminal bo'yicha
    template_norms_by_patok: dict = field(default_factory=dict)  # {sn: {nomi: {norma: dona}}}

    def asosiy(self, sn):
        return self.asosiy_by_patok.get(sn, 0)

    def templates(self, sn):
        """Shablonlar dona soni kamayish tartibida: [{'name', 'group', 'qty'}]."""
        items = self.templates_by_patok.get(sn, {}).values()
        return sorted(items, key=lambda x: (-x['qty'], x['name']))

    def template_lines(self, sn):
        """
        18:00 hisobot qatorlari: shablon x norma (bir shablonda ikki xil norma bo'lsa alohida qator),
        shablonlar templates() tartibida: [{'name', 'group', 'norm', 'qty'}]. Dona yig'indisi = asosiy(sn).
        """
        norms = self.template_norms_by_patok.get(sn, {})
        lines = []
        for tpl in self.templates(sn):
            for norm, qty in sorted(norms.get(tpl['name'], {}).items(), key=lambda x: (-x[1], x[0])):
                lines.append({'name': tpl['name'], 'group': tpl['group'], 'norm': norm, 'qty': qty})
        return lines

    def pastals(self, sn):
        return [{'code': code, 'qty': qty} for code, qty in sorted(self.pastals_by_patok.get(sn, {}).items())]

    def patok_dazmol(self, sn):
        return self.patok_dazmol_by_screen.get(sn, 0)

    def egasiz(self, sn):
        return self.egasiz_by_screen.get(sn, 0)


def compute_daily_dazmol(start, end):
    """Berilgan vaqt oralig'idagi yakuniy dazmolni asosiy / patok / egasiz bo'yicha taqsimlaydi."""
    tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(start, end),
    ).filter(
        Q(article_operation__operation__name__icontains='DAZMOL') |
        Q(article_operation__operation__code__icontains='DAZMOL')
    ).select_related(
        'article_operation__operation',
        'article_operation__article__operation_group',
        'article_operation__article__model',
        'box__cutting_batch_item__batch__order_item__article__operation_group',
        'box__cutting_batch_item__batch__order_item__article__model',
    )

    asosiy = defaultdict(int)
    templates = defaultdict(dict)
    pastals = defaultdict(lambda: defaultdict(int))
    patok = defaultdict(int)
    egasiz = defaultdict(int)
    template_norms = defaultdict(dict)

    for t in tickets:
        op = t.article_operation.operation if t.article_operation_id else None
        if not op or not is_main_dazmol_operation(op.name, op.code):
            continue
        qty = t.quantity
        if t.screen_number:
            patok[t.screen_number] += qty

        owner = get_box_owner_screen(t.box)
        if owner is None:
            if t.screen_number:
                egasiz[t.screen_number] += qty
            continue

        asosiy[owner] += qty
        name, group, template_article = _ticket_template_info(t)
        entry = templates[owner].setdefault(name, {'name': name, 'group': group, 'qty': 0})
        entry['qty'] += qty
        template_norms[owner].setdefault(name, defaultdict(int))[get_article_norm(template_article)] += qty
        pastals[owner][(t.box.pastal_code or '').strip() or "—"] += qty

    return DailyDazmol(
        asosiy_by_patok=dict(asosiy),
        templates_by_patok=dict(templates),
        pastals_by_patok={sn: dict(v) for sn, v in pastals.items()},
        patok_dazmol_by_screen=dict(patok),
        egasiz_by_screen=dict(egasiz),
        template_norms_by_patok={sn: {name: dict(v) for name, v in by_name.items()} for sn, by_name in template_norms.items()},
    )


def get_patok_accepted_pastals(activity_box_ids=()):
    """
    {patok: {pastal_code}} — patokka qabul qilingan pastallar (planshetda yoki Uychi omboridan topshirilgan).
    Faqat hali yopilmagan (OTK yopmagan yoki ta'mirda) yoki bugun harakati bo'lgan qutilar olinadi,
    shuning uchun butun tarix yuklanmaydi.
    """
    owned_boxes = Box.objects.filter(
        owned_box_q(),
        assigned_screen__isnull=False,
    ).exclude(
        status=Box.Status.CANCELLED
    ).exclude(
        cutting_batch_item__batch__dispatch_status__in=NOT_OWNED_BATCH_STATUSES
    ).filter(
        Q(is_controlled=False) | Q(controlled_repair_qty__gt=0) | Q(id__in=list(activity_box_ids))
    ).values(
        'assigned_screen',
        'pastal_number',
        'cutting_batch_item__batch__pastal_code',
        'cutting_batch_item__batch__batch_number',
    ).distinct()

    result = defaultdict(set)
    for row in owned_boxes:
        code = (row['pastal_number'] or row['cutting_batch_item__batch__pastal_code'] or '').strip()
        if not code and row['cutting_batch_item__batch__batch_number'] is not None:
            code = str(row['cutting_batch_item__batch__batch_number'])
        if code:
            result[row['assigned_screen']].add(code)
    return dict(result)
