import asyncio
import time
import logging
from dataclasses import dataclass

from backend.services.mirea_service import decrypt_session
from backend.database import get_db

logger = logging.getLogger(__name__)

CACHE_EXPIRY_SECONDS = 300  # 5 минут


@dataclass
class CachedSession:
    decrypted_cookies: dict
    loaded_at: float
    is_valid: bool


class SessionCache:
    def __init__(self):
        self._cache: dict[int, CachedSession] = {}
        self._lock = asyncio.Lock()

    async def get_session(self, telegram_id: int) -> dict | None:
        """Возвращает расшифрованную сессию из кеша."""
        async with self._lock:
            session = self._cache.get(telegram_id)
            if not session:
                return None
            if time.time() - session.loaded_at > CACHE_EXPIRY_SECONDS:
                del self._cache[telegram_id]
                return None
            return session.decrypted_cookies

    async def set_session(self, telegram_id: int, encrypted: str) -> None:
        """Расшифровывает и кеширует сессию."""
        try:
            cookies = decrypt_session(encrypted)
            async with self._lock:
                self._cache[telegram_id] = CachedSession(
                    decrypted_cookies=cookies,
                    loaded_at=time.time(),
                    is_valid=True,
                )
        except Exception as e:
            logger.error(f"Failed to cache session for {telegram_id}: {e}")

    async def set_decrypted(self, telegram_id: int, cookies: dict) -> None:
        """Кеширует уже расшифрованную сессию (после логина)."""
        async with self._lock:
            self._cache[telegram_id] = CachedSession(
                decrypted_cookies=cookies,
                loaded_at=time.time(),
                is_valid=True,
            )

    async def invalidate(self, telegram_id: int) -> None:
        """Удаляет сессию из кеша."""
        async with self._lock:
            self._cache.pop(telegram_id, None)

    async def preload_user_and_friends(self, telegram_id: int) -> None:
        """Предзагружает сессии пользователя и всех его друзей из БД."""
        try:
            async with get_db() as db:
                # Загрузить сессию пользователя
                async with db.execute(
                    "SELECT encrypted_session FROM users WHERE telegram_id = ? AND encrypted_session IS NOT NULL",
                    (telegram_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row["encrypted_session"]:
                        await self.set_session(telegram_id, row["encrypted_session"])

                # Загрузить сессии друзей
                async with db.execute(
                    """
                    SELECT u.telegram_id, u.encrypted_session
                    FROM friendships f
                    JOIN users u ON f.friend_id = u.telegram_id
                    WHERE f.user_id = ? AND f.status = 'accepted' AND f.marking_enabled = 1
                      AND u.encrypted_session IS NOT NULL AND u.session_valid = 1
                    """,
                    (telegram_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        await self.set_session(row["telegram_id"], row["encrypted_session"])
        except Exception as e:
            logger.error(f"Failed to preload sessions for {telegram_id}: {e}")

    async def get_all_sessions_for_mark(self, telegram_id: int) -> list[tuple[int, str, dict]]:
        """
        Возвращает список (telegram_id, display_name, cookies) для
        пользователя и всех его друзей — готовый для mark_attendance_parallel.
        """
        await self.preload_user_and_friends(telegram_id)

        sessions: list[tuple[int, str, dict]] = []
        try:
            async with get_db() as db:
                # Сам пользователь
                async with db.execute(
                    "SELECT display_name FROM users WHERE telegram_id = ?",
                    (telegram_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    user_name = row["display_name"] if row else "Вы"

                user_cookies = await self.get_session(telegram_id)
                if user_cookies:
                    sessions.append((telegram_id, user_name, user_cookies))

                # Друзья
                async with db.execute(
                    """
                    SELECT u.telegram_id, u.display_name
                    FROM friendships f
                    JOIN users u ON f.friend_id = u.telegram_id
                    WHERE f.user_id = ? AND f.status = 'accepted' AND f.marking_enabled = 1
                    """,
                    (telegram_id,),
                ) as cursor:
                    friends = await cursor.fetchall()

                for friend in friends:
                    fid = friend["telegram_id"]
                    fname = friend["display_name"]
                    cookies = await self.get_session(fid)
                    if cookies:
                        sessions.append((fid, fname, cookies))
        except Exception as e:
            logger.error(f"Failed to get sessions for marking: {e}")

        return sessions


# Singleton
session_cache = SessionCache()
