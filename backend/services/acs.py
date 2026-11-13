"""Пропуск: события турникетов из Пульса и проверка «вошёл ли студент в корпус пары».

Корпус сверяем по названиям: аудитория из расписания («А-213», корпус «В-78»)
против зоны турникета («В78 А 2эт ЦентральныйВход», «В78 Корпус И»). Что не
удалось разобрать — не сравниваем: лучше промолчать, чем показать ложную ошибку.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from backend.services.mirea_client import ACS_ENTER, ACS_EXIT, get_acs_events, get_me

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
NOT_INSIDE = "По турникетам вы не в вузе — отметка может не засчитаться"
PRESENCE_TIMEOUT_S = 6.0

# telegram_id -> userId в Пульсе (не меняется, поэтому достаточно памяти процесса)
_user_ids: dict[int, str] = {}

_CAMPUS_RE = re.compile(r"([А-ЯЁ]{1,3})-?(\d{1,3})")
_ZONE_CAMPUS_RE = re.compile(r"\s*([А-ЯЁ]{1,3})-?(\d{1,3})\b")
_ZONE_LETTER_RE = re.compile(r"[Кк]орпус\s+([А-ЯЁ])\b|^\s*[А-ЯЁ]{1,3}-?\d{1,3}\s+([А-ЯЁ])\b")
_ROOM_LETTER_RE = re.compile(r"\s*([А-ЯЁ])-?\d")


def today_msk() -> date:
    return datetime.now(MSK).date()


def day_bounds(day: date) -> tuple[float, float]:
    start = datetime(day.year, day.month, day.day, tzinfo=MSK)
    return start.timestamp(), (start + timedelta(days=1)).timestamp() - 1


def lesson_place(room: str | None, building: str | None) -> tuple[str | None, str | None]:
    """(кампус, буква корпуса) аудитории пары: «А-213» + «В-78» → («В78», «А»)."""
    room, building = room or "", building or ""
    paren = " ".join(re.findall(r"\(([^)]*)\)", room))
    m = _CAMPUS_RE.search(f"{building} {paren}")
    campus = f"{m[1]}{m[2]}" if m else None
    letter = _ROOM_LETTER_RE.match(room) or re.search(r"[Кк]орпус\s+([А-ЯЁ])\b|^\s*([А-ЯЁ])\s*$", building)
    return campus, next((g for g in letter.groups() if g), None) if letter else None


def zone_place(zone: str | None) -> tuple[str | None, str | None]:
    """(кампус, буква корпуса) зоны турникета: «В78 Корпус И» → («В78», «И»)."""
    m = _ZONE_CAMPUS_RE.match(zone or "")
    letter = _ZONE_LETTER_RE.search(zone or "")
    return (f"{m[1]}{m[2]}" if m else None), next((g for g in letter.groups() if g), None) if letter else None


def _same_campus(a: str, b: str) -> bool:
    """«МП1» в расписании и «П1» у турникетов Пироговки — один кампус: сверяем по концу кода."""
    return a == b or a.endswith(b) or b.endswith(a)


def presence_problem(events: list[dict], now: float, room: str | None, building: str | None) -> str | None:
    """Почему студента нет в корпусе пары по последнему событию турникетов (или None)."""
    past = [e for e in events if e["time"] <= now + 60]
    last = past[-1] if past else None
    zone = (last["enter"] or last["exit"]) if last else ""
    # Пока сверка новая — пишем, что с чем сравнивали, чтобы поправить по реальным названиям.
    logger.warning("пропуск: аудитория=%r корпус=%r событий=%d последнее=%r турникет=%r",
                   room, building, len(past), last and last["kind"], zone)
    if not last or last["kind"] == ACS_EXIT or "неконтролируем" in zone.lower():
        return NOT_INSIDE
    # Сверяем только кампус (адрес): Пироговка / Стромынка / Вернадского — они у турникетов
    # и в расписании совпадают надёжно. Корпус/букву внутри кампуса НЕ сверяем: пары идут
    # по разным корпусам одного адреса, а названия в Пульсе оформлены по-разному — ложные срабатывания.
    lesson_campus, _ = lesson_place(room, building)
    zone_campus, _ = zone_place(zone)
    if lesson_campus and zone_campus and not _same_campus(lesson_campus, zone_campus):
        return f"По турникетам вы в другом кампусе ({zone}) — отметка может не засчитаться"
    return None


def describe(ev: dict) -> dict:
    """Событие для вкладки «Пропуск» — так же, как его подписывает Пульс."""
    if ev["kind"] == ACS_ENTER:
        kind, title, place = "enter", "Вход в вуз", ev["enter"] or ev["exit"]
        place = f"через: {place}" if place else ""
    elif ev["kind"] == ACS_EXIT:
        kind, title, place = "exit", "Выход из вуза", ev["exit"] or ev["enter"]
        place = f"из: {place}" if place else ""
    else:
        kind, title = "move", ev["enter"] or "Переход"
        place = f"из: {ev['exit']}" if ev["exit"] else ""
    return {"time": ev["time"], "kind": kind, "title": title, "place": place}


async def pulse_user_id(telegram_id: int, cookies: dict) -> str | None:
    uid = _user_ids.get(telegram_id)
    if not uid:
        uid = (await get_me(cookies)).get("user_id")
        if uid:
            _user_ids[telegram_id] = uid
    return uid


async def day_events(telegram_id: int, cookies: dict, day: date) -> dict:
    """События турникетов за день. {"error": ...} при сбое Пульса."""
    uid = await pulse_user_id(telegram_id, cookies)
    if not uid:
        return {"error": "Не удалось получить профиль Пульса"}
    start, end = day_bounds(day)
    res = await get_acs_events(cookies, uid, start, end)
    if res.get("error"):
        return res
    return {"error": None, "events": [describe(e) for e in res["events"]]}


async def presence_warning(telegram_id: int, cookies: dict, room: str | None, building: str | None) -> str | None:
    """Предупреждение, если по турникетам студента нет в вузе (или он в другом кампусе).

    Любой сбой (Пульс не ответил, нет id) — None: из-за проверки отметка не должна выглядеть хуже.
    """
    async def _check() -> str | None:
        uid = await pulse_user_id(telegram_id, cookies)
        if not uid:
            return None
        now = time.time()
        # Запрашиваем весь день, как вкладка «Пропуск»: на диапазон «до текущей минуты» Пульс
        # отвечал пустым списком тем, кто был в корпусе. Будущее отсекает presence_problem.
        start, end = day_bounds(today_msk())
        res = await get_acs_events(cookies, uid, start, end)
        if res.get("error"):
            return None
        return presence_problem(res["events"], now, room, building)

    try:
        return await asyncio.wait_for(_check(), timeout=PRESENCE_TIMEOUT_S)
    except Exception as e:
        logger.warning("проверка пропуска не удалась для %s: %s", telegram_id, e)
        return None
