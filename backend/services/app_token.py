"""Токен авторизации для Android-приложения.

Telegram-версия опознаёт пользователя по подписи initData. У приложения такой
подписи нет, поэтому после входа в Пульс сервер выдаёт свой токен, привязанный
к id аккаунта. Токен подписан HMAC (ключ производный от SESSION_KEYS) — подделать
нельзя, расшифровывать нечего (внутри только id аккаунта и время выпуска).

Своя реализация без PyJWT — проект и так считает HMAC вручную (подпись Telegram).
"""
import base64
import hashlib
import hmac
import json
import time

from backend.config import settings


def _secret() -> bytes:
    # Отдельный ключ, производный от SESSION_KEYS — сам Fernet-ключ не используем.
    return hashlib.sha256(("insync-app-token:" + (settings.session_keys or "")).encode()).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_app_token(account_id: int) -> str:
    """Подписанный токен для аккаунта (без срока годности — это токен устройства;
    реальный доступ всё равно ограничен живостью сессии Пульса)."""
    body = _b64e(json.dumps({"aid": account_id, "iat": int(time.time())},
                            separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_app_token(token: str) -> int:
    """Возвращает id аккаунта из токена. Бросает ValueError, если подпись неверна."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        raise ValueError("Неверный токен")
    expected = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Неверная подпись токена")
    try:
        payload = json.loads(_b64d(body))
        aid = payload["aid"]
    except Exception:
        raise ValueError("Повреждённый токен")
    if not isinstance(aid, int):
        raise ValueError("Повреждённый токен")
    return aid
