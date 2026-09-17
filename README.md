# TERRY JAR — Ishbay Oylik va Ishlab Chiqarishni Boshqarish Tizimi

TERRY JAR korxonasining tikuvchilik va ishlab chiqarish jarayonlarini avtomatlashtirish, ishbay oylik hisoblash hamda QR-kodli bilet/qutilar orqali real vaqtda kuzatish tizimi.

## 🚀 Asosiy Imkoniyatlar
- **Ishbay oylik hisob-kitobi:** Tikuvchilar va ishchilarning bajargan operatsiyalari va kunlik normasi bo'yicha avtomatik oylik hisoblash.
- **QR-kodli biletlar va stikerlar:** Har bir mahsulot qutisi (Box) va operatsiya uchun maxsus 100x60 o'lchamdagi QR-kodli stikerlar generatsiyasi.
- **Tezkor Skaner Terminali:** Brauzer orqali ishchilar va biletlar QR-kodini bir zumda skanerlash va qayd etish.
- **Jonli TV Ekranlar:** Sexdagi katta ekranlar uchun real vaqtda yangilanib turuvchi operatsiyalar va ishchilar reytingi monitori.
- **Superadmin Paneli:** Narxlar, operatsiyalar, xodimlar, buyurtmalar va oylik hisobotlarini to'liq nazorat qilish.

## 🛠 Texnologiyalar
- **Backend:** Python 3.12, Django 5.x
- **Baza:** PostgreSQL 16
- **Server & Konteyner:** Docker, Docker Compose, Gunicorn, Nginx
- **Frontend:** Bootstrap 5, HTML5, CSS3, JavaScript

## 📦 Ishga tushirish (Docker orqali)
```bash
cp .env.production.example .env
docker compose up -d --build
./restore_data.sh
```

---
*TERRY JAR Production System © 2026*

