import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from backend.models import (
    AttendanceRequest, AttendanceResponse, AttendanceResult,
)
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services.session_cache import session_cache
from backend.services.mirea_service import mark_attendance_parallel
from backend.services.mirea_client import warm_connection
from backend.database import get_db, opted_out_now

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/attendance", tags=["attendance"])

# Сериализует запись истории: волны одного скана идут последовательно и не плодят дубли.
_LOG_LOCK = asyncio.Lock()


async def _notify_marked_friends(user: TelegramUser, raw_results: list[dict]) -> None:
    """Шлёт в Telegram уведомление каждому успешно отмеченному другу (не себе).

    В тексте — имя того, кто отметил (из Telegram).
    """
    from backend.main import bot  # ленивый импорт — избегаем циклической зависимости
    if not bot:
        return
    marker = user.display_name
    for r in raw_results:
        if r.get("success") and r["telegram_id"] != user.telegram_id:
            try:
                await bot.send_message(
                    r["telegram_id"],
                    f"✔️ Вас отметил на паре {marker} (через InSync).",
                )
            except Exception:
                pass


def _is_retriable(success: bool, error: str | None) -> bool:
    """Стоит ли повторять на следующем QR.

    Нет смысла повторять «мёртвые» ошибки: протухшая сессия / нужен перезаход /
    нераспознанный QR. Всё остальное (сеть, «пара ещё не началась», неизвестный
    ответ) — временное, повторяем на следующем токене.
    """
    if success:
        return False
    low = (error or "").lower()
    if "сесси" in low or "распознать qr" in low or "войд" in low or "перезай" in low:
        return False
    return True


