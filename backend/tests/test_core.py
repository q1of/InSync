"""Юнит-тесты чистой логики InSync (без сети).

Запуск из корня проекта:
    venv/Scripts/python -m pytest backend/tests   (Windows)
    venv/bin/python -m pytest backend/tests       (Linux)
"""
import asyncio
import base64

import httpx
import pytest

from backend import database, main as app_main
from backend.middleware.telegram_auth import TelegramUser
from backend.models import AttendanceRequest
from backend.routes import attendance, auth
from backend.services import acs as acs_mod, mirea_client, mirea_service, pulse_auth
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


class _no_db:
    """Заглушка get_db: запись истории в этих тестах не проверяется."""

    async def __aenter__(self):
        raise RuntimeError("no db")

    async def __aexit__(self, *_):
        return False


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


class TestApproveRefresh:
    QR = "12345678-1234-1234-1234-123456789abc"
    APPROVED = b"\x00\x00\x00\x00\x02\x12\x00" + b"\x80\x00\x00\x00\x0e" + b"grpc-status:0\r\n"[:14]

    def _handler(self, sso_page: str):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            path, cookie = request.url.path, request.headers.get("cookie", "")
            calls.append(path)
            if path.endswith("SelfApproveAttendanceThroughQRCode"):
                if "Pulse.Auth.Cookie=new" in cookie:
                    return httpx.Response(200, content=self.APPROVED)
                return httpx.Response(401)
            if path == "/api/auth/login":
                return httpx.Response(302, headers={"Location": "https://sso.mirea.ru/realms/mirea/x"})
            if path == "/realms/mirea/x":
                return httpx.Response(200, text=sso_page)
            if path == "/api/mireaauth":
                return httpx.Response(302, headers={
                    "Location": "https://pulse.mirea.ru/services",
                    "Set-Cookie": "Pulse.Auth.Cookie=new; Path=/",
                })
            return httpx.Response(200, text="ok")

        handler.calls = calls
        return handler

    def _approve(self, handler, cookies, monkeypatch):
        monkeypatch.setattr(mirea_client, "_pulse_transport", httpx.MockTransport(handler))
        return asyncio.run(mirea_client.self_approve(cookies, self.QR))[:3]

    def test_expired_cookie_is_refreshed_and_mark_retried(self, monkeypatch):
        handler = self._handler(FORM_POST_PAGE)
        ok, err, cookies = self._approve(
            handler, {"Pulse.Auth.Cookie": "old", "KEYCLOAK_IDENTITY": "id"}, monkeypatch)
        assert (ok, err) == (True, None)
        assert cookies["Pulse.Auth.Cookie"] == "new"
        assert cookies["KEYCLOAK_IDENTITY"] == "id"
        assert handler.calls.count("/api/mireaauth") == 1

    def test_no_identity_means_no_refresh(self, monkeypatch):
        handler = self._handler(FORM_POST_PAGE)
        ok, err, cookies = self._approve(handler, {"Pulse.Auth.Cookie": "old"}, monkeypatch)
        assert (ok, err, cookies) == (False, SESSION_EXPIRED, None)
        assert "/api/auth/login" not in handler.calls

    def test_dead_identity_stays_expired(self, monkeypatch):
        handler = self._handler("<html>форма входа</html>")
        ok, err, cookies = self._approve(
            handler, {"Pulse.Auth.Cookie": "old", "KEYCLOAK_IDENTITY": "id"}, monkeypatch)
        assert (ok, err, cookies) == (False, SESSION_EXPIRED, None)

    def test_max_skip_page_is_passed_during_refresh(self, monkeypatch):
        posted = []
        base = self._handler(FORM_POST_PAGE)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/realms/mirea/x":
                return httpx.Response(200, text=MAX_PAGE)
            if request.url.path.endswith("required-action") and request.method == "POST":
                posted.append(request.content.decode())
                return httpx.Response(200, text=FORM_POST_PAGE)
            return base(request)

        ok, err, _ = self._approve(handler, {"Pulse.Auth.Cookie": "old", "KEYCLOAK_IDENTITY": "id"}, monkeypatch)
        assert (ok, err, posted) == (True, None, ["skip=true"])

    def test_refreshed_cookies_are_saved(self, monkeypatch):
        saved = []

        async def save(tid, enc):
            saved.append(tid)

        async def no_notify(*_):
            return None

        monkeypatch.setattr(attendance, "update_session", save)
        monkeypatch.setattr(attendance, "encrypt_session", lambda c: "enc")
        monkeypatch.setattr(attendance, "_notify_marked_friends", no_notify)
        results = [{"telegram_id": 5, "user_name": "Друг", "success": True, "cookies": {"a": "1"}}]
        asyncio.run(attendance._log_and_notify(TelegramUser(telegram_id=1, display_name="Я"), results, None))
        assert saved == [5]


