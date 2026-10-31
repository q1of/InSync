"""Юнит-тесты чистой логики InSync (без сети).

Запуск из корня проекта:
    venv/Scripts/python -m pytest backend/tests   (Windows)
    venv/bin/python -m pytest backend/tests       (Linux)
"""
import asyncio
import base64

import httpx
import pytest

from backend import main as app_main
from backend.middleware.telegram_auth import TelegramUser
from backend.models import AttendanceRequest
from backend.routes import attendance, auth
from backend.services import mirea_service, pulse_auth
from backend.services.mirea_client import (
    PulseClient, SESSION_EXPIRED, SessionCrypto, extract_qr_token, is_session_expired,
)
from backend.services.pulse_auth import PulseLogin, _otp_challenge
from backend.routes.attendance import _is_retriable

UUID = "12345678-1234-1234-1234-123456789abc"


def _kc_html(page_id: str, action: str = "https://sso.mirea.ru/x/authenticate?session_code=abc") -> str:
    return '<script>window.kc={"pageId":"%s","loginAction":"%s"};</script>' % (page_id, action)


class TestExtractQrToken:
    def test_query_token(self):
        assert extract_qr_token(f"https://pulse.mirea.ru/x?token={UUID}") == UUID

    def test_path_uuid(self):
        # Реальный формат QR Пульса — UUID в пути (раньше не распознавался).
        assert extract_qr_token(f"https://pulse.mirea.ru/students/self-approve/{UUID}") == UUID

    def test_bare_uuid(self):
        assert extract_qr_token(UUID) == UUID

    def test_uppercase_in_path(self):
        u = UUID.upper()
        assert extract_qr_token(f"https://pulse.mirea.ru/x/{u}") == u

    def test_invalid(self):
        assert extract_qr_token("https://example.com/no-uuid-here") is None
        assert extract_qr_token("") is None
        assert extract_qr_token(None) is None


class TestSessionCrypto:
    def test_roundtrip(self):
        c = SessionCrypto("test-key-123")
        data = {"a": "1", "cookie": "value", "юни": "код"}
        enc = c.encrypt(data)
        assert isinstance(enc, str)
        assert c.decrypt(enc) == data

    def test_wrong_key_returns_none(self):
        enc = SessionCrypto("key-a").encrypt({"x": "1"})
        assert SessionCrypto("key-b").decrypt(enc) is None

    def test_decrypt_garbage(self):
        c = SessionCrypto("k")
        assert c.decrypt("not-a-token") is None
        assert c.decrypt("") is None

    def test_ready_32byte_key(self):
        key = base64.urlsafe_b64encode(b"x" * 32).decode()
        c = SessionCrypto(key)
        assert c.decrypt(c.encrypt({"k": "v"})) == {"k": "v"}


class TestOtpChallenge:
    def test_max_otp_is_generic_code_form(self):
        # Никакой спец-обработки MAX: login-max-otp ловится как обычная форма кода.
        ch = _otp_challenge(_kc_html("login-max-otp"), "https://sso.mirea.ru/x")
        assert ch is not None
        assert ch.field_name == "otp"

    def test_email_code_form(self):
        ch = _otp_challenge(_kc_html("email-code-form"), "https://sso.mirea.ru/x")
        assert ch is not None
        assert ch.field_name == "emailCode"
        assert ch.kind == "email_code"

    def test_non_otp_page(self):
        assert _otp_challenge(_kc_html("login-username"), "https://sso.mirea.ru/x") is None


class TestIsRetriable:
    def test_success_not_retriable(self):
        assert _is_retriable(True, None) is False

    def test_dead_session_not_retriable(self):
        assert _is_retriable(False, "Сессия истекла") is False
        assert _is_retriable(False, "Не удалось распознать QR-код") is False
        assert _is_retriable(False, "Войдите заново") is False
        assert _is_retriable(False, "Нужно перезайти") is False

    def test_temporary_retriable(self):
        assert _is_retriable(False, "Пара ещё не началась") is True
        assert _is_retriable(False, "Ошибка сети") is True
        assert _is_retriable(False, None) is True


