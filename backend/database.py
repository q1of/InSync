import os
import aiosqlite
from contextlib import asynccontextmanager
from typing import Optional, List

from .config import settings

@asynccontextmanager
async def get_db():
    """
    Асинхронный контекстный менеджер для получения подключения к базе данных SQLite.
    Использует aiosqlite.Row в качестве row_factory для доступа к столбцам по имени.
    """
    conn = await aiosqlite.connect(settings.database_path)
    conn.row_factory = aiosqlite.Row
    # Ждать до 5с на заблокированной БД вместо мгновенной ошибки "database is locked".
    await conn.execute("PRAGMA busy_timeout=5000")
    # С WAL безопасно и заметно быстрее на записи (теряем максимум последний коммит при аварии).
    await conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        await conn.close()

async def init_db():
    """
    Создает необходимые директории и инициализирует таблицы в базе данных.
    """
    # Убедимся, что директория для БД существует
    db_dir = os.path.dirname(settings.database_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
        
    async with get_db() as db:
        # WAL: чтения не блокируются записями — меньше «database is locked» под нагрузкой.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                telegram_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                username TEXT,          -- Telegram @username (для добавления в друзья)
                mirea_login TEXT,
                group_name TEXT,        -- parsed on login
                encrypted_session TEXT,
                session_valid BOOLEAN DEFAULT 0,
                session_updated_at INTEGER,
                notif_marked INTEGER DEFAULT 1,   -- уведомлять об отметках на парах
                notif_login INTEGER DEFAULT 1,    -- уведомлять о входе в аккаунт
                notif_session INTEGER DEFAULT 1,  -- уведомлять о слёте сессии
                created_at INTEGER DEFAULT (unixepoch())
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS friendships(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(telegram_id),
                friend_id INTEGER NOT NULL REFERENCES users(telegram_id),
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending','accepted')),
                marking_enabled INTEGER DEFAULT 1,   -- отмечать ли этого друга при скане
                created_at INTEGER DEFAULT (unixepoch()),
                UNIQUE(user_id, friend_id)
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                initiator INTEGER NOT NULL REFERENCES users(telegram_id),
                target_user INTEGER NOT NULL REFERENCES users(telegram_id),
                qr_token TEXT,
                success BOOLEAN,
                error_msg TEXT,
                scan_id TEXT,            -- группирует все волны одной отметки (QR меняется каждые 5с)
                lesson_name TEXT,        -- на какой паре (по расписанию во время скана)
                created_at INTEGER DEFAULT (unixepoch())
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lesson_optout(
                telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
                start_epoch INTEGER NOT NULL,   -- начало пары = идентификатор занятия
                end_epoch INTEGER,              -- конец пары (окно «сейчас идёт»)
                lesson_name TEXT,
                created_at INTEGER DEFAULT (unixepoch()),
                PRIMARY KEY (telegram_id, start_epoch)
            );
        """)

        # Миграция старых БД: добавить marking_enabled, если его нет
        async with db.execute("PRAGMA table_info(friendships)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if "marking_enabled" not in cols:
            await db.execute("ALTER TABLE friendships ADD COLUMN marking_enabled INTEGER DEFAULT 1")

        async with db.execute("PRAGMA table_info(users)") as cur:
            ucols = [r["name"] for r in await cur.fetchall()]
        if "username" not in ucols:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        for col in ("notif_marked", "notif_login", "notif_session"):
            if col not in ucols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 1")

        async with db.execute("PRAGMA table_info(attendance_log)") as cur:
            acols = [r["name"] for r in await cur.fetchall()]
        if "scan_id" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN scan_id TEXT")
        if "lesson_name" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN lesson_name TEXT")

        # Индексы для быстрых выборок (списки друзей, история отметок)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_user ON friendships(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attlog_initiator ON attendance_log(initiator, scan_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_optout_user ON lesson_optout(telegram_id, start_epoch)")
        # Поиск аккаунта по логину Пульса — основа общего аккаунта для TG и Android.
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_mirea_login ON users(mirea_login)")

        await db.commit()

async def get_user(telegram_id: int) -> Optional[aiosqlite.Row]:
    """Получает данные пользователя по telegram_id."""
    async with get_db() as db:
        async with db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)) as cursor:
            return await cursor.fetchone()

async def upsert_user(
    telegram_id: int, 
    display_name: str, 
    mirea_login: Optional[str] = None,
    group_name: Optional[str] = None,
    username: Optional[str] = None
) -> None:
    """Создает нового пользователя или обновляет существующего."""
    async with get_db() as db:
        await db.execute("""
            INSERT INTO users (telegram_id, display_name, username, mirea_login, group_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                display_name = excluded.display_name,
                username = coalesce(excluded.username, users.username),
                mirea_login = coalesce(excluded.mirea_login, users.mirea_login),
                group_name = coalesce(excluded.group_name, users.group_name)
        """, (telegram_id, display_name, username, mirea_login, group_name))
        await db.commit()

async def update_session(telegram_id: int, encrypted_session: str) -> None:
    """Обновляет зашифрованную сессию пользователя."""
    async with get_db() as db:
        await db.execute("""
            UPDATE users 
            SET encrypted_session = ?, 
                session_valid = 1, 
                session_updated_at = unixepoch()
            WHERE telegram_id = ?
        """, (encrypted_session, telegram_id))
        await db.commit()

async def clear_session(telegram_id: int) -> None:
    """Сбрасывает сессию пользователя при выходе (logout)."""
    async with get_db() as db:
        await db.execute("""
            UPDATE users
            SET encrypted_session = NULL,
                session_valid = 0,
                session_updated_at = unixepoch()
            WHERE telegram_id = ?
        """, (telegram_id,))
        await db.commit()

LESSON_DEFAULT_LEN = 5400   # ~1.5ч — если Пульс не прислал конец пары


async def get_optouts(telegram_id: int) -> List[int]:
    """start_epoch всех пар, на которых пользователь отключил отметку."""
    async with get_db() as db:
        async with db.execute(
            "SELECT start_epoch FROM lesson_optout WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            return [r["start_epoch"] for r in await cur.fetchall()]


async def set_optout(
    telegram_id: int, start_epoch: int, end_epoch: Optional[int],
    lesson_name: Optional[str], enabled: bool,
) -> None:
    """Включает/снимает отключение отметки на конкретном занятии."""
    if not end_epoch or end_epoch <= start_epoch:
        end_epoch = start_epoch + LESSON_DEFAULT_LEN
    async with get_db() as db:
        if enabled:
            await db.execute(
                """INSERT INTO lesson_optout (telegram_id, start_epoch, end_epoch, lesson_name)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(telegram_id, start_epoch) DO UPDATE SET
                       end_epoch = excluded.end_epoch, lesson_name = excluded.lesson_name""",
                (telegram_id, start_epoch, end_epoch, lesson_name),
            )
        else:
            await db.execute(
                "DELETE FROM lesson_optout WHERE telegram_id = ? AND start_epoch = ?",
                (telegram_id, start_epoch),
            )
        await db.commit()


async def opted_out_now(telegram_ids: List[int], now: int, grace: int = 1800) -> dict:
    """Для каждого id: название отключённой пары, если она идёт прямо сейчас (±grace).

    Окно пары сохраняется при отключении, поэтому расписание на скане дёргать не нужно.
    """
    if not telegram_ids:
        return {}
    ph = ",".join("?" * len(telegram_ids))
    async with get_db() as db:
        async with db.execute(
            f"""SELECT telegram_id, lesson_name FROM lesson_optout
                WHERE telegram_id IN ({ph})
                  AND start_epoch - ? <= ? AND end_epoch + ? >= ?""",
            (*telegram_ids, grace, now, grace, now),
        ) as cur:
            return {r["telegram_id"]: (r["lesson_name"] or "эту пару") for r in await cur.fetchall()}


_NOTIF_COLS = {"marked": "notif_marked", "login": "notif_login", "session": "notif_session"}


async def get_notif_prefs(telegram_id: int) -> dict:
    """Настройки уведомлений пользователя (по умолчанию всё включено)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT notif_marked, notif_login, notif_session FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return {"marked": True, "login": True, "session": True}
    return {"marked": bool(row["notif_marked"]), "login": bool(row["notif_login"]),
            "session": bool(row["notif_session"])}


async def set_notif_pref(telegram_id: int, key: str, enabled: bool) -> None:
    col = _NOTIF_COLS.get(key)
    if not col:
        return
    async with get_db() as db:
        await db.execute(f"UPDATE users SET {col} = ? WHERE telegram_id = ?",
                         (1 if enabled else 0, telegram_id))
        await db.commit()


async def notif_enabled(telegram_id: int, key: str) -> bool:
    """True, если пользователь хочет получать уведомления этого типа."""
    return (await get_notif_prefs(telegram_id)).get(key, True)


async def get_active_session_users() -> List[aiosqlite.Row]:
    """Пользователи, у кого сейчас считается живая сессия — для утренней проверки."""
    async with get_db() as db:
        async with db.execute(
            "SELECT telegram_id, encrypted_session FROM users "
            "WHERE session_valid = 1 AND encrypted_session IS NOT NULL"
        ) as cur:
            return await cur.fetchall()


async def set_session_invalid(telegram_id: int) -> None:
    """Помечает сессию как невалидную (не трогая сам токен) — чтобы друга больше
    не дёргали на сканах, пока он не перезайдёт. Токен оставляем на случай, что
    он восстановится по KEYCLOAK_IDENTITY при следующем явном входе."""
    async with get_db() as db:
        await db.execute("UPDATE users SET session_valid = 0 WHERE telegram_id = ?", (telegram_id,))
        await db.commit()


# ───────────────────────── Общий аккаунт (TG + Android) ─────────────────────────
#
# Идентификатор аккаунта — логин Пульса МИРЭА. В одной записи users:
#   telegram_id > 0  — настоящий Telegram id (есть привязка к боту);
#   telegram_id < 0  — синтетический id аккаунта, заведённого только в приложении
#                      (Telegram ещё не привязан). Так Android-пользователь без
#                      Telegram тоже помещается в ту же таблицу и те же связи.
# При входе в Пульс (из бота ИЛИ из приложения) аккаунт ищется по mirea_login,
# поэтому один человек = один аккаунт в обеих версиях.


async def get_user_by_login(mirea_login: str) -> Optional[aiosqlite.Row]:
    """Аккаунт по логину Пульса (основной идентификатор). None — если не найден."""
    if not mirea_login:
        return None
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM users WHERE mirea_login = ? ORDER BY telegram_id DESC LIMIT 1",
            (mirea_login,),
        ) as cur:
            return await cur.fetchone()


async def _alloc_app_account_id(db) -> int:
    """Свободный отрицательный id для аккаунта без Telegram (чтобы не пересекался
    с настоящими Telegram id, которые положительны)."""
    async with db.execute("SELECT MIN(telegram_id) AS m FROM users") as cur:
        row = await cur.fetchone()
    m = row["m"] if row else None
    return (m - 1) if (m is not None and m < 0) else -1


async def get_or_create_app_account(
    mirea_login: str, display_name: Optional[str] = None, group_name: Optional[str] = None
) -> int:
    """id аккаунта для входа из приложения. Если человек уже входил через бота —
    вернётся его существующий аккаунт (общий), иначе заведётся новый (отрицательный id)."""
    async with get_db() as db:
        async with db.execute(
            "SELECT telegram_id FROM users WHERE mirea_login = ? ORDER BY telegram_id DESC LIMIT 1",
            (mirea_login,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            if group_name:
                await db.execute(
                    "UPDATE users SET group_name = ? WHERE telegram_id = ?",
                    (group_name, row["telegram_id"]),
                )
                await db.commit()
            return row["telegram_id"]

        new_id = await _alloc_app_account_id(db)
        await db.execute(
            "INSERT INTO users (telegram_id, display_name, mirea_login, group_name) "
            "VALUES (?, ?, ?, ?)",
            (new_id, display_name or mirea_login, mirea_login, group_name),
        )
        await db.commit()
        return new_id


async def merge_app_account_into_telegram(mirea_login: str, telegram_id: int) -> None:
    """Человек сначала завёл аккаунт в приложении (отрицательный id), потом вошёл
    через бота этим же логином Пульса — переносим его друзей/историю/пары на
    настоящий Telegram id и удаляем временную запись. Общий аккаунт сохраняется."""
    if not mirea_login or telegram_id < 0:
        return
    async with get_db() as db:
        async with db.execute(
            "SELECT telegram_id FROM users WHERE mirea_login = ? AND telegram_id < 0",
            (mirea_login,),
        ) as cur:
            rows = await cur.fetchall()
        src_ids = [r["telegram_id"] for r in rows if r["telegram_id"] != telegram_id]
        if not src_ids:
            return
        # Цель должна существовать до переноса связей.
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, display_name, mirea_login) VALUES (?, ?, ?)",
            (telegram_id, mirea_login, mirea_login),
        )
        for src in src_ids:
            # OR IGNORE гасит конфликты уникальности (общий друг / одна и та же пара),
            # остатки по src затем удаляем.
            await db.execute("UPDATE OR IGNORE friendships SET user_id = ? WHERE user_id = ?", (telegram_id, src))
            await db.execute("UPDATE OR IGNORE friendships SET friend_id = ? WHERE friend_id = ?", (telegram_id, src))
            await db.execute("DELETE FROM friendships WHERE user_id = ? OR friend_id = ?", (src, src))
            await db.execute("UPDATE attendance_log SET initiator = ? WHERE initiator = ?", (telegram_id, src))
            await db.execute("UPDATE attendance_log SET target_user = ? WHERE target_user = ?", (telegram_id, src))
            await db.execute("UPDATE OR IGNORE lesson_optout SET telegram_id = ? WHERE telegram_id = ?", (telegram_id, src))
            await db.execute("DELETE FROM lesson_optout WHERE telegram_id = ?", (src,))
            await db.execute("DELETE FROM users WHERE telegram_id = ?", (src,))
        await db.commit()