LESSON = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
OTHER_LESSON = "cccccccc-1111-2222-3333-dddddddddddd"


class TestApprovedLessonId:
    def test_uuid_is_read_from_approved(self):
        inner = b"\x0a\x24" + LESSON.encode()
        assert mirea_client._approved_lesson_id(b"\x12\x26" + inner) == LESSON

    def test_empty_approved_has_no_id(self):
        assert mirea_client._approved_lesson_id(b"\x12\x00") is None

    @pytest.mark.parametrize("error,expected", [
        ("Вы уже отмечены на этом занятии", True),
        ("Посещение уже подтверждено", True),
        ("Занятие уже закончилось", False),
        (None, False),
    ])
    def test_already_marked_text(self, error, expected):
        assert mirea_client.is_already_marked(error) is expected


class TestRepeatMarks:
    USER = TelegramUser(telegram_id=1, display_name="Я")

    @pytest.fixture
    def db_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database.settings, "database_path", str(tmp_path / "t.db"))
        return asyncio.run(self._prepare())

    @staticmethod
    async def _prepare():
        await database.init_db()
        await database.upsert_user(1, "Я")
        await database.upsert_user(2, "Друг")

    def _mark(self, tid=2, scan="s1", lesson_id=LESSON, lesson_start=None, success=True, error=None):
        r = {"telegram_id": tid, "user_name": "x", "success": success, "error": error, "lesson_id": lesson_id}
        asyncio.run(attendance._record_marks(self.USER, [r], "qr", scan, "Матан", lesson_start))
        return r

    def test_first_mark_is_new(self, db_path):
        assert "already_marked" not in self._mark()

    def test_same_lesson_in_new_scan_is_repeat(self, db_path):
        self._mark()
        assert self._mark(scan="s2")["already_marked"] is True

    def test_other_lesson_is_not_repeat(self, db_path):
        self._mark()
        assert "already_marked" not in self._mark(scan="s2", lesson_id=OTHER_LESSON)

    def test_same_scan_is_not_repeat(self, db_path):
        self._mark()
        assert "already_marked" not in self._mark()

    def test_schedule_start_works_without_lesson_id(self, db_path):
        self._mark(lesson_id=None, lesson_start=1000)
        assert self._mark(scan="s2", lesson_id=None, lesson_start=1000)["already_marked"] is True
        assert "already_marked" not in self._mark(scan="s3", lesson_id=None, lesson_start=2000)

    def test_failed_mark_does_not_count(self, db_path):
        self._mark(success=False, error="Вы покинули корпус", lesson_id=None, lesson_start=1000)
        r = self._mark(scan="s2", success=False, error="Вы покинули корпус", lesson_id=None, lesson_start=1000)
        assert (r["success"], r["error"]) == (False, "Вы покинули корпус")

    def test_pulse_refusal_for_marked_student_becomes_repeat(self, db_path):
        self._mark(lesson_start=1000)
        r = self._mark(scan="s2", success=False, error="QR истёк", lesson_id=None, lesson_start=1000)
        assert (r["success"], r["error"], r["already_marked"]) == (True, None, True)

    def test_dead_session_is_not_hidden(self, db_path):
        self._mark(lesson_start=1000)
        r = self._mark(scan="s2", success=False, error=SESSION_EXPIRED, lesson_id=None, lesson_start=1000)
        assert r["success"] is False

    def test_simultaneous_scans_see_each_other(self, db_path):
        rows = [{"telegram_id": 2, "user_name": "x", "success": True, "error": None, "lesson_id": LESSON}
                for _ in range(2)]

        async def run():
            await asyncio.gather(*(attendance._record_marks(self.USER, [r], "qr", f"s{i}", None, None)
                                   for i, r in enumerate(rows)))

        asyncio.run(run())
        assert sorted(bool(r.get("already_marked")) for r in rows) == [False, True]

    def test_history_separates_repeats(self, db_path):
        self._mark()
        self._mark(scan="s2")
        scans = asyncio.run(attendance.attendance_history(self.USER))["scans"]
        assert [t["already_marked"] for s in scans for t in s["targets"]] == [True, False]


