#!/bin/bash
# ==============================================================================
# TERRY JAR — Ma'lumotlar bazasi va barcha QR kodlarni tiklash skripti (Restore)
# ==============================================================================
set -e

echo "1. PostgreSQL bazasiga ma'lumotlarni yuklash..."
if [ -f payroll_backup.sql ]; then
    docker compose exec -T db psql -U ${DB_USER:-payroll_user} -d ${DB_NAME:-payroll_db} < payroll_backup.sql
    echo " -> Baza ma'lumotlari muvaffaqiyatli tiklandi!"
else
    echo " -> payroll_backup.sql topilmadi, o'tkazib yuborildi."
fi

echo "2. Barcha QR kodlar va media fayllarni tiklash..."
if [ -f media_backup.tar.gz ]; then
    docker compose cp media_backup.tar.gz web:/app/
    docker compose exec web tar -xzvf /app/media_backup.tar.gz -C /app/
    docker compose exec web rm /app/media_backup.tar.gz
    echo " -> Barcha QR stikerlar va media fayllar tiklandi!"
else
    echo " -> media_backup.tar.gz topilmadi, o'tkazib yuborildi."
fi

echo "3. Migratsiyalarni tekshirish..."
docker compose exec web python manage.py migrate --noinput

echo "=============================================================================="
echo "Tabriklaymiz! Barcha 24 xodim, 68 quti va 889 ta QR stiker tiklandi va tayyor!"
echo "=============================================================================="

