# 📜 TERRY JAR — Loyiha Versiyalari va O'zgarishlar Tarixi (Changelog)

Ushbu hujjatda loyihada kiritilgan barcha yangi funksiyalar, tuzatishlar va arxitektura o'zgarishlari har bir Git commit ID si bilan birga qayd etib boriladi.
Istalgan paytda tizimni avvalgi holatiga qaytarish yoki muayyan versiyani tekshirish uchun ushbu commit ID lardan foydalanish mumkin.

---

## 🔄 Muayyan Versiyaga Qaytish Bo'yicha Qo'llanma (Rollback Guide)

- **Vaqtincha eski versiyani ko'rish (Read-only / Inspect):**
  ```bash
  git checkout <COMMIT_ID>
  ```
- **Asosiy (main) branchga qaytish:**
  ```bash
  git checkout main
  ```
- **Oxirgi o'zgarishni bekor qilish (Xavfsiz revert):**
  ```bash
  git revert <COMMIT_ID>
  git push origin main
  ```
- **Loyiha bazasini va serverni qayta ishga tushirish:**
  ```bash
  docker compose restart web
  ```

---

## 📌 Versiyalar va Funksiyalar Ro'yxati

### 🔹 [Commit: `9593bf9`] — 09.10.2026, 14:50
* **Turi:** `feat(statistics)` — Tikim Statistikasida Pastallar Bo'yicha 4-Bosqichli Quvur (Pipeline Balansi), Oraliq (Dazmoldan O'tgan, OTK Kutmoqda) va 100% Yakuniy Yopilish Muhrlanishi
* **Mavzu:** Har bir patok tikayotgan pastallar bo'yicha to'liq 4-bosqichli hisob-kitob (1. Jami kirgan reja dona va quti, 2. Hali tikimda [dazmolgacha], 3. Dazmoldan o'tgan, 4. Oraliq: dazmoldan o'tgan va hali OTK ga kirmagan kutayotgan qutilar, 5. OTK o'tgan 1-sort, ta'mir va brak, hamda 20/20 quti o'tganda 100% yakuniy yopilish muhrlanishi)
* **O'zgartirilgan fayllar:**
  - `production/sewing_statistics_views.py`
  - `templates/production/sewing_statistics_daily.html`
  - `production/tests.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **4-Bosqichli Quvur Balansi (4-Stage Pipeline):**
     - **1-Bosqich (Jami Kirgan Reja):** Pastal bo'yicha jami rejalashtirilgan donalar va qutilar soni (masalan 1000 dona / 20 ta quti).
     - **2-Bosqich (Hali Tikimda):** Dazmolga yetib bormagan, tikuvchilar qo'lida tikilayotgan qutilar va donalar soni (masalan 200 dona / 4 ta quti).
     - **3-Bosqich (Dazmoldan O'tgan & Oraliq):** Dazmoldan o'tgan umumiy qutilar (masalan 800 dona / 16 ta quti) hamda **Oraliqda turgan** (dazmoldan o'tgan, lekin hali OTK nazoratiga kirmagan/kutayotgan) qutilar va donalar (masalan 400 dona / 8 ta quti).
     - **4-Bosqich (Kontroldan O'tgan / OTK Yopgan):** OTK tekshirib yopgan mahsulotlar (masalan 400 dona / 8 ta quti) va ularning sifat taqsimoti: 1-sort (300 dona), ta'mirga qaytgan (90 dona), brak/2-sort (10 dona).
  2. **Pastal 100% To'liq Yopilishi va Yakuniy Muhr (100% Closed & Final Seal):**
     - 20 ta quti kirdimi, 20 tasi ham dazmoldan o'tib, 20 tasi ham OTK dan to'liq o'tgan va ta'mirlar 0 bo'lganda pastal "🏁 100% TO'LIQ YOPILDI" deb belgilanadi va yakuniy son muhrlanadi.
     - Agar hali to'liq yopilmagan bo'lsa, qolgan qutilar (tikimda + oraliqda + ta'mirda) va bitish foizi aniq ko'rsatiladi.
  3. **Patok Darajasidagi Umumiy Xulosa (Patok Summary Strip):**
     - Har bir patokning barcha pastallari bo'yicha umumiy 6 talik tezkor ko'rsatkichlar paneli qo'shildi.
  4. **Razmerlar Kesimidagi Kengaytirilgan Balans:**
     - Har bir razmer bo'yicha tikimda, dazmoldan o'tgan, oraliqda kutayotgan, 1-sort, 2-sort, ta'mir, brak va defitsit/mato ehtiyoji batafsil ko'rsatiladi.
  5. **183 ta test (117 production + 66 accounts)** 100% muvaffaqiyatli o'tdi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 9593bf9
  ```

---

### 🔹 [Commit: `4cc5f18`] — 08.10.2026, 18:16
* **Turi:** `feat(cutting)` — Maxsus Kesimchi (CUTTER) Hisobi, Avtomatik Unikal Pastal Kodi (P{MM}-{N}) va PC Desktop Kengaytirilgan Maydoni
* **Mavzu:** `/cutting/` uchun maxsus akkount ruxsatlari (middleware orqali qat'iy cheklov), yangi kesim qo'shishda avtomatik unikal pastal kodi generatsiyasi (`P10-1`..`P10-120`..`P10-999`..`P10-1000`..`P10-9999`, saqlanmasa raqam yo'qolmaydi), hamda PC/Desktop monitorlar uchun kengaytirilgan (1920px widescreen) qulay ish maydoni va grid modallar
* **O'zgartirilgan fayllar:**
  - `accounts/middleware.py`
  - `accounts/views.py`
  - `production/services.py`
  - `production/cutting_views.py`
  - `production/cutting_urls.py`
  - `production/tests.py`
  - `templates/cutting/base_cutting.html`
  - `templates/cutting/dashboard.html`
  - `templates/cutting/order_detail.html`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **CUTTER Roli va Qat'iy Xavfsizlik Middleware'i (`MasterTerminalAccessRestrictionMiddleware`):**
     - Kesim bo'limi uchun maxsus `cutting` akkounti yaratildi (`role = CUTTER`).
     - Ushbu foydalanuvchi faqat `/cutting/` sahifalari va uning API'larida ishlay oladi. Boshqa bo'limlarga (`/orders/`, `/terminal/`, `/superadmin/` va h.k.) kirish qat'iy bloklanib, avtomatik ravishda `/cutting/` ga yo'naltiriladi; AJAX so'rovlarda 403 Forbidden qaytariladi.
     - Login qilganda ham to'g'ridan-to'g'ri `/cutting/` dashboardiga yo'naltiriladi.
  2. **Avtomatik Unikal Pastal Kodi Generatsiyasi (`generate_next_pastal_code`):**
     - Format: `P{MM}-{N}` (masalan: `P10-1`, `P10-120`, `P10-999`, `P10-1000` ... `P10-9999`).
     - `P` = Pastal; `{MM}` = joriy oy (masalan 10 = Oktyabr, 11 = Noyabr).
     - Barcha zakazlar bo'ylab yagona factory-wide ketma-ketlik va 100% unikal kod kafolatlanadi.
     - **Saqlasa olsin, saqlamasa olmasin:** Generatsiya qilingan raqam faqat "Saqlash" bosilgandagina bazaga biriktiriladi. Modalni shunchaki ochib ko'rish yoki bekor qilish raqamni sarflamaydi/yo'qotmaydi.
     - Raqamlash 1 dan boshlanadi, 999 dan oshsa avtomatik tarzda 1000..9999 ga kengayadi.
  3. **Yangi Pastal API (`/cutting/api/next-pastal/`):**
     - Modal ochilganda brauzer 0 ms ichida eng oxirgi bo'sh unikal kodni olib, maydonni to'ldiradi.
     - "Kodni Yangilash" (Refresh) tugmasi va chiroyli visual status indikatori qo'shildi.
  4. **PC / Desktop Kengaytirilgan Ish Maydoni (Widescreen 1920px):**
     - Barcha kesim sahifalari (`base_cutting.html`, `dashboard.html`, `order_detail.html`) tor `max-w-7xl` cheklovidan chiqarilib, katta PC ekranlarga mos `max-w-[1920px]` keng formatga o'tkazildi.
     - Partiya qo'shish va tahrirlash modallari `max-w-3xl` ga kengaytirildi, razmerlar kiritish ro'yxati 3 ustunli qulay kartochkalar to'plamiga (grid) aylantirildi.
  5. **182 ta test (116 production + 66 accounts)** to'liq muvaffaqiyatli o'tdi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 4cc5f18
  ```

---

### 🔹 [Commit: `ee22aa1`] — 08.10.2026, 15:12
* **Turi:** `fix(terminal)` — Rapid-Scan Barqarorligi va Zero-Drop Arxitekturasi
* **Mavzu:** Skanerlash terminalida soxta dublikat xatolari va biletlar tushib qolishini bartaraf etish: ketma-ket FIFO Queue (navbat), apparat takrorini filtrlash (hardware dedup), ko'p martalik Enter hodisalarini birlashtirish va Finalize reconciliation
* **O'zgartirilgan fayllar:**
  - `production/terminal_views.py`
  - `templates/terminal/index.html`
  - `production/test_terminal.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Sequential FIFO Queue (Navbat Tizimi):** Skaner qanchalik tez o'qisa ham, biletlar brauzerdagi navbatga olinadi va serverga bittalab, ketma-ket yuboriladi. Natijada Django sessiyalarining bir-birini ustiga yozib yuborishi (Race Condition) 100% yo'qotildi.
  2. **Hardware Bounce Dedup (350ms):** Skaner apparati nurini ushlab turganda bitta QR-kodni millisekundlar ichida 2 marta o'qishi oqibatida kelib chiqadigan soxta "DUBLIKAT" xatosi jim filtrlanadi va keyingi stikerga xalaqit bermaydi.
  3. **Yagona va Toza Enter Hodisasi:** `scanInput` dagi parallel `keypress`, `keyup`, `change` lardan `submitScan` chaqiruvi tozalandi. Faqat bitta toza `keydown` qoldirildi.
  4. **Stikerlarni Xavfsiz Ajratish (`splitScanTokens`):** Skaner tezligi tufayli bir nechta stiker buferda bitta qatorga yopishib qolsa (`TICKET:TK-...TICKET:TK-...` yoki `#7MAG7064#4DXN5YBF`), ular alohida tokenlarga ajratilib, navbatga teriladi.
  5. **Zero-Drop Finalize Reconciliation:** Yakunlash (F2) paytida client tasdiqlagan bilet ID lari ro'yxati server sessiyasi bilan birlashtiriladi. Biron bir bilet oralarida tushib qolishi butunlay bartaraf etildi.
  6. **180 ta test (114 production + 66 accounts)** to'liq muvaffaqiyatli o'tdi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout ee22aa1
  ```

---

### 🔹 [Commit: `b967f3d`] — 08.10.2026, 08:10
* **Turi:** `feat(statistics)` — Yangi imkoniyat va Snapshot Arxitekturasi
* **Mavzu:** Tikim patoklarining kunlik normasi va bajarilish monitoringi, vaqt chegaralari (bugun real-time, kecha va avvalgi kunlar 24:00 tungi muhr) hamda `DailyPatokProgress` modeli
* **O'zgartirilgan fayllar:**
  - `production/models.py`
  - `production/migrations/0031_dailypatokprogress.py`
  - `production/sewing_statistics_views.py`
  - `templates/production/sewing_statistics_daily.html`
  - `production/tests.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **DailyPatokProgress Modeli va Migratsiya (`0031_dailypatokprogress`):** Har bir patokning kunlik rejasi (norma), bajarilgan donasi, foizi, dazmol, OTK, kutayotgan va ta'mirdagi donalari bazada arxivlanadi va muhrlanadi.
  2. **Aniq Vaqt Chegarasi (Bugun Real-time vs Avvalgi Kunlar Tungi 24:00):**
     - Bugungi kun uchun `scanned_at` va `created_at` so'rovlari hozirgi soniyagacha (`timezone.now()`) jonli real-time ishlaydi.
     - Kecha va undan oldingi kunlar uchun aniq tungi 23:59:59 gacha bo'lgan vaqt oralig'i olinadi va bazada o'sha kungi holat sifatida qat'iy saqlanadi.
  3. **Kunlik Norma Hisobi:** Har bir patokda tikilayotgan modellar (artikullar)ning `daily_norm` ko'rsatkichi asosida patokning kunlik normasi (reja) hisoblanadi (standart 1200 dona).
  4. **Bajarilgan Natija & Foiz:** Dazmoldan chiqqan (yoki OTK tekshirgan) mahsulotlar normaga nisbatan taqqoslanadi (`completed_norm_units`, `norm_percentage`, `norm_diff`).
  5. **Desktop Dashboard UI Yangilanishi:**
     - KPI bloki: "Norma Natijasi" kartochkasi (`avg_norm_pct`%, reja va bajarilgan jami sonlar).
     - Jadval ustuni: "Kunlik Norma & Natija" ustuni (reja soni, bajarilgani, dinamik rangli progress-bar va `+X` oshirildi / `-Y` qoldi ko'rsatkichi).
     - Batafsil panelida patok normasi va farqi ko'rsatilishi.
  6. **179 ta test (113 production + 66 accounts)** to'liq muvaffaqiyatli o'tdi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout b967f3d
  ```

---

### 🔹 [Commit: `e9af786`] — 07.10.2026, 21:05
* **Turi:** `feat(statistics)` — Yangi imkoniyat
* **Mavzu:** Kunlik tikim va pastallar monitoringi: yaxlit desktop interfeys, pastal statusi va razmerlar kesimidagi mato/ta'mir balansi
* **O'zgartirilgan fayllar:**
  - `production/sewing_statistics_views.py`
  - `templates/production/sewing_statistics_daily.html`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. `https://terryjar.uz/sewing-statistics/daily/` sahifasi desktop monitorlarga to'liq moslashtirildi (keng ekranli yuqori axborot zichligi).
  2. Patoklar, zakazlar va modellar yaxlitlandi (ixcham chiplar ko'rinishida, ortiqcha qatorlarsiz).
  3. **Tikayotgan Pastallar monitoringi:** Har bir patokda shu kunda tikilayotgan pastallar (`10-120`, `09-607` va h.k.) interaktiv badge ko'rinishida chiqarildi.
  4. **"Hamma qutilar yopilganda pastal yopildi"** mantiqi: Agar pastaldagi barcha qutilar OTK dan o'tgan bo'lsa `✓ TO'LIQ YOPILDI`, aks holda `⏳ JARAYONDA (X/Y quti)`.
  5. **Kunlik Dazmol va OTK:** Aynan bugungi dazmoldan chiqqan mahsulot soni, bugungi 1-sort, 2-sort va kutayotgan ishlar alohida ko'rsatildi.
  6. **💎 Razmerlar Kesimidagi Ta'mir va Mato Balansi Jadvali:**
     - Har bir pastal bo'yicha har bir razmer (50, 52, 54, 56 va h.k.) uchun: Kirgan dona, Dazmoldan o'tdi, Kontrolda kutmoqda, 1-sort, 2-sort, **Ta'mir (Remont)** va Brak.
     - **Qayta Tikish / Mato Ehtiyoji (Defitsit):** Ta'mir va brakka qarab bichuv sexiga qaysi razmerdan nechta mato qayta buyurtma qilish kerakligi aniq ko'rsatiladi.
  7. **Bajarilgan Operatsiyalar Jadvali:** Ism-familiyasiz faqat amallar ketma-ketligi, soni va turi (tikim/dazmol).
  8. **179 ta avtomatlashtirilgan test** muvaffaqiyatli o'tdi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout e9af786
  ```

---

### 🔹 [Commit: `d9857c5`] — 07.10.2026, 18:42
* **Turi:** `fix(production)` — Arxitektura himoyasi va Bugfix
* **Mavzu:** Bilet va stiker kodlari daxlsizligi (Oltin Qoida), chop etilgan qutilarni muzlatish va Superadmin 500 xatolik tuzatishi
* **O'zgartirilgan fayllar:**
  - `production/models.py`
  - `production/services.py`
  - `production/norma_views.py`
  - `accounts/superadmin_views.py`
  - `.gitignore`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Oltin Qoida (`Ticket.save`):** Bilet bir marta yaratilgandan keyin uning `ticket_code`, `stiker_code` va `box_id` qiymatlari HECH QACHON yo'qolmaydi va o'zgarmaydi.
  2. **Chop etilgan qutilar muzlatilishi (`ensure_box_tickets_fresh`):** Agar quti chop etilgan bo'lsa (`box.is_printed=True`), uning biletlari va kodlari qayta generatsiya qilinmaydi va o'chirilmaydi. Faqat narx o'zgargan bo'lsa, mavjud kutilayotgan biletlar narxi yangilanadi.
  3. **Biletlari bor qutilar himoyasi (`sync_box_tickets_if_unscanned`):** Mavjud biletlari bor qutilarga tegilmaydi, faqat yangi biletlari yo'q qutilar uchun xavfsiz generatsiya qilinadi.
  4. Norma Canvas'dagi xavfli `ao.tickets.all().delete()` butunlay olib tashlandi (kechqurun server qotishi va stikerlar yangilanib ketish xavfi bartaraf etildi).
  5. `accounts/superadmin_views.py` da yetishmayotgan `from django.urls import reverse` importi qo'shildi (Oylik muzlatish/qayta hisoblashdagi 500 xatolik tuzatildi).
  6. `.gitignore` ga `*.sql.gz` fayllari qo'shildi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout d9857c5
  ```

---

### 🔹 [Commit: `40fab3c`] — 07.10.2026, 10:26
* **Turi:** `feat(plan)` — Yangi rol va statistika sahifasi
* **Mavzu:** PLAN roli qo'shilishi va tikim statistikasi sahifasiga yo'naltirish
* **O'zgartirilgan fayllar:**
  - `accounts/models.py`
  - `accounts/middleware.py`
  - `production/sewing_statistics_views.py`
  - `production/urls.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. `User.Role.PLAN` yangi roli qo'shildi.
  2. Plan roli foydalanuvchisi tizimga kirganda faqat tikim jarayoni statistikasi (`/sewing-statistics/daily/`) sahifasiga kira olishi ta'minlandi (boshqa sahifalar cheklandi).
  3. Qutilarni yopish yoki narxlarni o'zgartirish huquqi berilmadi (faqat ma'lumot oluvchi analitik rol).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 40fab3c
  ```

---

### 🔹 [Commit: `094fff9`] — 07.10.2026, 08:57
* **Turi:** `feat(control)` — Sozlama va boshqaruv
* **Mavzu:** OTK qabul qilishdan oldin barcha operatsiyalar skanerlangan bo'lishi talabini sozlamali (toggleable) qilish
* **O'zgartirilgan fayllar:**
  - `production/models.py`
  - `production/control_views.py`
  - `templates/control/index.html`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. `ControlSetting` modeliga `require_all_ops_scanned` parametri kiritildi.
  2. OTK planshetida agar barcha operatsiyalar tugallanmagan bo'lsa, qabul qilishni cheklash yoki ruxsat berish sozlamasi boshqaruv paneliga ulandi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 094fff9
  ```

---

### 🔹 [Commit: `db6b3b3`] — 06.10.2026, 20:49
* **Turi:** `fix(production)` — Barqarorlik va Performance
* **Mavzu:** Norma Canvas'dagi rekursiv bilet sinxronizatsiyasi va keng quti filtri natijasida server qotishini tuzatish
* **O'zgartirilgan fayllar:**
  - `production/models.py`
  - `production/norma_views.py`
  - `production/services.py`
  - `templates/norma/canvas.html`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. `sync_box_tickets_if_unscanned` dagi ortiqcha buyurtma ulanishlari olib tashlanib, faqat joriy artikul qutilariga cheklandi.
  2. Norma Canvas saqlash vaqtida brauzerda 35 soniyalik AbortController qo'shildi.
  3. Operatsiyalarni saqlashda rekursiv bilet yaratish chaqiriqlari to'xtatildi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout db6b3b3
  ```

---

### 🔹 [Commit: `2322256`] — 06.10.2026, 20:18
* **Turi:** `chore(config)` — Xavfsizlik va muhit
* **Mavzu:** Docker ishlab chiqarish muhitida DEBUG parametrini har doim False qilish
* **O'zgartirilgan fayllar:**
  - `config/settings.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. Docker muhitida xotira oqishi (memory leak) va xavfsizlik maqsadida `DEBUG=False` qat'iy o'rnatildi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 2322256
  ```

---

### 🔹 [Commit: `c0429f7`] — 06.10.2026, 20:13
* **Turi:** `perf(server)` — Xotira optimizatsiyasi
* **Mavzu:** Gunicorn workerlarni avtomatik qayta yuklash (max-requests recycling) va kesh hajmini optimallashtirish
* **O'zgartirilgan fayllar:**
  - `docker-compose.yml`
  - `config/settings.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. Gunicorn konfiguratsiyasiga `--max-requests 1000 --max-requests-jitter 100` qo'shildi, bu xotira to'lib ketishining oldini oladi.
  2. LRU keshlari hajmi kamaytirilib, server xotirasi barqarorlashtirildi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout c0429f7
  ```

---

### 🔹 [Commit: `ab38f50`] — 06.10.2026, 16:28
* **Turi:** `fix(pricing)` — Narxlar matritsasi tuzatishi
* **Mavzu:** Operatsiya o'chirilganda tartib raqamlarini (sequence) avtomatik siljitish
* **O'zgartirilgan fayllar:**
  - `production/norma_views.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. Narxlar matritsasida operatsiya olib tashlanganda keyingi barcha operatsiyalarning tartib raqamlari (sequence) avtomatik bir qadam surilishi ta'minlandi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout ab38f50
  ```

---

*Hujjat avtomatik ravishda har bir commit oldidan yangilab boriladi.*
