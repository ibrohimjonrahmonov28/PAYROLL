import os
import datetime
import tempfile
import subprocess
import httpx
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Q

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


def send_full_db_backup(chat_id: str = None, bot_token: str = None) -> dict:
    """
    To'liq ma'lumotlar bazasi (PostgreSQL dump) zaxira nusxasini (.sql.gz yoki .json.gz)
    yaratib, Telegram guruhga yuboruvchi funksiya.
    """
    now = timezone.localtime()
    date_str = now.strftime('%Y_%m_%d_%H%M%S')

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
            'message': "Telegram BOT_TOKEN sozlanmagan. Iltimos, .env faylida BOT_TOKEN ni kiriting."
        }

    if not target_chat_id:
        return {
            'success': False,
            'message': "TELEGRAM_REPORT_CHAT_ID sozlanmagan. Iltimos, guruh Chat ID sini kiriting."
        }

    db_conf = settings.DATABASES.get('default', {})
    db_name = db_conf.get('NAME', 'payroll_db')
    db_user = db_conf.get('USER', 'postgres')
    db_password = db_conf.get('PASSWORD', '')
    db_host = db_conf.get('HOST', 'localhost')
    db_port = str(db_conf.get('PORT', '5432'))

    backup_filename = f"payroll_db_backup_{date_str}.sql.gz"
    temp_dir = tempfile.gettempdir()
    backup_path = os.path.join(temp_dir, backup_filename)

    # 1. pg_dump orqali to'liq SQL dump olish va gzip bilan siqish
    pg_dump_success = False
    env_vars = os.environ.copy()
    if db_password:
        env_vars['PGPASSWORD'] = db_password

    try:
        dump_cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', db_port,
            '-U', db_user,
            '-d', db_name,
            '--clean',
            '--if-exists',
            '--no-owner',
            '--no-privileges'
        ]
        with open(backup_path, 'wb') as f_out:
            p1 = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env_vars)
            p2 = subprocess.Popen(['gzip'], stdin=p1.stdout, stdout=f_out)
            p1.stdout.close()
            _, err = p1.communicate()
            p2.communicate()
            if p1.returncode == 0 and p2.returncode == 0 and os.path.getsize(backup_path) > 0:
                pg_dump_success = True
    except Exception:
        pg_dump_success = False

    # 2. Agar pg_dump ishlamasa, Django dumpdata bilan JSON dump olish va siqish
    if not pg_dump_success:
        backup_filename = f"payroll_db_backup_{date_str}.json.gz"
        backup_path = os.path.join(temp_dir, backup_filename)
        from django.core.management import call_command
        import gzip
        try:
            with gzip.open(backup_path, 'wt', encoding='utf-8') as f_gz:
                call_command('dumpdata', natural_foreign=True, natural_primary=True,
                             exclude=['contenttypes', 'auth.permission'], stdout=f_gz)
        except Exception as e:
            return {
                'success': False,
                'message': f"Baza nusxasini yaratishda xatolik yuz berdi: {str(e)}"
            }

    # Fayl hajmini hisoblash
    file_size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    file_size_str = f"{file_size_mb:.2f} MB" if file_size_mb >= 1 else f"{file_size_mb * 1024:.1f} KB"

    caption_text = (
        f"💾 *TERRY JAR — BAZA ZAXIRA NUSXASI (FULL DB BACKUP)*\n"
        f"📅 *Sana:* {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"📦 *Fayl:* `{backup_filename}` ({file_size_str})\n"
        f"🔒 *Barcha jadvallar, xodimlar, narxlar va biletlar to'liq saqlandi.*"
    )

    api_url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(backup_path, 'rb') as f_doc:
            files = {'document': (backup_filename, f_doc, 'application/gzip')}
            data = {
                'chat_id': str(target_chat_id),
                'caption': caption_text,
                'parse_mode': 'Markdown'
            }
            with httpx.Client(timeout=60.0) as client:
                response = client.post(api_url, data=data, files=files)
                resp_json = response.json()

                if response.status_code == 200 and resp_json.get('ok'):
                    return {
                        'success': True,
                        'message': f"Baza zaxira nusxasi ({file_size_str}) Telegram guruhga ({target_chat_id}) muvaffaqiyatli yuborildi.",
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
            'message': f"Telegramga yuborishda xatolik: {str(e)}"
        }
    finally:
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except Exception:
                pass


