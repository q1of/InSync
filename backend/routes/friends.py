from fastapi import APIRouter, Depends
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services import friend_service
from pydantic import BaseModel


class RequestIdBody(BaseModel):
    request_id: int

router = APIRouter(prefix="/api/friends", tags=["friends"])

@router.get("/")
async def list_friends(user: TelegramUser = Depends(get_current_user)):
    return await friend_service.get_friends(user.telegram_id)

class FriendRequestBody(BaseModel):
    identifier: str   # @username или числовой Telegram ID


@router.post("/request")
async def send_request(body: FriendRequestBody, user: TelegramUser = Depends(get_current_user)):
    to_id = await friend_service.resolve_user(body.identifier)
    if to_id is None:
        return {"success": False, "error": "Пользователь не найден. Он должен хотя бы раз войти в InSync."}
    return await friend_service.send_request(user.telegram_id, to_id)

@router.get("/pending")
async def pending_requests(user: TelegramUser = Depends(get_current_user)):
    return await friend_service.get_pending_requests(user.telegram_id)

@router.post("/accept")
async def accept_request(body: RequestIdBody, user: TelegramUser = Depends(get_current_user)):
    return await friend_service.accept_request(user.telegram_id, body.request_id)

@router.post("/reject")
async def reject_request(body: RequestIdBody, user: TelegramUser = Depends(get_current_user)):
    return await friend_service.reject_request(user.telegram_id, body.request_id)

class MarkingBody(BaseModel):
    friend_telegram_id: int
    enabled: bool


@router.post("/marking")
async def set_marking(body: MarkingBody, user: TelegramUser = Depends(get_current_user)):
    """Включить/выключить отметку друга при скане."""
    return await friend_service.set_marking(user.telegram_id, body.friend_telegram_id, body.enabled)


@router.delete("/{friend_id}")
async def remove_friend(friend_id: int, user: TelegramUser = Depends(get_current_user)):
    return await friend_service.remove_friend(user.telegram_id, friend_id)
