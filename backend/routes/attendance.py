import asyncio
import html
import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from backend.models import (
    AttendanceRequest, AttendanceResponse, AttendanceResult,
)
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services.session_cache import session_cache
from backend.services.mirea_service import encrypt_session, mark_attendance_parallel
from backend.services.mirea_client import is_already_marked, is_session_expired, warm_connection
from backend.database import get_db, opted_out_now, notif_enabled, set_session_invalid, update_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/attendance", tags=["attendance"])

# Сериализует проверку повтора и запись истории: волны одного скана не плодят дубли,
# а двое друзей, сканирующих одновременно, видят отметки друг друга.
_LOG_LOCK = asyncio.Lock()


async def _notify_marked_friends(user: TelegramUser, raw_results: list[dict],
                                 lesson_name: str | None = None) -> None:
    """Уведомления в Telegram по итогам скана (с учётом настроек получателя):
      • другу, которого отметили — «вас отметил …»;
      • другу, которого пропустили из-за его же отключения — «вас не отметили, вы отключили».
    """
    from backend.main import bot  # ленивый импорт — избегаем циклической зависимости
    if not bot:
        return
    marker = html.escape(user.display_name or "")
    where = f"на «{html.escape(lesson_name)}»" if lesson_name else "на паре"
    for r in raw_results:
        tid = r["telegram_id"]
        if tid == user.telegram_id:
            continue  # себе не шлём
        if not await notif_enabled(tid, "marked"):
            continue
        try:
            if r.get("already_marked"):
                continue  # уже был отмечен — новой отметки нет, сообщать не о чем
            if r.get("success"):
                warning = f"\n⚠️ {html.escape(r['warning'])}." if r.get("warning") else ""
                await bot.send_message(
                    tid, f"✅ <b>Вас отметили</b>\n{marker} отметил вас {where} через InSync.{warning}",
                    parse_mode="HTML")
            elif r.get("opted_out"):
                await bot.send_message(
                    tid, f"ℹ️ <b>Вас не отметили</b>\nВы отключили отметку {where}.",
                    parse_mode="HTML")
            elif is_session_expired(r.get("error")):
                # Именно слёт сессии — лично сообщаем, что нужно перезайти.
                await bot.send_message(
                    tid, f"⚠️ <b>Не удалось отметить</b>\nНе вышло отметить вас {where} — "
                         f"войдите заново через /start.",
                    parse_mode="HTML")
        except Exception:
            pass


def _is_retriable(success: bool, error: str | None) -> bool:
    """Стоит ли повторять на следующем QR.

    Нет смысла повторять «мёртвые» ошибки: протухшая сессия / нужен перезаход /
    нераспознанный QR / «не подтвердил» (без явного approved — не успех). Всё
    остальное (сеть, «пара ещё не началась») — временное, повторяем на след. токене.
    """
    if success:
        return False
    low = (error or "").lower()
    if any(x in low for x in ("сесси", "распознать qr", "войд", "перезай", "не подтвердил")):
        return False
    return True


async def _flag_repeat(db, r: dict, scan_id: str | None, lesson_start: int | None) -> None:
    """Отмечает результат как повтор, если человек уже отмечен на этой паре.

    Пульс на повторную отметку отвечает обычным успехом, поэтому сверяемся с историей:
    по id пары из ответа Пульса, а если его нет — по началу пары из расписания.
    """
    error = r.get("error")
    if is_session_expired(error):
        return
    already = is_already_marked(error)
    lesson_id = r.get("lesson_id")
    if not already and (lesson_id or lesson_start):
        async with db.execute(
            """SELECT 1 FROM attendance_log
               WHERE target_user = ? AND success = 1 AND scan_id IS NOT ?
                 AND created_at >= unixepoch() - 86400
                 AND (lesson_id = ? OR ((lesson_id IS NULL OR ? IS NULL) AND lesson_start = ?))
               LIMIT 1""",
            (r["telegram_id"], scan_id, lesson_id, lesson_id, lesson_start),
        ) as cur:
            already = await cur.fetchone() is not None
    if already:
        r.update(success=True, error=None, warning=None, already_marked=True)


