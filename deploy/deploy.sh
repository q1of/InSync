#!/usr/bin/env bash
# Полный деплой InSync на одном Docker-хосте (без docker compose):
# backend (FastAPI + Telegram-бот) + web (nginx + собранный фронтенд) в общей сети.
#
# Использование:
#   cp backend/.env.example backend/.env   # и заполнить значения
#   bash deploy/deploy.sh                   # публичный порт по умолчанию 8888
#   PORT=80 bash deploy/deploy.sh           # сменить публичный порт
set -euo pipefail

PORT="${PORT:-8888}"      # публичный порт (nginx с фронтендом)
NET="insync-net"          # общая docker-сеть, чтобы web видел backend по имени

# Переходим в корень проекта (скрипт лежит в deploy/).
cd "$(dirname "$0")/.."

if [ ! -f backend/.env ]; then
  echo "❌ Нет backend/.env — скопируйте backend/.env.example в backend/.env и заполните." >&2
  exit 1
fi

# Сеть (создаём, если ещё нет).
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

echo "==> Сборка и запуск backend (API + бот)"
docker build -t insync-backend .
docker rm -f insync-backend >/dev/null 2>&1 || true
docker run -d \
  --name insync-backend \
  --init --restart unless-stopped \
  --env-file backend/.env \
  -v insync-data:/app/data \
  --network "$NET" \
  insync-backend

echo "==> Сборка и запуск web (nginx + фронтенд)"
docker build -t insync-web ./webapp
docker rm -f insync-web >/dev/null 2>&1 || true
docker run -d \
  --name insync-web \
  --restart unless-stopped \
  --network "$NET" \
  -p "${PORT}:80" \
  insync-web

echo "✅ Готово. Веб: http://<IP-сервера>:${PORT}  (бот запущен внутри insync-backend)"
echo "   Логи:  docker logs -f insync-backend"
