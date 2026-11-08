#!/usr/bin/env bash
# Обновление уже установленного InSync до свежей версии из GitHub:
#   bash /opt/insync/deploy/update.sh
# Делает только нужное: пакеты фронта ставит заново, лишь если они поменялись (npm ci
# съедает почти всю память слабого сервера и выдавливает бота в подкачку), фронт
# пересобирает при изменениях в webapp/, бота перезапускает при изменениях в backend/.
set -euo pipefail

DIR=/opt/insync
STATE="$DIR/.deployed"   # коммит, который сейчас стоит на сервере
say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

cd "$DIR"
git pull --ff-only
new="$(git rev-parse HEAD)"
old="$(cat "$STATE" 2>/dev/null || true)"
echo "Версия: $(git log --oneline -1)"

# Менялись ли пути с прошлого деплоя (без записи о нём — считаем, что менялось всё).
changed(){ [ -z "$old" ] || ! git diff --quiet "$old" "$new" -- "$@" 2>/dev/null; }

if changed backend/requirements.txt; then
  say "Python-зависимости"
  venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -d webapp/node_modules ] || changed webapp/package.json webapp/package-lock.json; then
  say "Пакеты фронтенда"
  (cd webapp && npm ci)
fi

if changed webapp || [ ! -f /var/www/insync/index.html ]; then
  say "Сборка фронтенда"
  (cd webapp && npm run build)
  rm -rf /var/www/insync/* && cp -r webapp/dist/* /var/www/insync/
fi

if changed backend; then
  say "Перезапуск бота"
  systemctl restart insync
  sleep 2
fi

echo "$new" > "$STATE"
printf '\n\033[1;32m✅ Готово: %s, бот %s\033[0m\n' "$(git log --oneline -1)" "$(systemctl is-active insync)"