async def _record_marks(user: TelegramUser, raw_results: list[dict], qr_data: str, scan_id: str | None,
                        lesson_name: str | None, lesson_start: int | None) -> None:
    """Находит повторы и пишет историю. Отметка на этот момент уже сделана.

    При scan_id обновляем строку этой сессии (а не плодим по записи на каждый токен),
    чтобы в истории была одна отметка с финальным статусом.
    """
    async with _LOG_LOCK:
        try:
            async with get_db() as db:
                for r in raw_results:
                    if r.get("opted_out"):
                        continue  # сам отключил пару — это не попытка отметки, не пишем
                    await _flag_repeat(db, r, scan_id, lesson_start)
                    already = r.get("already_marked", False)
                    if scan_id:
                        cur = await db.execute(
                            """UPDATE attendance_log
                               SET success = ?, error_msg = ?, qr_token = ?,
                                   lesson_name = COALESCE(?, lesson_name), lesson_id = COALESCE(?, lesson_id),
                                   lesson_start = COALESCE(?, lesson_start), already_marked = ?,
                                   created_at = unixepoch()
                               WHERE initiator = ? AND target_user = ? AND scan_id = ?""",
                            (r["success"], r.get("error") or r.get("warning"), qr_data, lesson_name, r.get("lesson_id"),
                             lesson_start, already, user.telegram_id, r["telegram_id"], scan_id),
                        )
                        if cur.rowcount:
                            continue
                    await db.execute(
                        """INSERT INTO attendance_log
                           (initiator, target_user, qr_token, success, error_msg, scan_id,
                            lesson_name, lesson_id, lesson_start, already_marked)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (user.telegram_id, r["telegram_id"], qr_data, r["success"], r.get("error") or r.get("warning"),
                         scan_id, lesson_name, r.get("lesson_id"), lesson_start, already),
                    )
                await db.commit()
        except Exception as e:
            logger.error("attendance log failed: %s", e)


async def _log_and_notify(user: TelegramUser, raw_results: list[dict], lesson_name: str | None) -> None:
    """Фоновое сохранение сессий + уведомления — вне критического пути ответа на скан."""
    # Cookie обновилась при отметке — сохраняем, иначе следующий скан снова потратит на это время.
    for r in raw_results:
        if r.get("cookies"):
            try:
                await update_session(r["telegram_id"], encrypt_session(r["cookies"]))
                await session_cache.set_decrypted(r["telegram_id"], r["cookies"])
            except Exception as e:
                logger.error("session save failed for %s: %s", r["telegram_id"], e)

    # Слёт сессии — сразу помечаем невалидной, чтобы на следующих сканах этого
    # человека не дёргали впустую (и профиль покажет «войти заново»).
    for r in raw_results:
        if not r.get("success") and not r.get("opted_out") and is_session_expired(r.get("error")):
            try:
                await set_session_invalid(r["telegram_id"])
                await session_cache.invalidate(r["telegram_id"])
            except Exception:
                pass

    await _notify_marked_friends(user, raw_results, lesson_name)


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
    no_session: list[int] = []
    if request.only is not None:
        wanted = set(request.only)
        sessions = [s for s in sessions if s[0] in wanted]
        # Кого просили, но живой сессии нет: отвечаем явно, иначе сканер ждёт их бесконечно.
        no_session = sorted(wanted - {s[0] for s in sessions})

    if not sessions and not no_session:
        raise HTTPException(status_code=400, detail="Нет активных сессий для отметки")

    # Кто сам отключил отметку на идущей сейчас паре — того не трогаем (ни друга, ни себя).
    now = int(time.time())
    opted = await opted_out_now([s[0] for s in sessions], now)

    to_mark = [s for s in sessions if s[0] not in opted]
    raw_results = await mark_attendance_parallel(
        request.qr_data, to_mark, request.lesson_room, request.lesson_building) if to_mark else []
    for tid, name, _ in sessions:
        if tid in opted:
            raw_results.append({
                "telegram_id": tid, "user_name": name, "success": False,
                "error": f"Отключил отметку на этой паре ({opted[tid]})", "opted_out": True,
            })
    raw_results += [
        {"telegram_id": tid, "user_name": "", "success": False, "error": "Нужно перезайти"}
        for tid in no_session
    ]

    # Повторы определяем до ответа: сканирующему нужно показать «уже отмечен».
    await _record_marks(user, raw_results, request.qr_data, request.scan_id,
                        request.lesson_name, request.lesson_start)

    results = [
        AttendanceResult(
            user_name=r["user_name"],
            telegram_id=r["telegram_id"],
            success=r["success"],
            error=r.get("error"),
            opted_out=r.get("opted_out", False),
            already_marked=r.get("already_marked", False),
            warning=r.get("warning"),
            retriable=False if r.get("opted_out") else _is_retriable(r["success"], r.get("error")),
        )
        for r in raw_results
    ]

    # Уведомления — В ФОНЕ, чтобы отправка сообщений не держала ответ на скан.
    asyncio.create_task(_log_and_notify(user, raw_results, request.lesson_name))

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
            """SELECT a.target_user, a.qr_token, a.scan_id, a.success, a.error_msg, a.created_at, a.lesson_name,
                      a.already_marked, u.display_name AS target_name
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
            scan = {"created_at": row["created_at"], "lesson_name": row["lesson_name"],
                    "targets": [], "successful": 0, "total": 0}
            index[bucket] = scan
            scans.append(scan)
        elif not scan.get("lesson_name") and row["lesson_name"]:
            scan["lesson_name"] = row["lesson_name"]
        name = row["target_name"] or ("Вы" if row["target_user"] == user.telegram_id else "Студент")
        if row["target_user"] == user.telegram_id:
            name = "Вы"
        scan["targets"].append({
            "name": name,
            "is_self": row["target_user"] == user.telegram_id,
            "success": bool(row["success"]),
            "already_marked": bool(row["already_marked"]),
            # У успешной отметки в error_msg лежит предупреждение «не в корпусе»
            "error": row["error_msg"],
        })
        scan["total"] += 1
        if row["success"]:
            scan["successful"] += 1

    return {"scans": scans}