def send_month_to_date_telegram_report(chat_id: str = None, bot_token: str = None) -> dict:
    """
    Joriy oy boshidan (1-kuni 00:00 dan) to hozirgacha bo'lgan to'liq oylik hisobotni (Excel)
    va to'liq ma'lumotlar bazasi zaxira nusxasini (Full DB Backup .sql.gz) Telegram guruhga yuboruvchi funksiya.
    """
    now = timezone.localtime()
    start_of_month = now.date().replace(day=1)

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
            'message': "Telegram BOT_TOKEN sozlanmagan. Iltimos, .env faylida BOT_TOKEN ni kiriting."
        }

    if not target_chat_id:
        return {
            'success': False,
            'message': "TELEGRAM_REPORT_CHAT_ID sozlanmagan. Iltimos, guruh Chat ID sini kiriting."
        }

    # 1. Agregatsiya statistikasi (caption uchun)
    tz = timezone.get_current_timezone()
    month_start_dt = timezone.make_aware(datetime.datetime.combine(start_of_month, datetime.time.min), tz)
    month_end_dt = now

    from production.models import Ticket
    from accounts.models import WorkerPayout

    month_tickets = Ticket.objects.filter(
        status=Ticket.Status.SCANNED,
        scanned_at__range=(month_start_dt, month_end_dt)
    )
    total_units = month_tickets.aggregate(s=Sum('quantity'))['s'] or 0
    total_gross = month_tickets.aggregate(s=Sum('total_amount'))['s'] or Decimal('0.00')
    active_workers_count = month_tickets.values('worker_id').distinct().count()

    payouts = WorkerPayout.objects.filter(
        payout_date__range=(start_of_month, now.date())
    ).aggregate(
        advances=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.ADVANCE)),
        salaries=Sum('amount', filter=Q(payout_type=WorkerPayout.PayoutType.SALARY))
    )
    total_advances = payouts['advances'] or Decimal('0.00')
    total_salaries = payouts['salaries'] or Decimal('0.00')
    total_payable = total_gross - total_advances - total_salaries

    # 2. Excel faylni yaratish
    from production.excel_reports import generate_month_to_date_excel_report
    try:
        excel_buffer = generate_month_to_date_excel_report(target_date=now.date())
    except Exception as e:
        return {
            'success': False,
            'message': f"Oylik Excel hisobotni shakllantirishda xatolik: {str(e)}"
        }

    filename = f"Oylik_Hisobot_{now.strftime('%Y_%m')}_{now.strftime('%d_%H%M')}.xlsx"

    caption_text = (
        f"📊 *TERRY JAR — OYLIK ISH HAQI VA MONITORING HISOBOTI*\n"
        f"📅 *Davr:* {start_of_month.strftime('%d.%m.%Y')} 00:00 — {now.strftime('%d.%m.%Y %H:%M')} (Oy boshidan hozirgacha)\n\n"
        f"👥 *Faol tikuvchilar:* {active_workers_count} nafar\n"
        f"👕 *Tikilgan jami mahsulot:* {total_units:,} dona\n"
        f"💰 *Jami hisoblangan ish haqi:* {total_gross:,.0f} UZS\n"
        f"💵 *Berilgan avanslar:* {total_advances:,.0f} UZS\n"
        f"💳 *To'lanishi kerak qoldiq:* {total_payable:,.0f} UZS\n\n"
        f"📁 *Barcha xodimlar va stikerlar bo'yicha to'liq oylik tabel ilova qilingan Excel faylda keltirilgan.*"
    ).replace(",", " ")

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
        with httpx.Client(timeout=60.0) as client:
            response = client.post(api_url, data=data, files=files)
            resp_json = response.json()
            if not (response.status_code == 200 and resp_json.get('ok')):
                err_desc = resp_json.get('description', 'Noma\'lum xatolik')
                return {
                    'success': False,
                    'message': f"Telegram API xatosi: {err_desc} (kod: {resp_json.get('error_code')})"
                }
    except Exception as e:
        return {
            'success': False,
            'message': f"Excel yuborishda xatolik: {str(e)}"
        }

    # 3. Va to'liq ma'lumotlar bazasi zaxira nusxasini (DB Backup) ham yuborish!
    db_backup_res = send_full_db_backup(chat_id=target_chat_id, bot_token=token)

    if db_backup_res.get('success'):
        combined_msg = f"Oylik hisobot (Excel) va to'liq baza zaxira nusxasi (.sql.gz) Telegram guruhga muvaffaqiyatli yuborildi!"
        return {
            'success': True,
            'message': combined_msg
        }
    else:
        return {
            'success': True,
            'message': f"Oylik hisobot yuborildi, lekin DB zaxirada ogohlantirish: {db_backup_res.get('message')}"
        }
