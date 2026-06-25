# BharatStocks — Indian Stock Analyzer (production image)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# One worker keeps the in-memory cache shared (the app relies on a per-process
# TTL cache + ThreadPoolExecutor); threads provide concurrency. Override with
# WEB_CONCURRENCY / THREADS env vars if needed.
CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} --threads ${THREADS:-8} -k gthread -b 0.0.0.0:${PORT:-8000} --timeout 60 app:app"]
