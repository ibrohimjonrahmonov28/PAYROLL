"""
Master scanning pipeline business logic & state machine.
Used both by Telegram Bot and the Web Bot Simulator.
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from accounts.models import Worker, User
from production.models import Ticket

# In-memory active scanning sessions: {user_identifier: session_dict}
# session_dict: {
#   'worker_id': int,
#   'worker_code': str,
#   'worker_name': str,
#   'pending_ticket_ids': list[int],
#   'updated_at': datetime
# }
ACTIVE_SESSIONS = {}


def extract_worker_code(text: str) -> str:
    text = (text or '').strip()
    if text.startswith('WORKER:'):
        return text[len('WORKER:'):].strip()
    return text


def extract_ticket_code(text: str) -> str:
    text = (text or '').strip()
    if text.startswith('TICKET:'):
        return text[len('TICKET:'):].strip()
    return text


def get_or_create_session(session_key: str):
    return ACTIVE_SESSIONS.get(session_key)


def clear_session(session_key: str):
    if session_key in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[session_key]


def identify_worker(session_key: str, worker_input: str) -> tuple[bool, str, Worker | None]:
    """
    1-qadam: Tikuvchini identifikatsiya qilish.
    """
    code = extract_worker_code(worker_input)
    try:
        worker = Worker.objects.get(worker_id__iexact=code, is_active=True)
    except Worker.DoesNotExist:
        return False, f"❌ Xodim topilmadi yoki nofaol holatda (Kiritildi: '{code}')", None

    ACTIVE_SESSIONS[session_key] = {
        'worker_id': worker.id,
        'worker_code': worker.worker_id,
        'worker_name': worker.full_name,
        'pending_ticket_ids': [],
        'updated_at': timezone.now()
    }
    msg = f"✅ Tikuvchi tasdiqlandi: <b>{worker.full_name}</b> (ID: {worker.worker_id})\nEndi biletlar QR kodlarini ketma-ket yuboring:"
    return True, msg, worker


def scan_ticket_item(session_key: str, ticket_input: str) -> tuple[bool, str, dict | None]:
    """
    2-qadam: Operatsiya biletlarini ketma-ket skanerlash va dublikatlarni bloklash.
    """
    session = ACTIVE_SESSIONS.get(session_key)
    if not session or not session.get('worker_id'):
        return False, "⚠️ Avval tikuvchi xodimni tanlang yoki uning QR nishonini skanerlang!", None

    code = extract_ticket_code(ticket_input)
    try:
        ticket = Ticket.objects.select_related('article_operation__operation', 'worker', 'box__order').get(ticket_code__iexact=code)
    except Ticket.DoesNotExist:
        return False, f"❌ Noma'lum bilet kodi: '{code}'. Bunday bilet tizimda mavjud emas!", None

    # 1. Bazada allaqachon skanerlanganligini tekshirish
    if ticket.status == Ticket.Status.SCANNED:
        worker_info = ticket.worker.full_name if ticket.worker else "Noma'lum"
        scanned_time = timezone.localtime(ticket.scanned_at).strftime("%d.%m.%Y %H:%M") if ticket.scanned_at else ""
        return False, (
            f"⚠️ <b>DIQQAT: Ushbu bilet allaqachon qabul qilingan!</b>\n"
            f"Operatsiya: {ticket.article_operation.operation.name}\n"
            f"Bajaruvchi: {worker_info}\n"
            f"Skanerlangan vaqt: {scanned_time}\n"
            f"Qayta skanerlash taqiqlanadi!"
        ), None

    # 2. Joriy sessiyada allaqachon kiritilganligini tekshirish
    if ticket.id in session['pending_ticket_ids']:
        return False, f"⚠️ Ushbu bilet ({ticket.ticket_code}) joriy partiyada allaqachon skanerlangan!", None

    # Sessiyaga qo'shish
    session['pending_ticket_ids'].append(ticket.id)

    # Hisob-kitoblar
    current_tickets = Ticket.objects.filter(id__in=session['pending_ticket_ids'])
    total_amount = sum(t.total_amount for t in current_tickets)
    total_units = sum(t.quantity for t in current_tickets)

    data = {
        'ticket_code': ticket.ticket_code,
        'operation_name': ticket.article_operation.operation.name,
        'quantity': ticket.quantity,
        'amount': ticket.total_amount,
        'total_tickets_count': len(session['pending_ticket_ids']),
        'running_units': total_units,
        'running_total_amount': total_amount,
        'running_total_amount_formatted': f"{int(total_amount):,}".replace(",", " ")
    }

    msg = (
        f"✅ <b>Qabul qilindi:</b> {ticket.article_operation.operation.name}\n"
        f"📦 Miqdor: {ticket.quantity} dona | 💰 Summa: {int(ticket.total_amount):,} UZS\n"
        f"📊 Joriy jami: <b>{len(session['pending_ticket_ids'])} ta bilet</b> | <b>{int(total_amount):,} UZS</b>"
    )
    return True, msg, data


@transaction.atomic
def finalize_and_route(session_key: str, screen_number: int, master_user: User | None = None) -> tuple[bool, str, dict | None]:
    """
    3-qadam: Yakunlash va 1-10 ekranlardan biriga atomik tranzaksiya bilan biriktirish.
    """
    if not (1 <= screen_number <= 10):
        return False, "❌ Noto'g'ri ekran raqami! 1 dan 10 gacha tanlang.", None

    session = ACTIVE_SESSIONS.get(session_key)
    if not session or not session.get('pending_ticket_ids'):
        return False, "⚠️ Skanerlangan biletlar mavjud emas!", None

    worker = Worker.objects.get(id=session['worker_id'])
    ticket_ids = session['pending_ticket_ids']

    # Atomik tekshiruv va select_for_update
    tickets = list(Ticket.objects.select_for_update().filter(id__in=ticket_ids))

    # Yana bir bor pending ekanligini qat'iy tekshirish
    for t in tickets:
        if t.status == Ticket.Status.SCANNED:
            return False, f"❌ Xatolik: '{t.ticket_code}' bileti boshqa joydan skanerlab ulgurilgan!", None

    now = timezone.now()
    total_earned = Decimal('0')
    total_units = 0

    for t in tickets:
        t.status = Ticket.Status.SCANNED
        t.worker = worker
        t.scanned_by = master_user
        t.screen_number = screen_number
        t.scanned_at = now
        t.save(update_fields=['status', 'worker', 'scanned_by', 'screen_number', 'scanned_at'])
        total_earned += t.total_amount
        total_units += t.quantity

    # Sessiyani tozalash
    clear_session(session_key)

    summary = {
        'worker_name': worker.full_name,
        'worker_id': worker.worker_id,
        'screen_number': screen_number,
        'tickets_count': len(tickets),
        'total_units': total_units,
        'total_amount': total_earned,
        'total_amount_formatted': f"{int(total_earned):,}".replace(",", " "),
        'time_str': timezone.localtime(now).strftime("%H:%M:%S")
    }

    msg = (
        f"🎉 <b>Muvaffaqiyatli yakunlandi va ekranga uzatildi!</b>\n\n"
        f"👤 Tikuvchi: <b>{worker.full_name}</b> ({worker.worker_id})\n"
        f"🎫 Biletlar soni: <b>{len(tickets)} ta</b>\n"
        f"📦 Bajarilgan donalar: <b>{total_units} dona</b>\n"
        f"💵 Hisoblangan ish haqi: <b>{int(total_earned):,} UZS</b>\n"
        f"📺 Tayinlangan ekran: <b>EKRAN {screen_number}</b>\n\n"
        f"<i>Natijalar {screen_number}-ekranda aks etmoqda. Keyingi xodimni skanerlashingiz mumkin.</i>"
    )
    return True, msg, summary

