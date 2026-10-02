"""
Terry Jar — Pastal va Stikerlarni Tekshirish Web va Webhook Ko'rinishlari
"""

import json
import httpx
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.conf import settings
from django.contrib.auth.decorators import login_required

from .passport_bot_service import (
    verify_pastal_for_sewing,
    decode_qr_from_image_bytes,
)


@csrf_exempt
def passport_telegram_webhook(request):
    """
    Telegram Bot Webhook Endpoint:
    Telegram bot serverdan Webhook rejimida keluvchi POST so'rovlarni qabul qiladi
    va 'PATOKKA BERISH MUMKIN' / 'BERIB BO'LMAYDI' xabarlarini qaytaradi.
    """
    if request.method != 'POST':
        return HttpResponse("Method not allowed", status=405)

    try:
        body = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)

    token = getattr(settings, 'TELEGRAM_PASSPORT_BOT_TOKEN', None) or '8679375634:AAF5bd9mCMpkfpVJhhFuoiAoCLLZa_LYeFU'

    message = body.get('message') or body.get('edited_message')
    if not message:
        return JsonResponse({'ok': True})

    chat_id = message.get('chat', {}).get('id')
    if not chat_id:
        return JsonResponse({'ok': True})

    text = message.get('text', '').strip()
    photo = message.get('photo')

    response_text = None

    if text:
        if text.startswith('/start'):
            response_text = (
                "👋 *Assalomu alaykum!*\n"
                "🏭 *TERRY JAR — Pastal va Stikerlarni Tekshirish Boti*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "Ushbu bot mahsulotlarni tikuv patogiga berishdan oldin ularning "
                "haqiqiyligi, aktivligi va narxlarini bittalab tekshirish uchun xizmat qiladi.\n\n"
                "📲 *Qanday ishlatiladi?*\n"
                "1️⃣ Pastal pasportidagi QR kodni yoki qutidagi stikerni *rasmga olib yuboring*.\n"
                "2️⃣ Yoki kodni to'g'ridan-to'g'ri *matn ko'rinishida yozing* (masalan: `PASTAL:12` yoki `#489DA37F`).\n\n"
                "🔍 Bot har bir quti va stikerni bittalab tekshirib, patokka berish mumkin yoki yo'qligini aytadi."
            )
        elif text.startswith('/help'):
            response_text = (
                "ℹ️ *YORDAM:*\n"
                "Pastal pasporti QR kodi (`PASTAL:12`), stiker kodi (`TICKET:TK-...`), "
                "yoki quti kodini (`CONTROL:A9-234`) yuboring."
            )
        else:
            res = verify_pastal_for_sewing(text)
            response_text = res['message']

    elif photo:
        # Eng katta rasmni olish
        best_photo = photo[-1]
        file_id = best_photo.get('file_id')
        if file_id and token:
            try:
                # 1. Telegramdan file_path olish
                info_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
                with httpx.Client(timeout=15.0) as client:
                    info_res = client.get(info_url).json()
                    if info_res.get('ok'):
                        file_path = info_res['result']['file_path']
                        download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
                        img_bytes = client.get(download_url).content
                        codes = decode_qr_from_image_bytes(img_bytes)
                        if codes:
                            res = verify_pastal_for_sewing(codes[0])
                            response_text = res['message']
                        else:
                            response_text = (
                                "⚠️ *Rasmdan QR kod aniqlanmadi!*\n"
                                "Iltimos, QR kodni yaxshiroq yorug'likda, yaqinroqdan va aniqroq qilib rasmga oling "
                                "yoki kodni matn sifatida yozing."
                            )
            except Exception as e:
                response_text = f"⚠️ Rasmni qayta ishlashda xatolik yuz berdi: {str(e)}"

    if response_text and token and chat_id:
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': response_text,
            'parse_mode': 'Markdown'
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(send_url, json=payload)
        except Exception:
            pass

    return JsonResponse({'ok': True})


@login_required
def passport_checker_web_view(request):
    """
    Web-interfeys: Ombordagi kompyuter yoki planshetda barcode skaner
    yordamida pastal va qutilarni tekshirish oynasi.
    """
    query = request.GET.get('q', '').strip()
    result = None
    if query:
        result = verify_pastal_for_sewing(query)

    return render(request, 'production/passport_checker.html', {
        'query': query,
        'result': result,
    })


@csrf_exempt
def passport_checker_api(request):
    """
    Tezkor AJAX API (skaner quroli bilan brauzerda sahifani qayta yuklamasdan tekshirish)
    """
    code = request.GET.get('code') or request.POST.get('code', '')
    code = str(code).strip()
    if not code:
        return JsonResponse({'success': False, 'message': "Kod kiritilmadi"}, status=400)

    result = verify_pastal_for_sewing(code)
    return JsonResponse(result)
