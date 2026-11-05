from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.database import create_group, get_group, join_group, leave_group, set_group_marking
from backend.middleware.telegram_auth import get_current_user, TelegramUser

router = APIRouter(prefix="/api/group", tags=["group"])


class GroupMarkingBody(BaseModel):
    enabled: bool


class CreateGroupBody(BaseModel):
    name: str


class JoinGroupBody(BaseModel):
    code: str


async def _invite_link(code: str) -> str | None:
    """Ссылка, которая открывает InSync сразу с кодом группы (startapp → start_param)."""
    from backend.main import bot  # ленивый импорт — избегаем циклической зависимости
    try:
        me = await bot.me() if bot else None
    except Exception:
        return None
    return f"https://t.me/{me.username}?startapp=g{code}" if me and me.username else None


async def _group_response(telegram_id: int) -> dict:
    group = await get_group(telegram_id)
    if group:
        group["invite_link"] = await _invite_link(group["code"])
    return {"group": group}


@router.get("")
async def group(user: TelegramUser = Depends(get_current_user)):
    """Группа пользователя (или null) с участниками и тумблером «отмечаться с группой»."""
    return await _group_response(user.telegram_id)


@router.post("/create")
async def group_create(body: CreateGroupBody, user: TelegramUser = Depends(get_current_user)):
    error = await create_group(user.telegram_id, body.name)
    return {"success": False, "error": error} if error else {"success": True, **await _group_response(user.telegram_id)}


@router.post("/join")
async def group_join(body: JoinGroupBody, user: TelegramUser = Depends(get_current_user)):
    error = await join_group(user.telegram_id, body.code)
    return {"success": False, "error": error} if error else {"success": True, **await _group_response(user.telegram_id)}


@router.post("/leave")
async def group_leave(user: TelegramUser = Depends(get_current_user)):
    await leave_group(user.telegram_id)
    return {"success": True}


@router.post("/marking")
async def group_marking(body: GroupMarkingBody, user: TelegramUser = Depends(get_current_user)):
    await set_group_marking(user.telegram_id, body.enabled)
    return {"success": True}
