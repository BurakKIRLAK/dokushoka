# ---- Dokushoka için tek ortam: Windows'ta da, Linux sunucuda da AYNI image çalışır ----

FROM python:3.12-slim

# Derleme sırasında gerekebilecek sistem paketleri
# (cryptography gibi paketler için derleyici araçları + sertifika dosyaları)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Önce sadece requirements.txt'i kopyala -> Docker layer cache sayesinde
# kod değiştiğinde paketler tekrar tekrar kurulmaz, build hızlanır.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# Projenin tamamını kopyala
COPY . .

# Flask varsayılan portu
EXPOSE 5000

# Production'da Gunicorn ile çalıştır (README'de zaten Gunicorn belirtilmişti)
# Geliştirme sırasında docker-compose.yml bu komutu override edip
# "flask run --debug" ile değiştirebilir.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "app:app"]
