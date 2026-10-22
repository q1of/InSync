from fastapi import APIRouter, Depends

from backend.models import OptoutRequest
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.database import get_optouts, set_optout

router = APIRouter(prefix="/api/optouts", tags=["optouts"])


@router.get("")
async def list_optouts(user: TelegramUser = Depends(get_current_user)):
    """start_epoch всех пар, на которых пользователь отключил отметку."""
    return {"start_epochs": await get_optouts(user.telegram_id)}


@router.post("")
async def toggle_optout(body: OptoutRequest, user: TelegramUser = Depends(get_current_user)):
    """Отключить (enabled=True) или вернуть (False) отметку на конкретном занятии."""
    await set_optout(
        user.telegram_id, body.start_epoch, body.end_epoch, body.lesson_name, body.enabled
    )
    return {"success": True}
