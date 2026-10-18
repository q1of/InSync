# InSync

Telegram Mini App для автоотметки студентов МИРЭА.

- **Отметка по QR-коду** пары — за себя и за друзей одним сканом
- **Вход через Пульс МИРЭА** (логин/пароль + 2FA), хранится только зашифрованная сессия
- **Друзья, история отметок, расписание**

**Стек:** backend — FastAPI + Telegram-бот (aiogram), один процесс; webapp — Preact + Vite.

---

## Конфигурация

Скопируйте пример окружения и заполните значения:

```bash
cp backend/.env.example backend/.env
```

| Переменная      | Назначение                                                        |
|-----------------|-------------------------------------------------------------------|
| `BOT_TOKEN`     | Токен Telegram-бота (получить у [@BotFather](https://t.me/BotFather)) |
| `WEBAPP_URL`    | Публичный адрес webapp (ngrok или домен), он же в настройках Mini App |
| `SESSION_KEYS`  | Ключ шифрования сессий (32 байта в base64)                        |
| `MIREA_PROXY`   | Прокси для `pulse.mirea.ru` (опционально; из дата-центров Пульс часто закрыт) |
| `FRIEND_LIMIT`  | Лимит друзей на пользователя (по умолчанию 30)                    |
| `DATABASE_PATH` | Путь к базе SQLite                                                |

Сгенерировать `SESSION_KEYS`:

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

---

## Запуск через Docker

Образ — это **бэкенд (API + Telegram-бот)**, основной сервис проекта.

### 1. Собрать образ

```bash
docker build -t insync-backend .
```

### 2. Запустить (выходной порт 8888, автоподъём при падении)

```bash
docker run -d \
  --name insync-backend \          # имя контейнера
  --init \                         # корректная обработка сигналов и уборка процессов
  --restart unless-stopped \       # поднимать снова при падении/перезагрузке (кроме ручного docker stop)
  --env-file backend/.env \        # секреты не зашиты в образ
  -v insync-data:/app/data \       # база SQLite переживает пересборку образа
  -p 8888:8000 \                   # выходной порт 8888 (внутри контейнера uvicorn слушает 8000)
  insync-backend
```

После запуска:

- Приложение (API + бот) — `http://localhost:8888`
- Проверка живости — `http://localhost:8888/api/health`

> `--restart unless-stopped` поднимает контейнер после краша и после перезагрузки хоста,
> но не трогает его, если вы сами остановили через `docker stop`.
> Нужен подъём даже после ручной остановки — используйте `--restart always`.

### Фичи образа

- Запуск под **непривилегированным пользователем** (безопасность)
- **tzdata + `TZ=Europe/Moscow`** — московское время в логах
- **HEALTHCHECK** через `curl` → `docker ps` показывает `healthy` / `unhealthy`
- **OCI-labels** (title, description, source, license)
- **VOLUME** для данных и кеширование слоя зависимостей при сборке

### Полезные команды

```bash
docker logs -f insync-backend     # смотреть логи
docker stop insync-backend        # остановить
docker start insync-backend       # запустить снова
```

Обновить образ после изменений в коде:

```bash
docker rm -f insync-backend
docker build -t insync-backend .
# затем снова команда docker run из шага 2
```

> ⚠️ Бот читает апдейты по long polling. Не запускайте **несколько** контейнеров
> с одним и тем же `BOT_TOKEN` — Telegram не отдаёт апдейты двум процессам сразу.

---

## Полный стек в Docker (backend + webapp)

Фронтенд статический. Чтобы поднять его рядом с бэкендом в своей docker-сети
(`webapp/Dockerfile` собирает SPA и раздаёт через nginx, проксируя `/api` на `insync-backend`):

```bash
# общая сеть, чтобы web видел backend по имени
docker network create insync-net

# backend в этой сети
docker run -d \
  --name insync-backend \
  --init --restart unless-stopped \
  --env-file backend/.env \
  -v insync-data:/app/data \
  --network insync-net \
  -p 8888:8000 \
  insync-backend

# web (nginx + собранный фронтенд) на порту 80
docker build -t insync-web ./webapp
docker run -d \
  --name insync-web \
  --restart unless-stopped \
  --network insync-net \
  -p 80:80 \
  insync-web
```

Веб будет на `http://localhost`, запросы `/api` пойдут на `insync-backend:8000`.

---

## Бесплатный хостинг 24/7 (Oracle Cloud Always Free)

Telegram-бот работает по **long polling** — нужен постоянно живой процесс.
Поэтому serverless и «засыпающие» бесплатные тарифы не подходят:

| Платформа               | Почему не годится для бота                          |
|-------------------------|-----------------------------------------------------|
| Cloudflare Workers/Pages| serverless, нет постоянного процесса и Python-рантайма |
| Google Cloud Run        | масштабируется в ноль — polling прерывается          |
| Render / Railway (free) | засыпают при простое — бот отваливается              |
| **Oracle Cloud Always Free** | **настоящая всегда-бесплатная VM, Docker, без засыпания** ✅ |

### Шаги

1. **Создайте Always Free VM** в [Oracle Cloud](https://www.oracle.com/cloud/free/):
   Compute → Instance, образ **Ubuntu 22.04**, shape **VM.Standard.A1.Flex** (Ampere, Always Free).
2. **Откройте порт** приложения: в Security List подсети разрешите входящий TCP на `8888`
   (и на самой VM: `sudo iptables -I INPUT -p tcp --dport 8888 -j ACCEPT` или через `ufw`).
3. **Установите Docker и git**, склонируйте проект:
   ```bash
   sudo apt update && sudo apt install -y docker.io git
   sudo usermod -aG docker "$USER" && newgrp docker
   git clone https://github.com/yohjidev/InSync.git && cd InSync
   ```
4. **Заполните окружение** и запустите деплой (backend + web, автоперезапуск):
   ```bash
   cp backend/.env.example backend/.env
   nano backend/.env            # BOT_TOKEN, WEBAPP_URL, SESSION_KEYS (+ MIREA_PROXY при необходимости)
   bash deploy/deploy.sh        # поднимет оба контейнера; публичный порт 8888
   ```
5. **Пропишите адрес** `http://<IP-VM>:8888` (или свой домен с HTTPS) в `WEBAPP_URL`
   и в настройках Mini App у [@BotFather](https://t.me/BotFather).

> `deploy/deploy.sh` собирает и запускает `insync-backend` и `insync-web` в общей
> docker-сети `insync-net` с `--restart unless-stopped` — после краша/перезагрузки
> контейнеры поднимаются сами. Сменить публичный порт: `PORT=80 bash deploy/deploy.sh`.

> ⚠️ Telegram Mini App требует **HTTPS**. Для боевого домена поставьте перед `:8888`
> reverse-proxy с сертификатом (Caddy/nginx + Let's Encrypt) или Cloudflare Tunnel.

> ⚠️ С зарубежной VM `pulse.mirea.ru` часто недоступен — укажите российский
> `MIREA_PROXY` в `backend/.env` (socks5/http). Проект это поддерживает из коробки.

---

## Локальная разработка (без Docker)

**Backend** (Python 3.13, из корня проекта):

```bash
python -m venv venv
source venv/Scripts/activate          # Windows; на Linux/macOS: source venv/bin/activate
pip install -r backend/requirements.txt
python -m backend.main                 # uvicorn на 0.0.0.0:8000 с автоперезагрузкой
```

**Webapp** (Node 18+):

```bash
cd webapp
npm install
npm run dev                            # Vite dev-сервер, /api проксируется на 127.0.0.1:8000
```

Для доступа Telegram к локальному webapp используйте туннель (например, ngrok)
и укажите его адрес в `WEBAPP_URL` и в настройках Mini App у @BotFather.

---

## Структура

```
backend/            FastAPI-приложение и Telegram-бот
  main.py           точка входа: бот (aiogram) + FastAPI в одном процессе
  config.py         настройки из окружения
  database.py       SQLite (aiosqlite)
  models.py         Pydantic-схемы
  routes/           auth, friends, attendance
  services/         логика МИРЭА (вход, Пульс-клиент, кеш сессий)
  middleware/       проверка Telegram initData
webapp/             фронтенд (Preact + Vite)
  src/              страницы, api-клиент, хуки, стили
  Dockerfile        сборка SPA + раздача через nginx
  nginx.conf        конфиг nginx для web-контейнера
deploy/             примеры nginx-сайта и systemd-сервиса
Dockerfile          образ бэкенда
```
