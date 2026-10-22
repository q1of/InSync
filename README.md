# InSync

Telegram Mini App для автоотметки студентов МИРЭА.

- **Отметка по QR-коду** пары — за себя и за друзей одним сканом
- **Вход через Пульс МИРЭА** (логин/пароль + 2FA), хранится только зашифрованная сессия
- **Расписание на неделю** с выбором пар, на которых не хочешь отмечаться
- **Самоотметка только на лекциях** — практику/лабы отмечает преподаватель
- **Друзья** (кого отмечать при скане) и **история отметок**

**Стек:** backend — FastAPI + Telegram-бот (aiogram) в одном процессе; webapp — Preact + Vite.

---

## Структура

```
InSync/
├─ backend/                  # FastAPI API + Telegram-бот (один процесс)
│  ├─ main.py                #   точка входа: бот (aiogram) + FastAPI, lifespan, lock бота
│  ├─ config.py              #   настройки из backend/.env
│  ├─ database.py            #   SQLite (aiosqlite): схема, миграции, запросы
│  ├─ models.py              #   Pydantic-схемы запросов/ответов
│  ├─ middleware/
│  │  └─ telegram_auth.py    #   проверка подписи Telegram initData
│  ├─ routes/                # HTTP-роуты
│  │  ├─ auth.py             #   вход/2FA/сессия/расписание
│  │  ├─ friends.py          #   друзья и заявки
│  │  ├─ attendance.py       #   отметка по QR + история
│  │  └─ optout.py           #   пары, на которых пользователь не отмечается
│  ├─ services/              # бизнес-логика
│  │  ├─ mirea_service.py    #   вход, шифрование сессий, параллельная отметка
│  │  ├─ pulse_auth.py       #   логин на pulse.mirea.ru (SSO + 2FA)
│  │  ├─ mirea_client.py     #   gRPC-Web клиент Пульса (расписание, self-approve)
│  │  ├─ friend_service.py   #   операции с друзьями в БД
│  │  └─ session_cache.py    #   кеш расшифрованных сессий (с asyncio.Lock)
│  ├─ tests/test_core.py     #   юнит-тесты чистой логики (без сети)
│  ├─ requirements.txt
│  └─ .env.example           #   шаблон окружения (скопировать в backend/.env)
│
├─ webapp/                   # фронтенд (Preact + Vite)
│  └─ src/
│     ├─ api/client.ts       #   обёртка над /api (шлёт X-Telegram-Init-Data)
│     ├─ hooks/useTelegram.ts#   доступ к Telegram WebApp SDK
│     ├─ pages/              #   Scanner, Lessons, Friends, Profile, Login
│     │                      #     (History встроена во вкладку Lessons)
│     ├─ components/icons.tsx
│     └─ styles/global.css
│
├─ deploy/setup.sh           # установка на сервер одной командой (Caddy + systemd)
├─ data/                     # локальные данные (БД, lock) — в .gitignore
└─ .gitignore
```

Вкладки приложения: **Сканер · Пары · Друзья · Профиль**. Во вкладке «Пары» —
расписание (дни-аккордеоны) с переключателем «Расписание / История»; лекции можно
отключить от самоотметки, практику/лабы отмечает преподаватель.

> ФИО из профиля Пульса не парсится (ненадёжно) — везде показывается имя из Telegram.

---

## Конфигурация

Скопируйте шаблон и заполните значения:

```bash
cp backend/.env.example backend/.env       # Windows: copy backend\.env.example backend\.env
```

| Переменная      | Назначение                                                        |
|-----------------|-------------------------------------------------------------------|
| `BOT_TOKEN`     | Токен бота от [@BotFather](https://t.me/BotFather)                |
| `WEBAPP_URL`    | Публичный адрес webapp (он же в настройках Mini App у @BotFather)  |
| `SESSION_KEYS`  | Ключ шифрования сессий (32 байта в base64)                        |
| `FRIEND_LIMIT`  | Лимит друзей на пользователя (по умолчанию 30)                    |
| `DATABASE_PATH` | Путь к базе SQLite (по умолчанию `./data/insync.db`)             |

Сгенерировать `SESSION_KEYS`:

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

---

## Запуск на сервере (рекомендуется)

На чистом Ubuntu/Debian — одной командой под root (ставит swap, зависимости,
Node, Caddy с HTTPS, собирает фронт в статику, поднимает backend как systemd-сервис):

```bash
curl -fsSL https://raw.githubusercontent.com/yohjidev/InSync/main/deploy/setup.sh | bash
```

Скрипт спросит `BOT_TOKEN` и домен (должен заранее указывать на IP сервера — по IP
Let's Encrypt сертификат не выдаст). После установки открой `https://<домен>` и впиши
этот адрес в **@BotFather → Mini App**.

**Обновление после `git push`:**

```bash
cd /opt/insync && git pull && cd webapp && npm run build && cd .. \
  && cp -r webapp/dist/* /var/www/insync/ && systemctl restart insync
```

Логи: `journalctl -u insync -f` · перезапуск: `systemctl restart insync`.

---

## Локальный запуск (разработка, через ngrok)

```bash
python -m venv venv
venv/bin/pip install -r backend/requirements.txt    # Windows: venv\Scripts\pip ...
(cd webapp && npm install)
cp backend/.env.example backend/.env                # заполнить BOT_TOKEN, SESSION_KEYS
```

Нужны установленные **Node.js** и **ngrok** (`ngrok config add-authtoken ...`).
Запустить три процесса (в трёх вкладках терминала):

```bash
# 1) туннель — первым, чтобы узнать публичный адрес
ngrok http 5173

# 2) webapp (Vite dev) на :5173
cd webapp && npm run dev

# 3) backend (API + бот) на :8000
venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Публичный https-адрес ngrok (строка `Forwarding`) впишите в `backend/.env` → `WEBAPP_URL`
и в **@BotFather → Mini App**. `WEBAPP_URL` читается при старте бэкенда, поэтому задайте его
**до** запуска процесса №3.

> Бэкенд запускается **в одном экземпляре**: второй процесс не стартует polling бота
> (файловая блокировка `data/insync.bot.lock`), чтобы не ловить `Telegram Conflict`.
> Не запускайте uvicorn с `--workers` — приложение рассчитано на один процесс.

---

## Тесты

```bash
venv/bin/python -m pytest backend/tests      # Windows: venv\Scripts\python -m pytest backend/tests
```
