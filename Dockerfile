# InSync backend — FastAPI API + Telegram-бот (aiogram) в одном процессе.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/insync.db

WORKDIR /app

# Сначала зависимости — чтобы слой кешировался между сборками.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Код бэкенда.
COPY backend ./backend

# Каталог для SQLite (монтируется как volume в compose).
RUN mkdir -p /app/data

EXPOSE 8000

# Проверка живости — через stdlib, без лишних пакетов в образе.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
