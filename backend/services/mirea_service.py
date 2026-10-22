"""Логика работы с МИРЭА для InSync — поверх своего клиента (без pymirea).

Отвечает за: вход (логин/пароль + 2FA), шифрование сессий, проверку
живости, профиль, расписание и параллельную отметку посещаемости.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from backend.config import settings
from backend.services.mirea_client import SessionCrypto, get_me, get_schedule, self_approve
from backend.services.pulse_auth import PulseLogin, Challenge, code_hint

logger = logging.getLogger(__name__)

# --- Шифрование сессий (ленивый синглтон) ---
_crypto: SessionCrypto | None = None


def _get_crypto() -> SessionCrypto:
    global _crypto
    if _crypto is None:
        _crypto = SessionCrypto(settings.session_keys)
    return _crypto


def encrypt_session(cookies: dict) -> str:
    return _get_crypto().encrypt(cookies)


def decrypt_session(encrypted: str) -> dict:
    return _get_crypto().decrypt(encrypted) or {}


# --- Незавершённые входы с 2FA ---
# challenge_id -> (PulseLogin, Challenge, owner_id, created_at)
_PENDING_2FA: dict[str, tuple[PulseLogin, Challenge, int | None, float]] = {}
_PENDING_TTL_S = 10 * 60


async def _cleanup_pending() -> None:
    now = time.time()
    for key, (pl, _, _, created) in list(_PENDING_2FA.items()):
        if now - created > _PENDING_TTL_S:
            _PENDING_2FA.pop(key, None)
            await pl.close()


async def _success_payload(cookies: dict) -> dict[str, Any]:
    # Профиль (ФИО/группа) — один лёгкий запрос. Расписание НЕ тянем здесь,
    # чтобы вход был быстрым: оно грузится по запросу через /api/auth/schedule.
    profile = await parse_user_profile(cookies, with_schedule=False)
    return {"success": True, "needs_2fa": False,
            "tokens": profile.get("cookies", cookies),
            "group_name": profile.get("group_name"),
            "full_name": profile.get("full_name")}


async def login(username: str, password: str, owner_id: int | None = None) -> dict[str, Any]:
    """Вход через pulse.mirea.ru.

    {"success": True, "tokens", ...} |
    {"needs_2fa": True, "challenge_id", "hint"} |
    {"success": False, "error"}.
    """
    await _cleanup_pending()
    pl = PulseLogin()
    keep_open = False
    try:
        out = await pl.start(username.strip(), password)
        if out.challenge:
            cid = secrets.token_urlsafe(16)
            _PENDING_2FA[cid] = (pl, out.challenge, owner_id, time.time())
            keep_open = True
            return {"success": False, "needs_2fa": True, "challenge_id": cid,
                    "hint": code_hint(out.challenge)}
        if not out.success:
            return {"success": False, "needs_2fa": False, "error": out.error or "Неверный логин или пароль"}
        return await _success_payload(out.cookies)
    except Exception as e:
        logger.exception("Login failed for %s", username)
        return {"success": False, "needs_2fa": False, "error": f"Ошибка входа: {e}"}
    finally:
        if not keep_open:
            await pl.close()


async def complete_2fa(challenge_id: str, code: str, owner_id: int | None = None) -> dict[str, Any]:
    """Отправка кода подтверждения (письмо / MAX / приложение)."""
    await _cleanup_pending()
    pending = _PENDING_2FA.get(challenge_id)
    if not pending:
        return {"success": False, "error": "Сессия входа истекла, войдите заново"}
    pl, challenge, pending_owner, _ = pending
    if owner_id is not None and pending_owner is not None and owner_id != pending_owner:
        return {"success": False, "error": "Чужой запрос на вход"}

    try:
        out = await pl.submit_code(challenge, code)
        if out.challenge:
            _PENDING_2FA[challenge_id] = (pl, out.challenge, pending_owner, time.time())
            return {"success": False, "error": out.error or "Неверный код, попробуйте ещё раз"}
        if not out.success:
            # Не трогаем pending — дадим ввести код повторно (если TTL не истёк)
            return {"success": False, "error": out.error or "Неверный код"}

        _PENDING_2FA.pop(challenge_id, None)
        await pl.close()
        return await _success_payload(out.cookies)
    except Exception as e:
        logger.exception("2FA completion failed")
        return {"success": False, "error": f"Ошибка входа: {e}"}


async def resend_2fa(challenge_id: str, owner_id: int | None = None) -> dict[str, Any]:
    """Повторно отправить код подтверждения."""
    await _cleanup_pending()
    pending = _PENDING_2FA.get(challenge_id)
    if not pending:
        return {"success": False, "error": "Сессия входа истекла, войдите заново"}
    pl, _, pending_owner, _ = pending
    if owner_id is not None and pending_owner is not None and owner_id != pending_owner:
        return {"success": False, "error": "Чужой запрос на вход"}
    out = await pl.resend_code()
    if out.challenge:
        _PENDING_2FA[challenge_id] = (pl, out.challenge, pending_owner, time.time())
        return {"success": True, "hint": code_hint(out.challenge)}
    return {"success": False, "error": out.error or "Не удалось отправить код повторно"}


async def parse_user_profile(cookies: dict, with_schedule: bool = False) -> dict[str, Any]:
    """ФИО и группа (и опционально расписание) после входа."""
    try:
        me = await get_me(cookies)
        merged = me.get("cookies", cookies)
        sched = []
        if with_schedule:
            try:
                sched = await get_schedule(merged, days=7)
            except Exception as e:
                logger.warning("schedule fetch failed: %s", e)
        return {"group_name": me.get("group_name"), "full_name": me.get("full_name"),
                "cookies": merged, "schedule": sched}
    except Exception as e:
        logger.warning("Failed to parse user profile: %s", e)
        return {"group_name": None, "full_name": None, "cookies": cookies, "schedule": []}


async def check_session(cookies: dict) -> dict[str, Any]:
    """Живость сессии Пульса. Может вернуть обновлённые cookies."""
    try:
        me = await get_me(cookies)
        refreshed_at = int(cookies.get("__token_refreshed_at") or 0)
        age = int(time.time()) - refreshed_at if refreshed_at else 0
        return {"valid": me["valid"], "age_seconds": age, "cookies": me.get("cookies"),
                "full_name": me.get("full_name"), "group_name": me.get("group_name"),
                "error": me.get("error")}
    except Exception as e:
        logger.error("Check session failed: %s", e)
        return {"valid": False, "age_seconds": 0, "cookies": None, "error": str(e)}


async def fetch_schedule(cookies: dict, days: int = 7) -> list[dict]:
    return await get_schedule(cookies, days=days)


async def mark_attendance_parallel(
    qr_data: str,
    sessions: list[tuple[int, str, dict]],
) -> list[dict[str, Any]]:
    """Параллельная отметка за себя и друзей.

    sessions: [(telegram_id, display_name, decrypted_cookies), ...]
    """
    async def _one(telegram_id: int, name: str, cookies: dict) -> dict:
        try:
            ok, err = await self_approve(cookies, qr_data)
            return {"telegram_id": telegram_id, "user_name": name,
                    "success": ok, "error": None if ok else (err or "Не удалось отметить")}
        except Exception as e:
            logger.error("Attendance failed for %s: %s", telegram_id, e)
            return {"telegram_id": telegram_id, "user_name": name, "success": False, "error": str(e)}

    return list(await asyncio.gather(*[_one(t, n, c) for t, n, c in sessions]))