MAX_URL = "https://sso.mirea.ru/realms/mirea/login-actions/required-action?execution=max-account-config"
MAX_PAGE = (
    '<script>{"pageId": "login-max-otp", "showSkip": true, '
    '"loginAction": "https://sso.mirea.ru/realms/mirea/login-actions/required-action'
    '?session_code=S\\u0026execution=max-account-config"}</script>'
)
FORM_POST_PAGE = (
    '<form method="post" action="https://pulse.mirea.ru/api/mireaauth">'
    '<input type="hidden" name="code" value="c"><input type="hidden" name="state" value="s"></form>'
)


def _login_after_skips(max_shows: int) -> tuple:
    """Цепочка из трейсов: страница MAX показывается max_shows раз, затем form_post в Пульс."""
    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/mireaauth":
            posted.append(request.content.decode())
            return httpx.Response(302, headers={
                "Location": "https://pulse.mirea.ru/services",
                "Set-Cookie": "Pulse.Auth.Cookie=x; Path=/",
            })
        if request.url.path == "/services":
            return httpx.Response(200, text="ok")
        handler.shows += 1
        return httpx.Response(200, text=MAX_PAGE if handler.shows <= max_shows else FORM_POST_PAGE)

    handler.shows = 0

    async def run():
        pl = PulseLogin()
        await pl.client.aclose()
        pl.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        try:
            return await pl._analyze(await pl.client.get(MAX_URL), "code")
        finally:
            await pl.close()

    return asyncio.run(run()), posted


class TestMaxSkip:
    @pytest.mark.parametrize("max_shows", [1, 4, 5, 9, 20])
    def test_form_post_is_submitted_after_any_number_of_skips(self, max_shows):
        out, posted = _login_after_skips(max_shows)
        assert out.success, out.error
        assert posted == ["code=c&state=s"]

    def test_stuck_max_page_returns_error_without_token_exchange(self):
        out, posted = _login_after_skips(10_000)
        assert not out.success
        assert "MAX" in out.error
        assert posted == []

    def test_skip_button_is_post_to_login_action(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path, request.content.decode()))
            if request.url.path == "/api/mireaauth":
                return httpx.Response(302, headers={
                    "Location": "https://pulse.mirea.ru/services",
                    "Set-Cookie": "Pulse.Auth.Cookie=x; Path=/",
                })
            if request.url.path == "/services":
                return httpx.Response(200, text="ok")
            if request.method == "POST":
                return httpx.Response(200, text=FORM_POST_PAGE)
            return httpx.Response(200, text=MAX_PAGE)

        async def run():
            pl = PulseLogin()
            await pl.client.aclose()
            pl.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            try:
                return await pl._analyze(await pl.client.get(MAX_URL), "code")
            finally:
                await pl.close()

        out = asyncio.run(run())
        assert out.success, out.error
        assert ("POST", "/realms/mirea/login-actions/required-action", "skip=true") in requests
        assert sum(1 for method, path, _ in requests if method == "GET" and "required-action" in path) == 1

    def test_max_code_form_without_skip_asks_for_code(self):
        html = MAX_PAGE.replace('"showSkip": true', '"showSkip": false')
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.method)
            return httpx.Response(200, text=html)

        async def run():
            pl = PulseLogin()
            await pl.client.aclose()
            pl.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
            try:
                return await pl._analyze(await pl.client.get(MAX_URL), "code")
            finally:
                await pl.close()

        out = asyncio.run(run())
        assert out.challenge is not None
        assert calls == ["GET"]