class TestRepeatNotification:
    def test_repeat_sends_nothing(self, monkeypatch):
        sent = []

        class Bot:
            async def send_message(self, tid, text, **_):
                sent.append(tid)

        async def enabled(*_):
            return True

        monkeypatch.setattr(app_main, "bot", Bot())
        monkeypatch.setattr(attendance, "notif_enabled", enabled)
        results = [{"telegram_id": 2, "success": True, "already_marked": True},
                   {"telegram_id": 3, "success": True}]
        asyncio.run(attendance._notify_marked_friends(TelegramUser(telegram_id=1, display_name="Я"), results))
        assert sent == [3]


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


def _pb(field: int, payload: bytes) -> bytes:
    return mirea_client._msg(field, payload)


def _acs_event(t: int, kind: int, enter: str = "", exit_: str = "") -> bytes:
    ev = _pb(1, b"id") + _pb(2, b"\x08" + mirea_client._varint(t)) + b"\x28" + bytes([kind])
    if enter:
        ev += _pb(3, _pb(1, b"z") + _pb(2, enter.encode()))
    if exit_:
        ev += _pb(4, _pb(1, b"z") + _pb(2, exit_.encode()))
    return ev


class TestAcsTimeline:
    T = 1_793_600_000

    def test_periods_are_flattened_by_time(self):
        movement = _pb(1, _acs_event(self.T + 60, 3, "В78 А 1эт ЛК-3", "Неконтролируемая территория"))
        movement += b"\x11" + b"\x00" * 8
        raw = (_pb(1, _pb(2, _acs_event(self.T + 120, 2, exit_="В78 А 1эт ЛК-3")))
               + _pb(1, _pb(2, _acs_event(self.T, 1, "В78 А 2эт ЦентральныйВход")))
               + _pb(1, _pb(1, movement)))
        events = mirea_client._parse_acs_timeline(raw)
        assert [(e["time"] - self.T, e["kind"]) for e in events] == [(0, 1), (60, 3), (120, 2)]
        assert events[1]["exit"] == "Неконтролируемая территория"

    def test_request_layout(self):
        req = mirea_client._encode_acs_request("h", self.T, self.T + 10, True)
        fields = [(f, v) for f, _, v in mirea_client._fields(req)]
        assert fields[0] == (1, b"h") and fields[2:] == [(3, 1), (4, 1)]
        rng = [mirea_client._parse_timestamp(v) for _, _, v in mirea_client._fields(fields[1][1])]
        assert rng == [self.T, self.T + 10]

    def test_user_id_from_me_info(self):
        raw = _pb(1, _pb(1, _pb(1, b"user-uuid") + _pb(2, "Иван".encode())))
        assert mirea_client._pulse_user_id(raw) == "user-uuid"

    def test_describe_matches_pulse_wording(self):
        e = {"time": 1, "kind": 1, "enter": "В78 Корпус И", "exit": ""}
        assert acs_mod.describe(e)["place"] == "через: В78 Корпус И"
        e = {"time": 1, "kind": 3, "enter": "В78 А ЛК-3", "exit": "Неконтролируемая территория"}
        assert (acs_mod.describe(e)["title"], acs_mod.describe(e)["place"]) == (
            "В78 А ЛК-3", "из: Неконтролируемая территория")


class TestPresence:
    NOW = 1_000_000

    def ev(self, kind, zone, dt=-600):
        return {"time": self.NOW + dt, "kind": kind, "enter": zone if kind != 2 else "",
                "exit": zone if kind == 2 else ""}

    @pytest.mark.parametrize("room,building,expected", [
        ("А-213", "В-78", ("В78", "А")),
        ("И-205-а (В-78)", "", ("В78", "И")),
        ("ИВЦ-104", "В-78", ("В78", None)),
        ("435", "С-20", ("С20", None)),
        ("", "", (None, None)),
    ])
    def test_lesson_place(self, room, building, expected):
        assert acs_mod.lesson_place(room, building) == expected

    @pytest.mark.parametrize("zone,expected", [
        ("В78 А 2эт ЦентральныйВход", ("В78", "А")),
        ("В78 Корпус И", ("В78", "И")),
        ("Неконтролируемая территория", (None, None)),
    ])
    def test_zone_place(self, zone, expected):
        assert acs_mod.zone_place(zone) == expected

    def test_inside_right_building(self):
        assert acs_mod.presence_problem([self.ev(1, "В78 А 2эт ЦентральныйВход")], self.NOW, "А-213", "В-78") is None

    def test_other_building(self):
        problem = acs_mod.presence_problem([self.ev(1, "В78 Корпус И")], self.NOW, "А-213", "В-78")
        assert "другом корпусе" in problem

    def test_left_or_never_entered(self):
        assert acs_mod.presence_problem([], self.NOW, "А-213", "В-78") == acs_mod.NOT_INSIDE
        events = [self.ev(1, "В78 А 2эт", -900), self.ev(2, "В78 А 1эт ЛК-3", -300)]
        assert acs_mod.presence_problem(events, self.NOW, "А-213", "В-78") == acs_mod.NOT_INSIDE

    def test_unknown_names_are_not_an_error(self):
        assert acs_mod.presence_problem([self.ev(1, "Главный вход")], self.NOW, "ИВЦ-104", "") is None

    def test_future_events_are_ignored(self):
        events = [self.ev(1, "В78 А 2эт", -900), self.ev(2, "В78 А 1эт", 3600)]
        assert acs_mod.presence_problem(events, self.NOW, "А-213", "В-78") is None

    def test_presence_failure_stays_silent(self, monkeypatch):
        async def broken(*_):
            raise RuntimeError("Пульс упал")

        monkeypatch.setattr(acs_mod, "pulse_user_id", broken)
        assert asyncio.run(acs_mod.presence_warning(1, {}, "А-213", "В-78")) is None


