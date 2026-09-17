FROM python:3.12-slim

# Muhit o'zgaruvchilari
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Tizim paketlari (PostgreSQL, OpenCV, Pillow grafik modullari uchun)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libgl1 \
    libglib2.0-0 \
    libjpeg-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python bog'liqliklarini o'rnatish
COPY requirements.txt /app/
RUN pip install --no-cache-dir --default-timeout=120 --retries 5 -r requirements.txt

# Loyiha kodlarini ko'chirish
COPY . /app/

# Static va Media papkalarini tayyorlash
RUN mkdir -p /app/staticfiles /app/media

EXPOSE 8000

# Serverni Gunicorn orqali ishga tushirish
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]

