import os
import secrets
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
                lesson_id TEXT,          -- id пары из ответа Пульса
                lesson_start INTEGER,    -- начало пары по расписанию (если Пульс не прислал id)
                already_marked INTEGER DEFAULT 0,  -- уже был отмечен на этой паре до этого скана
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

        # Группы: создаёт любой, остальные вступают по коду. Человек — максимум в одной группе.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS study_groups(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,      -- код приглашения (и для ссылки в бота)
                created_by INTEGER REFERENCES users(telegram_id),
                created_at INTEGER DEFAULT (unixepoch())
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS group_members(
                telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                group_id INTEGER NOT NULL REFERENCES study_groups(id),
                marking INTEGER DEFAULT 1,      -- отмечаться с группой (в обе стороны)
                joined_at INTEGER DEFAULT (unixepoch())
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
        if "lesson_id" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN lesson_id TEXT")
        if "lesson_start" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN lesson_start INTEGER")
        if "already_marked" not in acols:
            await db.execute("ALTER TABLE attendance_log ADD COLUMN already_marked INTEGER DEFAULT 0")

        # Индексы для быстрых выборок (списки друзей, история отметок)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_user ON friendships(user_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attlog_initiator ON attendance_log(initiator, scan_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attlog_target ON attendance_log(target_user, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_optout_user ON lesson_optout(telegram_id, start_epoch)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_group_members ON group_members(group_id)")

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


async def opted_out_now(telegram_ids: List[int], now: int, grace: int = 300) -> dict:
    """Для каждого id: название отключённой пары, если она идёт прямо сейчас (±grace).

    Окно пары сохраняется при отключении, поэтому расписание на скане дёргать не нужно.
    Запас — 5 минут: перерыв между парами в МИРЭА 10 минут, и с большим запасом
    отключение одной пары задевало соседнюю (отключил 9:00 — не отметили на 10:40).
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


async def mark_targets(telegram_id: int) -> List[aiosqlite.Row]:
    """Кого отмечает скан пользователя: друзья с включённой отметкой и его группа.

    Группа — все её участники, если тумблер «отмечаться с группой» включён и у
    сканирующего, и у них. UNION убирает тех, кто и друг, и в группе.
    """
    async with get_db() as db:
        async with db.execute(
            """SELECT u.telegram_id, u.display_name, u.encrypted_session, u.session_valid
               FROM friendships f JOIN users u ON f.friend_id = u.telegram_id
               WHERE f.user_id = ? AND f.status = 'accepted' AND f.marking_enabled = 1
               UNION
               SELECT u.telegram_id, u.display_name, u.encrypted_session, u.session_valid
               FROM group_members me
               JOIN group_members m ON m.group_id = me.group_id AND m.telegram_id != me.telegram_id
               JOIN users u ON u.telegram_id = m.telegram_id
               WHERE me.telegram_id = ? AND me.marking = 1 AND m.marking = 1
               ORDER BY 2 COLLATE NOCASE""",
            (telegram_id, telegram_id),
        ) as cur:
            return await cur.fetchall()


GROUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без похожих O/0 и I/1
GROUP_NAME_MAX = 40


async def get_group(telegram_id: int) -> Optional[dict]:
    """Группа пользователя с участниками, или None, если он ни в одной не состоит."""
    async with get_db() as db:
        async with db.execute(
            """SELECT g.id, g.name, g.code, m.marking FROM group_members m
               JOIN study_groups g ON g.id = m.group_id WHERE m.telegram_id = ?""",
            (telegram_id,),
        ) as cur:
            me = await cur.fetchone()
        if not me:
            return None
        async with db.execute(
            """SELECT u.telegram_id, u.display_name, u.session_valid, m.marking
               FROM group_members m JOIN users u ON u.telegram_id = m.telegram_id
               WHERE m.group_id = ? ORDER BY m.marking DESC, u.display_name COLLATE NOCASE""",
            (me["id"],),
        ) as cur:
            rows = await cur.fetchall()
    return {
        "name": me["name"], "code": me["code"], "enabled": bool(me["marking"]),
        "members": [{"telegram_id": r["telegram_id"], "name": r["display_name"],
                     "is_self": r["telegram_id"] == telegram_id, "enabled": bool(r["marking"]),
                     "session_alive": bool(r["session_valid"])} for r in rows],
    }


async def create_group(telegram_id: int, name: str) -> Optional[str]:
    """Создаёт группу и вступает в неё. Ошибка текстом (или None, если всё хорошо)."""
    name = " ".join((name or "").split())[:GROUP_NAME_MAX]
    if not name:
        return "Придумай название группы"
    async with get_db() as db:
        async with db.execute("SELECT 1 FROM group_members WHERE telegram_id = ?", (telegram_id,)) as cur:
            if await cur.fetchone():
                return "Ты уже в группе — сначала выйди из неё"
        for _ in range(10):
            code = "".join(secrets.choice(GROUP_CODE_ALPHABET) for _ in range(6))
            try:
                cur = await db.execute(
                    "INSERT INTO study_groups (name, code, created_by) VALUES (?, ?, ?)",
                    (name, code, telegram_id))
                break
            except aiosqlite.IntegrityError:
                continue   # такой код уже занят — берём другой
        else:
            return "Не удалось создать группу, попробуй ещё раз"
        await db.execute("INSERT INTO group_members (telegram_id, group_id) VALUES (?, ?)",
                         (telegram_id, cur.lastrowid))
        await db.commit()
    return None


async def join_group(telegram_id: int, code: str) -> Optional[str]:
    """Вступление по коду. Ошибка текстом (или None)."""
    code = (code or "").strip().upper()
    async with get_db() as db:
        async with db.execute("SELECT id FROM study_groups WHERE code = ?", (code,)) as cur:
            group = await cur.fetchone()
        if not group:
            return "Группа с таким кодом не найдена"
        async with db.execute("SELECT group_id FROM group_members WHERE telegram_id = ?", (telegram_id,)) as cur:
            current = await cur.fetchone()
        if current:
            return None if current["group_id"] == group["id"] else "Ты уже в другой группе — сначала выйди из неё"
        await db.execute("INSERT INTO group_members (telegram_id, group_id) VALUES (?, ?)",
                         (telegram_id, group["id"]))
        await db.commit()
    return None


async def leave_group(telegram_id: int) -> None:
    """Выход из группы. Опустевшую группу удаляем — её код больше не нужен."""
    async with get_db() as db:
        async with db.execute("SELECT group_id FROM group_members WHERE telegram_id = ?", (telegram_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return
        await db.execute("DELETE FROM group_members WHERE telegram_id = ?", (telegram_id,))
        await db.execute(
            "DELETE FROM study_groups WHERE id = ? AND NOT EXISTS "
            "(SELECT 1 FROM group_members WHERE group_id = ?)", (row["group_id"], row["group_id"]))
        await db.commit()


async def set_group_marking(telegram_id: int, enabled: bool) -> None:
    async with get_db() as db:
        await db.execute("UPDATE group_members SET marking = ? WHERE telegram_id = ?",
                         (1 if enabled else 0, telegram_id))
        await db.commit()


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
