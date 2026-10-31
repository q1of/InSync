"""Юнит-тесты чистой логики InSync (без сети).

Запуск из корня проекта:
    venv/Scripts/python -m pytest backend/tests   (Windows)
    venv/bin/python -m pytest backend/tests       (Linux)
"""
import asyncio
import base64

import httpx
import pytest

from backend.services import pulse_auth
from backend.services.mirea_client import extract_qr_token, SessionCrypto
from backend.services.pulse_auth import PulseLogin, _max_skip_url, _otp_challenge
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


def _login_after_skips(max_shows: int, monkeypatch) -> tuple:
    """Цепочка из трейсов: страница MAX показывается max_shows раз, затем form_post в Пульс."""
    posted = []

    async def no_sleep(_):
        return None

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
    monkeypatch.setattr(pulse_auth.asyncio, "sleep", no_sleep)

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
    def test_form_post_is_submitted_after_any_number_of_skips(self, max_shows, monkeypatch):
        out, posted = _login_after_skips(max_shows, monkeypatch)
        assert out.success, out.error
        assert posted == ["code=c&state=s"]

    def test_stuck_max_page_returns_error_without_token_exchange(self, monkeypatch):
        out, posted = _login_after_skips(10_000, monkeypatch)
        assert not out.success
        assert "MAX" in out.error
        assert posted == []

    def test_skip_url_is_unescaped(self):
        html = r'{"a":"https:\/\/sso.mirea.ru\/x?s=A&skip_logout=true","b":"https:\/\/sso.mirea.ru\/x?s=A&skip_logout=false"}'
        assert _max_skip_url(html, "https://sso.mirea.ru/") == "https://sso.mirea.ru/x?s=A&skip_logout=false"