async def _log_and_notify(user: TelegramUser, raw_results: list[dict],
                          qr_data: str, scan_id: str | None) -> None:
    """Фоновая запись истории + уведомления — вне критического пути ответа на скан.

    При scan_id обновляем строку этой сессии (а не плодим по записи на каждый токен),
    чтобы в истории была одна отметка с финальным статусом. Лок сериализует волны.
    """
    async with _LOG_LOCK:
        try:
            async with get_db() as db:
                for r in raw_results:
                    if r.get("opted_out"):
                        continue  # сам отключил пару — это не попытка отметки, не пишем
                    if scan_id:
                        cur = await db.execute(
                            """UPDATE attendance_log
                               SET success = ?, error_msg = ?, qr_token = ?, created_at = unixepoch()
                               WHERE initiator = ? AND target_user = ? AND scan_id = ?""",
                            (r["success"], r.get("error"), qr_data,
                             user.telegram_id, r["telegram_id"], scan_id),
                        )
                        if cur.rowcount == 0:
                            await db.execute(
                                """INSERT INTO attendance_log
                                   (initiator, target_user, qr_token, success, error_msg, scan_id)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                                (user.telegram_id, r["telegram_id"], qr_data,
                                 r["success"], r.get("error"), scan_id),
                            )
                    else:
                        await db.execute(
                            """INSERT INTO attendance_log
                               (initiator, target_user, qr_token, success, error_msg)
                               VALUES (?, ?, ?, ?, ?)""",
                            (user.telegram_id, r["telegram_id"], qr_data,
                             r["success"], r.get("error")),
                        )
                await db.commit()
        except Exception as e:
            logger.error("attendance log failed: %s", e)
    await _notify_marked_friends(user, raw_results)


@router.post("/mark", response_model=AttendanceResponse)
async def mark_attendance(
    request: AttendanceRequest,
    user: TelegramUser = Depends(get_current_user),
):
    """
    Критический путь: сканирование QR → параллельная отметка за себя и друзей.
    Поддерживает подмножество (`only`) — повтор только тем, кто ещё не отметился,
    и `scan_id` — чтобы все волны одной отметки склеились в одну запись истории.
    """
    # Получаем все сессии (свою + друзей) из кеша
    sessions = await session_cache.get_all_sessions_for_mark(user.telegram_id)

    # Повтор: ограничиваемся теми, кого прислал клиент
    if request.only is not None:
        wanted = set(request.only)
        sessions = [s for s in sessions if s[0] in wanted]

    if not sessions:
        raise HTTPException(status_code=400, detail="Нет активных сессий для отметки")

    # Кто сам отключил отметку на идущей сейчас паре — того не трогаем (ни друга, ни себя).
    now = int(time.time())
    opted = await opted_out_now([s[0] for s in sessions], now)

    to_mark = [s for s in sessions if s[0] not in opted]
    raw_results = await mark_attendance_parallel(request.qr_data, to_mark) if to_mark else []
    for tid, name, _ in sessions:
        if tid in opted:
            raw_results.append({
                "telegram_id": tid, "user_name": name, "success": False,
                "error": f"Отключил отметку на этой паре ({opted[tid]})", "opted_out": True,
            })

    results = [
        AttendanceResult(
            user_name=r["user_name"],
            telegram_id=r["telegram_id"],
            success=r["success"],
            error=r.get("error"),
            opted_out=r.get("opted_out", False),
            retriable=False if r.get("opted_out") else _is_retriable(r["success"], r.get("error")),
        )
        for r in raw_results
    ]

    # Запись истории + уведомления — В ФОНЕ, чтобы ответ на скан вернулся сразу
    # (запись в SQLite с commit и отправка сообщений не держат критический путь).
    asyncio.create_task(_log_and_notify(user, raw_results, request.qr_data, request.scan_id))

    return AttendanceResponse(
        results=results,
        total=len(results),
        successful=sum(1 for r in results if r.success),
    )


@router.post("/prewarm")
async def prewarm(user: TelegramUser = Depends(get_current_user)):
    """Прогрев перед сканом: расшифровать и закешировать сессии (свою + друзей),
    чтобы ПЕРВАЯ отметка не платила за расшифровку. Фронт зовёт при открытии сканера."""
    sessions = await session_cache.get_all_sessions_for_mark(user.telegram_id)
    # Прогрев TLS-соединения к Пульсу в фоне — чтобы первый self-approve был мгновенным.
    asyncio.create_task(warm_connection())
    return {"ready": len(sessions)}


@router.get("/history")
async def attendance_history(user: TelegramUser = Depends(get_current_user)):
    """История сканов: что когда отмечено и для кого (с именами и статусом)."""
    async with get_db() as db:
        async with db.execute(
            """SELECT a.target_user, a.qr_token, a.scan_id, a.success, a.error_msg, a.created_at,
                      u.display_name AS target_name
               FROM attendance_log a
               LEFT JOIN users u ON u.telegram_id = a.target_user
               WHERE a.initiator = ?
               ORDER BY a.id DESC LIMIT 200""",
            (user.telegram_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    # Группируем один скан: по scan_id (новые записи), иначе — qr_token + близкое время (старые).
    scans: list[dict] = []
    index: dict[tuple, dict] = {}
    for row in rows:
        if row["scan_id"]:
            bucket = ("sid", row["scan_id"])
        else:
            bucket = ("legacy", row["qr_token"], (row["created_at"] or 0) // 120)  # окно 2 минуты
        scan = index.get(bucket)
        if scan is None:
            scan = {"created_at": row["created_at"], "targets": [], "successful": 0, "total": 0}
            index[bucket] = scan
            scans.append(scan)
        name = row["target_name"] or ("Вы" if row["target_user"] == user.telegram_id else "Студент")
        if row["target_user"] == user.telegram_id:
            name = "Вы"
        scan["targets"].append({
            "name": name,
            "is_self": row["target_user"] == user.telegram_id,
            "success": bool(row["success"]),
            "error": row["error_msg"],
        })
        scan["total"] += 1
        if row["success"]:
            scan["successful"] += 1

    return {"scans": scans}
