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
from backend.services.acs import presence_warning
from backend.database import (
    get_db, mark_targets, opted_out_now, notif_enabled, set_session_invalid, update_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/attendance", tags=["attendance"])

# Сериализует проверку повтора и запись истории: волны одного скана не плодят дубли,
# а двое друзей, сканирующих одновременно, видят отметки друг друга.
_LOG_LOCK = asyncio.Lock()

# Фоновые задачи держим здесь: на задачу без сильной ссылки asyncio может собрать мусор
# посреди работы — и уведомление молча не уйдёт.
_BACKGROUND: set[asyncio.Task] = set()


def _in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def _notify_marked_friends(user: TelegramUser, raw_results: list[dict], lesson_name: str | None = None,
                                 cookies_by_id: dict[int, dict] | None = None,
                                 room: str | None = None, building: str | None = None) -> None:
    """Уведомления в Telegram по итогам скана (с учётом настроек получателя):
      • другу, которого отметили — «вас отметил …» (и предупреждение, если он не в корпусе пары);
      • другу, которого пропустили из-за его же отключения — «вас не отметили, вы отключили».
    """
    from backend.main import bot  # ленивый импорт — избегаем циклической зависимости
    if not bot:
        return
    marker = html.escape(user.display_name or "")
    where = f"на «{html.escape(lesson_name)}»" if lesson_name else "на паре"

    # Турникеты проверяем здесь, уже после ответа на скан: на 15 человек проверка заметно
    # задерживала итог, а нужна она только самому отмеченному — в его уведомлении.
    cookies_by_id = cookies_by_id or {}
    fresh = [r["telegram_id"] for r in raw_results
             if r.get("success") and not r.get("already_marked")
             and r["telegram_id"] != user.telegram_id and r["telegram_id"] in cookies_by_id]
    found = await asyncio.gather(*(presence_warning(t, cookies_by_id[t], room, building) for t in fresh))
    warnings = {t: w for t, w in zip(fresh, found) if w}

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
                warning = f"\n⚠️ {html.escape(warnings[tid])}." if tid in warnings else ""
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
        except Exception as e:
            logger.warning("уведомление для %s не отправлено: %s", tid, e)


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
        r.update(success=True, error=None, already_marked=True)


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
                            (r["success"], r.get("error"), qr_data, lesson_name, r.get("lesson_id"),
                             lesson_start, already, user.telegram_id, r["telegram_id"], scan_id),
                        )
                        if cur.rowcount:
                            continue
                    await db.execute(
                        """INSERT INTO attendance_log
                           (initiator, target_user, qr_token, success, error_msg, scan_id,
                            lesson_name, lesson_id, lesson_start, already_marked)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (user.telegram_id, r["telegram_id"], qr_data, r["success"], r.get("error"),
                         scan_id, lesson_name, r.get("lesson_id"), lesson_start, already),
                    )
                await db.commit()
        except Exception as e:
            logger.error("attendance log failed: %s", e)


async def _log_and_notify(user: TelegramUser, raw_results: list[dict], lesson_name: str | None,
                          cookies_by_id: dict[int, dict] | None = None,
                          room: str | None = None, building: str | None = None) -> None:
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

    await _notify_marked_friends(user, raw_results, lesson_name, cookies_by_id, room, building)


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
    raw_results = await mark_attendance_parallel(request.qr_data, to_mark) if to_mark else []
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
            retriable=False if r.get("opted_out") else _is_retriable(r["success"], r.get("error")),
        )
        for r in raw_results
    ]

    # Уведомления — В ФОНЕ, чтобы отправка сообщений не держала ответ на скан.
    # Для проверки турникетов — свежие cookie, если Пульс их обновил при отметке.
    cookies_by_id = {tid: c for tid, _, c in to_mark}
    cookies_by_id.update({r["telegram_id"]: r["cookies"] for r in raw_results if r.get("cookies")})
    _in_background(_log_and_notify(user, raw_results, request.lesson_name, cookies_by_id,
                                   request.lesson_room, request.lesson_building))

    return AttendanceResponse(
        results=results,
        total=len(results),
        successful=sum(1 for r in results if r.success),
    )


@router.get("/targets")
async def mark_targets_list(user: TelegramUser = Depends(get_current_user)):
    """Кого сканер отметит вместе с пользователем: друзья и группа (без него самого)."""
    return [{"telegram_id": r["telegram_id"], "name": r["display_name"]}
            for r in await mark_targets(user.telegram_id)]


@router.post("/prewarm")
async def prewarm(user: TelegramUser = Depends(get_current_user)):
    """Прогрев перед сканом: расшифровать и закешировать сессии (свою + друзей),
    чтобы ПЕРВАЯ отметка не платила за расшифровку. Фронт зовёт при открытии сканера."""
    sessions = await session_cache.get_all_sessions_for_mark(user.telegram_id)
    # Прогрев TLS-соединения к Пульсу в фоне — чтобы первый self-approve был мгновенным.
    _in_background(warm_connection())
    return {"ready": len(sessions)}


def _lesson_key(row) -> tuple:
    """Идентификатор пары — чтобы отличать разные пары и склеивать записи об одной.

    Приоритет: id пары из Пульса → начало пары по расписанию → название + день.
    """
    if row["lesson_id"]:
        return ("id", row["lesson_id"])
    if row["lesson_start"]:
        return ("start", row["lesson_start"])
    day = (row["created_at"] or 0) // 86400
    if row["lesson_name"]:
        return ("name", row["lesson_name"], day)
    return ("scan", row["scan_id"] or row["created_at"])


def _add_my_scan(row, me: int, index: dict, scans: list, self_marked: set) -> None:
    """Запись о скане, который сделал сам пользователь (initiator = me)."""
    if row["scan_id"]:
        bucket = ("sid", row["scan_id"])
    else:
        bucket = ("legacy", row["qr_token"], (row["created_at"] or 0) // 120)  # окно 2 минуты
    scan = index.get(bucket)
    if scan is None:
        scan = {"kind": "mine", "created_at": row["created_at"], "lesson_name": row["lesson_name"],
                "scan_id": row["scan_id"], "targets": [], "successful": 0, "total": 0}
        index[bucket] = scan
        scans.append(scan)
    elif not scan.get("lesson_name") and row["lesson_name"]:
        scan["lesson_name"] = row["lesson_name"]
    is_self = row["target_user"] == me
    scan["targets"].append({
        "telegram_id": row["target_user"],   # по нему сканер сверяет итог, если ответ оборвался
        "name": "Вы" if is_self else (row["target_name"] or "Студент"),
        "is_self": is_self,
        "success": bool(row["success"]),
        "already_marked": bool(row["already_marked"]),
        # У старых успешных отметок в error_msg лежало предупреждение «не в корпусе» — не показываем.
        "error": None if row["success"] else row["error_msg"],
    })
    scan["total"] += 1
    if row["success"]:
        scan["successful"] += 1
    if is_self and row["success"]:
        self_marked.add(_lesson_key(row))


def _add_incoming(row, incoming: dict) -> None:
    """Отметка, которую за пользователя сделал друг/группа (target = me). Одна пара — одна карточка."""
    key = _lesson_key(row)
    card = incoming.get(key)
    if card is None:
        card = {"kind": "incoming", "key": key, "created_at": row["created_at"],
                "lesson_name": row["lesson_name"], "by": []}
        incoming[key] = card
    if (row["created_at"] or 0) < (card["created_at"] or 0):
        card["created_at"] = row["created_at"]   # когда меня отметили впервые
    name = row["initiator_name"] or "Студент"
    if name not in card["by"]:
        card["by"].append(name)


@router.get("/history")
async def attendance_history(user: TelegramUser = Depends(get_current_user)):
    """История: сканы самого пользователя и отметки, которые сделали за него друзья/группа."""
    me = user.telegram_id
    async with get_db() as db:
        async with db.execute(
            """SELECT a.initiator, a.target_user, a.qr_token, a.scan_id, a.success, a.error_msg,
                      a.created_at, a.lesson_name, a.lesson_id, a.lesson_start, a.already_marked,
                      ut.display_name AS target_name, ui.display_name AS initiator_name
               FROM attendance_log a
               LEFT JOIN users ut ON ut.telegram_id = a.target_user
               LEFT JOIN users ui ON ui.telegram_id = a.initiator
               WHERE a.initiator = ? OR a.target_user = ?
               ORDER BY a.id DESC LIMIT 400""",
            (me, me),
        ) as cursor:
            rows = await cursor.fetchall()

    scans: list[dict] = []
    index: dict[tuple, dict] = {}
    self_marked: set = set()            # пары, где я отметился сам (успешно)
    incoming: dict[tuple, dict] = {}    # пары, где меня отметил кто-то другой

    for row in rows:
        if row["initiator"] == me:
            _add_my_scan(row, me, index, scans, self_marked)
        elif row["target_user"] == me and row["success"] and not row["already_marked"]:
            _add_incoming(row, incoming)

    # «Вас отметили» не показываем на парах, где я уже отметился сам, — иначе два блока об одном.
    scans += [card for key, card in incoming.items() if key not in self_marked]
    scans.sort(key=lambda s: s["created_at"] or 0, reverse=True)
    return {"scans": scans}
