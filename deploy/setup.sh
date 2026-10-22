#!/usr/bin/env bash
# Установка InSync на чистый Ubuntu/Debian-сервер, оптимизировано под МАЛО RAM (~0.6 ГБ).
# Фронт собирается в статику и отдаётся Caddy (HTTPS), backend — через systemd (24/7).
# Прокси НЕ нужен (сервер в РФ, Пульс доступен напрямую).
#
# Запуск от root одной командой:
#   curl -fsSL https://raw.githubusercontent.com/yohjidev/InSync/main/deploy/setup.sh | bash
set -euo pipefail

DIR=/opt/insync
REPO=https://github.com/yohjidev/InSync.git
say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Запусти от root (sudo -i)"; exit 1; }

# --- swap (критично при 0.6 ГБ RAM: иначе сборка/pip упадут с OOM) ---
if ! swapon --show 2>/dev/null | grep -q .; then
  say "Создаю swap 2 ГБ (мало оперативки)"
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip git curl ca-certificates gnupg

command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; apt-get install -y nodejs; }

if ! command -v caddy >/dev/null 2>&1; then
  say "Caddy (для HTTPS)"
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y && apt-get install -y caddy
fi

say "Код проекта в $DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only || true; else git clone "$REPO" "$DIR"; fi
cd "$DIR"

say "Настройка backend/.env"
[ -f backend/.env ] || cp backend/.env.example backend/.env
printf 'Вставь BOT_TOKEN (от @BotFather): '; read -r BOT </dev/tty
[ -n "${BOT:-}" ] && sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${BOT}|" backend/.env
if grep -q '^SESSION_KEYS=base64' backend/.env || ! grep -q '^SESSION_KEYS=.\+' backend/.env; then
  KEY="$(python3 -c 'import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())')"
  sed -i "s|^SESSION_KEYS=.*|SESSION_KEYS=${KEY}|" backend/.env
fi
printf 'Домен (направь его на IP этого сервера заранее), напр. innsync.duckdns.org: '; read -r DOMAIN </dev/tty
if printf '%s' "${DOMAIN:-}" | grep -qE '^[0-9.]+$'; then
  echo "❌ Это IP, а не домен. Для HTTPS нужен ДОМЕН, указывающий на этот сервер"
  echo "   (напр. innsync.duckdns.org -> IP сервера). По IP Let's Encrypt сертификат не выдаст."
  exit 1
fi
sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://${DOMAIN}|" backend/.env

say "Python-зависимости backend"
python3 -m venv venv
venv/bin/pip install --upgrade pip >/dev/null
venv/bin/pip install -r backend/requirements.txt

say "Сборка webapp в статику (пара минут, терпим на слабой машине)"
( cd webapp && npm ci && npm run build )
rm -rf /var/www/insync && mkdir -p /var/www/insync && cp -r webapp/dist/* /var/www/insync/

say "Caddy: статика + /api -> backend + HTTPS на ${DOMAIN}"
cat >/etc/caddy/Caddyfile <<EOF
${DOMAIN} {
    handle /api/* {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * /var/www/insync
        try_files {path} /index.html
        file_server
    }
}
EOF
systemctl restart caddy

say "systemd-сервис backend (24/7, автозапуск, рестарт при падении)"
cat >/etc/systemd/system/insync.service <<EOF
[Unit]
Description=InSync backend (FastAPI + Telegram bot)
After=network-online.target
Wants=network-online.target
[Service]
WorkingDirectory=${DIR}
ExecStart=${DIR}/venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now insync

sleep 4
say "Проверка"
curl -s http://127.0.0.1:8000/api/health || echo "(backend ещё поднимается)"
echo
printf '\033[1;32m✅ Готово. Открой https://%s и вставь этот адрес в @BotFather -> Mini App.\033[0m\n' "$DOMAIN"
echo "Логи бота:   journalctl -u insync -f"
echo "Перезапуск:  systemctl restart insync"
