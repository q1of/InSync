# InSync

Автоотметка посещаемости для студентов МИРЭА — за себя и друзей одним сканом QR.
Две версии от одного бэкенда:

- **Mini App** — Telegram Mini App (`miniapp/`);
- **APK** — Android-приложение (`apk/`, Capacitor).

Главный идентификатор — **аккаунт Пульса МИРЭА**: один человек = один аккаунт в обеих
версиях (друзья, история, настройки общие). Telegram — лишь привязка к аккаунту Пульса.

## Возможности

- **Отметка по QR-коду** пары — за себя и друзей одним сканом
- **Вход через Пульс МИРЭА** (логин/пароль + 2FA), хранится только зашифрованная сессия
- **Расписание на неделю** с выбором пар, на которых не хочешь отмечаться
- **Самоотметка только на лекциях** — практику/лабы отмечает преподаватель
- **Друзья** (кого отмечать при скане) и **история отметок**

**Стек:** backend — FastAPI + Telegram-бот (aiogram) в одном процессе; фронтенд —
Preact + Vite; APK — Capacitor (обёртка нативного Android над тем же интерфейсом).

---

## Структура

```
InSync/
├─ backend/                  # общий сервер (FastAPI API + Telegram-бот) — для обеих версий
│  ├─ main.py                #   точка входа: бот (aiogram) + FastAPI, lifespan, lock бота
│  ├─ config.py              #   настройки из backend/.env
│  ├─ database.py            #   SQLite (aiosqlite): схема, миграции, общий аккаунт по логину
│  ├─ models.py              #   Pydantic-схемы запросов/ответов
│  ├─ middleware/
│  │  └─ telegram_auth.py    #   вход: подпись Telegram initData ИЛИ Bearer-токен приложения
│  ├─ routes/
│  │  ├─ auth.py             #   вход/2FA/сессия/расписание (Telegram)
│  │  ├─ app_auth.py         #   вход/2FA для приложения (выдаёт токен)
│  │  ├─ friends.py          #   друзья и заявки
│  │  ├─ attendance.py       #   отметка по QR + история
│  │  ├─ optout.py           #   пары, на которых пользователь не отмечается
│  │  └─ settings.py         #   настройки уведомлений
│  ├─ services/
│  │  ├─ mirea_service.py    #   вход, шифрование сессий, параллельная отметка
│  │  ├─ pulse_auth.py       #   логин на pulse.mirea.ru (SSO + 2FA)
│  │  ├─ mirea_client.py     #   gRPC-Web клиент Пульса (расписание, self-approve)
│  │  ├─ app_token.py        #   подписанный токен авторизации для приложения
│  │  ├─ friend_service.py   #   операции с друзьями в БД
│  │  └─ session_cache.py    #   кеш расшифрованных сессий
│  ├─ tests/test_core.py     #   юнит-тесты чистой логики (без сети)
│  └─ .env.example
│
├─ miniapp/                  # Telegram Mini App — фронтенд (Preact + Vite)
│  ├─ src/
│  │  ├─ env.ts              #   режим (Telegram/приложение), адрес API, токен
│  │  ├─ api/client.ts       #   обёртка над /api
│  │  ├─ pages/              #   Scanner, Lessons, Friends, Profile, Login
│  │  └─ ...
│  └─ package.json
│
├─ apk/                      # Android-приложение (Capacitor) — своя копия фронтенда
│  ├─ src/                   #   тот же интерфейс, что и в miniapp
│  ├─ capacitor.config.ts    #   appId, webDir=dist
│  ├─ android/               #   нативный Android-проект (Gradle)
│  └─ package.json           #   + @capacitor/*; скрипты app:sync / app:open / app:apk
│
├─ deploy/setup.sh           # установка сервера одной командой (Caddy + systemd, раздаёт miniapp)
├─ data/                     # локальные данные (БД, lock) — в .gitignore
└─ .gitignore
```

> Фронтенд у `miniapp` и `apk` — две независимые копии (по запросу). Интерфейс один и тот
> же; код определяет среду в рантайме (`src/env.ts`): в Telegram — вход по initData и API на
> том же домене, в приложении — вход по токену и API по абсолютному адресу бэкенда.

---

## Конфигурация бэкенда

```bash
cp backend/.env.example backend/.env       # Windows: copy backend\.env.example backend\.env
```

| Переменная      | Назначение                                                        |
|-----------------|-------------------------------------------------------------------|
| `BOT_TOKEN`     | Токен бота от [@BotFather](https://t.me/BotFather)                |
| `WEBAPP_URL`    | Публичный адрес Mini App (он же в настройках Mini App у @BotFather)|
| `SESSION_KEYS`  | Ключ шифрования сессий (32 байта в base64)                        |
| `FRIEND_LIMIT`  | Лимит друзей на пользователя (по умолчанию 30)                    |
| `DATABASE_PATH` | Путь к базе SQLite (по умолчанию `./data/insync.db`)             |

Сгенерировать `SESSION_KEYS`:

```bash
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

---

## Запуск сервера (Mini App)

На чистом Ubuntu/Debian — одной командой под root (ставит swap, зависимости, Node, Caddy
с HTTPS, собирает `miniapp` в статику, поднимает backend как systemd-сервис):

```bash
curl -fsSL https://raw.githubusercontent.com/q1of/InSync/main/deploy/setup.sh | bash
```

**Обновление после `git push`:**

```bash
cd /opt/insync && git pull && cd miniapp && npm run build && cd .. \
  && cp -r miniapp/dist/* /var/www/insync/ && systemctl restart insync
```

Логи: `journalctl -u insync -f` · перезапуск: `systemctl restart insync`.

---

## Локальная разработка (Mini App, через ngrok)

```bash
python -m venv venv
venv/bin/pip install -r backend/requirements.txt    # Windows: venv\Scripts\pip ...
(cd miniapp && npm install)
cp backend/.env.example backend/.env                # заполнить BOT_TOKEN, SESSION_KEYS
```

Три процесса (в трёх вкладках): `ngrok http 5173`, `cd miniapp && npm run dev`,
`venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`. Публичный
https-адрес ngrok впишите в `backend/.env` → `WEBAPP_URL` и в **@BotFather → Mini App**.

> Бэкенд работает **в одном экземпляре** (файловая блокировка `data/insync.bot.lock`).
> Не запускайте uvicorn с `--workers`.

---

## Сборка APK (Android)

Нужны: **JDK 17–21** (Gradle 8.11 не поддерживает JDK 25) и **Android SDK** (platform-tools,
`platforms;android-35`, `build-tools;35.0.0`).

```bash
cd apk
npm install
# адрес бэкенда для приложения (по умолчанию зашит в apk/src/env.ts):
#   VITE_API_BASE=https://<ваш-домен>/api  — при необходимости переопределить
npm run app:apk        # сборка фронта + cap sync + gradle assembleDebug (Windows)
```

Готовый файл: `apk/android/app/build/outputs/apk/debug/app-debug.apk`.

Открыть проект в Android Studio: `npm run app:open`.

---

## Тесты

```bash
venv/bin/python -m pytest backend/tests      # Windows: venv\Scripts\python -m pytest backend/tests
```
