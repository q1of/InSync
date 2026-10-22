# InSync

Telegram Mini App для автоотметки студентов МИРЭА.

- **Отметка по QR-коду** пары — за себя и за друзей одним сканом
- **Вход через Пульс МИРЭА** (логин/пароль + 2FA), хранится только зашифрованная сессия
- **Друзья, история отметок, расписание**

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
│  │  └─ attendance.py       #   отметка по QR + история
│  ├─ services/              # бизнес-логика
│  │  ├─ mirea_service.py    #   вход, шифрование сессий, параллельная отметка
│  │  ├─ pulse_auth.py       #   логин на pulse.mirea.ru (SSO + 2FA)
│  │  ├─ mirea_client.py     #   gRPC-Web клиент Пульса (профиль, расписание, self-approve)
│  │  ├─ friend_service.py   #   операции с друзьями в БД
│  │  └─ session_cache.py    #   кеш расшифрованных сессий (с asyncio.Lock)
│  ├─ requirements.txt
│  └─ .env.example           #   шаблон окружения (скопировать в backend/.env)
│
├─ webapp/                   # фронтенд (Preact + Vite)
│  └─ src/
│     ├─ api/client.ts       #   обёртка над /api (шлёт X-Telegram-Init-Data)
│     ├─ hooks/useTelegram.ts#   доступ к Telegram WebApp SDK
│     ├─ pages/              #   Scanner, Friends, History, Profile, Login
│     ├─ components/icons.tsx
│     └─ styles/global.css
│
├─ data/                     # локальные данные (БД, lock) — в .gitignore
└─ .gitignore
```

---

## Конфигурация

Скопируйте шаблон и заполните значения:

```bash
copy backend\.env.example backend\.env
```

| Переменная      | Назначение                                                        |
|-----------------|-------------------------------------------------------------------|
| `BOT_TOKEN`     | Токен бота от [@BotFather](https://t.me/BotFather)                |
| `WEBAPP_URL`    | Публичный адрес webapp (ngrok-URL, он же в настройках Mini App)    |
| `SESSION_KEYS`  | Ключ шифрования сессий (32 байта в base64)                        |
| `FRIEND_LIMIT`  | Лимит друзей на пользователя (по умолчанию 30)                    |
| `DATABASE_PATH` | Путь к базе SQLite                                                |

Сгенерировать `SESSION_KEYS`:

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

---

## Запуск

Разово подготовить (Linux/сервер):

```bash
python3 -m venv venv
venv/bin/pip install -r backend/requirements.txt
(cd webapp && npm install)
cp backend/.env.example backend/.env    # заполнить BOT_TOKEN, SESSION_KEYS
```

На Windows: `venv\Scripts\pip ...`, `copy backend\.env.example backend\.env`.
Нужны установленные **Node.js** и **ngrok** (`ngrok config add-authtoken ...`).

Запустить три процесса (в трёх вкладках/окнах терминала, либо через tmux/screen):

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