class TestGroups:
    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database.settings, "database_path", str(tmp_path / "g.db"))

        async def prepare():
            await database.init_db()
            for tid, name in [(1, "Я"), (2, "Одногруппник"), (3, "Выключил"), (4, "Друг"), (5, "Чужой")]:
                await database.upsert_user(tid, name)
            assert await database.create_group(1, "  ИКБО   01 ") is None
            code = (await database.get_group(1))["code"]
            for tid in (2, 3):
                assert await database.join_group(tid, code.lower()) is None
            await database.set_group_marking(3, False)
            async with database.get_db() as conn:
                await conn.execute("INSERT INTO friendships (user_id, friend_id, status) VALUES (1, 4, 'accepted')")
                await conn.execute("INSERT INTO friendships (user_id, friend_id, status) VALUES (1, 2, 'accepted')")
                await conn.commit()
            return code

        return asyncio.run(prepare())

    def targets(self, tid):
        return sorted(r["telegram_id"] for r in asyncio.run(database.mark_targets(tid)))

    def test_friends_and_group_with_toggle_on(self, db):
        # 2 — и друг, и в группе: один раз; 3 выключил тумблер; 5 не в группе.
        assert self.targets(1) == [2, 4]
        assert self.targets(2) == [1]

    def test_toggle_off_means_no_group(self, db):
        asyncio.run(database.set_group_marking(1, False))
        assert self.targets(1) == [2, 4]   # остаются только друзья
        assert self.targets(2) == []       # и группа больше не отмечает пользователя 1

    def test_group_view(self, db):
        g = asyncio.run(database.get_group(1))
        assert (g["name"], g["code"], g["enabled"]) == ("ИКБО 01", db, True)
        assert len(db) == 6 and set(db) <= set(database.GROUP_CODE_ALPHABET)
        assert [(m["telegram_id"], m["enabled"], m["is_self"]) for m in g["members"]] == [
            (2, True, False), (1, True, True), (3, False, False)]
        assert asyncio.run(database.get_group(5)) is None

    def test_one_group_per_person(self, db):
        assert asyncio.run(database.create_group(1, "Ещё одна")) is not None
        assert asyncio.run(database.create_group(5, "Своя")) is None
        assert asyncio.run(database.join_group(5, db)) is not None
        assert asyncio.run(database.join_group(2, db)) is None   # повторное вступление в свою — не ошибка

    def test_bad_code_and_empty_name(self, db):
        assert asyncio.run(database.join_group(5, "NOPE00")) is not None
        assert asyncio.run(database.create_group(5, "   ")) is not None

    def test_last_member_leaving_deletes_group(self, db):
        for tid in (1, 2):
            asyncio.run(database.leave_group(tid))
        assert self.targets(2) == []
        asyncio.run(database.leave_group(3))
        assert asyncio.run(database.join_group(5, db)) is not None


def test_non_uuid_lesson_id_is_ignored():
    # Не UUID мог бы совпасть у разных пар — тогда новая отметка ушла бы в «повтор» без уведомления.
    assert mirea_client._approved_lesson_id(_pb(2, b"\x08\x01")) is None


def test_background_task_is_kept_until_done():
    async def run():
        done = asyncio.Event()

        async def job():
            await asyncio.sleep(0)
            done.set()

        attendance._in_background(job())
        assert len(attendance._BACKGROUND) == 1
        await done.wait()
        await asyncio.sleep(0)
        return len(attendance._BACKGROUND)

    assert asyncio.run(run()) == 0