class TestMarkWithoutSession:
    def test_friend_without_session_gets_explicit_result(self, monkeypatch):
        async def no_sessions(_):
            return []

        async def no_log(*_):
            return None

        monkeypatch.setattr(attendance.session_cache, "get_all_sessions_for_mark", no_sessions)
        monkeypatch.setattr(attendance, "_log_and_notify", no_log)
        user = TelegramUser(telegram_id=1, display_name="Я")

        async def run():
            return await attendance.mark_attendance(AttendanceRequest(qr_data="x", only=[2]), user)

        res = asyncio.run(run())
        assert [(r.telegram_id, r.success, r.retriable) for r in res.results] == [(2, False, False)]


class TestSessionExpiredDetection:
    @pytest.mark.parametrize("status,headers,expected", [
        (401, {}, SESSION_EXPIRED),
        (200, {"grpc-status": "16"}, SESSION_EXPIRED),
        (503, {}, "Ошибка сервера: 503"),
        (403, {}, "Ошибка сервера: 403"),
    ])
    def test_grpc_response(self, status, headers, expected):
        async def run():
            cli = PulseClient({})
            cli.client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(status, headers=headers)))
            try:
                return await cli.grpc("https://pulse.mirea.ru/x", b"")
            finally:
                await cli.client.aclose()

        assert asyncio.run(run())[1] == expected

    def test_pulse_text_is_not_treated_as_expired_session(self):
        assert not is_session_expired("Срок действия QR-кода истек")
        assert not is_session_expired(None)
        assert is_session_expired(SESSION_EXPIRED)


class TestMorningSessionCheck:
    def test_failed_checks_are_retried(self, monkeypatch):
        calls = []

        async def users():
            return [{"telegram_id": 1, "encrypted_session": "a"}, {"telegram_id": 2, "encrypted_session": "b"}]

        async def check(tid, _):
            calls.append(tid)
            return tid == 2 and calls.count(2) < 2

        real_sleep = asyncio.sleep

        async def no_sleep(_):
            await real_sleep(0)

        monkeypatch.setattr(app_main, "get_active_session_users", users)
        monkeypatch.setattr(app_main, "_check_one_session", check)
        monkeypatch.setattr(app_main.asyncio, "sleep", no_sleep)
        asyncio.run(app_main._check_all_sessions())
        assert sorted(calls) == [1, 2, 2]

    @pytest.mark.parametrize("error,dead", [("Ошибка сети: ConnectError", False), (SESSION_EXPIRED, True)])
    def test_only_dead_session_is_invalidated(self, error, dead, monkeypatch):
        invalidated = []

        async def check(_):
            return {"valid": False, "error": error}

        async def mark_invalid(tid):
            invalidated.append(tid)

        monkeypatch.setattr(mirea_service, "decrypt_session", lambda _: {"a": "1"})
        monkeypatch.setattr(mirea_service, "check_session", check)
        monkeypatch.setattr(app_main, "set_session_invalid", mark_invalid)
        monkeypatch.setattr(app_main, "bot", None)
        asyncio.run(app_main._check_one_session(7, "enc"))
        assert invalidated == ([7] if dead else [])


class TestScheduleCache:
    def test_empty_schedule_is_not_cached(self, monkeypatch):
        async def session(_):
            return {"a": "1"}

        async def empty(*_, **__):
            return []

        auth._schedule_cache.clear()
        monkeypatch.setattr(auth.session_cache, "get_session", session)
        monkeypatch.setattr(mirea_service, "fetch_schedule", empty)
        asyncio.run(auth.schedule(7, TelegramUser(telegram_id=1, display_name="Я")))
        assert auth._schedule_cache == {}


class TestDebugFlag:
    @pytest.mark.parametrize("value,enabled", [("0", False), ("", False), ("1", True), ("true", True)])
    def test_only_explicit_value_enables_debug(self, value, enabled, monkeypatch):
        import importlib
        monkeypatch.setenv("PULSE_DEBUG", value)
        assert importlib.reload(pulse_auth).DEBUG_ENABLED is enabled
        monkeypatch.undo()
        importlib.reload(pulse_auth)
