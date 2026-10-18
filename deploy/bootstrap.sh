#!/usr/bin/env bash
# Полный автоматический деплой InSync на чистый Ubuntu/Debian VPS.
# Запускать от root в веб-консоли провайдера (или по SSH) одной строкой:
#
#   curl -fsSL https://raw.githubusercontent.com/yohjidev/InSync/main/deploy/bootstrap.sh | bash
#
# Скрипт: ставит Docker + Caddy, клонирует репозиторий, заполняет .env
# (спросит BOT_TOKEN), поднимает backend + web в Docker с автоперезапуском
# и настраивает HTTPS на https://innsync.duckdns.org.
set -euo pipefail

DOMAIN="innsync.duckdns.org"
DUCK_SUBDOMAIN="innsync"
REPO="https://github.com/yohjidev/InSync.git"
DIR="/opt/insync"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти от root (sudo -i)." >&2; exit 1
fi

say "Обновление пакетов"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git ufw gnupg

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

say "Caddy (для HTTPS)"
if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y
  apt-get install -y caddy
fi

say "Клонирование репозитория в $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only || true
else
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

say "Настройка backend/.env"
[ -f backend/.env ] || cp backend/.env.example backend/.env

# BOT_TOKEN — читаем с терминала (/dev/tty), чтобы работало даже через curl | bash.
printf 'Вставь BOT_TOKEN от @BotFather и нажми Enter: '
read -r BOT </dev/tty
if [ -n "${BOT:-}" ]; then
  sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${BOT}|" backend/.env
fi

# SESSION_KEYS — генерируем, если ещё дефолтный.
if grep -q '^SESSION_KEYS=base64_encoded_32_bytes' backend/.env || ! grep -q '^SESSION_KEYS=' backend/.env; then
  KEY="$(openssl rand -base64 32)"
  sed -i "s|^SESSION_KEYS=.*|SESSION_KEYS=${KEY}|" backend/.env
fi

sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://${DOMAIN}|" backend/.env

# DuckDNS: направить домен на этот сервер (опционально).
printf 'Токен DuckDNS (Enter чтобы пропустить и указать IP вручную на сайте): '
read -r DUCK </dev/tty
SERVER_IP="$(curl -s https://api.ipify.org || true)"
if [ -n "${DUCK:-}" ] && [ -n "${SERVER_IP:-}" ]; then
  curl -s "https://www.duckdns.org/update?domains=${DUCK_SUBDOMAIN}&token=${DUCK}&ip=${SERVER_IP}" \
    && echo " -> DuckDNS указывает на ${SERVER_IP}"
fi

say "Сборка и запуск контейнеров"
docker network inspect insync-net >/dev/null 2>&1 || docker network create insync-net

docker build -t insync-backend .
docker rm -f insync-backend >/dev/null 2>&1 || true
docker run -d --name insync-backend --init --restart unless-stopped \
  --env-file backend/.env -v insync-data:/app/data --network insync-net insync-backend

docker build -t insync-web ./webapp
docker rm -f insync-web >/dev/null 2>&1 || true
docker run -d --name insync-web --restart unless-stopped \
  --network insync-net -p 127.0.0.1:8080:80 insync-web

say "Caddy: HTTPS на ${DOMAIN}"
cat >/etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    reverse_proxy 127.0.0.1:8080
}
EOF
systemctl restart caddy

say "Firewall (открываем 22/80/443)"
ufw allow 22/tcp  >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
yes | ufw enable  >/dev/null 2>&1 || true

say "Проверка"
sleep 5
docker ps --format 'table {{.Names}}\t{{.Status}}'
echo
echo "Локальный health:"; curl -s http://127.0.0.1:8080/api/health || echo " (ещё поднимается)"
echo
printf '\033[1;32m✅ Готово. Открой: https://%s\033[0m\n' "$DOMAIN"
echo "Если сертификат ещё выпускается — подожди 1–2 минуты. Логи бота: docker logs -f insync-backend"
echo "Не забудь прописать https://${DOMAIN} в настройках Mini App у @BotFather."
