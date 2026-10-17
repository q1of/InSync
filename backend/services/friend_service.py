import logging

from backend.config import settings
from backend.database import get_db

logger = logging.getLogger(__name__)


async def resolve_user(identifier: str) -> int | None:
    """Находит telegram_id по @username или числовому ID."""
    s = (identifier or "").strip().lstrip("@")
    if not s:
        return None
    if s.isdigit():
        return int(s)
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT telegram_id FROM users WHERE lower(username) = lower(?)", (s,)
            ) as cursor:
                row = await cursor.fetchone()
                return row["telegram_id"] if row else None
    except Exception as e:
        logger.error(f"resolve_user failed for {identifier!r}: {e}")
        return None


async def send_request(from_id: int, to_id: int) -> dict:
    """Отправляет заявку в друзья. Проверяет лимит (30) и дубликаты."""
    if from_id == to_id:
        return {"success": False, "error": "Нельзя добавить себя в друзья"}

    try:
        async with get_db() as db:
            # Проверяем что оба юзера существуют
            async with db.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IN (?, ?)",
                (from_id, to_id),
            ) as cursor:
                rows = await cursor.fetchall()
                if len(rows) != 2:
                    return {"success": False, "error": "Пользователь не найден"}

            # Проверяем лимиты
            from_count = await count_friends(from_id)
            if from_count >= settings.friend_limit:
                return {"success": False, "error": "Вы достигли лимита друзей"}

            to_count = await count_friends(to_id)
            if to_count >= settings.friend_limit:
                return {"success": False, "error": "У пользователя лимит друзей"}

            # Проверяем дубликаты
            async with db.execute(
                "SELECT status FROM friendships WHERE user_id = ? AND friend_id = ?",
                (from_id, to_id),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"success": False, "error": f"Заявка уже существует ({row['status']})"}

            # Создаём заявку
            await db.execute(
                "INSERT INTO friendships (user_id, friend_id, status) VALUES (?, ?, 'pending')",
                (from_id, to_id),
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        logger.error(f"Failed to send friend request {from_id} -> {to_id}: {e}")
        return {"success": False, "error": "Ошибка базы данных"}


async def accept_request(user_id: int, request_id: int) -> dict:
    """Принимает заявку. request_id — telegram_id отправителя."""
    friend_id = request_id
    try:
        async with get_db() as db:
            # Проверяем что заявка существует и направлена к нам
            async with db.execute(
                "SELECT status FROM friendships WHERE user_id = ? AND friend_id = ? AND status = 'pending'",
                (friend_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return {"success": False, "error": "Заявка не найдена"}

            # Проверяем лимиты
            if await count_friends(user_id) >= settings.friend_limit:
                return {"success": False, "error": "Вы достигли лимита друзей"}
            if await count_friends(friend_id) >= settings.friend_limit:
                return {"success": False, "error": "У отправителя лимит друзей"}

            # Принимаем: обновляем исходную + создаём обратную запись
            await db.execute(
                "UPDATE friendships SET status = 'accepted' WHERE user_id = ? AND friend_id = ?",
                (friend_id, user_id),
            )
            await db.execute(
                "INSERT OR REPLACE INTO friendships (user_id, friend_id, status) VALUES (?, ?, 'accepted')",
                (user_id, friend_id),
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        logger.error(f"Failed to accept request for {user_id}: {e}")
        return {"success": False, "error": "Ошибка базы данных"}


async def reject_request(user_id: int, request_id: int) -> dict:
    """Отклоняет заявку. request_id — telegram_id отправителя."""
    friend_id = request_id
    try:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM friendships WHERE user_id = ? AND friend_id = ? AND status = 'pending'",
                (friend_id, user_id),
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        logger.error(f"Failed to reject request for {user_id}: {e}")
        return {"success": False, "error": "Ошибка базы данных"}


async def remove_friend(user_id: int, friend_id: int) -> dict:
    """Удаляет друга (обе стороны)."""
    try:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM friendships WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)",
                (user_id, friend_id, friend_id, user_id),
            )
            await db.commit()
            return {"success": True}
    except Exception as e:
        logger.error(f"Failed to remove friend {friend_id} for {user_id}: {e}")
        return {"success": False, "error": "Ошибка базы данных"}


async def get_friends(user_id: int) -> list[dict]:
    """Возвращает список принятых друзей с информацией о сессии."""
    try:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT u.telegram_id, u.display_name, u.full_name, u.group_name,
                       u.session_valid, f.marking_enabled
                FROM friendships f
                JOIN users u ON f.friend_id = u.telegram_id
                WHERE f.user_id = ? AND f.status = 'accepted'
                ORDER BY u.full_name, u.display_name
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

            return [
                {
                    "telegram_id": row["telegram_id"],
                    "name": row["full_name"] or row["display_name"],
                    "group": row["group_name"],
                    "session_alive": bool(row["session_valid"]),
                    "marking_enabled": bool(row["marking_enabled"]),
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to get friends for {user_id}: {e}")
        return []


async def get_pending_requests(user_id: int) -> list[dict]:
    """Возвращает входящие заявки в друзья."""
    try:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT u.telegram_id, u.display_name, u.full_name, u.group_name, f.created_at
                FROM friendships f
                JOIN users u ON f.user_id = u.telegram_id
                WHERE f.friend_id = ? AND f.status = 'pending'
                ORDER BY f.created_at DESC
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()

            return [
                {
                    "telegram_id": row["telegram_id"],
                    "name": row["full_name"] or row["display_name"],
                    "group": row["group_name"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to get pending requests for {user_id}: {e}")
        return []


async def count_friends(user_id: int) -> int:
    """Считает количество принятых друзей."""
    try:
        async with get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM friendships WHERE user_id = ? AND status = 'accepted'",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row["cnt"] if row else 0
    except Exception as e:
        logger.error(f"Failed to count friends for {user_id}: {e}")
        return 0


async def set_marking(user_id: int, friend_id: int, enabled: bool) -> dict:
    """Включает/выключает отметку конкретного друга при скане QR."""
    try:
        async with get_db() as db:
            await db.execute(
                "UPDATE friendships SET marking_enabled = ? WHERE user_id = ? AND friend_id = ? AND status = 'accepted'",
                (1 if enabled else 0, user_id, friend_id),
            )
            await db.commit()
            return {"success": True, "marking_enabled": enabled}
    except Exception as e:
        logger.error(f"Failed to set marking {user_id}->{friend_id}: {e}")
        return {"success": False, "error": "Ошибка базы данных"}
