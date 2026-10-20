"""Юнит-тесты чистой логики InSync (без сети).

Запуск из корня проекта:
    venv/Scripts/python -m pytest backend/tests   (Windows)
    venv/bin/python -m pytest backend/tests       (Linux)
"""
import base64

from backend.services.mirea_client import extract_qr_token, SessionCrypto
from backend.services.pulse_auth import _otp_challenge, code_hint
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
    def test_max_otp_is_code_form(self):
        # login-max-otp теперь распознаётся как форма ввода кода из MAX (а не «скип»).
        ch = _otp_challenge(_kc_html("login-max-otp"), "https://sso.mirea.ru/x")
        assert ch is not None
        assert ch.field_name == "otp"
        assert ch.kind == "max"
        assert code_hint(ch) == "Введите код из приложения MAX"

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