class TestPresenceCampuses:
    NOW = 1_000_000

    def test_pirogovka_zone_matches_mp1_lesson(self):
        zone = "П1 А 1эт ЦентральныйВход ДополнительныйВход"
        assert acs_mod.zone_place(zone) == ("П1", "А")
        events = [{"time": self.NOW - 600, "kind": 1, "enter": zone, "exit": ""}]
        assert acs_mod.presence_problem(events, self.NOW, "А-123", "МП-1") is None

    def test_stromynka_zone(self):
        events = [{"time": self.NOW - 600, "kind": 1, "enter": "С20 1эт ЦентральныйВход", "exit": ""}]
        assert acs_mod.presence_problem(events, self.NOW, "435", "С-20") is None
        assert "другом корпусе" in acs_mod.presence_problem(events, self.NOW, "А-213", "В-78")

    def test_check_asks_pulse_for_the_whole_day(self, monkeypatch):
        asked = []

        async def uid(*_):
            return "u"

        async def events(_cookies, _uid, start, end):
            asked.append(end - start)
            return {"error": None, "events": []}

        monkeypatch.setattr(acs_mod, "pulse_user_id", uid)
        monkeypatch.setattr(acs_mod, "get_acs_events", events)
        asyncio.run(acs_mod.presence_warning(1, {}, "А-1", "В-78"))
        assert asked == [86399]


class TestPresenceInNotification:
    def test_warning_goes_only_to_notification(self, monkeypatch):
        sent = {}

        class Bot:
            async def send_message(self, tid, text, **_):
                sent[tid] = text

        async def enabled(*_):
            return True

        async def presence(tid, cookies, room, building):
            return "По турникетам не в корпусе — отметка может не засчитаться" if tid == 2 else None

        monkeypatch.setattr(app_main, "bot", Bot())
        monkeypatch.setattr(attendance, "notif_enabled", enabled)
        monkeypatch.setattr(attendance, "presence_warning", presence)
        results = [{"telegram_id": 2, "success": True}, {"telegram_id": 3, "success": True}]
        asyncio.run(attendance._notify_marked_friends(
            TelegramUser(telegram_id=1, display_name="Я"), results, "Матан", {2: {}, 3: {}}, "А-1", "В-78"))
        assert "не в корпусе" in sent[2] and "не в корпусе" not in sent[3]

    def test_scan_response_has_no_warning(self):
        assert "warning" not in attendance.AttendanceResult.model_fields


class TestHistoryIncoming:
    ME = TelegramUser(telegram_id=1, display_name="Я")

    @pytest.fixture
    def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(database.settings, "database_path", str(tmp_path / "h.db"))

        async def prepare():
            await database.init_db()
            await database.upsert_user(1, "Я")
            await database.upsert_user(2, "Петя")
            await database.upsert_user(3, "Вася")

        asyncio.run(prepare())

    def _record(self, initiator, target, scan, lesson_id=LESSON, success=True):
        user = TelegramUser(telegram_id=initiator, display_name="x")
        r = {"telegram_id": target, "user_name": "x", "success": success, "error": None, "lesson_id": lesson_id}
        asyncio.run(attendance._record_marks(user, [r], "qr", scan, "Матан", None))

    def _history(self):
        return asyncio.run(attendance.attendance_history(self.ME))["scans"]

    def test_marked_by_friend_shows_incoming(self, db):
        self._record(2, 1, "s1")   # Петя отметил меня
        scans = self._history()
        assert len(scans) == 1
        assert scans[0]["kind"] == "incoming" and scans[0]["by"] == ["Петя"]

    def test_second_marker_is_repeat_not_a_new_card(self, db):
        self._record(2, 1, "s1")   # Петя отметил меня
        self._record(3, 1, "s2")   # Вася на той же паре — я уже отмечен, не новая карточка
        scans = self._history()
        assert len(scans) == 1 and scans[0]["by"] == ["Петя"]

    def test_own_scan_hides_incoming(self, db):
        self._record(1, 1, "s1")   # отметился сам
        self._record(2, 1, "s2")   # и Петя отметил — дубль-карточки быть не должно
        scans = self._history()
        assert [s["kind"] for s in scans] == ["mine"]

    def test_my_scan_and_incoming_on_different_lessons(self, db):
        self._record(1, 2, "s1", lesson_id=LESSON)        # я отметил Петю на паре A
        self._record(3, 1, "s2", lesson_id=OTHER_LESSON)  # Вася отметил меня на паре B
        scans = self._history()
        assert sorted(s["kind"] for s in scans) == ["incoming", "mine"]

    def test_failed_incoming_is_ignored(self, db):
        self._record(2, 1, "s1", success=False)
        assert self._history() == []
