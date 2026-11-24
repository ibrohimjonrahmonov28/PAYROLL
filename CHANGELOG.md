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

### 🔹 [Commit: `612f7a7`] — 24.11.2026
* **Turi:** `feat(tayyorlov)` — `/tayyorlov/meto-tasnif/` login/parolsiz kutish ekrani
* **Mavzu:** Umumiy Meto va Tasnif plansheti (`/tayyorlov/meto-tasnif/`) endi istalgan qurilmadan login/parol so'ramasdan to'g'ridan-to'g'ri QR-birka kutish ekraniga ochiladi (xuddi `/master-tablet/` kabi). Amallar faqat skanerlangan faol `METO` yoki `TASNIF` masteri sessiyasi bilan bajariladi; oddiy `/tayyorlov/meto/` va `/tayyorlov/tasnif/` sahifalari login himoyasida qoladi.
* **O'zgartirilgan fayllar:**
  - `production/meto_flow_views.py`, `templates/meto_hub/meto_tasnif_kiosk.html`, `production/test_meto_flow.py`, `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `cde1dc4`] — 24.11.2026
* **Turi:** `fix(users)` — `/superadmin/users/` da `Meto masteri` roli, filtrlar va jadval belgilari
* **Mavzu:** Foydalanuvchi yaratish, tahrirlash va tezkor rol o'zgartirish oynalariga `METO` (`Meto masteri`) hamda `TASNIF` (`Tasnif masteri`) qo'shildi; jadvalda Tayyorlov va boshqa rollar “Oddiy User” o'rniga o'z nomi bilan chiqadigan qilindi; tepadagi filtrga `Meto` va `Tasnif` tablari qo'shildi.
* **O'zgartirilgan fayllar:**
  - `templates/superadmin/users.html`, `accounts/superadmin_views.py`, `production/test_meto_flow.py`

### 🔹 [Commit: `bb9b4f8`] — 23.11.2026
* **Turi:** `feat(tayyorlov)` — Meto va Tasnif uchun bitta umumiy QR-birkali planshet (`/tayyorlov/meto-tasnif/`)
* **Mavzu:** Foydalanuvchi talabi («meto va tasnif uchun bitta planshet bo'ladi, masterlar QR kodli birka bilan skaner qilsa o'z oynasi ochiladi, ekran kutishda turadi, kim qilgani aniq ko'rinadi»):
  - **1. Kutish ekrani va avtomatik yo'naltirish:** `/tayyorlov/meto-tasnif/` doim QR birka kutish ekranida turadi; `METO` birkasi → faqat Meto oynasi, `TASNIF` birkasi → faqat Tasnif oynasi ochiladi. Boshqa rollar hamda kirill klaviatura aniq xabar bilan rad etiladi.
  - **2. Aniq mualliflik:** barcha qabul, bekor qilish, qaytarish va tugatish amallari planshet akkauntiga emas, QR birkasini skanerlagan master nomiga (`_action_user`) yoziladi; Tasnif kutish kartochkalarida Metoni tugatgan master ham ko'rinadi.
  - **3. Xavfsiz qaytish va almashtirish:** «Chiqish» tugmasi planshetni tizimdan chiqarmasdan kutish ekraniga qaytaradi; 2 daqiqa harakatsizlik taymeri (modal va son kiritish paytida to'xtab turadi) + boshqa master QR skanerlaganda darhol o'tish; kompyuterdagi oddiy `/tayyorlov/meto/` va `/tayyorlov/tasnif/` o'zgarmagan.
  - **4. Birka yozuvlari:** bittalik, ommaviy va PDF birkalarda `METO MASTERI` va `TASNIF MASTERI` (shuningdek sklad rollari) aniq ko'rsatiladi.
* **O'zgartirilgan fayllar:**
  - `production/tayyorlov_urls.py`, `production/meto_flow_views.py`
  - `templates/meto_hub/meto_tasnif_kiosk.html`, `templates/meto_hub/hub.html`, `templates/meto_hub/flow/meto.html`, `templates/meto_hub/flow/tasnif.html`
  - `templates/superadmin/user_badge_print.html`, `templates/superadmin/users_badges_batch_print.html`, `accounts/superadmin_views.py`
  - `production/test_meto_flow.py` (`MetoTasnifKioskTest`), `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `f065f1b`] — 23.11.2026
* **Turi:** `feat(konteyner)` — Yorliqdagi raqamlar ustma-ust, tik
* **Mavzu:** Foydalanuvchi talabi («raqamlar ustma-ust tik tursin va sonlar kattalashgani sari yani 1000 bo'lsa sig'ishi kerak»):
  - **1. Ustma-ust raqamlar:** o'ngdagi nolsiz raqamning har xonasi alohida qatorda, tik turadi (50 → 5 tepada, 0 pastda).
  - **2. Doim sig'adi:** shrift xonalar soniga qarab kichrayadi — 7 ~39 mm, 50 ~32 mm, 999 ~21 mm, 1000 ~16 mm, 10000 ~13 mm.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/container_labels_print.html`
  - `production/meto_container_views.py`
  - `production/test_meto_containers.py`
  - `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `58d08ed`] — 22.11.2026
* **Turi:** `feat(konteyner)` — Konteyner yorlig'ining yangi formati
* **Mavzu:** Foydalanuvchi talabi («uni ga chiqadigan stiker ni formati o'zgaradi yani 100 x 80 bo'ladi chap tomonda katta QR kod bo'ladi undan konteyner id yonida terry jar yozuvi va tepadan pastga 0 lar olib tashlangan holatda id bo'ladi. misol id 000050 bo'lsa 50 yoziladi u yuqoridan pastga yoziladi shunda butun list ishlatiladi va har bir pechatda 2 tadan chiqadi bir quti uchun 2 tomoniga»):
  - **1. Yangi joylashuv:** chapda katta QR, ostida KONT-000050 va TERRY JAR; o'ngda nolsiz raqam (50) yuqoridan pastga, butun balandlik bo'ylab. QR matni o'zgarmagan — eski yorliqlar ham ishlaydi.
  - **2. Raqam o'lchami:** xonalar soniga qarab (1–3 xonali ~31 mm, 5 xonali ~18 mm), doim yorliqqa sig'adi.
  - **3. Har konteynerga 2 ta yorliq** (ikki tomoniga), ketma-ket; bir martada 250 ta konteyner (500 ta yorliq).
  - **4. Ta'mir konteyneri:** qora «TA'MIR KONTEYNERI» belgisi bilan ajraladi.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/container_labels_print.html`
  - `templates/meto_hub/containers.html`
  - `production/meto_container_views.py`
  - `production/container_services.py`
  - `production/test_meto_containers.py`
  - `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `37eeffd`] — 21.11.2026
* **Turi:** `feat(tayyorlov)` — Bosh sklad: ixcham ro'yxat, filtrlar, qidiruv, Koson/Uychiga jo'natish va jonli yangilanish
* **Mavzu:** Foydalanuvchi talabi («https://terryjar.uz/tayyorlov/sklad/ da men hozirgina konteynerga qutini biriktirdim ammo bu sahifada o'zgarmasdan turibdi va yana bu yerda ham list emas keyin bu yer sklad emas bosh sklad bo'ladi va bu yerda filterlar bo'lishi kerak stiker chiqmagan konteyner birikmagan va pastal bo'yicha qidirish funksiyalari va uni ham Kosonga Uychiga jo'natish funksiyasi bo'lishi kerak»):
  - **1. Jonli yangilanish:** shu sahifada biriktirilgach ro'yxat darhol (sahifa yuklanmasdan) yangilanadi; planshetdagi o'zgarishlar har 20 soniyada ko'rinadi. Yozilayotgan kg va belgilangan qatorlar saqlanadi.
  - **2. «Bosh sklad» va ixcham ro'yxat:** bo'lim nomi «Bosh sklad»; katta kartalar o'rniga planshetdagi kabi jadval. Uychi skladi sahifasi ham shu ro'yxatda.
  - **3. Filtrlar va qidiruv:** Kg kiritilmagan, Stiker chiqmagan, Konteyner birikmagan, Tayyor, Planshetda tekshiruvda (sanog'i bilan); pastal / zakaz / artikul yoki konteyner QR / quti stikeri bo'yicha qidiruv.
  - **4. Jo'natish:** har qatorda «Koson patokiga» (konteyner tekshiruvi) va «Uychiga» (planshetda tekshirib yuk xati); tanlanganlarni birdan Uychiga. Tayyor bo'lmaguncha tugmalar o'chiq, sababi yoziladi. Kg ro'yxatda kiritiladi / tuzatiladi.
  - **5. Tuzatish:** tekshiruvni «Bekor qilish» tugmasi endi ishlaydi (avval mavjud bo'lmagan funksiyani chaqirardi).
* **O'zgartirilgan fayllar:**
  - `production/meto_flow_views.py`
  - `templates/meto_hub/flow/sklad.html`, `uychi.html`, `_link_panel.html`
  - `templates/meto_hub/flow/_sklad_top.html`, `_sklad_filters.html`, `_sklad_list.html`, `_sklad_bottom.html`, `_sklad_js.html` (yangi)
  - `templates/meto_hub/flow/_sklad_card.html` (olib tashlandi)
  - `production/test_meto_flow.py`
  - `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `dbf4920`] — 21.11.2026
