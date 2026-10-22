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
        """Расшифровывает и кеширует сессию (пропускает, если уже свежо в кеше)."""
        async with self._lock:
            s = self._cache.get(telegram_id)
            if s and time.time() - s.loaded_at <= CACHE_EXPIRY_SECONDS:
                return  # свежая запись уже есть — не тратим время на расшифровку
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

    async def get_all_sessions_for_mark(self, telegram_id: int) -> list[tuple[int, str, dict]]:
        """
        (telegram_id, display_name, cookies) для пользователя и включённых друзей
        — готовый список для mark_attendance_parallel. Один проход: по одному
        запросу на пользователя и на друзей (имя + сессия сразу), кеш наполняем
        по ходу.
        """
        sessions: list[tuple[int, str, dict]] = []
        try:
            async with get_db() as db:
                # Сам пользователь (имя + сессия одним запросом)
                async with db.execute(
                    "SELECT display_name, encrypted_session FROM users WHERE telegram_id = ?",
                    (telegram_id,),
                ) as cursor:
                    urow = await cursor.fetchone()
                user_name = urow["display_name"] if urow else "Вы"
                if urow and urow["encrypted_session"]:
                    await self.set_session(telegram_id, urow["encrypted_session"])
                user_cookies = await self.get_session(telegram_id)
                if user_cookies:
                    sessions.append((telegram_id, user_name, user_cookies))

                # Друзья: имя + сессия + валидность одним запросом
                async with db.execute(
                    """
                    SELECT u.telegram_id, u.display_name, u.encrypted_session, u.session_valid
                    FROM friendships f
                    JOIN users u ON f.friend_id = u.telegram_id
                    WHERE f.user_id = ? AND f.status = 'accepted' AND f.marking_enabled = 1
                    """,
                    (telegram_id,),
                ) as cursor:
                    friends = await cursor.fetchall()

                for fr in friends:
                    if fr["session_valid"] and fr["encrypted_session"]:
                        await self.set_session(fr["telegram_id"], fr["encrypted_session"])
                    cookies = await self.get_session(fr["telegram_id"])
                    if cookies:
                        sessions.append((fr["telegram_id"], fr["display_name"], cookies))
        except Exception as e:
            logger.error(f"Failed to get sessions for marking: {e}")

        return sessions


# Singleton
session_cache = SessionCache()
