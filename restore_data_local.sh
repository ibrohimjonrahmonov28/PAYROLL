#!/bin/bash
set -e

echo "1. Migratsiyalarni tekshirish..."
docker compose -f docker-compose.local.yml exec web python manage.py migrate --noinput

echo "2. Baza ma'lumotlarini tiklash (loaddata)..."
if [ -f data_backup.json ]; then
    docker compose -f docker-compose.local.yml exec web python manage.py loaddata data_backup.json
    echo " -> Ma'lumotlar bazasi muvaffaqiyatli tiklandi!"
else
    echo " -> data_backup.json topilmadi."
fi

echo "=============================================================================="
echo "Muvaffaqiyatli yakunlandi!"
echo "Loyihani ochish: http://localhost:8000"
echo "Admin parolini o'zgartirish: docker compose -f docker-compose.local.yml exec web python manage.py changepassword admin"
echo "=============================================================================="
