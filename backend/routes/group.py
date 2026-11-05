from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.database import get_group, set_group_marking
from backend.middleware.telegram_auth import get_current_user, TelegramUser

router = APIRouter(prefix="/api/group", tags=["group"])


class GroupMarkingBody(BaseModel):
    enabled: bool


@router.get("")
async def group(user: TelegramUser = Depends(get_current_user)):
    """Учебная группа из профиля Пульса: участники в InSync и тумблер «отмечаться с группой»."""
    return await get_group(user.telegram_id)


@router.post("/marking")
async def group_marking(body: GroupMarkingBody, user: TelegramUser = Depends(get_current_user)):
    await set_group_marking(user.telegram_id, body.enabled)
    return {"success": True}