* **Turi:** `fix(tayyorlov)` — Planshetda quti va konteyner istalgan tartibda biriktiriladi, avto-Enter, aniq xato xabarlari
* **Mavzu:** Foydalanuvchi talabi («konteyner id KONT-000050 ... /tayyorlov/tablet/ da qutiga biriktirish uchun yozsam stiker yoki konteyner topilmadi deyapti ... auto enter ham kerak ... stikerni qr kodini scan qilsa u orqali quti id bilan konteyner id boglanishi kerak»):
  - **1. Tartib erkin:** avval konteyner (qo'lda yozilgan ID), keyin stiker QR — yoki teskarisi; ikkalasi ma'lum bo'lishi bilan o'zi biriktiriladi. Boshqa quti urilsa tanlov almashadi. `/tayyorlov/sklad/` va `/tayyorlov/uychi/` panelida ham shunday.
  - **2. Avto-Enter:** skaner Enter yubormasa ham, qo'lda to'liq `KONT-000123` yozilganda ham o'zi yuboriladi (chala kod yuborilmaydi); yuk xati / patok / Uychi tekshiruv oynalarida ham. Qo'lda yozilgan quti kodi uchun «OK» tugmasi.
  - **3. Aniq xatolar:** yaroqsiz konteyner/quti (kichik, band, bo'sh emas, bekor qilingan, allaqachon biriktirilgan, skladda emas) urilgan zahoti aytiladi; yaratilmagan KONT kodi uchun «tizimda yo'q, oxirgi yaratilgani KONT-…»; kirill klaviatura uchun «lotinga o'tkazing» maslahati; uzun chiziqcha (–, —) oddiy «-» deb olinadi.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/tablet.html`
  - `templates/meto_hub/flow/_link_panel.html`
  - `templates/meto_hub/_scan_auto_enter.html` (yangi)
  - `production/meto_flow_views.py`
  - `production/container_services.py`
  - `production/test_meto_flow.py`
  - `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `4b9d074`] — 21.11.2026
* **Turi:** `fix(tayyorlov)` — /tayyorlov/ tekshiruvida topilgan xatolar tuzatildi
* **Mavzu:** Foydalanuvchi talabi («https://terryjar.uz/tayyorlov/ ni qildim qanday xatolar bor organib chiq toliq», keyin «qolgan muamolarni hal qil»). Telegram bot va eski `/meto-hub/` egasi qarori bilan hozircha o'zgarmaydi («eski holat hozircha ishlasin»).
  - **1. Yuk xati va patok to'qnashuvi:** tekshiruvga yuborilgan yoki yo'ldagi pastal patokka berilmaydi, planshetda «tayyor» ko'rinmaydi; yuk xati jo'natilishdan oldin pastallar qulflanib qayta tekshiriladi (avval bitta pastal ham patokka, ham mashinaga ketardi).
  - **2. To'g'ridan-to'g'ri yuklash:** xato bo'lsa «tekshiruv kutmoqda» yuk xati qolib ketmaydi (bitta tranzaksiya).
  - **3. Beta belgisi:** boshlangan pastallar belgi o'chirilsa ham yangi oqimda tugaydi; har yoqilganda hisob «hozirdan» boshlanadi.
  - **4. Uychidan qaytgan bo'sh konteyner:** Kosonda biriktirilganda avtomatik Kosonga o'tadi («Uychi dan bo'sh qaytdi» voqeasi).
  - **5. Ruxsatlar:** kg ni faqat Tayyorlov skladi kiritadi; Koson → Uychi yuk xatini faqat Koson jo'natadi; har filial faqat o'ziga kelgan yuk xatini qabul qiladi; Uychi sklad planshetida filial roldan olinadi.
  - **6. Tezlik:** planshet va sklad sahifasidagi SQL so'rovlar patokdagi pastallar soniga qarab o'smaydi.
  - **7. Mayda:** Meto avto-yangilanishi saqlanmagan sonni o'chirmaydi; noto'g'ri qiymatda 400; planshet JSON ekranlangan.
* **O'zgartirilgan fayllar:**
  - `production/meto_flow.py`
  - `production/meto_flow_views.py`
  - `production/meto_tablet_views.py`
  - `templates/meto_hub/flow/meto.html`
  - `production/test_meto_flow.py`
  - `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `1536e26`] — 21.11.2026
* **Turi:** `feat(tayyorlov)` — Meto Planshet: Tasnifdan chiqqan pastallarga kg kiritish, paketlangan (qutisiz) holat va Meto Sklad vizual farqlanishi
* **Mavzu:** Foydalanuvchi talabi («tasnifdan chiqqan ishlar har doim kg olinadi va saqlash uchun qoyiladi farqi doim ham qutiga solinmaydi bazida quti yetmaganda paketlab qoyadi shuni ham hisobga ol yani tasnifdan chiqqandan keyin u meto planshetda kg kiritladigan pastallar royxati paydo boladi kg kirgandan keyin avtomatik metoskladga tushadi https://terryjar.uz/meto-hub/?tab=sklad dagu stilda boladi faqat farqi konteyner yoki stiker chiqmaganlari avotatik belgilangan ajralib turadi»):
  - **1. Tasnifdan chiqqan pastallar (Kg kiritish ro'yxati):**
    Tasnif tugagach (`finish_tasnif`), pastal bosh binoda skladda bo'ladi, lekin hali og'irligi (`actual_weight_kg`) kiritilmagan bo'ladi. Meto Planshetda (`/tayyorlov/tablet/` Koson) maxsus **«Tasnifdan Chiqqan Pastallar (Kg kiritish)»** bo'limi paydo bo'ladi. Planshetda sensorli qulay raqamli kiritish orqali real kg kiritiladi va «Skladga Qabul» tugmasi bosiladi (`/tayyorlov/api/tablet/kg-receive/`).
  - **2. Paketlangan (Qutisiz) holatni to'liq qo'llab-quvvatlash:**
    Qutilar yetmagan paytda pastallar paketlab qo'yiladi (`boxes_count == 0`). Tizim hech qachon quti yoki stiker talab qilmasdan kg ni kiritishga va omborga qabul qilishga to'liq ruxsat beradi.
  - **3. Avtomatik Meto Skladga o'tish va Holatlar bo'yicha aniq ajralib turish:**
    Kg kiritilgach, pastal darhol «Meto Sklad» (Saqlanayotgan pastallar) ro'yxatiga tushadi. `https://terryjar.uz/meto-hub/?tab=sklad` uslubida kartochkalar, o'lchamlar, jami tasnif, real og'irlik va qabul vaqti aks etadi. Kartochkalar holatiga ko'ra avtomatik ajralib turadi:
    - **📦 PAKETDA (Qutiga solinmagan):** Zarg'aldoq (amber) ramka va miltillovchi yorliq.
    - **🏷️ STIKER KUTILMOQDA:** Binafsharang (purple) ramka va stiker chop etilmagani haqida yorliq.
    - **🔗 KONTEYNER KUTILMOQDA:** Qizil-pushti (rose) ramka va 1-bo'limga o'tib konteynerga biriktirish havolasi.
    - **✅ TO'LIQ TAYYOR:** Yashil (emerald) ramka va to'g'ridan-to'g'ri patokka berish imkoniyati.
  - **4. Tezkor filtrlar va qidiruv:** Meto Sklad ichida `Barchasi`, `📦 Paketda`, `🏷️ Stiker`, `🔗 Konteyner`, `✅ Tayyor` filtr tugmalari va qidiruv satri.
* **O'zgartirilgan fayllar:**
  - `production/meto_tablet_views.py`
  - `production/meto_flow_views.py`
  - `production/tayyorlov_urls.py`
  - `templates/meto_hub/tablet.html`
  - `templates/meto_hub/flow/_sklad_card.html`
  - `production/test_meto_flow.py`
* **Turi:** `feat(tayyorlov)` — Meto Planshet: Bosh bino tekshiruvi (20/20 qizil/yashil), yuk xati jo'natish, chop etish va Uychi filialida konteyner orqali qabul qilish
* **Mavzu:** Foydalanuvchi talablari asosida to'liq zanjir joriy etildi:
  - **1. Quti ↔ Konteyner biriktirish va bekor qilish xavfsizligi:** Qutilar va konteynerlarni biriktirish huquqi faqat Koson bosh binosida bo'ladi. Konteynerni qutidan ajratish (bo'shatish) yoki bekor qilish rejimiga o'tishda ogohlantiruvchi tasdiqlash (`confirm()`) darchasi qo'shildi.
  - **2. Tayyorlov bo'limi (Kompyuter / Desktop):** Skladdagi tayyor pastallarni tanlab "Tekshirishga yuborish (Planshetga)" tugmasi orqali Meto planshetiga yo'naltirish (`Shipment.Status.PENDING`). Planshetda tekshirilayotgan pastallar uchun "Bekor qilish" imkoniyati. Planshetda tekshiruv yakunlanib jo'natilgach (`IN_TRANSIT`), Tayyorlov masteri ekranida darhol "Chop etish" tugmasi ochilishi (`/tayyorlov/yukxat/<id>/chop-etish/`).
  - **3. Meto Planshet (Koson Bosh Bino):** "Tekshirish kutayotgan pastallar" ro'yxati chiqadi. Pastal tanlanganda tekshirish boshlanadi: undagi qutilar soni (masalan 20 ta) bo'lsa, yuqoridagi status bar `20/20 o'tdi` bo'lmaguncha QIZIL (`🔴 X/20 o'tdi (Chala)`) turadi. Barcha qutilar skanerlangach YASHIL (`🟢 20/20 o'tdi (To'liq)`) ga aylanadi va keyingi pastalga o'tadi. Barcha pastallar to'liq yashil bo'lgach, «Yuk xati uchun jo'natish» tugmasi ochiladi va yuk yo'lga chiqariladi.
  - **4. Uychi Filiali Sklad Plansheti:** Kelgan yuk xatlari bo'yicha "Tekshiruvga Olish" tugmasi. Pastallar bo'yicha har bir konteyner skanerlanganda, tizim avtomatik ravishda qaysi quti va pastalga tegishli ekanini aniqlaydi va yashil qiladi. Barchasi tugagach «Saqlash (Filial Balansiga Tushirish)» tugmasi bosiladi va barcha pastallar hamda konteynerlar Uychi filiali balansiga o'tadi.
* **O'zgartirilgan va yangi fayllar:**
  - `production/models.py` (`PENDING` status qo'shildi)
  - `production/migrations/0056_alter_shipment_status_alter_shipmentpastal_status.py`
  - `production/meto_flow.py` (`request_verification`, `cancel_verification`, `dispatch_shipment`, `receive_shipment`)
  - `production/meto_flow_views.py` (API endpoints va `_card`, `_ctx_sklad`)
  - `production/meto_tablet_views.py` (`_tablet_pending_shipments`, `_tablet_incoming_shipments`, context)
  - `production/tayyorlov_urls.py`
  - `templates/meto_hub/flow/sklad.html`
  - `templates/meto_hub/flow/_sklad_card.html`
  - `templates/meto_hub/tablet.html`
  - `production/test_meto_flow.py`

### 🔹 [Commit: `6f572d6`] — 21.11.2026
* **Turi:** `feat(tayyorlov)` — Meto Planshet: Uychi filialida quti biriktirishni yashirish, faqat yuk qabuli, omborda saqlash va patokka berish
* **Mavzu:** Egasi talabi («https://terryjar.uz/tayyorlov/tablet/?branch=UYCHI da quti bilan konteyner birlashtirish funksiyasi bolmaydi ular faqat yukni qabul qiladi saqlaydi va kerakli paytda trekshirib patokka beradi»):
  - **Quti ↔ Konteyner biriktirish rejimi Uychida olib tashlandi:** Uychi filialida qutilar alohida konteynerga biriktirilmaydi (bu faqat Koson bosh binosida amalga oshiriladi). `1. Biriktirish` tugmasi va rejimi Uychi planshetida to'liq yashirildi.
  - **1. Yuk Qabuli:** Kosondan yo'lda kelayotgan yuk xatlarini ko'rish, konteynerlarni skanerlab omborga qabul qilish (`/tayyorlov/api/receive/<line_id>/`).
  - **2. Saqlash (Ombor):** Uychi omborida saqlanayotgan pastallar va ularning konteynerlari ro'yxati (har bir kartochkada saqlanayotgan `KONT-...` kodlari vizual ko'rsatiladi va to'g'ridan-to'g'ri patokka berish tugmasi mavjud).
  - **3. Patokka Berish:** Uychi tikuv patoklariga (`U1`..`U6`) konteynerlarni skanerlab tekshirib chiqarish (`/tayyorlov/api/give/`).
  - **Statistika va navigatsiya:** Uychi filiali uchun alohida konteyner va saqlash hisoblagichi, Uychi ochilganda avtomatik ravishda Yuk Qabuli yoki Saqlash rejimining ochilishi (`default_mode`).
* **O'zgartirilgan fayllar:**
  - `production/meto_tablet_views.py`
  - `templates/meto_hub/tablet.html`
  - `production/test_meto_flow.py`

### 🔹 [Commit: `c8ec6b4`] — 20.11.2026
* **Turi:** `feat(tayyorlov)` — Meto Planshet (Tablet) interfeysi: Quti ↔ Konteyner biriktirish, Yuk xati tekshiruvi va Patokka yuk berish
* **Mavzu:** Meto ombori va tayyorlov xodimlari planshet/mobil qurilmalari orqali ishlashi uchun maxsus planshet UI (`/tayyorlov/tablet/` / `/tayyorlov/planshet/`):
  - **1. Quti ↔ Konteyner biriktirish (Box ↔ Container Linking):** Sklad va filiallar bo'yicha konteynerlar holati, qutilarni konteynerga skaner orqali biriktirish (`api/link/`) yoki ajratish (`api/unlink/`), tezkor filtrlash va avtomatik yuklanish.
  - **2. Yuk xati tekshiruvi (Shipment Verification & Receive):** Filialdan kelayotgan yoki yuborilayotgan yuk xatidagi konteynerlarni skanerlash orqali solishtirish, har bir konteynerni tekshirib qabul qilish (`api/receive/`) yoki jo'natish (`api/ship/`).
  - **3. Patokka berish tekshiruvi (Patok Dispatch & Verification):** Tikuv patoklariga chiqarilayotgan konteynerlarni qat'iy nazorat bilan tekshirish (`api/give/`), patok tanlash va chiqarish holatini kuzatish.
  - **Interfeys va UX:** Planshetlar uchun moslashtirilgan katta tugmalar (touch-first), shtrix-kod skanerini uzluksiz qabul qilish (auto-focus), Web Audio API orqali muvaffaqiyatli/xato skan tovushlari (audio chimes), va jonli statistika API (`/tayyorlov/api/tablet/state/`).
  - **Navigatsiya:** Tayyorlov menyusiga va Meto Hub boshqaruv paneliga «Planshet» tezkor o'tish tugmalari qo'shildi.
* **O'zgartirilgan va yangi fayllar:**
  - `production/meto_tablet_views.py` (yangi)
  - `templates/meto_hub/tablet.html` (yangi)
  - `production/tayyorlov_urls.py`
  - `templates/tayyorlov/base_tayyorlov.html`
  - `templates/meto_hub/hub.html`
  - `production/test_meto_flow.py`

### 🔹 [Commit: `ed9c4f5`] — 20.11.2026
* **Turi:** `feat(tayyorlov)` — `/tayyorlov/stiker/` sahifasini to'liq Desktop keng ekranli (widescreen) formatga o'tkazish
* **Mavzu:** Egasi talabi («https://terryjar.uz/tayyorlov/stiker/ sahifa full desktop versiyada boladi»):
  - **Keng ekranli konteyner:** Tor `max-w-7xl` (1280px) cheklovi olib tashlanib, `w-full` hamda `base_tayyorlov.html` da `max-w-[1920px]` ga kengaytirildi (katta monitorlarda chetlaridagi bo'sh qora joylar yo'qotildi).
  - **Desktop 4-ustunli KPI qatori:** Chop etilmagan qutilar, Tikuvga berilganlar, Stikerli buyurtmalar va Stiker o'lchami (65x45 mm).
  - **Desktop To'liq Jadvali:** Zakaz raqami va muddati, Model/Artikul (kodi, nomi va rasm thumbnaili), Buyurtmachi, Pastallar soni va bo'linmaganlik ko'rsatkichi, Tasnif soni (dona), Qutilar soni va vizual progress bar (chop etilgan/jami foizi bilan), hamda tezkor amallar («Qutilarga Bo'lish» / «Qutilarni Ochish» va «Chop Etish»).
  - **Buyurtma qutilari sahifasi:** `/tayyorlov/stiker/orders/<id>/` ham to'liq desktop kenglikka (`w-full`) moslashtirildi.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/flow/stiker.html`
  - `templates/tayyorlov/stiker_order_boxes.html`
  - `templates/tayyorlov/base_tayyorlov.html`
  - `production/tayyorlov_stiker_views.py`
  - `CHANGELOG.md`

---

### 🔹 [Commit: `454029f`] — 20.11.2026
* **Turi:** `feat(tayyorlov)` — Tasnifda Qaytarish + Butun ekranli «Pastalni tugatish» oynasi va Tayyorlov Stiker (`/stickers/` ning 1 ga 1 nusxasi)
* **Mavzu:** Egasi talabi bo'yicha Tasnif → Stiker zanjiri to'liq yo'lga qo'yildi (eski `/stickers/` bo'limiga tegilmasdan):
  - **Tasnif kutmoqda (chap ustun):** «Qabul qilish» bilan birga **«Qaytarish»** tugmasi qo'shildi — sabab bilan Meto bo'limiga qaytarish (`PastalFlow.tasnif_return_reason/returned_by/returned_at`, `0055` migratsiya); Metoda «Tasnifdan qaytarildi» ogohlantirishi ko'rsatiladi.
  - **Tasnif bo'lmoqda (o'ng ustun):** Pastal paralanib bo'lgach **«Pastalni tugatish»** tugmasi bosiladi va **butun ekran rejimida** razmerlar sonini kiritish oynasi ochiladi (Meto sonidan ko'paytirish/kamaytirish, jonli farq). Oyna faqat **«Saqlash»** yoki **«Bekor qilish»** bosilganda yopiladi (Escape va tashqariga bosish bloklangan).
  - **Pastal ma'lumotlari:** Saqlanganda tasnif soni ham `PastalFlowSize.tasnif_qty`, ham `CuttingBatchItem.real_quantity` ga yoziladi; `/statistics/?pastal_info=...` kartochkasida ham Tasnif ustuni va vaqt chizig'i ko'rinadi.
  - **Tayyorlov Stiker (`/tayyorlov/stiker/` va `/tayyorlov/stiker/orders/<id>/`):** Eski `https://terryjar.uz/stickers/` ning 1 ga 1 nusxasi yaratildi (eski `/stickers/` o'zgarishsiz qoldi). Farqi: Tasnifdan o'tgan pastallar avtomatik ro'yxatga tushadi, sonlar Tasnifdan keladi, stikerchi ularni qutilarga bo'ladi va xatolar hisobiga chop etilguncha qo'lda o'zgartira oladi (chop etilgach oltin qoida bo'yicha qulflanadi).
* **O'zgartirilgan / yangi fayllar:**
  - `production/models.py`, `production/migrations/0055_pastalflow_tasnif_return.py`
  - `production/meto_flow.py`, `production/meto_flow_views.py`, `production/tayyorlov_urls.py`
  - `production/tayyorlov_stiker_views.py` (yangi — `/stickers/` ning Tayyorlov 1 ga 1 nusxasi)
  - `production/pastal_info.py`, `templates/production/partials/pastal_info_card.html`
  - `templates/meto_hub/flow/tasnif.html`, `templates/meto_hub/flow/meto.html`, `templates/meto_hub/flow/stiker.html`
  - `templates/tayyorlov/stiker_order_boxes.html`, `templates/tayyorlov/partials/stiker_batch_boxes.html` (yangi)
  - `production/test_meto_flow.py` (`48` ta test — barchasi o'tdi)
* **Serverda:** `git pull origin main && docker compose exec web python manage.py migrate && docker compose restart web`

---

### 🔹 [Commit: `f383853`] — 20.11.2026
* **Turi:** `refactor(tasnif)` — Tasnif sahifasidan tugatilgan pastallar tarixi olib tashlandi
* **Mavzu:** Egasi talabi («Tugatilgan pastallar tarixi (1) kerak emas»): Tasnif bo'limi faqat oqimdagi faol pastallarga qaratildi:
  - Pastdagi «Tugatilgan pastallar tarixi» jadvali va sahifalash bloki butunlay olib tashlandi.
  - Filtrlash panelidan ortiqcha holat va faqat tarix jadvaliga xizmat qilgan smena tanlagichi olib tashlandi.
  - Smena statistikasi yuqoridagi ixcham banner va joriy smena KPI kartasida to'liq saqlab qolindi.
  - Sahifa sof 2 ustunli («Tasnif kutmoqda» va «Tasnif bo'lmoqda») qulay sensorli interfeysga keltirildi.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/flow/tasnif.html`: tarix jadvali va ortiqcha filtrlar olib tashlandi
  - `production/test_meto_flow.py`: tarix jadvali chiqmasligi bo'yicha regressiya testi
* **Serverda:** `git pull origin main && docker compose restart web`

---

### 🔹 [Commit: `d776b58`] — 20.11.2026
* **Turi:** `feat(tasnif)` — Tayyorlov Tasnif bo'limi: 2 smena tizimi, 2 tomonli ekran (kutmoqda / bo'lmoqda), qabulni bekor qilish va «Meto omboriga jo'natish»
* **Mavzu:** https://terryjar.uz/tayyorlov/tasnif/ bo'limi egasi talabi bo'yicha to'liq yangilandi:
  - Ekran 2 tomonga bo'lindi: chap tomonda «Tasnif kutmoqda», o'ng tomonda «Tasnif bo'lmoqda».
  - «Qabul qilish» bosilganda pastal chap tomondan o'ng tomonga o'tadi.
  - Yangi imkoniyat: «Qabulni bekor qilish» tugmasi orqali adashib qabul qilingan pastalni yana kutmoqda holatiga qaytarish mumkin.
  - 2 smenali ish tartibi tatbiq etildi (`08:00–18:00` 1-smena, `18:00–08:00` 2-smena / tungi; `00:00–07:59` vaqtidagi ishlar kechagi ish kunining 2-smenasiga yoziladi).
  - Jonli smena banneri, 1- va 2-smena, joriy smena va 24 soatlik kun jami KPI ko'rsatkichlari.
  - Razmerlar bo'yicha aniq son kiritish: `[-]` va `[+]` qadam tugmalari, `onfocus="this.select()"`, har bir razmerda va pastal jami bo'yicha meto soniga nisbatan jonli farq indikatori (`✓`, `+`, `-`).
  - Tugatish tugmasi nomi egasi aytgandek «Meto omboriga jo'natish» deb belgilandi.
  - Formani topshirishda ortiqcha yoki kamomad bo'lsa, razmerlar kesimida tasdiqlash so'raladi.
  - Qidiruv (`/` tezkor tugmasi bilan), zakaz, holat (`faol`, `wait`, `work`, `done`) va smena (`current`, `day`, `s1`, `s2`) filtrlari, avto-yangilanish (`30s`, `60s`).
  - Tugatilgan pastallar tarixi jadvali: Meto → Tasnif sonlari, farqi, smena badji, tasnifchi, vaqt va joriy bosqich.
* **O'zgartirilgan fayllar:**
  - `production/meto_flow.py`: `cancel_tasnif(batch, user)` funksiyasi va son tekshiruvi
  - `production/meto_flow_views.py`: `_card` (tasnif_shift, tasnif_diff), `_ctx_tasnif` (2 smena KPI, filtrlash, qidiruv), `tasnif_cancel` view
  - `production/tayyorlov_urls.py`: `/<batch_id>/tasnif/bekor/` yo'nalishi
  - `templates/meto_hub/flow/tasnif.html`: 2 tomonli ekran, smena paneli, stepplar, farq hisoblagich, «Meto omboriga jo'natish»
  - `production/test_meto_flow.py`: `test_tasnif_lifecycle_take_cancel_finish`, `test_tasnif_two_shifts`, `test_tasnif_ui_elements_and_two_columns`
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).

---

### 🔹 [Commit: `09c2b9f`] — 20.11.2026
* **Turi:** `feat(meto)` — Tayyorlov Meto bo'limi uchun sensorli ekran qulayliklari (stepperlar, izoh shablonlari, kamomad ogohlantirishi, avto-yangilanish)
* **Mavzu:** Meto bo'limida planshet/ekranda ishlash qulaylashtirildi: har bir razmer yoniga `[-]` va `[+]` qadam tugmalari qo'shildi, katakka bosilganda son to'liq tanlanadi (`onfocus="this.select()"`), `meto_note` maydoni ostida tezkor izoh shablonlari (`Qavat yetmadi`, `Mato nuqsoni`, va h.k.), kamomad va ortiqchalik bo'yicha batafsil ogohlantirish, `/` tugmasi bilan qidiruvga o'tish, aqlli avto-yangilanish (`30s`, `60s`), hamda `wait` yoki `work` filtrlarida tarix jadvalini yashirish kiritildi.
* **O'zgartirilgan fayllar:**
  - `templates/meto_hub/flow/meto.html`: stepper tugmalari, izoh chiplari, qidiruv avtofokusi, avto-yangilanish tugmasi va skripti
  - `production/test_meto_flow.py`: `test_meto_ui_steppers_chips_and_holat_visibility`
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 09c2b9f
  ```

---

### 🔹 [Commit: `b059688`] — 20.11.2026
* **Turi:** `revert(backup)` — `f242eb5` dagi tungi rasmlar (media) zaxirasi olib tashlandi
* **Mavzu:** Egasi qarori: rasmlar zaxirasi kerak emas. Faqat zaxira qismi qaytarildi; `f242eb5` ga tushib qolgan boshqa o'zgarishlar (0052 migratsiya, meto shabloni, dispatch belgisi) joyida. Tungi baza zaxirasi (.sql.gz) avvalgidek ishlaydi.
* **O'zgartirilgan fayllar:**
  - `production/media_backup.py`, `production/management/commands/send_media_backup.py`, `production/test_media_backup.py` (o'chirildi)
  - `production/management/commands/run_scheduler.py` (`f242eb5` dan oldingi holat), `CLAUDE.md`
* **Serverda:** `git pull origin main && docker compose restart scheduler`

---

### 🔹 [Commit: `159c5bb`] — 20.11.2026
* **Turi:** `feat(meto)` — Tayyorlov Meto bo'limi uchun 2 smenali ish tartibi va hisob-kitobi
* **Mavzu:** Meto bo'limida 2 smena hisobi yo'lga qo'yildi (`08:00–18:00` 1-smena, `18:00–08:00` 2-smena / tungi; tungi soat `00:00–07:59` kechagi ish kuniga tegishli). Tugatilgan pastallar avtomatik joriy smenaga yoziladi; sahifada jonli smena ko'rsatkichi, joriy smena va 24 soatlik ish kuni KPI ko'rsatkichlari, smena filtri (`current`, `day`, `s1`, `s2`) va tarix jadvalida smena badji qo'shildi.
* **O'zgartirilgan fayllar:**
  - `production/shifts.py` (yangi): `Asia/Tashkent` vaqt mintaqasida 2 smenali ish kuni sikli va intervallar hisobi (`get_shift_info`, `get_shift_for_timestamp`, `get_work_day_shifts`)
  - `production/meto_flow_views.py`: `_ctx_meto` da joriy smena, 24 soatlik kun va 1/2-smena ajratmasi, `_card` da `meto_shift` biriktirilishi
  - `templates/meto_hub/flow/meto.html`: jonli smena paneli, joriy smena KPI kartasi, smena filtri va tarix jadvalida smena badji
  - `production/test_meto_flow.py`: `MetoTwoShiftsTest` (barcha 39 ta test muvaffaqiyatli)
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 159c5bb
  ```

---

### 🔹 [Commit: `f242eb5`] — 20.11.2026
* **Turi:** `feat(backup)` — Har kecha Telegramga media fayllar (rasmlar va QR kodlar) zaxirasini yuborish
* **Mavzu:** Dushanba kechalari to'liq media arxivi, boshqa kechalari esa haftalik o'zgarishlar (incremental) Telegram backup chatiga yuboriladi (50 MB chegarasi uchun avtomatik bo'laklash bilan).
* **O'zgartirilgan fayllar:**
  - `production/media_backup.py`, `production/management/commands/send_media_backup.py`, `production/test_media_backup.py`
  - `production/management/commands/run_scheduler.py`
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout f242eb5
  ```

---

### 🔹 [Commit: `a9b5a5d`] — 20.11.2026
* **Turi:** `feat(tayyorlov)` — `/tayyorlov/` bosh menyusi 3 zonali dizayni va alohida bo'lim sahifalarini izolyatsiya qilish
* **Mavzu:** Tayyorlov menyusi `Inter` shriftli zamonaviy 3 zonali ko'rinishga o'tkazildi (Bosh bino `01–04`, Uychi va logistika, Inventar va sozlamalar); alohida bo'lim sahifalaridan (`/tayyorlov/meto/`, `/tasnif/`, `/konteynerlar/` va b.) boshqa bo'limlarga barcha havolalar olib tashlandi.
* **O'zgartirilgan fayllar:**
  - `templates/tayyorlov/base_tayyorlov.html` (yangi): Tayyorlov uchun alohida zamonaviy asos (boshqa bo'lim navigatsiyasisiz, Kun/Tun qo'llab-quvvatlovi bilan)
  - `templates/meto_hub/hub.html`, `production/meto_flow_views.py`: 3 zonali Hub, tezkor ko'rsatkichlar va jonli pilllar
  - `templates/meto_hub/flow.html`, `containers.html`, `container_detail.html`: faqat joriy bo'limga xos mustaqil sahifalar
  - `production/test_meto_flow.py`, `.claude/project-knowledge/*.json`: 3 zona va boshqa bo'lim havolalari yo'qligi testlari
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout a9b5a5d
  ```

---

### 🔹 [Commit: `ee7c32a`] — 19.11.2026
* **Turi:** `feat(tayyorlov)` — `/tayyorlov/` bosh menyusi (Hub) va har bir bo'lim uchun alohida `/tayyorlov/<bolim>/` sahifalar
* **Mavzu:** Tayyorlov bo'limi bitta `?tab=` sahifasidan bosh menyu (Hub) va mustaqil URL manzillarga ajratildi; bo'lim kartochkalari yangi brauzer tabida ochiladi.
* **O'zgartirilgan fayllar:**
  - `production/tayyorlov_urls.py`, `production/meto_flow_views.py`: `/tayyorlov/` Hub (`flow_view`), `/tayyorlov/meto/`, `/tasnif/`, `/stiker/`, `/sklad/`, `/uychi/`, `/yukxat/`, `/zakazlar/` (`section_view`) va `?tab=` redirect
  - `templates/meto_hub/hub.html` (yangi), `flow.html`, `flow/meto.html`, `flow/zakazlar.html`, `shipment_print.html`: yangi tabda ochiluvchi Hub kartochkalari va alohida sahifa navigatsiyasi
  - `production/meto_views.py`, `templates/meto/dashboard.html`, `templates/meto/order_detail.html`: eski `/meto/` dan `/tayyorlov/meto/` ga o'tkazish
  - `CLAUDE.md`, `.claude/project-knowledge/*.json`, `production/test_meto_flow.py`: hujjat (2 smenali hisobot rejasi bilan) va yangilangan testlar
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya talab qilinmaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout ee7c32a
  ```

---

### 🔹 [Commit: `8ef9fb2`] — 19.11.2026
* **Turi:** `feat(tayyorlov)` — `/tayyorlov/` yo'nalishi va Meto bo'limini minglab pastal uchun qayta ishlash
* **Mavzu:** Tayyorlov bo'limi (`/meto-hub/yangi/` va konteynerlar) asosiy `/tayyorlov/` manziliga ko'chirildi; Meto bosqichi minglab pastallar uchun qidiruv/filtr/sahifalash, qabulni bekor qilish, jonli farq va izohlar bilan kengaytirildi.
* **O'zgartirilgan fayllar:**
  - `production/tayyorlov_urls.py` (yangi), `config/urls.py`, `production/meto_hub_urls.py`: `/tayyorlov/` va `/tayyorlov/konteynerlar/`, eski `/meto-hub/yangi/` va `/meto-hub/konteynerlar/` dan redirect
  - `production/models.py` + `production/migrations/0054_pastalflow_meto_note_alter_pastalflow_meto_done_at_and_more.py`: `PastalFlow.meto_note`, `meto_done_at`/`tasnif_done_at` hamda bosqich bo'yicha indekslar
  - `accounts/models.py` + `accounts/migrations/0025_alter_user_role.py`, `accounts/middleware.py`, `accounts/views.py`: `Tayyorlov skladi` nomi va `/tayyorlov/` ruxsatlari
  - `production/meto_flow.py`, `production/meto_flow_views.py`, `production/meto_views.py`, `production/services.py`: `cancel_meto`, `finish_meto` ortiqcha son himoyasi va `meto_note`, `ensure_flows()` tezlashtirilishi, `_ctx_meto` KPI/qidiruv/sahifalash/tarix, `tasnif_qty=0` va `boxes_created_qty` (`CANCELLED` chegirish), eski `/meto/` da `meto_beta` bloklovi
  - `templates/meto_hub/flow/meto.html`, `_card_head.html`, `flow.html`, `base_meto_hub.html`, `tasnif.html`, `templates/meto/*`, `templates/stickers/base_stickers.html`, `templates/superadmin/*`, `templates/base.html`: Tayyorlov menyulari va Meto UI
  - `production/test_meto_flow.py`: 4 ta yangi test (`TayyorlovMetoUpgradeTest`)
* **Serverda:** `git pull origin main && docker compose restart web` (`accounts.0025` va `production.0054` migratsiyalari qo'llanadi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 8ef9fb2
  ```

---

### 🔹 [Commit: `a629ed7`] — 19.11.2026
* **Turi:** `feat(meto-hub)` — Yangi meto oqimi (beta): konteynerlar, meto → tasnif → stiker → sklad, patokka berish, Uychi yuk xati va qaytarish, OTK v2
* **Mavzu:** Egasining 19.11.2026 dagi qarorlari. Fabrikadagi ~10 000 konteynerga doimiy QR yorliq (`KONT-000123`) beriladi. Har stikerli quti bitta konteynerga bog'lanadi: patokka berish, Uychiga yuklash va qabul qilishda pastalning hamma konteyneri skaner qilinadi (yashil / qizil). Bittasi yo'q bo'lsa pastal to'xtaydi. Oqim faqat beta zakazning belgi yoqilgandan keyin kesilgan pastallarida ishlaydi. Eski `/meto/`, eski Meto Markazi tablari, Telegram bot va muzlatilgan `/control/` sahifasi o'zgarmadi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0053_meto_flow.py`: `Container`, `ContainerEvent`, `PastalFlow`, `PastalFlowSize`, `Shipment`, `ShipmentPastal`, `Order.meto_beta` / `meto_beta_since`
  - `accounts/models.py` + `0024_meto_flow_roles.py`: yangi rollar `TASNIF`, `METO_SKLAD`, `UYCHI_SKLAD`
  - `production/meto_flow.py` (yangi): oqim mantiqi
  - `production/meto_flow_views.py` (yangi), `production/meto_hub_urls.py`: `/meto-hub/yangi/`
  - `templates/meto_hub/flow.html`, `templates/meto_hub/flow/*`, `templates/meto_hub/shipment_print.html` (yangi)
  - `production/container_services.py`, `production/meto_container_views.py`, `templates/meto_hub/containers.html`, `container_detail.html`, `container_labels_print.html` (yangi): `/meto-hub/konteynerlar/`
  - `production/control_v2_views.py`, `templates/control_v2/control_home.html` (yangi), `production/control_urls.py`: `/control/v2/`
  - `production/packing_services.py`: sanoqchi qog'ozni urganda konteyner bo'shaydi
  - `accounts/middleware.py`, `accounts/views.py`, `accounts/superadmin_views.py`, `templates/superadmin/users.html`, `templates/meto_hub/base_meto_hub.html`: rollar, kirish va menyu
  - `production/test_meto_flow.py` (33 ta test), `production/test_meto_containers.py` (8 ta test), `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Beta zakazlar:** superadmin zakazga "yangi meto oqimi"ni yoqadi. Shundan keyin kesilgan pastallar yangi oqimga tushadi; avval kesilganlari eski tartibda qoladi.
  2. **Meto:** meto masteri pastalni qabul qiladi ("meto bo'lmoqda") va razmer bo'yicha meto sonini yozadi. "Tugatish"dan keyin son qulflanadi.
  3. **Tasnif** (yangi rol): real sonni beradi, pastal avtomatik Koson meto skladiga o'tadi.
  4. **Stiker:** tasnif sonini qutilarga bo'ladi (quti soni yoki sig'im) va odatdagi stiker sahifasida chop etadi. Stiker chop etilguncha tasnif sonini o'zgartirish mumkin: bo'lingan qutilar bekor qilinadi, o'chirilmaydi, kodi o'zgarmaydi.
  5. **Meto sklad** (yangi rol): real kg ni yozadi. Planshetda qutining istalgan stikerini, keyin bo'sh katta konteynerni uradi; bitta konteynerga bitta quti. Biriktirishni sababsiz bekor qilish mumkin (yo'qolgan QR — stiker uriladi).
  6. **Konteyner tekshiruvi:** patokka berish, yuk xati va qabul oynasida har konteyner skaner qilinadi. Hammasi yashil bo'lmaguncha tasdiqlanmaydi; konteynersiz quti qizil turadi.
  7. **Koson patogi:** pastal master planshetida qabul qilinadi (qabul o'zgarmadi). Master rad etsa pastal skladga qaytadi.
  8. **Uychi:** yuk xati (`YX-000001`, mashina, haydovchi, A4 chop etish). Uychi skladi faqat to'liq pastalni qabul qiladi, aks holda "Qaytarish" orqali Kosonga qaytaradi. Kosondagi qabuldan keyin o'z patoklariga beradi (berish = qabul, eski Uychi tartibi kabi).
  9. **Konteyner holatlari:** Bo'sh, Skladda, Yo'lda, Patokda ("Patok oldida" / "Patok ichida" — qutining birinchi stikeri urilganda), Ta'mirda. Har harakat tarixda (kim, qachon, qayerda) saqlanadi.
  10. **OTK v2** (`/control/v2/`, `/control/` nusxasi): yangi oqim qutisi ta'mirga ketsa bo'sh kichik konteyner skaner qilinadi (chek odatdagidek chiqadi). Qayta ta'mirda o'sha konteyner qoladi. 1-sort bo'lmasa katta konteyner darhol bo'shaydi. Boshqa qutilar `/control/` bilan aynan bir xil ishlaydi.
  11. **Bo'shash:** sanoqchi qog'ozni urganda (faqat qadoqlash beta zakazida): CONTROL — katta konteyner, TAMIRn — o'sha chekning kichik konteyneri.
  12. **Rollar:** tasnif masteri, meto sklad va Uychi sklad faqat `/meto-hub/yangi/` ga kiradi (skladlar konteynerlar sahifasiga ham). Filial admini Uychi tabiga kiradi; superadmin hammasini ko'radi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiyalar `0053` va `accounts 0024` avtomatik qo'llanadi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout a629ed7
  ```

---

### 🔹 [Commit: `707c0df`] — 18.11.2026
* **Turi:** `feat(beta)` — Qadoqlash markazida 2-sort balansidan jo'natish, dinamik turlar va oylik hisobot
* **Mavzu:** `Upakovka` (`2-Sort Balans`) bo'limida qabul qilingan 2-sortdan chiqim qilish tizimi qo'shildi: 1 razmerni tezkor jo'natish (`⚡`) hamda har xil zakaz va modellarni bitta `JS-XXXXXX` yig'ma jo'natmasiga to'plab jo'natish; boshlang'ich 3 ta tur (`Sotuvga`, `Zakaz uchun`, `Qayta omborga`) va keyinchalik interfeysdan yangi turlar qo'shish/tahrirlash/nofaol qilish imkoniyati; xato jo'natmani bekor qilish; oy boshiga + qabul − turlar kesimida chiqim = oy oxirigi qoldiq shaklidagi oylik hisobot va Excel (`CSV`) eksport.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `production/migrations/0051_second_sort_dispatch.py`, `0052_second_sort_dispatch_types_seed.py` (`SecondSortDispatchType`, `SecondSortDispatch`, `SecondSortDispatchItem`), `production/admin.py`
  - `production/packing_services.py`, `production/packing_urls.py`, `production/packing_views.py` (`second_sort_balance_data`, `create_second_sort_dispatch`, `cancel_second_sort_dispatch`, `second_sort_monthly_report`, `api_second_sort_dispatch`, `second_sort_report_csv`)
  - `templates/packing/tabs/upakovka.html`, `templates/packing/partials/second_sort_balance.html`, `templates/packing/partials/dispatch_type_badge.html`
  - `production/test_second_sort_dispatch.py` (5 ta yangi test; jami 23 ta test o'tgan)

### 🔹 [Commit: `e672c55`] — 16.11.2026
* **Turi:** `feat(screens)` — Taxlovchilar TV ekranida (`taxlov/<n>/`) tepa patoklar ro'yxati va pul ustunlari olib tashlandi
* **Mavzu:** TV sarlavhasidan umumiy `PATOKLAR` ro'yxati olib tashlandi; har taxlovchining patogi alohida o'z qatorida (yonida) `Patok` va `Jami Taxladi` ustunlari bilan ko'rsatildi. `Bugungi Ish Haqi` va `Oylik Maosh` ustunlari TV ekranidan hozircha yashirildi.
* **O'zgartirilgan fayllar:**
  - `templates/screens/taxlov_monitor.html` (`PATOKLAR` bloki va pul ustunlari olib tashlandi, `Patok` + `Jami Taxladi` ustunlari, JS `fetchScreenData` yangilandi)
  - `screens/test_packer_screens.py` (yangi tekshiruv — jami 30 ta test o'tgan)

### 🔹 [Commit: `615d3e2`] — 16.11.2026
* **Turi:** `feat(beta)` — Sanoqchida badge'dan oldin patok tanlash (TV ekranlar uchun) + Sanoqchi va Upakovka yangi dizayni
* **Mavzu:** Sanoqchi taxlovchi badge'ini skanerlashdan avval patok (`K1–K13` / `U1–U27`) ni tanlaydi va shu patok `PackingBatch` / `PackingScan` (`screen_number`) ga saqlanib, taxlovchi TV ekranlari (`packer_screens.py`) qutining patogidan ustuvor ravishda aynan sanoqchi qo'ygan patok bo'yicha chiqaradi. Sanoqchi (`sanoqchi.html`), Upakovka (`upakovka.html`) va umumiy qismlar (`cards.html`, `basket_rows.html`, `basket_search.html`) barcha funksiyalari saqlangan holda split-workbench va KPI lentali yangi ko'rinishga o'tkazildi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `production/migrations/0050_packing_patok.py` (`PackingBatch.screen_number`, `PackingScan.screen_number`)
  - `production/packing_services.py`, `production/packing_urls.py`, `production/packing_views.py` (`api_counter_patok`, `_apply_batch_patok`, patok uzatish va TV kesh tozalash), `production/packer_screens.py` (`Coalesce` ustuvorligi)
  - `templates/packing/tabs/sanoqchi.html`, `templates/packing/tabs/upakovka.html`, `templates/packing/partials/{cards,basket_rows,basket_search}.html`
  - `production/test_counter_packing.py`, `screens/test_packer_screens.py` (jami 41 ta test o'tgan), `.claude/project-knowledge/*`

### 🔹 [Commit: `3b0b81e`] — 16.11.2026
* **Turi:** `feat(beta)` — Beta qutilarda `CONTROL` stikeri `CONTROL / TAXLOV` ko'rinishida, qiyinlik va summa bilan chop etiladi
* **Mavzu:** Faqat beta qutilar (`OperationGroup.is_beta` dan keyin yaratilgan quti yoki `Order.packing_beta` bo'yicha beta quti) 65×45 stiker chop etishda `CONTROL` stikerini `BOSQICH: OTK + TAXLOV` / `CONTROL / TAXLOV` (`BETA` belgisi) ko'rinishida chiqaradi; pastki kataklarida narxlar shablonidagi `Qiyinlik` va `Summa` (`taxlov_price`) ko'rsatiladi. QR kodi (`CONTROL:<quti_kodi>`) o'zgarishsiz qoladi; odatiy qutilarda eski `CONTROL` stikeri saqlangan.
* **O'zgartirilgan fayllar:**
  - `production/packing_services.py` (`control_sticker_meta`, qiyinlik va narx formatlash)
  - `production/views.py` (`box_print_stickers_view`, `order_print_all_stickers_view` — N+1 so'rovlarsiz beta meta uzatish)
  - `templates/production/box_stickers_print.html` (beta `CONTROL / TAXLOV`, `QIYINLIK` va `SUMMA` kartasi; odatiy quti o'zgarishsiz)
  - `production/test_template_beta.py` (2 ta yangi test — jami 39 ta qadoqlash/ekran testi va 7 ta chop etish testi o'tgan)

### 🔹 [Commit: `a38aec9`] — 16.11.2026
* **Turi:** `feat(screens)` — taxlovchilar uchun 3 ta TV, sex ekranlarida alohida bo'lim
* **Mavzu:** Yangi qadoqlash tartibining 6-bosqichi (oxirgisi). `/screens/` sahifasida 40 ta chevar kartochkasi ostida alohida "Taxlovchilar ekranlari" bo'limi; chevarlar kartochkalari va TV'lari o'zgarmagan. TV sahifasi `/screens/taxlov/<n>/` chevarlar TV'si bilan bir xil, faqat headeri boshqacha (PATOKLAR, TAXLOVCHI, BUGUN TAXLANDI, NORMA 1000, O'RTACHA FOIZ). Superadmin `/screens/taxlov/sozlash/` da har patokni bitta ekranga bir marta biriktiradi. Faqat sanoqchi yuborgan taxlov hisoblanadi: taxlovchi bugungi oxirgi yuborilgan qutisi patogining ekranida, kunlik butun ishi bilan; ball = dona × shablondagi taxlov qiyinligi, foiz = ball / 1000; oylik maosh = oylik taxlov + stiker puli.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0048_packer_screens.py`, `0049_packer_screens_seed.py` (`PackerScreen`, 3 ta ekran)
  - `production/packer_screens.py` (yangi: `packer_screens_data`)
  - `screens/views.py`, `screens/urls.py` (TV, API, sozlash; `/screens/` dagi bo'lim)
  - `templates/screens/taxlov_monitor.html`, `taxlov_settings.html`, `partials/taxlov_patok_grid.html` (yangi), `templates/screens/overview.html` (faqat pastga yangi bo'lim)
  - `screens/test_packer_screens.py` (4 ta test — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `22c0ce3`] — 16.11.2026
* **Turi:** `feat(beta)` — sanoqchi tabidan "Hali urilmagan qog'ozlar" ro'yxati olib tashlandi
* **Mavzu:** Egasi talabi: sanoqchiga kerak emas. Jadval, "1-Sort" tugmasidagi "N kutilmoqda" belgisi va uning hisob-kitobi olib tashlandi. Keyinchalik superadmin uchun ochiladi (kod `3266dee` da). Qutining qog'ozlari paneli va pastal kartochkasidagi "Ta'mir cheklari" qoladi.
* **O'zgartirilgan fayllar:** `production/packing_views.py`, `templates/packing/tabs/sanoqchi.html`, `production/test_counter_packing.py`, `CLAUDE.md`

### 🔹 [Commit: `240b126` + `c61d6b8`] — 16.11.2026
* **Turi:** `feat(packing)` + `test(beta)` — /beta/ dizayni yangilandi; upakovka tabidagi 500 xatosi tuzatildi
* **Mavzu:** `0047` migratsiyasi avvalgi "jo'natilgan" savatlarni qabul qilingan deb belgiladi, lekin ularda qabul qilgan master yo'q edi — "Oxirgi savatlar" jadvali va savat qidiruvi bo'sh maydonni o'qib 500 berdi (`/beta/?tab=upakovka`). Yangi dizayn shablonlarida bunday maydonlar himoyalangan; regressiya testi eski shablonlarda aynan shu xato bilan yiqiladi.
* **O'zgartirilgan fayllar:**
  - `templates/packing/` (hub, tablar va qismlar — yangi dizayn, bo'sh master/sanoqchi uchun himoya)
  - `production/test_packing_beta.py` (`test_pages_render_with_migrated_basket_without_master`)

### 🔹 [Commit: `95589a6`] — 16.11.2026
* **Turi:** `feat(beta)` — 2-sort savatlari (SV-123456), upakovka masteri, beta "bugundan"
* **Mavzu:** Yangi qadoqlash tartibining 5-bosqichi. Sanoqchi patokdagi 2-sortni zakaz × model × razmer qatorlari bo'yicha (yoki hammasini) savatga oladi — "real son" maydoni hozircha yopiq; savat kodi SV-123456 bo'yicha qidiriladi. Yangi rol "Upakovka masteri" savatni qabul qiladi (upakovkaning 2-sort balansiga) yoki sabab bilan qaytaradi — sanoqchi tuzatib qayta jo'natadi. Eski taxlovchi tabi olib tashlandi. Zakaz beta belgisi endi yoqilgan vaqtdan ishlaydi: undan oldin OTK dan o'tgan qutilar eski tartibda qoladi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0046_basket_master.py`, `0047_basket_codes.py` (`SecondSortBatch`: kod, qabul/qaytarish; `Order.packing_beta_since`; eski savatlarga kod, eski jo'natilganlar qabul qilingan)
  - `accounts/models.py` + `0023_basket_master.py` (rol `PACKING_MASTER`), `accounts/views.py`, `accounts/middleware.py`, `accounts/superadmin_views.py`, `templates/superadmin/users.html`
  - `production/packing_views.py`, `production/packing_urls.py`, `production/packing_services.py` (`master_accept`, `master_return`, `is_beta_box` / `beta_box_q` boshlanish vaqti bilan)
  - `templates/packing/tabs/sanoqchi.html`, `upakovka.html`, `zakazlar.html`, `partials/basket_rows.html`, `partials/basket_search.html`; `templates/packing/tabs/taxlovchi.html` o'chirildi
  - `production/test_packing_beta.py`, `production/test_counter_packing.py` (qadoqlash testlari 48 ta — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `efd0fda`] — 16.11.2026
* **Turi:** `feat(payroll)` — taxlovchi puli (sanoqchi yuborgan ro'yxatlardan), sariq rangda alohida
* **Mavzu:** Yangi qadoqlash tartibining 4-bosqichi. Sanoqchi "Saqlash va yuborish" bosganda har qator taxlovchi hisobiga pul bo'ladi: dona × shablondagi "OTK Control/Taxlov" narxi (hozircha 1 so'm), yuborilgan kunga. Chevar stikerlari bilan bir xil qoidalar: narx o'zgarsa muzlatilmaganlari qayta hisoblanadi, oy muzlatilsa qulflanadi, kunlik yopishda muhrlanadi. Maosh sahifasi, kunlik tafsilot, CSV, oylik Excel tabel va kun varaqlarida sariqda alohida ko'rinadi.
* **O'zgartirilgan fayllar:**
  - `production/taxlov_pay.py` (yangi: `credit_batch`, `reprice_taxlov`, `taxlov_by_worker`, `taxlov_by_worker_day`)
  - `production/models.py` + `0045_taxlov_pay.py` (`PackingScan`: narx, summa, pul vaqti, muzlatish), `accounts/models.py` + `0022_taxlov_pay.py` (`DailyWorkerClosing.taxlov_units/amount`, `Worker.total_earned`)
  - `accounts/freeze_services.py` (muzlatish/ochish va oylik reestr), `accounts/management/commands/close_daily_payroll.py`
  - `accounts/superadmin_views.py`, `templates/superadmin/payroll.html` (maosh sahifasi, kunlik tafsilot, CSV, narx o'zgarishi)
  - `production/excel_reports.py` (oylik tabel L ustuni, kun varaqlari va kunlik hisobotda TAXLOV bloki), `production/packing_views.py`
  - `production/test_taxlov_pay.py` (6 ta test — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `3266dee`] — 16.11.2026
* **Turi:** `feat(beta)` — sanoqchi taxlovchi badge'i va qog'ozlarini uradi; ta'mir tarixi va urilmagan qog'ozlar
* **Mavzu:** Yangi qadoqlash tartibining 3-bosqichi. Taxlovchi tizimda hech narsa urmaydi. Sanoqchi tabida: taxlovchi badge'i → uning CONTROL stikerlari va TAMIRn cheklari (son bazadan; har qog'oz bir marta, tartib ahamiyatsiz) → ochiq ro'yxat → "Saqlash va yuborish" → upakovkaning 1-sort balansi. Har skanda qutining hamma qog'ozlari ko'rinadi (necha marta ta'mir, qaysi qog'oz urilmagan); "Hali urilmagan qog'ozlar" ro'yxati; pastal kartochkasida "Ta'mir cheklari". Beta quti endi shablon belgisi bilan ham aniqlanadi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0044_packing_batches.py` (`PackingBatch`, `PackingScan.batch`)
  - `production/packing_services.py` (`is_beta_box`, `beta_box_q`, `SENT_SCANS`, `take_paper`, `paper_status`)
  - `production/packing_views.py`, `production/packing_urls.py` (sanoqchi API: taxlovchi, qog'oz, olib tashlash, yuborish; `_unscanned_papers`)
  - `templates/packing/tabs/sanoqchi.html`, `production/pastal_info.py`, `templates/production/partials/pastal_info_card.html`
  - `production/test_counter_packing.py` (5 ta test; jami 40 ta qadoqlash testi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `04c5212`] — 14.11.2026
* **Turi:** `feat(control)` — har ta'mir aylanmasiga alohida chek: `TAMIR1:`, `TAMIR2:`, ...
* **Mavzu:** Yangi qadoqlash tartibining 2-bosqichi. OTK ta'mirga yuborsa 1-chek (`TAMIR1:<quti kodi>`) chiqadi; ta'mirdan qaytganda ochiq chek yopiladi (tuzalganlar — shu chekning 1-sorti), yana ta'mirga ketganlari uchun keyingi chek (`TAMIR2:`, ...). Quti necha marta ta'mirga ketgani bazada ko'rinadi; quti kodi va stikerlar o'zgarmaydi. Eski `TAMIR:` cheklari 1-chek sifatida ishlayveradi. OTK plansheti (`/control/`) `TAMIRn:` ni, skaner "+" xatosi bilan kelganini ham, taniydi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0043_repair_receipt_cycles.py` (`RepairReceipt`: cycle, created_log, returned_log, returned_at, first_sort_qty; `PackingScan.repair_receipt`; eski cheklarni ko'chirish)
  - `production/packing_services.py` (`create_repair_receipt`, `close_repair_receipt`, `parse_packing_code` → TAMIRn, chek bo'yicha qism)
  - `production/control_views.py` (har ta'mirda chek, qaytishda yopish; TAMIRn qidiruvi), `production/receipt_printing.py` (QR va "N-ta'mir")
  - `production/packing_views.py`, `templates/packing/` (OTK tabi, A4 chek, taxlovchi)
  - `production/test_packing_beta.py`, `production/test_receipt_printing.py` (32 ta test — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `92529a2`] — 14.11.2026
* **Turi:** `feat(pricing)` — narx shablonida Beta belgisi va sariq "OTK Control/Taxlov" qatori
* **Mavzu:** Yangi qadoqlash tartibining 1-bosqichi: beta belgisi zakazdan narx shabloniga ko'chdi. Har shablon jadvali ostida sariq qator — beta belgisi, Control/Taxlov narxi (hozircha 1 so'm) va qiyinligi. Qutidagi mavjud CONTROL stikeri shu ish bo'ladi; yangi stiker yaratilmaydi. Faqat belgi yoqilgandan keyin yaratilgan qutilar yangi tartibda ishlaydi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` + `0042_operation_group_beta.py` (`OperationGroup.is_beta`, `beta_since`, `taxlov_price`, `taxlov_difficulty`)
  - `accounts/superadmin_views.py` (`update_group_beta` action), `templates/superadmin/pricing.html` (sariq qator, BETA belgisi)
  - `production/packing_services.py` (`beta_group`), `production/test_template_beta.py` (3 ta test — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `f38b98e`] — 14.11.2026
* **Turi:** `feat(control)` — Terry Jar Print: planshetga USB bilan ulangan printerdan ta'mir cheki
* **Mavzu:** Boshliq kompyuter agentiga ruxsat bermadi. Printer patok OTK planshetiga USB (OTG hub) bilan ulanadi; planshetdagi "Terry Jar Print" ilovasi (Android 11–16) orqa fonda shu patokning cheklarini serverdan olib printerga chiqaradi. DLT ilovasi va `/control/` o'zgarmaydi.
* **O'zgartirilgan fayllar:**
  - `android_print/` (ilova: `MainActivity`, `PrintService`, `UsbPrinter`, `BootReceiver`; `build_apk.sh`, `README.md`)
  - `production/print_views.py` (`tablet` printer turi, `?patok=` filtri, kompyuter agenti planshet cheklariga tegmaydi)
  - `production/models.py` + `0041_receipt_printer_last_seen.py` (`ReceiptPrinter.last_seen_at`), `templates/superadmin/printers.html` ("● ulangan / uzilgan")
  - `production/test_receipt_printing.py` (13 ta test — hammasi o'tgan), `CLAUDE.md`, `.gitignore`, `.claude/project-knowledge/*`

### 🔹 [Commit: `19468ec`] — 14.11.2026
* **Turi:** `feat(control)` — OTK ta'mir cheki: tikuvchi bo'yicha guruhlash, kattaroq QR, bo'sh qog'ozsiz
* **Mavzu:** Egasi tasdiqlagan yakuniy dizayn: quti / pastal / zakaz / model / razmer, katta "Ta'mirga: N dona", har tikuvchi qalin "Ism (UID):" va ostida u qilgan operatsiyalar soni ("ETAK RESHMA 5 ta"), OTK, patok va vaqt, kattaroq QR (~26 mm). QR tagidagi quti raqami va tepa/pastdagi bo'sh qatorlar olib tashlandi. Rasm (raster) ishlatilmaydi — XP-80TS USB'da buzib chiqargan.
* **O'zgartirilgan fayllar:**
  - `production/receipt_printing.py` (printer matni + o'z QR buyrug'i, ~1 KB; `_repair_workers` tikuvchi bo'yicha guruhlaydi)
  - `production/test_receipt_printing.py` (10 ta test — hammasi o'tgan), `CLAUDE.md`

### 🔹 [Commit: `bcdc10e`] — 13.11.2026, 17:10
* **Turi:** `feat(control)` — ixcham OTK ta'mir cheki (egasi tasdiqlagan dizayn)
* **Mavzu:** Qog'oz imkon qadar kam bo'lishi uchun chek qayta chizildi: tepada qalin "K2 · TA'MIR · 8 dona", har tikuvchi bir qatorda (6 xonali UID, ism, ×N, operatsiya), pastda QR chapda va yonida zakaz / quti ID / model / artikul. Oldingi tuzatishlar: xitoy rejimi o'chirildi (`a0c7cb8`), OTK foydalanuvchisining patoki login nomidan aniqlanadi (`c3fdcbb`).
* **O'zgartirilgan fayllar:**
  - `production/receipt_printing.py` (sarlavha va ro'yxat printer matni; faqat QR bloki kichik rasm, 24 qatorli bo'laklarda; boshida NUL + ikki marta ESC @)
  - `production/test_receipt_printing.py` (9 ta test — hammasi o'tgan), `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `f73b886`] — 13.11.2026, 15:20
* **Turi:** `feat(control)` — OTK ta'mir cheki termo printerda
* **Mavzu:** Egasi talabiga ko'ra ta'mirga ketgan qutiga OTK stikerini qaytarish o'rniga, OTK `/control/` da ta'mirni saqlaganda shu patok printeridan chek chiqadi: patok, quti, pastal, zakaz, model, razmer, ta'mir soni, har dona bo'yicha operatsiya va uni tikkan ishchi, OTK nazoratchisi, vaqt va qutining mavjud QR kodi (`CONTROL:<quti kodi>`). Yangi kod yaratilmaydi; chek chiqmasa ham OTK saqlanadi. Test bosqichida bir necha patok egasining stolidagi Xprinter XP-80TS ga biriktiriladi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` (`ReceiptPrinter`, `PrintJob`), `production/migrations/0040_receipt_printers.py`
  - `production/receipt_printing.py` (ESC/POS chek, QR, kesish; `queue_repair_receipt`)
  - `production/print_views.py`, `production/print_urls.py` (`/printers/` superadmin sahifasi + pechat agenti API), `config/urls.py`
  - `production/control_views.py` (ta'mir / qayta ta'mir saqlanganda chek navbatga)
  - `templates/superadmin/printers.html`, `templates/superadmin/base_superadmin.html` (menyu: "Chek printerlari (OTK)")
  - `print_agent/` (agent: LAN 9100-port yoki USB `lp` raw; `install.sh`, `agent.conf.example`), `.gitignore`
  - `production/test_receipt_printing.py` (8 ta test — hammasi o'tgan), `CLAUDE.md`, `.claude/project-knowledge/*`

### 🔹 [Commit: `54453f6`] — 12.11.2026, 12:55
* **Turi:** `feat(beta)` — `/beta/` barcha tablar dizayni, UX va funksional kamchiliklarini tuzatish + qo'lda kiritilgan quti kodi
* **Mavzu:** Egasi talabiga ko'ra `/beta/` (OTK, Taxlovchi, Sanoqchi, Upakovka, Beta zakazlar) barcha tablaridagi dizayn, ishlash qulayligi va mantiqiy kamchiliklar bartaraf etildi hamda taxlovchi qo'lda yozgan quti kodini (`A10-394`, `BOX:`, `QUTI:`) tanish qo'shildi.
* **O'zgartirilgan fayllar:**
  - `production/packing_services.py` (`parse_packing_code`: old qo'shimchasiz quti kodi, `BOX:` / `QUTI:` ham = CONTROL stikeri; tikuvchi stikeri rad etiladi)
  - `production/packing_views.py` (`_tab_badges`, `_receipt_summary`, `api_otk_lookup` da `patok_code` + `existing_receipt`, `api_otk_submit` da `receipt_meta`, `_group_by_order_article` da `boxes`/`box_count`, `counter_basket_remove`, `_upakovka_context` da `packing_beta` himoyasi + `today_scans`, `_zakazlar_context` filtrlari)
  - `production/packing_urls.py` (`packing:counter_remove` — savatdan patok navbatiga qaytarish)
  - `templates/packing/hub.html`, `partials/cards.html`, `tabs/otk.html`, `tabs/taxlovchi.html`, `tabs/sanoqchi.html`, `tabs/upakovka.html`, `tabs/zakazlar.html`
  - `production/test_packing_beta.py` (15 ta test — hammasi o'tgan), `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **OTK tabi:** Patok va Pastal badge'lari, "Qutida jami" `-`/`+` tugmalari, son oshib ketishida qizil ogohlantirish, bir xil nuqson uchun `Soni` stepper (1 donadan bosib chiqmaslik uchun), tikilgan va urilmagan operatsiyalarni ajratish, ta'mirdan qaytishda oldingi chek tafsiloti va qayta chop etish, qidiriladigan/filtrlanadigan `Ta'mir cheklari` ro'yxati (`Ta'mirda: X` / `Yopilgan`).
  2. **Taxlovchi tabi:** 2-qadamli vizual banner, qo'lda yozilgan quti kodini (`A10-394`) to'g'ri tanish va ortiqcha `404` so'rov yubormaslik, matn belgilaganda fokusni tortib olmaslik, bugungi oxirgi skanlar ro'yxati.
  3. **Sanoqchi tabi:** Patok kartochkalarida "Qaysi qutilardan" tafsiloti, savatdan adashib olingan kartochkani qaytarish (`counter_remove`), skan bekor qilishda `patok` filtrini saqlab qolish.
  4. **Upakovka & Beta zakazlar:** Upakovkada KPI strip, tezkor qidiruv, bugungi skanlar jurnali va non-beta 2-sort filtr himoyasi; Beta zakazlarda `Barchasi / Beta yoqilgan / Beta o'chirilgan` filtrlari hamda model/quti sonlari.
  5. **Hub:** Tablarda jonli ko'rsatkich badge'lari, yopiladigan xabarlar va Kun/Tun (light/dark) mosligi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 54453f6
  ```

---

### 🔹 [Commit: `91ffc1a`] — 12.11.2026, 11:49
* **Turi:** `feat(beta)` — OTK → taxlovchi → qadoqlash, bitta `/beta/` sahifasida (faqat belgilangan zakazlarda): ta'mir cheki, taxlovchi, sanoqchi, upakovka
* **Mavzu:** Egasining 12.11.2026 dagi qarorlari. OTK stoli orqasidagi qadoqlash va 2-sortni sanash avtomatlashtirildi. Hozircha faqat superadmin belgilagan zakazlarda (test uchun ishlamayotgan patokda); boshqa zakazlar avvalgidek ishlaydi. Egasining so'rovi bilan hammasi bitta "Beta" sahifasida, tablar bilan — test uchun akkaunt almashtirib kirib-chiqish shart emas. Muzlatilgan `/control/` sahifasi o'zgarmadi. Karobka/eksport va upakovka masterining qabul qilishi keyin gaplashiladi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` (`Order.packing_beta`, `RepairReceipt`, `PackingScan`, `SecondSortBatch`, `BoxQualityInspectionLog.second_sort_batch`), `production/migrations/0039_packing_beta.py`
  - `accounts/models.py` (yangi rollar `PACKING` — upakovka/taxlovchi, `COUNTER` — sanoqchi), `accounts/migrations/0021_packing_counter_roles.py`
  - `production/packing_services.py`, `production/packing_views.py`, `production/packing_urls.py`, `templates/packing/` (yangi), `config/urls.py` (`/beta/`)
  - `production/control_views.py` (OTK `TAMIR:` QR ni ham taniydi; beta qutida ta'mir cheki yaratiladi — sahifa o'zgarmagan)
  - `accounts/middleware.py`, `accounts/views.py` (yangi rollar faqat `/beta/` ga kiradi), `accounts/superadmin_views.py`, `templates/superadmin/users.html`, `templates/superadmin/base_superadmin.html` ("Beta (OTK → qadoqlash)" havolasi)
  - `production/test_packing_beta.py` (10 ta test), `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Bitta sahifa** (`/beta/`): OTK, Taxlovchi, Sanoqchi, Upakovka, Beta zakazlar tablari. Superadmin hammasini ko'radi; upakovka akkaunti — Taxlovchi va Upakovka; sanoqchi akkaunti — faqat Sanoqchi.
  2. **Beta zakazlar tabi:** zakazni beta qilish yoki qaytarish.
  3. **OTK tabi:** qutining CONTROL stikeri yoki TAMIR cheki uriladi; har 2-sort va ta'mir donasi uchun operatsiya tanlanadi (tikuvchi avtomatik), 2-sortga sabab ham. Saqlash `/control/` ning o'sha server funksiyasi bilan, bir xil yoziladi; beta bo'lmagan zakaz qutisi qabul qilinmaydi.
  4. **Ta'mir cheki:** ta'mir bo'lsa shu kompyuterda A4 chek chiqadi; chekda har dona uchun operatsiya va tikuvchi; ta'mir uchun pul yo'q, faqat statistika. QR `TAMIR:<quti kodi>` — qutining o'sha kodi, faqat oldida boshqa so'z; eski stikerlar, CONTROL stikeri va quti kodi o'zgarmaydi (oltin qoida). Qayta ta'mirda yangi chek chiqmaydi, o'sha chek qaytadi. OTK tabining o'ng tomonida oxirgi 7 kun cheklari — qayta chop etish.
  5. **Ikki qism, ikki qog'oz:** CONTROL stikeri OTK birinchi tekshiruvdagi 1-sortni beradi (masalan, 40), ta'mir cheki ta'mirdan tuzalib chiqqanlarni (masalan, 4, keyin yana). Har urishda shu qismning hali qadoqlanmagani yoziladi: qaysi tartibda urilsa ham 40 va 4 to'g'ri bo'linadi, bitta qog'ozni qayta urish hech narsa qo'shmaydi.
  6. **Taxlovchi tabi:** avval badge, keyin CONTROL stikeri yoki TAMIR cheki; bitta qog'oz — bitta taxlovchi. Urilgan ish "Qadoqlash kutayotgan ishlar"ga tushadi.
  7. **Upakovka tabi:** qadoqlash kutayotgan ishlar (zakaz → artikul rasmi bilan → razmerlar), "Bugun kim qancha taxladi", sanoqchidan kelgan 2-sort. Taxlovchi puli hozircha yo'q.
  8. **Sanoqchi tabi:** patoklardagi OTK 2-sorti va brak (oxirgi qabuldan beri), har zakaz/artikul kartochkasi rasmi va razmerlari bilan. Sanalgan son OTK soniga teng bo'lsagina qabul qilinadi, aks holda OTK topib beradi. Qabul qilingani savatga, keyin "Upakovka 2-sort omboriga jo'natish". Xato urilgan taxlovchi skanini shu yerda bekor qiladi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiyalar `0039` va `0021` avtomatik qo'llanadi). Keyin superadmin → "Beta (OTK → qadoqlash)" → "Beta zakazlar" tabida test zakazini yoqing.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 91ffc1a
  ```

---

### 🔹 [Commit: `2c48f36`] — 11.11.2026, 16:34
* **Turi:** `fix(freeze)` — Oyni muzlatish endi saytni to'xtatmaydi (5000 tadan bo'lib, fonda) va "Oyliklar" sahifasi tez ochiladi (sahifalash, qidiruv)
* **Mavzu:** 11.11.2026 da "Oyliklar" sahifasidagi "Muzlatish" tugmasi oktyabrning ~208 ming stikerini bitta katta yozuv bilan sayt so'rovi ichida muzlatdi; stikerlar jadvalida 21 ta indeks, server 4 GB — so'rov uzoq davom etib, butun sayt (terminal ham) 504 Gateway Time-out bo'lib qoldi. Muzlatishning o'zi (to'langan oyni narx o'zgarishidan himoya qilish) o'zgarmadi, faqat bajarilishi.
* **O'zgartirilgan fayllar:**
  - `accounts/freeze_services.py` (`update_in_batches` — 5000 tadan; `month_bounds` — oy sana oralig'i bilan indeksli qidiruv; `freeze_month_tickets`, `unfreeze_month_tickets`, `run_in_background`)
  - `accounts/superadmin_views.py` (oy muzlatish tugmasi holatni darhol o'zgartiradi, stikerlarni fonda muzlatadi/ochadi; "Oyliklar" sahifasi ochilganda muzlatish endi ishga tushmaydi)
  - `production/management/commands/run_scheduler.py` (har soatda muzlatish tekshiruvi: o'tgan oylar va 7 kunlik taymerlar)
  - `config/settings.py` (`FREEZE_IN_BACKGROUND`; testlarda darhol bajariladi)
  - `templates/superadmin/payroll.html` (qidiruv maydoni, sahifalar, bo'sh xodimlarni ko'rsatish tugmasi, qatorlarda ortiqcha bo'sh joysiz)
  - `production/test_salary_freeze.py` (3 ta yangi test), `production/test_payroll_page.py` (yangi), `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Bo'lib-bo'lib:** har bo'lak 5000 ta stikerni qisqa yozuv bilan muzlatadi — jadval soniyalar emas, daqiqalab band bo'lmaydi. Lokal nusxada oktyabr (208 565 ta) 42 bo'lakda 37 soniyada muzladi, eng uzun bo'lak 2,8 soniya.
  2. **Tugma fonda:** "Muzlatish"/"Muzdan chiqarish" darhol javob qaytaradi, stikerlar bir necha daqiqada fonda qulflanadi; sayt ishlashda davom etadi.
  3. **Sahifa ochilganda muzlatish yo'q:** avval "Oyliklar" sahifasi har ochilganda butun jadval bo'yicha muzlatish ishlardi (1-sanada butun o'tgan oyni sahifa ichida muzlatardi). Endi bu har soatda scheduler'da bajariladi; uzilib qolgan muzlatishni ham shu tekshiruv tugatadi.
  4. **Tezroq qidiruv:** oy "yil/oy" bo'yicha emas, sana oralig'i bilan (indeks) qidiriladi.
  5. **"Oyliklar" sahifasi yengil:** avval 998 xodimning hammasi bitta sahifada edi (7,4 MB). Endi 100 tadan sahifalar; bu oy ishi, to'lovi va balansi yo'q xodimlar standart holatda yashirin ("+ N ta bo'sh xodimni ko'rsatish"); qatorlardagi ortiqcha bo'sh joylar olib tashlandi. Lokal nusxada: 7,4 MB → 859 KB, 1,8–2,2 s → 0,5 s. Yuqoridagi jami summalar avvalgidek barcha xodimlar bo'yicha.
  6. **Qidiruv:** ID yoki ism-familiya bo'yicha, barcha sahifalar bo'yicha (serverda); bir nechta so'z bo'lsa ham topadi ("Malika Karimova", "karimova mal"). Qidiruvda bo'sh xodimlar ham chiqadi.
* **Serverda:** `git pull origin main && docker compose restart web scheduler` (scheduler ham qayta ishga tushishi shart; migratsiya yo'q).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 2c48f36
  ```

---

### 🔹 [Commit: `3a5001e`] — 11.11.2026, 13:53
* **Turi:** `feat(managers-new)` — Kompyuter ekraniga to'liq moslash, artikulda jo'natish sanasi yo'q, "Yangi zakaz" faqat ro'yxatda
* **Mavzu:** `/managers-new/` (egasining so'rovi bilan, menejerlar kompyuterda ishlaydi).
* **O'zgartirilgan fayllar:**
  - `templates/managers_new/base.html`, `order_form.html`, `article_form.html`, `order_detail.html`, `partials/fabric_row.html`
  - `production/manager_new_views.py`, `production/manager_new_excel.py`, `production/models.py` (izoh)
  - `production/test_managers_new.py`, `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Butun ekran:** sahifalar ekran kengligida; zakaz formasi bitta qatorda (4 ustun); artikul formasida chapda asosiy ma'lumot va razmerlar, o'ngda rasmlar; mato qismining asosiy maydonlari keng ekranda bitta qatorda.
  2. **Jo'natish sanasi:** artikul formasidan, zakaz kartasidan va Excel'lardan ("Дата отгрузки", "Инспекция датаси" ustuni) olib tashlandi; ma'lumot bazasidagi eski maydon tegilmadi (migratsiya yo'q).
  3. **"Yangi zakaz" tugmasi:** yuqori menyudan olib tashlandi — faqat zakazlar ro'yxatida turadi, zakaz ichida ko'rinmaydi.
  4. **Mato qismlari:** keng ekranda (1536 px dan) Excel kabi jadval — sarlavha bir marta, har mato bitta raqamlangan qator, tayyor/xom kg ustunlari va pastda "Jami"; kichikroq ekranda raqamli tartibli kartalar. Laboratoriya kartasi uchun ixcham "Rasm" tugmasi.
* **Serverda:** `git pull origin main && docker compose restart web`.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 3a5001e
  ```

---

### 🔹 [Commit: `b7d7809`] — 11.11.2026, 13:29
* **Turi:** `feat(managers-new)` — Razmerlar zakazda emas, har artikulda alohida
* **Mavzu:** `/managers-new/` (egasining so'rovi bilan): yangi zakaz ochishda razmer so'ralmaydi, chunki bir zakazdagi artikullarning razmerlari har xil bo'lishi mumkin (masalan kattalar va bolalar).
* **O'zgartirilgan fayllar:**
  - `production/models.py` (`OrderItem.size_labels`), `production/migrations/0038_orderitem_size_labels.py` (mavjud zakazlarning razmerlari artikullarga ko'chiriladi)
  - `production/manager_new_views.py`, `production/manager_new_excel.py`
  - `templates/managers_new/order_form.html`, `article_form.html`, `order_detail.html`
  - `production/test_managers_new.py`, `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Zakaz:** razmer maydoni olib tashlandi.
  2. **Artikul:** razmerlar shu yerda — tugma (kattalar 46–56, bolalar 110–146) yoki qo'lda; razmer o'zgarsa son kataklari darhol qayta chiziladi, yozilgan sonlar saqlanadi. Yangi artikulga shu zakazning oxirgi artikuli razmerlari taklif qilinadi.
  3. **Zakaz kartasi va "Zakaz list" Excel:** har artikul o'z razmerlari bilan.
  4. **Himoya:** kesilgan razmerni artikuldan olib tashlab bo'lmaydi.
* **Serverda:** `git pull origin main && docker compose restart web` (0038 migratsiyasi o'zi ishlaydi).
* **Qaytish buyrug'i:**
  ```bash
  git checkout b7d7809
  ```

---

### 🔹 [Commit: `6a2c5f0`] — 11.11.2026, 12:41
* **Turi:** `feat(managers-new)` — "Managerlar NEW" test bo'limi: menejer zakaz list va mato zakazini kiritadi
* **Mavzu:** Egasining so'rovi: zakaz kiritishni to'liq tizimga o'tkazish (1-bosqich — faqat menejer). Eski `/managers/` va hozirgi tartib o'zgarmagan; yangi bo'lim to'liq ishlagach unga o'tiladi, eskisi yashiriladi. Sklad (TMO) va kroy keyingi bosqichlarda.
* **O'zgartirilgan fayllar:**
  - `production/models.py`, `production/migrations/0037_managers_new.py` (zakazga menejer, sanalar, razmerlar, `is_new_flow`; artikulga Просчет, jo'natish sanasi, pechat rasmi; yangi `OrderItemFabric`, `OrderChangeLog`; yangi statuslar)
  - `production/manager_new_views.py`, `production/manager_new_urls.py`, `production/manager_new_excel.py` (yangi), `config/urls.py` (`/managers-new/`)
  - `templates/managers_new/*` (yangi), `templates/superadmin/base_superadmin.html` (menyuda "Managerlar NEW"), `templates/superadmin/users.html` (rol ro'yxatida "Menejer"; filial admini menejer ocha olmaydi)
  - `accounts/middleware.py`, `accounts/views.py` (menejer faqat yangi bo'limni ko'radi va kirganda o'sha yerga tushadi)
  - Eski ro'yxatlar yangi zakazlarni ko'rsatmaydi: `production/views.py`, `production/manager_views.py`, `production/norma_views.py`, `production/sewing_statistics_views.py`, `accounts/superadmin_views.py`
  - `production/test_managers_new.py` (yangi), `production/tests.py` (menejer endi eski sahifalarga kirmaydi — 3 ta test shunga moslandi), `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Zakaz:** raqam (qo'lda), mijoz, buyurtma sanasi, eksport sanalari, razmer to'plami (kattalar 46–56, bolalar 110–146 yoki qo'lda).
  2. **Artikul:** har zakazda yangi yaratiladi (zakaz ichida kod takrorlanmaydi), model rasmi majburiy, pechat ixtiyoriy — tanlangan zahoti ko'rinadi; jo'natish sanasi, har razmer soni, Просчет (zakaz + namuna uchun qo'shimcha).
  3. **Mato qismlari:** har detal o'z rangi bilan; tayyor kg = 1 dona kg × Просчет, xom kg = tayyor + xom % (standart 10), prosjyot %, eksport sanasi, laboratoriya rang kartasi rasmi.
  4. **Statuslar:** Qoralama → Tasdiqlandi → Kroyda → Tikuvda → Tugadi / Bekor; arxiv faqat superadmin. Kroy boshlangach tahrirni faqat superadmin qiladi, har o'zgarish tarixda yoziladi. Superadmin zakazni boshqa menejerga o'tkazadi.
  5. **Excel:** "Zakaz list" va "Mato zakazi" — namunadagi shaklda (qadoqlash va shtrix-kodsiz). Namunadagi ДМ 46 raqamlari bilan tekshirildi (293,76 kg, 247,2 kg ...).
  6. **Himoya:** kesilgan yoki qutisi bor artikul/zakaz o'chirilmaydi (stikerlar o'chib ketmasin).
* **Serverda:** `git pull origin main && docker compose restart web` (0037 migratsiyasi o'zi ishlaydi). Menejer foydalanuvchisini superadmin "Foydalanuvchilar" bo'limida "Menejer" roli bilan ochadi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 6a2c5f0
  ```

---

### 🔹 [Commit: `432d839`] — 11.11.2026, 09:39
* **Turi:** `fix(gifts)` — O'chirilgan test rasmi boshqa xodim o'rnida qayta paydo bo'lardi
* **Mavzu:** `/gifts/` (egasi xabar qildi). Rasm o'chirilganda uning fayl nomi (`awards/2026/11/photo.jpg`) bo'shab qolardi va keyingi yuklangan rasmga (K1, Azamova Nodira, 11.11 09:13) o'sha nom berilardi. nginx `/media/` ni brauzerda 7 kun keshlagani uchun TV va brauzer shu manzilda eski test rasmini ko'rsatardi.
* **O'zgartirilgan fayllar:**
  - `production/gift_views.py` (har bir yuklangan rasmga yangi noyob nom: `photo_<uuid>.jpg`)
  - `production/models.py` (`PatokAward.photo_src` / `gift_photo_src`: URL + `?v=<o'zgarish vaqti>`; migratsiya yo'q)
  - `templates/gifts/tv.html`, `templates/gifts/manage.html`, `templates/gifts/partials/award_form.html`
  - `production/test_gifts.py`, `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Nom qayta ishlatilmaydi:** har bir yangi rasm noyob nom bilan saqlanadi. O'chirilgan rasm nomi boshqa xodimga berilmaydi.
  2. **Kesh:** rasm manziliga o'zgarish vaqti qo'shiladi. Mavjud Azamova Nodira rasmi ham yangi manzil bilan ochiladi, TV va brauzer keshdagi eski test rasmini emas, haqiqiy rasmni oladi.
  3. **Test:** test rasmini o'chirib, keyin yangi xodimga rasm yuklash holati qo'shildi. Eski kodda bu test yiqiladi (`photo.jpg == photo.jpg`), tuzatilgan kodda o'tadi.
* **Serverda:** `git pull origin main && docker compose restart web`. Ochiq turgan taqdirlash sahifasini TV'da bir marta yangilash (F5) yoki ekrandan sovg'a belgisini qayta bosish kerak.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 432d839
  ```

---

### 🔹 [Commit: `d7d5287`] — 11.11.2026, 08:34
* **Turi:** `feat(gifts)` — TV taqdirlash sahifasida taqdirlashlar har 5 soniyada almashadi (avval 15)
* **Mavzu:** `/gifts/<patok>/` (egasining so'rovi bilan).
* **O'zgartirilgan fayllar:**
  - `production/gift_views.py` (`SLIDE_SECONDS = 5`), `templates/gifts/manage.html` (izoh matni)
  - `production/test_gifts.py`, `CLAUDE.md`
* **Serverda:** `git pull origin main && docker compose restart web`; ochiq turgan TV sahifasi yangi kiritilgan taqdirlashda yoki F5 bilan yangilanadi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout d7d5287
  ```

---

### 🔹 [Commit: `0016a0b`] — 09.11.2026, 11:07
* **Turi:** `feat(gifts)` — Patok taqdirlashlari: superadmin kiritadi, TV'da `/gifts/<patok>/` sahifasida ko'rinadi
* **Mavzu:** Egasining so'rovi: har bir patok uchun oy bo'yicha taqdirlash (ism-familiya, xodim rasmi, sovg'a rasmi, nominatsiya nomi va sababi). TV ekrandagi sovg'a belgisi shu patokning taqdirlash sahifasini ochadi.
* **O'zgartirilgan fayllar:**
  - `production/models.py` (`PatokAward`), `production/migrations/0036_patok_award.py`
  - `production/gift_views.py`, `production/gift_urls.py` (yangi), `config/urls.py` (`/gifts/`)
  - `templates/gifts/tv.html`, `templates/gifts/manage.html`, `templates/gifts/partials/award_form.html` (yangi)
  - `templates/superadmin/base_superadmin.html` (menyuda "Taqdirlash")
  - `screens/views.py` (`get_screen_gift_badge`), `templates/screens/monitor.html` (sovg'a tugmasi — egasining aniq so'rovi bilan; ekranning qolgan eski dizayni o'zgarmagan)
  - `accounts/middleware.py` (ekran akkaunti faqat o'z patokining taqdirlash sahifasini ochadi; filial admini — `/gifts/`)
  - `production/test_gifts.py`, `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Kiritish (`/gifts/boshqaruv/`, faqat superadmin):** patok va oy tanlanadi. Ism ro'yxatdan tanlanadi (shu oyda shu patokda ishlagan xodimlar, o'rtacha kunlik norma foizi bo'yicha saralangan) yoki qo'lda yoziladi. Majburiy: ism va nominatsiya; rasmlar va sabab ixtiyoriy. Tahrirlash va o'chirish bor.
  2. **Rasmlar:** telefon rasmining burilishi to'g'rilanadi va TV uchun 1600 px JPEG ga kichraytiriladi; taqdirlash o'chirilsa rasm fayllari ham o'chadi.
  3. **TV sahifasi (`/gifts/k1/` … `/gifts/u27/`):** `/screens/` kabi loginsiz ochiladi. Taqdirlash kiritilgan oxirgi oy ochiladi, ◀ ▶ bilan boshqa oylarga o'tiladi. Bir nechta taqdirlash har 15 soniyada almashadi; yangisi kiritilsa sahifa 1 daqiqa ichida o'zi yangilanadi; "Ekranga qaytish" tugmasi bor. 720p, 768p, 1080p TV va telefon kengligida tekshirildi.
  4. **TV ekrandagi belgi:** har bir `/screens/<n>/` sarlavhasida sovg'a tugmasi va oxirgi oydagi taqdirlashlar soni.
  5. **Kirish huquqi:** `ekrank1` faqat `/gifts/k1/` ni ochadi, boshqa patok sahifasiga o'ta olmaydi.
* **Serverda:** `git pull origin main && docker compose restart web` (0036 migratsiyasi o'zi ishlaydi). TV ekranlarda sovg'a belgisi chiqishi uchun bir marta qayta yuklash (F5) kerak.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 0016a0b
  ```

---

### 🔹 [Commit: `9c8ee44`] — 04.11.2026, 22:25
* **Turi:** `feat(screens)` — TV ekranlarda "Patok quvvati" o'rniga Patok normasi, Asosiy dazmol va Patok foizi (18:00 hisobot bilan bir xil)
* **Mavzu:** Barcha 40 ta `/screens/<n>/` TV ekrani (egasining aniq so'rovi bilan; sahifaning qolgan eski dizayni o'zgarmagan). Ekrandagi foiz istalgan patok uchun 18:00 hisobotdagi "Фоиз" bilan bir xil bo'lishi kerak.
* **O'zgartirilgan fayllar:**
  - `screens/views.py` (`get_screen_patok_metrics`: kunlik hisob barcha ekranlar uchun bir marta, 30 soniya kesh)
  - `templates/screens/monitor.html` (sarlavha va 30 soniyalik JS yangilanishi)
  - `production/daily_image_reports.py` (`get_assigned_batches_by_screen`, `patok_report_lines` — hisobot qatorlari alohida funksiyaga ajratildi, hisobot natijasi o'zgarmagan)
  - `screens/tests.py`, `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Patok normasi:** 18:00 hisobot qatorlari normalarining o'rta arifmetigi (asosiy dazmoli chiqqan shablon x norma; dazmol bo'lmasa — patok qabul qilgan oxirgi pastal modeli). Masalan futbolka 1000 va tunika 1500 — 1250.
  2. **Asosiy dazmol:** 18:00 hisobot qoidasi (patok qabul qilgan pastallar dazmoli, qayerda dazmollangan bo'lsa ham).
  3. **Patok foizi:** asosiy dazmol / patok normasi — hisobotning o'z `patok_foiz` funksiyasi bilan, shuning uchun istalgan patokda bir xil.
  4. **Tekshiruv:** real baza nusxasida 02.11 va 04.11, ikkala filialning 34 ta patokida ekran va hisobot o'rtasida farq 0. Test K1–K13 ning har biri uchun ekran = hisobotni tekshiradi; tegishli 40 ta testdan faqat avvaldan mavjud Telegram yozuvi testi xato (o'zgarishdan oldin ham).
  5. **Hali o'zgarmagan:** `/screens/` umumiy sahifasida "Patok quvvati" qolgan.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q). TV ekranlar sahifani 30 soniyada o'zi yangilaydi; sarlavha yangilanishi uchun bir marta qayta yuklash (F5) kerak.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 9c8ee44
  ```

---

### 🔹 [Commit: `6d055a0`] — 04.11.2026, 12:49
* **Turi:** `feat(statistics)` — Qutilar statistikasida pastal bo'yicha qidirish va pastal haqida to'liq ma'lumot
* **Mavzu:** `/statistics/` (Qutilar Statistikasi) sahifasida "Pastal bo'yicha qidirish" bloki: pastal kodi, pasport QR yoki quti kodi kiritilsa, pastal kartochkasi chiqadi va pastdagi qutilar ro'yxati shu pastalga filtrlanadi.
* **O'zgartirilgan fayllar:**
  - `production/pastal_info.py` (yangi): `find_pastal_batches`, `build_pastal_info` — faqat o'qiydi
  - `production/views.py` (`box_pipeline_statistics_view`: `?pastal_info=`), `templates/production/statistics_pipeline.html`
  - `templates/production/partials/pastal_info_card.html` (yangi)
  - `production/test_pastal_info.py` (yangi), `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Kartochka:** model, zakaz va mijoz, kesim raqami, yaratilgan vaqti, bichuvchi va kirituvchi, narxlar shabloni, hozirgi holati; kesilgan / metodan keyin / qutilarda, tikuv %, dazmol, 1-sort, 2-sort, ta'mirda.
  2. **Bo'limlar:** kesim va razmerlar (meto soni, metochi, qutilar, chop etish, mato, vazn); patok (bot jo'natishi, planshetda qabul, qaytarish, Uychi yuk xati); tikuv va dazmol (skanlar, tikuvchilar, ish haqi, qaysi patoklarda); OTK (qutilar, sortlar, ta'mir, tekshiruvlar); pastal tarixi — kesimdan yopilishgacha vaqt bilan.
  3. **Qidiruv:** aniq kod — shu kodli barcha pastallar (bir xil kodli ikki model alohida); pasport QR va quti kodi — o'sha pastal; aks holda o'xshash pastallar taklif qilinadi.
  4. **Testlar:** 3 ta yangi (kartochka raqamlari va tarix tartibi, qidiruv turlari, sahifa va qutilar filtri); tegishli 37 ta test o'tdi. Real baza nusxasida 10-773, 10-626, 10-522 tekshirildi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q).
* **Qaytish buyrug'i:**
  ```bash
  git checkout 6d055a0
  ```

---

### 🔹 [Commit: `19b600f`] — 04.11.2026, 12:23
* **Turi:** `feat(brand)` — Terry Jar Textile logotipi vektorda va butun loyiha uchun brauzer tabi belgisi
* **Mavzu:** Chrome tabida odatiy globus belgisi turardi (favicon umuman yo'q edi). Egasi bergan logotip rasmi (PNG) vektorga (SVG) aylantirildi va barcha sahifalar uchun tab belgisi qilindi.
* **O'zgartirilgan fayllar:**
  - `production/static/brand/` (yangi): `terryjar-logo.svg` (to'liq logotip — paxta shoxi, TERRY JAR TEXTILE, TERRY TOWELS), `terryjar-icon.svg` (tab belgisi: oq yumaloq kvadrat ustida paxta shoxi), `favicon.ico` (16/32/48), `apple-touch-icon.png` (180)
  - `templates/includes/theme_head.html` (`<link rel="icon">` va `apple-touch-icon`)
  - `config/urls.py` (`/favicon.ico` → `/static/brand/favicon.ico`), `accounts/middleware.py` (`/favicon.ico` barcha rollar uchun)
  - `accounts/tests.py` (`BrandFaviconTest`), `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Vektor:** OpenCV konturlari bilan 4x kattalashtirilgan silliq niqobdan chizildi; paxta shoxi chetlari silliqlandi, yozuvlarning serif burchaklari o'tkir qoldi; "TERRY TOWELS" ingichka chiziqlari uchun alohida bo'sag'a. Rangi `#040361`.
  2. **Tab belgisi:** 16 px da yozuv o'qilmaydi — faqat paxta shoxi, chiziqlari biroz qalinlashtirilgan; yorug' va qorong'i Chrome tablarida tekshirildi.
  3. **Butun loyiha:** `theme_head` ulangan sahifalar belgini havola orqali, qolganlari (muzlatilgan `/control/`, `/screens/`, bosma sahifalar) brauzerning `/favicon.ico` so'rovi orqali oladi — ularning shablonlariga tegilmadi.
  4. **Testlar:** 3 ta yangi test (fayllar, havolalar, `/favicon.ico` mehmon va MASTER/CONTROL/SCREEN uchun); tekshirilgan 20 ta test o'tdi.
* **Serverda:** `git pull origin main && docker compose restart web` (`collectstatic` web ishga tushganda yig'adi). Brauzer eski belgini keshda saqlashi mumkin — tabni yopib ochish yoki Ctrl+Shift+R.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 19b600f
  ```

---

### 🔹 [Commit: `e6d9c55`] — 04.11.2026, 11:47
* **Turi:** `fix(pricing)` — Yangi shablonda operatsiyalar belgilangan tartibda raqamlanadi
* **Mavzu:** Narxlar matritsasi → "Yangi shablon yaratish" oynasi. Stikerlar shablondagi tartib bo'yicha chop etiladi, lekin shablon yaratilganda "Tartib №" katalogdagi qator raqami (1…157) bo'lib qolardi va belgi qo'yilganda o'zgarmasdi. Natijada shablon (stikerlar ham) katalog — asosan alifbo tartibida saqlanardi: masalan "Mayka detskiy Kluch" da DAZMOL 11 tadan 4-o'rinda. Real bazada 4+ operatsiyali 42 shablondan 19 tasi shunday; 26.10 dan beri yaratilganlarning deyarli hammasi.
* **O'zgartirilgan fayllar:**
  - `templates/superadmin/pricing.html` (yaratish oynasi JS: `createSelectionOrder`, `renumberCreateSelection`, `onCreateSeqEdited`, `renderCreateSelectionPreview`)
  - `accounts/tests.py` (`PricingTemplateCreateClickOrderTest`)
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Bosilgan tartib:** belgi qo'yilgan operatsiya navbatdagi raqamni oladi (1, 2, 3 …); belgi olinsa qolganlari bo'sh joysiz qayta raqamlanadi; tanlanmagan qatorlarda raqam bo'sh.
  2. **Qo'lda o'zgartirish:** "Tartib №" ga raqam yozilsa operatsiya o'sha o'ringa o'tadi (belgilanmagan bo'lsa belgilanadi), qolganlari suriladi.
  3. **Ko'rinish:** oyna tepasida "Tanlangan tartib: 1. … → 2. …" qatori. Oynada tezkor qo'shilgan yangi operatsiya navbatdagi raqamni oladi (avval oxiriga tushardi). "Barchasi" tanlanganlar tartibini saqlab qolganlarini ro'yxat tartibida qo'shadi.
  4. **Server o'zgarmagan:** u allaqachon "Tartib №" bo'yicha saqlaydi. Mavjud shablonlar tartibi o'zgarmagan ("DC SHORTIK PIJAMA" dagi takroriy №7 ham — egasining so'rovi bilan tegilmadi).
  5. **Testlar:** headless Chrome'da bosish, belgi olish, qo'lda raqam yozish, "Barchasi"/"Tozalash" va serverga yuboriladigan raqamlar tekshirildi; shablon testlari (33 ta, 2 tasi yangi) o'tdi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q).
* **Qaytish buyrug'i:**
  ```bash
  git checkout e6d9c55
  ```

---

### 🔹 [Commit: `7857970`] — 04.11.2026, 10:57
* **Turi:** `fix(passport-bot)` — Vazn butun son bilan saqlanadi, qabul qilinmagan pastal boshqa patokka o'tkaziladi, bir xil kodli pastallardan tanlanadi
* **Mavzu:** Telegramdagi pastal pasporti va patokka biriktirish boti (`run_passport_bot`). Real bazada topilgan uchta xato tuzatildi.
* **O'zgartirilgan fayllar:**
  - `production/management/commands/run_passport_bot.py` (`parse_weight_text`, `batch_dispatch_state`, `dispatch_batch_async`, tanlash ro'yxati, `dp_pat` / `dp_cfm` / `dp_pick`)
  - `production/passport_bot_service.py` (`find_pastal_candidates`, `_cutting_batch_candidates_qs`)
  - `production/test_passport_bot.py`, `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Vazn:** vazn so'ralganda har qanday son vazn hisoblanadi (52, 48.5, 48,5, 52 kg). Avval butun son pastal kodi deb tushunilib, vazn saqlanmasdan boshqa pastal ochilardi: real bazada 1–200 sonlardan 199 tasi biror pastalga mos kelardi, kiritilgan 596 vazndan faqat 18 tasi butun son. 0.1 – 20 000 kg dan tashqari son qayta so'raladi.
  2. **Qabul qilinmagan pastalni boshqa patokka o'tkazish:** `14cf5ee` qarori (master qabul qilguncha qayta yo'naltirish mumkin) endi patok tanlash bosqichida ham ishlaydi, u yerda eski taqiq qolib ketgan edi. Tasdiqlashda "K3-Patokda kutilmoqda → K5-Patokka ko'chiriladi", natijada "K3-Patokdan ko'chirildi".
  3. **Eski tasdiqlash tugmasi:** tugmada pastal holati bor (`batch_dispatch_state`, masalan `D3`); holat o'zgargan bo'lsa pastal jo'natilmaydi. Yangilanishdan oldingi tugmalar "eskirgan" deydi. Jo'natish pastal qatorini qulflab bajariladi.
  4. **Bir xil kodli pastallar:** qo'lda yozilgan kod bir nechta pastalga mos kelsa (85 ta takroriy kod, ko'pi pijama ustki va shorti kabi ikki model), bot ro'yxat chiqaradi, jo'natilmagani birinchi. Pasport QR va quti kodi bilan ishlash o'zgarmagan.
  5. **Testlar:** `production.test_passport_bot` — 30 ta (3 ta yangi) o'tdi. `MasterTabletWorkflowTest` dagi kirillcha beydj testi bu o'zgarishdan oldin ham xato edi.
* **Serverda:** `git pull origin main && docker compose restart web passport_bot`. Yangilanishdan oldingi tasdiqlash tugmalari "eskirgan" deydi — pastalni qaytadan skanerlash kerak.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 7857970
  ```

---

### 🔹 [Commit: `cef25c3`] — 02.11.2026, 16:30
* **Turi:** `fix(control)` — Ta'mirdan qaytgan qutini ba'zi planshetlarda qabul qilib bo'lmasdi
* **Mavzu:** `/control/` (egasining so'rovi bilan). 30.10 dagi o'zgarishda +1 / son / −1 ustuniga `relative z-10` berilgan edi. Ta'mirdan qaytgan qutida banner, "Ta'mirda bo'lgan: N dona" qatori va uzunroq tugma yozuvi sahifani balandlashtiradi: ustun pastga cho'zilib "Tasdiqlash va Ta'mirni Tekshirish" tugmasini yopardi va uning bo'sh joyi har bir bosishni ushlab qolardi. Ekrani pastroq yoki zoomi kattaroq planshetlarda tugma umuman bosilmasdi; yangi qutida ustun qisqaroq bo'lgani uchun muammo yo'q edi.
* **O'zgartirilgan fayllar:**
  - `templates/production/control_home.html`
  - `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Tuzatish:** ustunning bo'sh joyi bosishni ushlamaydi (`pointer-events-none`), faqat +1, son maydoni va −1 bosiladi (`pointer-events-auto`). Joylashuv va ranglar o'zgarmadi.
  2. **Tekshiruv:** Chrome'da 1024 / 1280 kenglik, 600 / 700 / 800 balandlik, 85% / 100% zoomda: ta'mir rejimidagi "Tasdiqlash" 0% dan 100% gacha bosiladigan bo'ldi (1024 kenglik va 100% zoomda 78–82%: chetini −1 tugmasi yopadi), 2-qadamdagi "Yakunlash" hamma holatda 100%.
  3. **Testlar:** `ControlQualityInspectionWorkflowTest` (7 ta) va `/control/` ochiladigan `ScreenAccountAuthAndAccessRestrictionTest` o'tdi.
  4. **Ma'lum cheklov:** eng past ekranlarda (taxminan 600 px) ta'mir rejimida −1 tugmasi ekran pastidan chiqib ketadi (oldin ham shunday edi); sonni maydonga yozib kiritish mumkin.
* **Serverda:** `git pull origin main && docker compose restart web`; planshetlarda sahifani bir marta yangilash.
* **Qaytish buyrug'i:**
  ```bash
  git checkout cef25c3
  ```

---

### 🔹 [Commit: `994f51c`] — 02.11.2026, 13:20
* **Turi:** `feat(daily-report)` — 18:00 hisobot yangi shakli: Паток, Иш номи, Норма, Иш сони, Давомат, Операцион, Фоиз
* **Mavzu:** `/sewing-statistics/daily-report/` sahifasi va Telegramga ketadigan 18:00 rasm 7 ustunli bo'ldi; "Паток қуввати" o'rniga normaga nisbatan foiz.
* **O'zgartirilgan fayllar:**
  - `production/daily_dazmol.py` (`template_lines`, `get_article_norm`)
  - `production/daily_image_reports.py` (qatorlar, `patok_foiz`, rasm, Telegram izohi)
  - `production/sewing_statistics_views.py`, `templates/production/sewing_statistics_daily_report.html`
  - `production/test_daily_dazmol.py`, `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Norma:** shablonda ishlangan modelning kunlik normasi (artikul → model → 1200); bir shablonda ikki xil norma bo'lsa alohida qator. Ish sonlari yig'indisi Kunlik Patoklar sahifasidagi asosiy dazmolga teng qoladi.
  2. **Foiz:** ish sonlari yig'indisi / normalarning o'rtacha arifmetigi — masalan (100 + 900) / ((800 + 1000) / 2) = 111%. Filial foizi — faol patoklar o'rtachasi, fabrika — ikki filial o'rtachasining o'rtachasi.
  3. **Rasm va izoh:** 7 ustunli jadval; izohda "Ўртача фоиз".
  4. **Testlar:** 289 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi. Egasi misoli (111.1%), ikki normali shablon va rasm chizilishi tekshiriladi.
  5. **Hali o'zgarmagan:** oylik Excel va `DailyPatokProgress.patok_kpi` eski "quvvat" ni saqlaydi.
* **Serverda:** `git pull origin main && docker compose restart web scheduler` (migratsiya yo'q)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 994f51c
  ```

---

### 🔹 [Commit: `1d81ca0`] — 30.10.2026, 20:44
* **Turi:** `fix(statistics)` — Dazmol = OTK kutmoqda + Ta'mir + 1-sort + 2-sort tengligi (model, zakaz va fabrika sahifalarida)
* **Mavzu:** TFHZ604 (zakaz 2309) da Dazmol 6 179, qolganlari yig'indisi 5 737 edi (farq 442). Sabablar: ta'mirdagi qutilarning saralangan qismi hech qayerda sanalmasdi; OTK ko'rgan, lekin dazmol stikeri urilmagan qutilar Dazmolga kirmasdi; DAZMOL operatsiyasi stikerlar chop etilgandan keyin qo'shilgan qutilarda dazmol bileti umuman yo'q edi.
* **O'zgartirilgan fayllar:**
  - `production/sewing_statistics_views.py` (`box_dazmol_time`, `evaluate_box_progress`, model/zakaz sahifalari, qutilar oynasi)
  - `production/sewing_pipeline_summary.py` (fabrika jami — xuddi shu qoida)
  - `templates/production/sewing_statistics_model_detail.html`, `sewing_statistics_order_models.html`, `sewing_statistics_orders.html`, `partials/size_boxes_pipeline.html`
  - `production/test_pipeline_summary.py`, `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **1/2-sort:** OTK ko'rgan har bir qutining saralangan qismi sanaladi (ta'mirdagi qutiniki ham); "Ta'mir" da faqat ta'mirdagi dona.
  2. **Dazmol:** quti dazmoldan o'tgan — DAZMOL bileti urilgan, yoki barcha faol stikerlari urilgan, yoki OTK ko'rgan bo'lsa. Oxirgi ikkisi "⚠ N quti dazmol stikeri urilmagan/yo'q" deb ko'rsatiladi (dazmolchiga haq yozilmagan).
  3. **Natija:** TFHZ604: 6 358 = 471 + 71 + 5 710 + 106. Real bazada 27 zakazdan 26 tasida tenglik to'liq; 0309 da #19 quti brakni 2-sortdan alohida yozgan (5 dona) — ma'lumotni tuzatish kerak. Fabrika bo'yicha 211 quti (8 003 dona) dazmol bileti urilmagan/yo'q.
  4. **Testlar:** 287 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi. Yangi test uchala sahifada tenglikni tekshiradi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 1d81ca0
  ```

---

### 🔹 [Commit: `45979c4`] — 30.10.2026, 18:06
* **Turi:** `feat(statistics)` — Tikim statistikasi bosh sahifasida "Fabrika bo'yicha jami" — zakaz sahifasidagi 8 bosqichli quvur (sana bo'yicha)
* **Mavzu:** `/sewing-statistics/` yuqorisidagi eski 6 ta karta o'rniga zakaz va model sahifalaridagi kabi 8 bosqich: Reja, Kesilgan (+ Qutilarda), Tikim kutmoqda, Hozir patokda, Dazmol, OTK kutmoqda (tayyor/chala), Ta'mirda, 1/2-sort — "oldingi + shu kunda" sana tanlash bilan.
* **O'zgartirilgan fayllar:**
  - `production/sewing_pipeline_summary.py` (yangi)
  - `production/sewing_statistics_views.py`
  - `templates/production/sewing_statistics_orders.html`
  - `templates/production/sewing_statistics_order_models.html`
  - `production/test_pipeline_summary.py` (yangi)
  - `CLAUDE.md`, `.claude/project-knowledge/*`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Tezkor hisob:** qutilar bo'yicha belgilar bitta guruhlangan SQL so'rovda (biletlar Python obyektiga aylantirilmaydi) — real bazada sahifa 15 s → 2 s.
  2. **Aniqlik:** natija zakaz sahifasidagi `order_totals` bilan aynan bir xil (real bazadagi 27 zakaz, 2 sana — farq 0; test bilan himoyalangan). Jadval qatorlari ham shu hisobdan.
  3. **Sana:** oldingi/keyingi kun/bugun; sana filtr tablari, qidiruv, sahifalash va zakazga o'tish havolalarida saqlanadi.
  4. **Qutilarda:** zakaz sahifasidagi "Kesilgan" kartasiga ham qo'shildi (uchala darajada bir xil).
  5. Zakazlar sahifasi navigatsiyasidan "OTK Nazoratchilari" havolasi olib tashlangan (muharrirda qilingan o'zgarish).
  6. **Testlar:** 284 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi.
* **Serverda:** `git pull origin main && docker compose restart web scheduler` (migratsiya yo'q)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 45979c4
  ```

---

### 🔹 [Commit: `104bf1f`] — 30.10.2026, 18:06
* **Turi:** `fix(dazmol)` — Tayyor dazmol faqat aynan "DAZMOL" operatsiyasi
* **Mavzu:** Egasi qoidasi: dazmol operatsiyasi modelning oxirgisi; PLANKA / TASMA / CHO'NTAK va boshqa "... DAZMOL" oraliq amallar tayyor ish emas — asosiy dazmolda ham, patok dazmolida ham sanalmaydi.
* **O'zgartirilgan fayllar:**
  - `production/daily_dazmol.py`, `production/sewing_statistics_views.py`, `production/master_views.py`, `screens/views.py`, `accounts/superadmin_views.py`
  - `production/test_daily_dazmol.py`, `production/tests.py`, `production/test_master_statistics.py`, `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Yagona qoida:** `is_main_dazmol_operation` (aniq "DAZMOL") va `main_dazmol_operation_ids()`; avval 7 joyda alohida takrorlangan qoida endi bitta: kunlik sahifa, 18:00 hisobot, zakaz/model statistikasi, TV ekranlar dazmol soni (dizayn o'zgarmagan), master planshet, superadmin.
  2. **Real bazadagi ta'sir:** PLANKA DAZMOL (TERJAR118 ПОЛО) dazmolni oshirib, qutilarni ikki marta sanardi: 29.10 asosiy 20 084 → 18 629; K2 1 400 → 600; zakaz 118 dazmol 4 722 → 2 033.
  3. **Eslatma:** o'tgan kunlarning `DailyPatokProgress` (oylik Excel) qiymatlari o'sha kun sahifasi qayta ochilganda yangilanadi.
* **Qaytish buyrug'i:**
  ```bash
  git checkout 104bf1f
  ```

---

### 🔹 [Commit: `d128fa5`] — 30.10.2026, 16:08
* **Turi:** `fix(control)` — OTK planshet oynasi: −1 tugmasi "Tasdiqlash" ostida qolmaydi; ta'mir sariq, 2-sort qizil
* **Mavzu:** `/control/` (egasining aniq so'rovi bilan, sahifaning qolgan eski dizayni o'zgarmagan). Yotiq planshetda (1024×600) quti sonining −1 tugmasi "Tasdiqlash va qabul qilish" tugmasi ostida qolardi; ta'mir va 2-sort ranglari teskari edi.
* **O'zgartirilgan fayllar:**
  - `templates/production/control_home.html`
  - `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Real son bloki chapga:** +1 / son / −1 ustuni rasm yonida (`lg:items-start`), "Tasdiqlash" tugmasi ustida bosiladi (`relative z-10`).
  2. **Ranglar:** ta'mir — sariq, 2-sort — qizil: sarlavha hisoblagichlari, 2-qadam bloklari, 2-sort va ta'mir tanlash oynalari, ro'yxatlar, natija va smena yakuni oynalari, ta'mirdan qaytish banneridagi 2-sort. O'chirish tugmalari va ogohlantirishlar qizil qoladi.
  3. **Testlar:** 280 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi.
* **Serverda:** `git pull origin main && docker compose restart web`; planshetlarda sahifani bir marta yangilash.
* **Qaytish buyrug'i:**
  ```bash
  git checkout d128fa5
  ```

---

### 🔹 [Commit: `28a768e`] — 30.10.2026, 15:45
* **Turi:** `feat(statistics)` — Model sahifasida "Kesilgan" yonida qutilardagi real son (zakazning real soni)
* **Mavzu:** `/sewing-statistics/orders/<id>/items/<id>/` sahifasida kesimchi kiritgan son bilan birga metodan keyin qutilarga bo'lingan va OTK sanagan haqiqiy son (barcha qutilar yig'indisi) va farqi ko'rsatiladi.
* **O'zgartirilgan fayllar:**
  - `production/sewing_statistics_views.py`
  - `templates/production/sewing_statistics_model_detail.html`
  - `production/tests.py`
  - `CLAUDE.md`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **"2. Kesilgan" kartasi:** kesimchi soni ostida "Qutilarda: <son> (+/-farq)"; izohda kesimchi, meto aniq soni va qutilardagi son.
  2. **Razmerlar:** "Reja / Kesim / Qutida" (masalan 300 / 315 / 341 (+26)).
  3. **Dazmol izohi:** dazmol qutining real soni bo'yicha sanalgani uchun kesimdan ko'p bo'lishi mumkin — "Qutilarda" bilan solishtiriladi. Sabab (TFHZ508, zakaz 2309): OTK 32 ta qutini stikerdagidan ko'p sanagan (masalan 52 → 55), tizim quti sonini OTK soniga almashtiradi, stikerlar eski sonda qoladi (+216 dona).
  4. **Testlar:** 280 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi.
* **Serverda:** `git pull origin main && docker compose restart web` (migratsiya yo'q)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 28a768e
  ```

---

### 🔹 [Commit: `39af740`] — 30.10.2026, 15:45
* **Turi:** `fix(telegram)` — 18:00 filial hisobotlari to'g'ri Telegram guruhlarga
* **Mavzu:** Serverdagi `.env` da guruh ID'si bo'lmasa `docker-compose.yml` eski guruhlarni berardi: Koson `-2111243086` (bot chiqarilgan, "bot was kicked from the supergroup chat"), Uychi `-2697507612` ("chat not found"). Endi standart qiymatlar Koson_daily `-1004425170756` va Uychi_daily `-1003903019133` (ikkalasida @PAYROLL_MASTER_BOT admin).
* **O'zgartirilgan fayllar:**
  - `docker-compose.yml` (`web` va `scheduler`)
* **Serverda:** `git pull origin main && docker compose up -d web scheduler` (`restart` emas — muhit o'zgaruvchilari faqat `up -d` da yangilanadi)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 39af740
  ```

---

### 🔹 [Commit: `6ee41ed`] — 29.10.2026, 13:53
* **Turi:** `feat(daily-report)` — Kunlik dazmol yagona manbadan: Kunlik Patoklar va Kunlik Hisobot (18:00) sahifalari doim teng, sahifalar qulayroq, Uychi U15–U27 patoklari
* **Mavzu:** `/sewing-statistics/daily/` dagi har bir patokning asosiy dazmoli `/sewing-statistics/daily-report/` (va 18:00 rasm) dagi shu patok shablonlari yig'indisiga qat'iy teng. Asosiy dazmolga faqat patokka haqiqatan qabul qilingan pastallar kiradi; qabul qilinmagan pastallar dazmoli "egasiz" bo'lib, hech kimga yozilmaydi.
* **O'zgartirilgan fayllar:**
  - `production/daily_dazmol.py` (yangi)
  - `production/sewing_statistics_views.py`
  - `production/daily_image_reports.py`
  - `production/excel_reports.py`
  - `production/meto_hub_views.py`
  - `templates/production/sewing_statistics_daily.html`
  - `templates/production/sewing_statistics_daily_report.html`
  - `templates/meto_hub/uychi_dashboard.html`
  - `production/test_daily_dazmol.py` (yangi), `production/tests.py`, `production/test_master_statistics.py`
* **Kiritilgan funksiyalar va o'zgarishlar:**
  1. **Yagona dazmol manbasi (`production/daily_dazmol.py`):** asosiy / patok / egasiz dazmol bir joyda hisoblanadi; ikkala sahifa, 18:00 rasm va `DailyPatokProgress` (oylik Excel) shu natijani oladi. 1-sahifadagi ikki marta sanash va `DailyPatokProgress.dazmol_qty` ning sahifadan sahifaga o'zgarishi tuzatildi.
  2. **Qabul qoidasi:** Telegram bot yuborgan (`DISPATCHED`) pastal patok planshetida qabul qilinganda (`Box.is_received_by_patok`) yoki Uychi omboridan U-patokka topshirilganda shu patokniki bo'ladi. Faqat yuborilgan yoki hali omborda turgan pastal — egasiz.
  3. **Kunlik Patoklar sahifasi:** "Asosiy Dazmol" ustunida jami, shablonlar (18:00 hisobotdagidek) va pastallar; "Patok Dazmoli" ustunida terminal bo'yicha son va "shundan egasiz"; KPI kartalarida asosiy / patok / egasiz; tanlangan sana hisobot sahifasiga o'tadi; `?patok=K1` aniq filtr (K10–K13 ni ushlamaydi), filtr chipi va tafsilot avtomatik ochiladi; Telegram oynasida ogohlantirish.
  4. **Kunlik Hisobot (18:00) sahifasi:** patok raqamidan Kunlik Patoklar sahifasiga havola, "Фабрика жами", operatsiyalar soni shablon qatoriga tekislangan, "Жонли (ҳозиргача) / Кун якуни" yozuvi, Uychi tabida haqiqiy patoklar soni.
  5. **Uychi U15–U27:** Uychi ombori endi U1–U27 ga topshira oladi (avval U1–U14); oylik Excel'ning Uychi varag'ida U21 dan keyingi ishlagan patoklarga ham qator qo'shiladi (ЖАМИ qatori bilan mos).
  6. **Testlar:** 279 ta test; 7 ta xato bu o'zgarishdan oldin ham bor edi va unga aloqasiz. Yangi `production/test_daily_dazmol.py` ikki sahifa tengligini, qabul qoidasini (planshet API orqali) va oylik Excel qatorlarini tekshiradi.
* **Serverda:** `git pull origin main && docker compose restart web scheduler` (migratsiya yo'q)
* **Qaytish buyrug'i:**
  ```bash
  git checkout 6ee41ed
  ```

---

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
