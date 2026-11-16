from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services.acs import day_events, today_msk
from backend.services.mirea_client import is_session_expired
from backend.services.session_cache import session_cache
from backend.services.ttl_cache import TTLCache

router = APIRouter(prefix="/api/pass", tags=["pass"])

# Сегодня события ещё добавляются — держим совсем недолго, чтобы новый проход сразу был виден.
# Прошлые дни уже не меняются — их можно не перезапрашивать.
PASS_TODAY_TTL = 20
PASS_PAST_TTL = 24 * 3600
pass_cache = TTLCache(max_items=5000)   # день проходов — пара КБ


@router.get("/day")
async def pass_day(day: str | None = None, user: TelegramUser = Depends(get_current_user)):
    """События турникетов за день (по умолчанию сегодня)."""
    try:
        target = date.fromisoformat(day) if day else today_msk()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверная дата")
    cookies = await session_cache.load(user.telegram_id)
    if not cookies:
        raise HTTPException(status_code=401, detail="Нет активной сессии")
    ttl = PASS_PAST_TTL if target < today_msk() else PASS_TODAY_TTL
    res = await pass_cache.fetch((user.telegram_id, target), ttl,
                                 lambda: day_events(user.telegram_id, cookies, target),
                                 keep=lambda r: not r.get("error"))
    if res.get("error"):
        status = 401 if is_session_expired(res["error"]) else 502
        raise HTTPException(status_code=status, detail=res["error"])
    return {"day": target.isoformat(), "events": res["events"]}
