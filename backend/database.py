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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                telegram_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL,
                username TEXT,          -- Telegram @username (для добавления в друзья)
                mirea_login TEXT,
                group_name TEXT,        -- parsed on login
                full_name TEXT,         -- parsed on login  
                encrypted_session TEXT,
                session_valid BOOLEAN DEFAULT 0,
                session_updated_at INTEGER,
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

        async with db.execute("PRAGMA table_info(attendance_log)") as cur:
            acols = [r["name"] for r in await cur.fetchall()]
        if "scan_id" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN scan_id TEXT")

        # Индексы для быстрых выборок (списки друзей, история отметок)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_user ON friendships(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attlog_initiator ON attendance_log(initiator, scan_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_optout_user ON lesson_optout(telegram_id, start_epoch)")

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
    full_name: Optional[str] = None,
    username: Optional[str] = None
) -> None:
    """Создает нового пользователя или обновляет существующего."""
    async with get_db() as db:
        await db.execute("""
            INSERT INTO users (telegram_id, display_name, username, mirea_login, group_name, full_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                display_name = excluded.display_name,
                username = coalesce(excluded.username, users.username),
                mirea_login = coalesce(excluded.mirea_login, users.mirea_login),
                group_name = coalesce(excluded.group_name, users.group_name),
                full_name = coalesce(excluded.full_name, users.full_name)
        """, (telegram_id, display_name, username, mirea_login, group_name, full_name))
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


async def get_friends_sessions(telegram_id: int) -> List[aiosqlite.Row]:
    """Возвращает зашифрованные сессии всех принятых друзей с валидными сессиями."""
    async with get_db() as db:
        async with db.execute("""
            SELECT u.* FROM users u
            JOIN friendships f ON u.telegram_id = f.friend_id
            WHERE f.user_id = ? AND f.status = 'accepted' AND u.session_valid = 1
        """, (telegram_id,)) as cursor:
            return await cursor.fetchall()
