# Terry Jar Print

Patok OTK planshetida orqa fonda ishlaydigan ilova (egasi talabi 2026-11-14): shu patokda saqlangan ta'mir cheklarini
serverdan oladi (`/printers/agent/jobs/?patok=K13`) va planshetga USB (OTG hub) bilan ulangan termo printerga chiqaradi.
DLT ilovasi va `/control/` sahifasi o'zgarmaydi. Android 11–16.

**Yig'ish:** `token.txt` ga `/printers/` sahifasidagi tokenni yozing, keyin `./build_apk.sh` → `terryjar_print.apk`.
Yangilanishni o'rnatish uchun `VERSION_CODE=2 ./build_apk.sh` (raqam har safar oshadi) va `release.keystore` bilan imzo
(kalit yo'qolsa ilovani o'chirib qayta o'rnatish kerak).

**Planshetga o'rnatish (bir marta):**
1. `/printers/` sahifasida shu patok qatoriga `tablet` yozib saqlang.
2. APK'ni o'rnating, ochib patokni kiriting (masalan `K13`) → "Saqlash va ishga tushirish".
3. Bildirishnomaga ruxsat bering (Android 13+), "Batareya cheklovini o'chirish" → Ruxsat.
4. Printerni hub orqali ulang → "Terry Jar Print bilan ochish" → **"Doim"** belgisini qo'yib OK.
5. "Sinov cheki" — chek chiqsa tayyor. `/printers/` sahifasida patok qatorida "● ulangan" ko'rinadi.
