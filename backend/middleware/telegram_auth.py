import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl
from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from backend.config import settings
from backend.services.app_token import verify_app_token
from backend.database import get_user

class TelegramUser(BaseModel):
    telegram_id: int
    display_name: str
    username: str | None = None

init_data_header = APIKeyHeader(name="X-Telegram-Init-Data", auto_error=False)
authorization_header = APIKeyHeader(name="Authorization", auto_error=False)

def verify_init_data(init_data: str) -> dict:
    parsed_data = dict(parse_qsl(init_data))
    if 'hash' not in parsed_data:
        raise ValueError("Missing hash in initData")
    
    received_hash = parsed_data.pop('hash')
    
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed_data.items())
    )
    
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if calculated_hash != received_hash:
        raise ValueError("Invalid hash")
    
    auth_date = int(parsed_data.get('auth_date', 0))
    if time.time() - auth_date > 86400:
        raise ValueError("Init data expired")
        
    user_data = json.loads(parsed_data.get('user', '{}'))
    return user_data

async def _user_from_app_token(token: str) -> TelegramUser:
    """Опознаёт пользователя Android-приложения по его токену."""
    try:
        account_id = verify_app_token(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    row = await get_user(account_id)
    if not row:
        raise HTTPException(status_code=401, detail="Аккаунт не найден")
    display_name = row["display_name"] or str(account_id)
    return TelegramUser(telegram_id=account_id, display_name=display_name, username=row["username"])


async def get_current_user(
    x_telegram_init_data: str = Security(init_data_header),
    authorization: str = Security(authorization_header)
) -> TelegramUser:
    init_data = x_telegram_init_data
    # Android-приложение присылает свой токен: "Authorization: Bearer <token>".
    if authorization and authorization.startswith("Bearer "):
        return await _user_from_app_token(authorization[7:])
    if not init_data and authorization and authorization.startswith("tma "):
        init_data = authorization[4:]

    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram init data")

    try:
        user_data = verify_init_data(init_data)
        telegram_id = user_data.get('id')
        if not telegram_id:
            raise ValueError("No user id found")
            
        first_name = user_data.get('first_name', '')
        last_name = user_data.get('last_name', '')
        display_name = f"{first_name} {last_name}".strip() or str(telegram_id)
        username = user_data.get('username') or None
        
        return TelegramUser(telegram_id=telegram_id, display_name=display_name, username=username)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
