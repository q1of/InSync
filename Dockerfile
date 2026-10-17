# InSync backend — FastAPI API + Telegram-бот (aiogram) в одном процессе.
#
# Сборка:
#   docker build -t insync-backend .
# Запуск (порт 8888, автоподъём при падении) — см. README/ответ ассистента.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="InSync backend" \
      org.opencontainers.image.description="Telegram Mini App для автоотметки студентов МИРЭА (FastAPI + aiogram)" \
      org.opencontainers.image.source="https://github.com/yohjidev/InSync" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow \
    DATABASE_PATH=/app/data/insync.db

# tzdata — чтобы время в логах было московским (МИРЭА), curl — для удобной отладки.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала зависимости — слой кешируется, пока не менялся requirements.txt.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Код бэкенда.
COPY backend ./backend

# Непривилегированный пользователь + каталог данных под ним (безопасность).
RUN useradd --system --create-home --uid 1000 insync \
    && mkdir -p /app/data \
    && chown -R insync:insync /app
USER insync

# SQLite живёт здесь — монтируйте volume, чтобы данные переживали пересборку.
VOLUME ["/app/data"]

EXPOSE 8000

# Живость контейнера — Docker сам отметит unhealthy, если /api/health не отвечает.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
