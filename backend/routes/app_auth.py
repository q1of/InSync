"""Вход для Android-приложения.

Отличие от /api/auth (Telegram-версия): у приложения нет подписи Telegram, поэтому
эти роуты НЕ требуют авторизации заранее — они сами логинят в Пульс, находят или
заводят аккаунт по логину Пульса (общий с Telegram-версией) и возвращают токен.
Весь дальнейший доступ идёт по этому токену: "Authorization: Bearer <token>".
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.models import LoginRequest, TwoFARequest, UserInfo
from backend.services import mirea_service
from backend.services.session_cache import session_cache
from backend.services.app_token import make_app_token
from backend.database import get_or_create_app_account, update_session, get_user, notif_enabled

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/app", tags=["app-auth"])

# challenge_id -> логин Пульса (между /login и /2fa, чтобы запомнить логин).
_pending_logins: dict[str, str] = {}


class AppResendBody(BaseModel):
    challenge_id: str


async def _finish_app_login(result: dict, mirea_login: str) -> dict:
    """Сохраняет сессию Пульса и выдаёт токен приложения."""
    tokens = result["tokens"]
    encrypted = mirea_service.encrypt_session(tokens)

    account_id = await get_or_create_app_account(
        mirea_login, display_name=mirea_login, group_name=result.get("group_name")
    )
    await update_session(account_id, encrypted)
    await session_cache.set_decrypted(account_id, tokens)

    row = await get_user(account_id)
    display_name = (row["display_name"] if row else None) or mirea_login

    # Если аккаунт уже привязан к Telegram — продублируем уведомление о входе в бот.
    if account_id > 0:
        from backend.main import bot
        if bot and await notif_enabled(account_id, "login"):
            try:
                await bot.send_message(
                    account_id,
                    "👋 <b>Вход выполнен</b>\nВы вошли в InSync через приложение.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    return {
        "success": True,
        "needs_2fa": False,
        "token": make_app_token(account_id),
        "user_info": UserInfo(
            display_name=display_name,
            group_name=result.get("group_name"),
            session_valid=True,
        ),
    }


@router.post("/login")
async def app_login(request: LoginRequest):
    """Вход логином/паролем Пульса из приложения. Может вернуть challenge на 2FA."""
    result = await mirea_service.login(request.username, request.password)

    if result.get("needs_2fa"):
        _pending_logins[result["challenge_id"]] = request.username.strip()
        return {"success": False, "needs_2fa": True,
                "challenge_id": result["challenge_id"], "hint": result.get("hint")}

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Не удалось войти"))

    return await _finish_app_login(result, request.username.strip())


@router.post("/2fa")
async def app_complete_2fa(request: TwoFARequest):
    """Код подтверждения из письма/приложения МИРЭА (вход из приложения)."""
    result = await mirea_service.complete_2fa(request.challenge_id, request.code)
    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Неверный код"))

    mirea_login = _pending_logins.pop(request.challenge_id, None)
    if not mirea_login:
        raise HTTPException(status_code=400, detail="Сессия входа истекла, войдите заново")
    return await _finish_app_login(result, mirea_login)


@router.post("/2fa/resend")
async def app_resend_code(body: AppResendBody):
    """Повторно отправить код подтверждения (вход из приложения)."""
    result = await mirea_service.resend_2fa(body.challenge_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не удалось отправить код"))
    return {"success": True, "hint": result.get("hint")}
