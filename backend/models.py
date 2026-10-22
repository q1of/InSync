from pydantic import BaseModel
from typing import Optional, List

class UserInfo(BaseModel):
    display_name: str
    group_name: Optional[str] = None
    full_name: Optional[str] = None
    session_valid: bool = False
    session_updated_at: Optional[int] = None

class LoginRequest(BaseModel):
    username: str
    password: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "username": "ivanov_i_i",
                    "password": "secretpassword123"
                }
            ]
        }
    }

class TwoFARequest(BaseModel):
    challenge_id: str
    code: str
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "challenge_id": "auth_12345",
                    "code": "123456"
                }
            ]
        }
    }

class LoginResponse(BaseModel):
    success: bool
    needs_2fa: bool = False
    challenge_id: Optional[str] = None
    hint: Optional[str] = None          # куда отправлен код 2FA
    user_info: Optional[UserInfo] = None
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "needs_2fa": False,
                    "user_info": {
                        "display_name": "Ivan",
                        "group_name": "IKBO-01-22",
                        "full_name": "Ivanov Ivan Ivanovich",
                        "session_valid": True,
                        "session_updated_at": 1690000000
                    }
                }
            ]
        }
    }

class FriendRequest(BaseModel):
    friend_telegram_id: int
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"friend_telegram_id": 123456789}
            ]
        }
    }

class AttendanceRequest(BaseModel):
    qr_data: str
    # Подмножество telegram_id для отметки (повтор только тем, кто ещё не отметился).
    # None — отметить всех (себя + включённых друзей).
    only: Optional[List[int]] = None
    # Идентификатор одной сессии сканирования: объединяет все волны повторов
    # (QR на экране меняется каждые 5с, поэтому группируем не по токену).
    scan_id: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"qr_data": "https://pulse.mirea.ru/attendance/..."}
            ]
        }
    }

class AttendanceResult(BaseModel):
    user_name: str
    telegram_id: int
    success: bool
    error: Optional[str] = None
    # Есть ли смысл повторить на следующем QR (временный сбой), или нет (нужен перезаход)
    retriable: bool = False
    # Пользователь сам отключил отметку на этой паре — не отмечаем, не ошибка
    opted_out: bool = False

class AttendanceResponse(BaseModel):
    results: List[AttendanceResult]
    total: int
    successful: int
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "results": [
                        {
                            "user_name": "Ivan",
                            "telegram_id": 123456789,
                            "success": True,
                            "error": None
                        }
                    ],
                    "total": 1,
                    "successful": 1
                }
            ]
        }
    }

class OptoutRequest(BaseModel):
    start_epoch: int                     # начало пары — идентификатор занятия
    end_epoch: Optional[int] = None      # конец пары (окно «идёт сейчас»)
    lesson_name: Optional[str] = None
    enabled: bool = True                 # True — отключить отметку, False — вернуть

class SessionStatus(BaseModel):
    logged_in: bool = False          # есть сохранённая сессия Пульса
    valid: bool = False              # сессия реально живая (проверено на pulse.mirea.ru)
    updated_at: Optional[int] = None
    expires_in_seconds: Optional[int] = None
    full_name: Optional[str] = None
    group_name: Optional[str] = None
    mirea_login: Optional[str] = None
