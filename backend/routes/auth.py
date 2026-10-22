import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.models import (
    LoginRequest, LoginResponse, TwoFARequest,
    SessionStatus, UserInfo,
)
from backend.middleware.telegram_auth import get_current_user, TelegramUser
from backend.services import mirea_service
from backend.services.session_cache import session_cache
from backend.database import upsert_user, update_session, get_user, clear_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Короткие кеши, чтобы не дёргать Пульс на каждый открытый профиль.
_SESSION_CHECK_TTL = 60      # проверка живости сессии
_SCHEDULE_TTL = 3600         # расписание меняется редко
_session_check_cache: dict[int, tuple[float, dict]] = {}
_schedule_cache: dict[tuple[int, int], tuple[float, list]] = {}


async def _finish_login(user: TelegramUser, result: dict, mirea_login: str | None) -> LoginResponse:
    """Сохраняет токены Пульса после успешного входа (с 2FA или без)."""
    tokens = result["tokens"]
    encrypted = mirea_service.encrypt_session(tokens)

    await upsert_user(
        telegram_id=user.telegram_id,
        display_name=user.display_name,
        mirea_login=mirea_login,
        group_name=result.get("group_name"),
        full_name=result.get("full_name"),
        username=user.username,
    )
    await update_session(user.telegram_id, encrypted)
    await session_cache.set_decrypted(user.telegram_id, tokens)

    from backend.main import bot
    if bot:
        try:
            await bot.send_message(
                user.telegram_id,
                f"✔ Вход через Пульс МИРЭА выполнен!\n\n"
                f"{result.get('full_name') or user.display_name}, теперь можно пользоваться InSync.",
            )
        except Exception:
            pass

    return LoginResponse(
        success=True,
        needs_2fa=False,
        user_info=UserInfo(
            display_name=user.display_name,
            group_name=result.get("group_name"),
            full_name=result.get("full_name"),
            session_valid=True,
        ),
    )


# Логин нужен между /login и /2fa, чтобы сохранить его в БД
_pending_logins: dict[str, str] = {}


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, user: TelegramUser = Depends(get_current_user)):
    """Вход логином/паролем Пульса МИРЭА. Если МИРЭА просит код из почты — вернёт challenge_id."""
    result = await mirea_service.login(request.username, request.password, owner_id=user.telegram_id)

    if result.get("needs_2fa"):
        _pending_logins[result["challenge_id"]] = request.username.strip()
        return LoginResponse(success=False, needs_2fa=True, challenge_id=result["challenge_id"],
                             hint=result.get("hint"))

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Не удалось войти"))

    return await _finish_login(user, result, request.username.strip())


class ResendBody(BaseModel):
    challenge_id: str


@router.post("/2fa/resend")
async def resend_code(body: ResendBody, user: TelegramUser = Depends(get_current_user)):
    """Повторно отправить код подтверждения на почту."""
    result = await mirea_service.resend_2fa(body.challenge_id, owner_id=user.telegram_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Не удалось отправить код"))
    return {"success": True, "hint": result.get("hint")}


@router.post("/2fa", response_model=LoginResponse)
async def complete_2fa(request: TwoFARequest, user: TelegramUser = Depends(get_current_user)):
    """Код подтверждения из письма МИРЭА."""
    result = await mirea_service.complete_2fa(request.challenge_id, request.code, owner_id=user.telegram_id)
    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Неверный код"))

    mirea_login = _pending_logins.pop(request.challenge_id, None)
    return await _finish_login(user, result, mirea_login)


@router.post("/logout")
async def logout(user: TelegramUser = Depends(get_current_user)):
    """Выход — удаляет сессию из БД и кеша."""
    await clear_session(user.telegram_id)
    await session_cache.invalidate(user.telegram_id)
    _session_check_cache.pop(user.telegram_id, None)
    for k in [k for k in _schedule_cache if k[0] == user.telegram_id]:
        _schedule_cache.pop(k, None)
    return {"success": True}


@router.get("/session", response_model=SessionStatus)
async def get_session_status(check: bool = True, user: TelegramUser = Depends(get_current_user)):
    """Статус сессии Пульса. check=false — без запроса к МИРЭА (быстро)."""
    db_user = await get_user(user.telegram_id)
    if not db_user or not db_user["encrypted_session"]:
        return SessionStatus(logged_in=False, valid=False)

    base = dict(
        logged_in=True,
        updated_at=db_user["session_updated_at"],
        full_name=db_user["full_name"],
        group_name=db_user["group_name"],
        mirea_login=db_user["mirea_login"],
    )

    cookies = await session_cache.get_session(user.telegram_id)
    if not cookies:
        try:
            cookies = mirea_service.decrypt_session(db_user["encrypted_session"])
        except Exception:
            cookies = None
        if not cookies:
            return SessionStatus(**{**base, "logged_in": False})
        await session_cache.set_decrypted(user.telegram_id, cookies)

    if not check:
        return SessionStatus(**base, valid=True)

    # Кеш проверки живости на 60с — профиль не дёргает Пульс на каждом открытии.
    cached = _session_check_cache.get(user.telegram_id)
    if cached and time.time() - cached[0] < _SESSION_CHECK_TTL:
        status = cached[1]
    else:
        status = await mirea_service.check_session(cookies)
        _session_check_cache[user.telegram_id] = (time.time(), status)
        new_cookies = status.get("cookies")
        if status["valid"] and new_cookies and new_cookies != cookies:
            # Пульс выдал свежую .AspNetCore.Cookies — сохраняем
            await update_session(user.telegram_id, mirea_service.encrypt_session(new_cookies))
            await session_cache.set_decrypted(user.telegram_id, new_cookies)
        if status["valid"]:
            await upsert_user(user.telegram_id, user.display_name,
                              group_name=status.get("group_name"), full_name=status.get("full_name"),
                              username=user.username)

    if status["valid"]:
        base["full_name"] = status.get("full_name") or base["full_name"]
        base["group_name"] = status.get("group_name") or base["group_name"]

    return SessionStatus(
        **base,
        valid=status["valid"],
        expires_in_seconds=None,
    )


@router.get("/schedule")
async def schedule(days: int = 7, user: TelegramUser = Depends(get_current_user)):
    """Расписание студента на N дней. Кешируется на час (меняется редко)."""
    days = max(1, min(days, 14))
    key = (user.telegram_id, days)
    cached = _schedule_cache.get(key)
    if cached and time.time() - cached[0] < _SCHEDULE_TTL:
        return {"lessons": cached[1]}

    cookies = await session_cache.get_session(user.telegram_id)
    if not cookies:
        db_user = await get_user(user.telegram_id)
        if db_user and db_user["encrypted_session"]:
            try:
                cookies = mirea_service.decrypt_session(db_user["encrypted_session"])
            except Exception:
                cookies = None
            if cookies:
                await session_cache.set_decrypted(user.telegram_id, cookies)
    if not cookies:
        raise HTTPException(status_code=401, detail="Нет активной сессии")
    lessons = await mirea_service.fetch_schedule(cookies, days=days)
    _schedule_cache[key] = (time.time(), lessons)
    return {"lessons": lessons}
