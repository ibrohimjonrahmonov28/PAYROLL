import os
import datetime
import httpx
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum

from production.models import Ticket
from production.excel_reports import generate_daily_excel_report


def send_daily_excel_report(target_date: datetime.date = None, chat_id: str = None, bot_token: str = None) -> dict:
    """
    Har kuni kechki payt 00:00 da (yoki buyruq/admin orqali)
    Excel hisobotini shakllantirib, Telegram guruhga yoki chatga yuboruvchi funksiya.
    """
    if target_date is None:
        target_date = timezone.localdate()

    if bot_token is not None:
        token = bot_token
    else:
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or os.getenv('BOT_TOKEN', '')

    if chat_id is not None:
        target_chat_id = chat_id
    else:
        target_chat_id = getattr(settings, 'TELEGRAM_REPORT_CHAT_ID', '') or os.getenv('TELEGRAM_REPORT_CHAT_ID', '')

    if not token:
        return {
            'success': False,
            'message': "Telegram BOT_TOKEN sozlanmagan. Iltimos, .env yoki settings.py faylida BOT_TOKEN ni kiriting."
        }

    if not target_chat_id:
        return {
            'success': False,
            'message': "TELEGRAM_REPORT_CHAT_ID sozlanmagan. Iltimos, hisobot yuboriladigan guruh yoki chat ID sini kiriting."
        }

    # 1. Kunlik statistikani hisoblash (caption uchun)
    daily_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__date=target_date
    )
    
    total_units = daily_tickets.aggregate(s=Sum('quantity'))['s'] or 0
    total_earned = daily_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    active_workers_count = daily_tickets.values('worker_id').distinct().count()

    # 2. Excel faylni xotirada yaratish
    try:
        excel_buffer = generate_daily_excel_report(target_date=target_date)
    except Exception as e:
        return {
            'success': False,
            'message': f"Excel faylni shakllantirishda xatolik yuz berdi: {str(e)}"
        }

    filename = f"Kunlik_Hisobot_{target_date.strftime('%Y_%m_%d')}.xlsx"

    # 3. Caption matni
    caption_text = (
        f"📊 *TERRY JAR — KUNLIK ISH HAQI VA STIKERLAR HISOBOTI*\n"
        f"📅 *Sana:* {target_date.strftime('%d.%m.%Y')}\n\n"
        f"👥 *Faol tikuvchilar:* {active_workers_count} nafar\n"
        f"👕 *Tikilgan jami mahsulot:* {total_units:,} dona\n"
        f"💰 *Bugungi hisoblangan ish haqi:* {total_earned:,.0f} UZS\n\n"
        f"📁 *Barcha xodimlar va urilgan stikerlar ID lari tafsiloti ilova qilingan Excel faylda keltirilgan.*"
    ).replace(",", " ")

    # 4. Telegram Bot API orqali jo'natish
    api_url = f"https://api.telegram.org/bot{token}/sendDocument"
    files = {
        'document': (filename, excel_buffer.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    }
    data = {
        'chat_id': str(target_chat_id),
        'caption': caption_text,
        'parse_mode': 'Markdown'
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(api_url, data=data, files=files)
            resp_json = response.json()

            if response.status_code == 200 and resp_json.get('ok'):
                return {
                    'success': True,
                    'message': f"Hisobot Telegram guruhga ({target_chat_id}) muvaffaqiyatli yuborildi.",
                    'response': resp_json
                }
            else:
                err_desc = resp_json.get('description', 'Noma\'lum xatolik')
                return {
                    'success': False,
                    'message': f"Telegram API xatosi: {err_desc} (kod: {resp_json.get('error_code')})",
                    'response': resp_json
                }
    except Exception as e:
        return {
            'success': False,
            'message': f"Telegramga yuborishda tarmoq xatoligi: {str(e)}"
        }
