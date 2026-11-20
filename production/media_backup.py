"""
Rasmlar (media) zaxirasi — tungi baza zaxirasi bilan birga Telegramga (egasi talabi 2026-11-20).

Baza zaxirasi (.sql.gz) faqat jadvallarni oladi; fayllar Docker'ning media_volume'ida (/app/media) qolardi: artikul,
pechat, laboratoriya kartasi, taqdirlash rasmlari va xodim/foydalanuvchi QR kodlari.

- Dushanba kechasi — hamma fayllar (to'liq arxiv).
- Boshqa kechalar — shu haftaning dushanbasidan beri qo'shilgan/o'zgargan fayllar (farq arxivi); yo'q bo'lsa qisqa xabar.
- Tiklash: oxirgi to'liq arxivni, ustidan eng oxirgi farq arxivini media papkasiga ochish.
- Bot bitta faylga 50 MB dan ko'p yubora olmaydi: katta arxiv 45 MB li qismlarga bo'linadi (.tar.001, .tar.002, ...),
  yig'ish: cat nom.tar.gz.0* > nom.tar.gz. Rasmlar o'zi siqilgan, lekin gzip mayda fayllarning (QR) tar sarlavhalari
  va to'ldiruvchi nollarini yo'qotadi (lokal sinovda 303 MB tar o'rniga ~70 MB).
"""
import datetime
import os
import shutil
import tarfile
import tempfile
import time

import httpx
from django.conf import settings
from django.utils import timezone

PART_SIZE = 45 * 1024 * 1024
FULL_WEEKDAY = 0                       # dushanba
MARGIN = datetime.timedelta(hours=1)   # dushanba 00:00 atrofida yuklangan fayl ikkala arxivga ham tushsin


def week_start(now):
    """Shu haftaning dushanbasi 00:00 (Toshkent vaqti)."""
    monday = timezone.localtime(now).date()
    monday -= datetime.timedelta(days=monday.weekday())
    return timezone.make_aware(datetime.datetime.combine(monday, datetime.time.min))


def collect_files(root, since=None):
    """[(nisbiy yo'l, to'liq yo'l, hajm)]; since berilsa — faqat shundan keyin o'zgarganlar. Yashirin fayllar kirmaydi."""
    since_ts = since.timestamp() if since else None
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
        for name in sorted(filenames):
            if name.startswith('.'):
                continue
            path = os.path.join(dirpath, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if since_ts is None or stat.st_mtime >= since_ts:
                files.append((os.path.relpath(path, root), path, stat.st_size))
    return files


def _size(num):
    mb = num / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{num / 1024:.0f} KB"


def _post(client, token, method, data, files=None):
    """Telegram API chaqiruvi; xato matnini qaytaradi (None — yuborildi). 429 da aytilgan vaqt kutib qayta urinadi."""
    for attempt in range(4):
        response = client.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and body.get('ok'):
            return None
        if response.status_code == 429 and attempt < 3:
            time.sleep(int((body.get('parameters') or {}).get('retry_after', 5)) + 1)
            continue
        return f"{body.get('description', 'Telegram xatosi')} (kod: {body.get('error_code', response.status_code)})"


def send_media_backup(full=None, chat_id=None, bot_token=None, now=None):
    """Media zaxirasini Telegramga yuboradi. full=None — dushanba to'liq, boshqa kunlar farq arxivi."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    if full is None:
        full = local.weekday() == FULL_WEEKDAY
    token = bot_token or settings.TELEGRAM_BOT_TOKEN
    chat = chat_id or settings.TELEGRAM_REPORT_CHAT_ID
    if not token or not chat:
        return {'success': False, 'message': "Telegram BOT_TOKEN yoki TELEGRAM_REPORT_CHAT_ID sozlanmagan."}
    root = str(settings.MEDIA_ROOT)
    if not os.path.isdir(root):
        return {'success': False, 'message': f"Media papkasi topilmadi: {root}"}

    monday = week_start(now)
    files = collect_files(root, None if full else monday - MARGIN)
    stamp = local.strftime('%d.%m.%Y %H:%M')
    header = "🖼 TERRY JAR — RASMLAR ZAXIRASI"

    with httpx.Client(timeout=300.0) as client:
        if not files:
            text = (f"{header}\n📅 {stamp}\n{monday:%d.%m} (dushanba) dan beri yangi yoki o'zgargan rasm yo'q. "
                    f"To'liq arxiv dushanba kechasi keladi.")
            error = _post(client, token, 'sendMessage', {'chat_id': str(chat), 'text': text})
            return {'success': error is None, 'files': 0, 'parts': 0,
                    'message': error or "Yangi rasm yo'q — qisqa xabar yuborildi."}

        total = sum(size for _, _, size in files)
        tmp_dir = tempfile.mkdtemp(prefix='media_backup_')
        try:
            if shutil.disk_usage(tmp_dir).free < total + PART_SIZE:
                return {'success': False, 'message': f"Arxiv uchun diskda joy yetmaydi ({_size(total)} kerak)."}
            base = f"terryjar_media_{local:%Y_%m_%d}_{'full' if full else 'farq'}.tar.gz"
            tar_path = os.path.join(tmp_dir, base)
            with tarfile.open(tar_path, 'w:gz', compresslevel=6, format=tarfile.PAX_FORMAT) as tar:
                for rel, path, _ in files:
                    try:
                        tar.add(path, arcname=rel, recursive=False)
                    except OSError:
                        continue                      # shu payt o'chirilgan fayl
                    tar.members.clear()               # yozishda kerak emas; minglab fayl xotirada to'planmasin
            parts = max(1, -(-os.path.getsize(tar_path) // PART_SIZE))
            if full:
                title = f"{header} (TO'LIQ — hamma rasmlar)"
                restore = "Tiklash: shu to'liq arxiv, ustidan keyingi kunlarning eng oxirgi farq arxivi."
            else:
                title = f"{header} ({monday:%d.%m} dushanbadan beri o'zgarganlar)"
                restore = f"Tiklash: {monday:%d.%m} dagi to'liq arxiv, ustidan shu arxiv."
            with open(tar_path, 'rb') as tar_file:
                for index in range(1, parts + 1):
                    name = base if parts == 1 else f"{base}.{index:03d}"
                    caption = f"{title}\n📅 {stamp}\n📦 {len(files)} ta fayl · {_size(total)}"
                    if parts > 1:
                        caption += f" · {index}/{parts}-qism"
                    if index == parts:
                        caption += f"\n♻️ {restore}"
                        if parts > 1:
                            caption += f" Qismlarni yig'ish: cat {base}.0* > {base}"
                    error = _post(client, token, 'sendDocument', {'chat_id': str(chat), 'caption': caption},
                                  files={'document': (name, tar_file.read(PART_SIZE), 'application/gzip')})
                    if error:
                        return {'success': False, 'files': len(files), 'parts': parts,
                                'message': f"Rasmlar zaxirasi {index}/{parts}-qismda to'xtadi: {error}"}
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    kind = "to'liq" if full else "farq"
    return {'success': True, 'files': len(files), 'parts': parts,
            'message': f"Rasmlar zaxirasi ({kind}: {len(files)} ta fayl, {_size(total)}, {parts} qism) yuborildi."}
