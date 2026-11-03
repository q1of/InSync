from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services.acs import day_events, today_msk
from backend.services.mirea_client import is_session_expired
from backend.services.session_cache import session_cache

router = APIRouter(prefix="/api/pass", tags=["pass"])


@router.get("/day")
async def pass_day(day: str | None = None, user: TelegramUser = Depends(get_current_user)):
    """События турникетов за день (по умолчанию сегодня) и время в вузе."""
    try:
        target = date.fromisoformat(day) if day else today_msk()
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверная дата")
    cookies = await session_cache.load(user.telegram_id)
    if not cookies:
        raise HTTPException(status_code=401, detail="Нет активной сессии")
    res = await day_events(user.telegram_id, cookies, target)
    if res.get("error"):
        status = 401 if is_session_expired(res["error"]) else 502
        raise HTTPException(status_code=status, detail=res["error"])
    return {"day": target.isoformat(), "hours": res["hours"], "events": res["events"]}
