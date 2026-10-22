from fastapi import APIRouter, Depends

from backend.models import NotifPrefRequest
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.database import get_notif_prefs, set_notif_pref

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/notifications")
async def notifications(user: TelegramUser = Depends(get_current_user)):
    """Настройки уведомлений пользователя."""
    return await get_notif_prefs(user.telegram_id)


@router.post("/notifications")
async def set_notifications(body: NotifPrefRequest, user: TelegramUser = Depends(get_current_user)):
    """Включить/выключить уведомления типа key (marked | login | session)."""
    await set_notif_pref(user.telegram_id, body.key, body.enabled)
    return {"success": True}
