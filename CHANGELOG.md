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
