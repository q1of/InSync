"""Собственный клиент Пульса МИРЭА (без pymirea).

Содержит всё, что раньше тянулось из pymirea:
  * SessionCrypto   — шифрование cookies сессии в БД (Fernet + HKDF);
  * gRPC-Web клиент — кадрирование, protobuf varint/поля;
  * get_me          — группа студента + проверка сессии;
  * get_schedule    — расписание на N дней;
  * self_approve    — отметка посещаемости по токену из QR.

Общение с API Пульса идёт по gRPC-Web поверх `.AspNetCore.Cookies`
(и/или Bearer access_token). Эти cookies выдаёт логин (см. pulse_auth).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import struct
import time
from dataclasses import dataclass

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.services.pulse_auth import _auto_submit_form, _kc_login_action, is_max_skip_page

logger = logging.getLogger(__name__)

PULSE = "https://pulse.mirea.ru"
AUTH_LOGIN = f"{PULSE}/api/auth/login"

# gRPC-Web эндпоинты Пульса
AUTH_COOKIES = ("Pulse.Auth.Cookie", ".AspNetCore.Cookies")  # сессионная cookie Пульса
ME_INFO_URL = f"{PULSE}/rtu_tc.rtu_attend.app.UserService/GetMeInfo"
LESSONS_URL = f"{PULSE}/rtu_tc.attendance.api.LessonService/GetAvailableLessonsOfVisitingLogs"
SELF_APPROVE_URL = f"{PULSE}/rtu_tc.attendance.api.AttendanceService/SelfApproveAttendanceThroughQRCode"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Единственный признак слёта сессии. Текст из ответов Пульса сюда не попадает.
SESSION_EXPIRED = "Сессия истекла. Войдите заново."


def is_session_expired(error: str | None) -> bool:
    return error == SESSION_EXPIRED


_ALREADY_RE = re.compile(r"уже\s+(отмеч|подтвержд)|already", re.I)


def is_already_marked(error: str | None) -> bool:
    """Пульс отказал, потому что студент уже отмечен на этой паре."""
    return bool(_ALREADY_RE.search(error or ""))


# Внутренние (не-cookie) поля, которые нельзя слать как HTTP-cookie
_INTERNAL = {"access_token", "token_type", "refresh_token", "expires_in"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_UUID_ANY_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# ======================= Шифрование сессий =======================

_HKDF_SALT = b"versiti.session.v1"
_HKDF_INFO = b"mirea-session-cookies"


def _fernet_key(entry: str) -> bytes:
    """Из записи SESSION_KEYS делает ключ Fernet.

    Совместимо с прежним форматом: если это готовый 32-байтный
    base64-ключ — используем как есть, иначе выводим через HKDF
    (теми же salt/info, что раньше, — старые записи в БД читаются).
    """
    try:
        raw = base64.urlsafe_b64decode(entry.encode("ascii"))
        if len(raw) == 32:
            return entry.encode("ascii")
    except Exception:
        pass
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO)
    return base64.urlsafe_b64encode(hkdf.derive(entry.encode("utf-8")))


class SessionCrypto:
    def __init__(self, session_keys: str):
        keys: list[bytes] = []
        for part in (session_keys or "").split(","):
            part = part.strip()
            if not part:
                continue
            k = _fernet_key(part)
            if k not in keys:
                keys.append(k)
        if not keys:
            raise RuntimeError("SESSION_KEYS не задан")
        self._fernets = [Fernet(k) for k in keys]

    def encrypt(self, cookies: dict) -> str:
        data = json.dumps(cookies, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernets[0].encrypt(data).decode("ascii")

    def decrypt(self, token: str) -> dict | None:
        if not token:
            return None
        raw = token.encode("ascii", "ignore")
        for f in self._fernets:
            try:
                data = f.decrypt(raw)
            except InvalidToken:
                continue
            try:
                obj = json.loads(data.decode("utf-8"))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None


# ======================= protobuf / gRPC-Web =======================

def _varint(value: int) -> bytes:
    out = bytearray()
    v = int(value)
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    return result, pos


def _skip(data: bytes, pos: int, wt: int) -> int:
    if wt == 0:
        _, pos = _read_varint(data, pos); return pos
    if wt == 1:
        return pos + 8
    if wt == 5:
        return pos + 4
    if wt == 2:
        ln, pos = _read_varint(data, pos); return pos + ln
    return pos


def _ld(data: bytes, pos: int) -> tuple[bytes, int]:
    ln, pos = _read_varint(data, pos)
    if pos + ln > len(data):
        return b"", len(data)
    return data[pos:pos + ln], pos + ln


def _string_field(data: bytes, field_no: int) -> str | None:
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        if (key >> 3) == field_no and (key & 7) == 2:
            raw, p = _ld(data, p)
            try:
                return raw.decode("utf-8", "ignore").strip() or None
            except Exception:
                return None
        p = _skip(data, p, key & 7)
    return None


def _submessage(data: bytes, field_no: int) -> bytes | None:
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        wt = key & 7
        if wt == 2:
            raw, p = _ld(data, p)
            if (key >> 3) == field_no:
                return raw
        else:
            p = _skip(data, p, wt)
    return None


def _all_strings(data: bytes, depth: int = 0) -> list[str]:
    """Все UTF-8 строки из protobuf (без схемы) — для профиля."""
    out: list[str] = []
    p = 0
    while p < len(data):
        try:
            key, p = _read_varint(data, p)
        except Exception:
            break
        wt = key & 7
        if wt == 2:
            raw, p = _ld(data, p)
            nested = _all_strings(raw, depth + 1) if depth < 6 and raw else []
            if nested:
                out.extend(nested)
            else:
                try:
                    s = raw.decode("utf-8")
                    if s.isprintable() and s.strip():
                        out.append(s)
                except UnicodeDecodeError:
                    pass
        else:
            p = _skip(data, p, wt)
    return out


def _grpc_frame(payload: bytes) -> bytes:
    return struct.pack(">BI", 0, len(payload)) + payload


def _parse_grpc_frames(data: bytes) -> tuple[list[bytes], dict[str, str]]:
    msgs: list[bytes] = []
    trailers: dict[str, str] = {}
    pos = 0
    while pos + 5 <= len(data):
        flag = data[pos]
        length = struct.unpack(">I", data[pos + 1:pos + 5])[0]
        pos += 5
        if pos + length > len(data):
            break
        payload = data[pos:pos + length]; pos += length
        if flag == 0:
            msgs.append(payload)
        elif flag & 0x80:
            for line in payload.decode("utf-8", "ignore").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
    return msgs, trailers


# ======================= Клиент API =======================

def _http_cookies(session_cookies: dict) -> httpx.Cookies:
    jar = httpx.Cookies()
    for name, value in (session_cookies or {}).items():
        if not value or name in _INTERNAL or str(name).startswith("__"):
            continue
        jar.set(str(name), str(value), domain=".mirea.ru")
    return jar


# Общий пул соединений к pulse.mirea.ru: переиспользуем TLS/TCP между запросами
# (не делаем новое рукопожатие на каждую отметку/профиль). Куки остаются
# изолированы в jar каждого клиента — пул хранит только соединения.
_pulse_transport: httpx.AsyncHTTPTransport | None = None


def _get_transport() -> httpx.AsyncHTTPTransport:
    global _pulse_transport
    if _pulse_transport is None:
        _pulse_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50,
                                keepalive_expiry=60.0),
        )
    return _pulse_transport


async def warm_connection() -> None:
    """Прогреть TLS/TCP-соединение к Пульсу (в общий пул) ДО скана, чтобы первая
    отметка не платила за рукопожатие (~0.1–0.3с). Результат не важен, ошибки глушим.
    Клиент НЕ закрываем — иначе порвём общий пул (close у PulseClient тоже no-op)."""
    try:
        cli = httpx.AsyncClient(
            transport=_get_transport(), follow_redirects=False,
            timeout=httpx.Timeout(5.0, connect=5.0), headers={"User-Agent": UA},
        )
        await cli.get(f"{PULSE}/", headers={"Origin": PULSE, "Referer": f"{PULSE}/"})
    except Exception:
        pass


class PulseClient:
    """gRPC-Web клиент поверх cookies сессии Пульса."""

    def __init__(self, session_cookies: dict):
        self.session_cookies = dict(session_cookies or {})
        self.client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
            cookies=_http_cookies(self.session_cookies),
            transport=_get_transport(),
        )

    async def close(self):
        # Соединения живут в общем пуле (_pulse_transport) и переиспользуются,
        # поэтому сам клиент не закрываем — иначе порвём keep-alive к Пульсу.
        return

    def _bearer(self) -> str | None:
        tok = (self.session_cookies.get("access_token") or "").strip()
        if not tok:
            return None
        return f"{(self.session_cookies.get('token_type') or 'Bearer').strip()} {tok}"

    def _grpc_headers(self) -> dict:
        h = {
            "Content-Type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-requested-with": "XMLHttpRequest",
            "Origin": PULSE,
            "Referer": f"{PULSE}/",
            "pulse-app-type": "pulse-app",
            "Accept": "*/*",
        }
        b = self._bearer()
        if b:
            h["Authorization"] = b
        return h

    def _sync_cookies(self):
        for c in self.client.cookies.jar:
            self.session_cookies[c.name] = c.value

    async def ensure_cookie(self) -> tuple[bool, str | None]:
        """Гарантирует наличие `.AspNetCore.Cookies`.

        Если её нет, но есть KEYCLOAK_IDENTITY (rememberMe) — Пульс
        молча выдаст новую через /api/auth/login. Иначе нужен вход.
        """
        if any(self.client.cookies.get(n) for n in AUTH_COOKIES):
            return True, None
        try:
            resp = await self.client.get(
                AUTH_LOGIN, params={"redirectUri": "/api/baseinfo"},
                headers={"Origin": PULSE, "Referer": f"{PULSE}/"},
            )
            # SSO возвращает результат HTML-формой (form_post) и может показать шаг MAX.
            for _ in range(4):
                if any(self.client.cookies.get(n) for n in AUTH_COOKIES):
                    break
                html, url = resp.text or "", str(resp.url)
                form = _auto_submit_form(html, url)
                action = _kc_login_action(html, url) if is_max_skip_page(html) else None
                if form:
                    resp = await self.client.post(form[0], data=form[1], headers={"Referer": url})
                elif action:
                    resp = await self.client.post(action, data={"skip": "true"}, headers={"Referer": url})
                else:
                    break
        except httpx.HTTPError as e:
            return False, f"МИРЭА не отвечает: {type(e).__name__}"
        host = (httpx.URL(str(resp.url)).host or "").lower()
        if any(self.client.cookies.get(n) for n in AUTH_COOKIES):
            self._sync_cookies()
            logger.warning("Cookie Пульса обновлена без повторного входа")
            return True, None
        if "sso.mirea.ru" in host or "login.mirea.ru" in host or self._bearer():
            # Сессия SSO истекла (или полагаемся на Bearer в gRPC-заголовках)
            return (True, None) if self._bearer() else (False, SESSION_EXPIRED)
        return False, "Не удалось получить сессию Пульса"

    async def grpc(self, url: str, payload: bytes, timeout: float | None = None) -> tuple[bytes | None, str | None]:
        try:
            kw = {"timeout": timeout} if timeout is not None else {}
            resp = await self.client.post(url, content=_grpc_frame(payload), headers=self._grpc_headers(), **kw)
        except httpx.TimeoutException:
            return None, "Сервер не отвечает"
        except httpx.HTTPError as e:
            return None, f"Ошибка сети: {type(e).__name__}"

        if resp.status_code == 401:
            return None, SESSION_EXPIRED
        if resp.status_code != 200:
            return None, f"Ошибка сервера: {resp.status_code}"
        self._sync_cookies()

        msgs, trailers = _parse_grpc_frames(resp.content or b"")
        status = trailers.get("grpc-status")
        if "16" in (status, resp.headers.get("grpc-status")):   # UNAUTHENTICATED
            return None, SESSION_EXPIRED
        if status and status != "0":
            return None, trailers.get("grpc-message") or f"gRPC ошибка {status}"
        if not msgs:
            preview = (resp.content[:600] or b"").decode("utf-8", "ignore")
            low = preview.lower()
            if any(x in low for x in ("<html", "<!doctype", "login", "signin", "keycloak", "авториз", "войти")):
                return None, SESSION_EXPIRED
            # Настоящий gRPC-ответ всегда несёт grpc-status. Нет статуса и нет
            # сообщения — это НЕ подтверждение (чаще всего слёт сессии/редирект),
            # поэтому НЕ считаем успехом (иначе ложное «отмечен»).
            if trailers.get("grpc-status") == "0":
                return b"", None
            logger.warning("gRPC без сообщений и без grpc-status=0: %s trailers=%s preview=%r",
                           url.rsplit("/", 1)[-1], trailers, preview[:120])
            return None, "Пульс не подтвердил отметку"
        return msgs[0], None


# ------- Высокоуровневые операции -------

_GROUP_RE = re.compile(r"\b[А-ЯЁ]{4}-\d{2}-\d{2}\b")


async def get_me(session_cookies: dict) -> dict:
    """Профиль + проверка сессии. Возвращает valid / cookies / group_name.

    При протухшей `.AspNetCore.Cookies` пробует обновить её по KEYCLOAK_IDENTITY.
    """
    async def _attempt(cookies: dict):
        cli = PulseClient(cookies)
        try:
            ok, err = await cli.ensure_cookie()
            if not ok:
                return None, err, cli.session_cookies
            raw, err = await cli.grpc(ME_INFO_URL, b"")
            return raw, err, cli.session_cookies
        finally:
            await cli.close()

    raw, err, cookies_out = await _attempt(session_cookies)
    if raw is None or b"api/auth/login" in (raw or b""):
        fresh = {k: v for k, v in session_cookies.items() if k not in AUTH_COOKIES}
        raw, err, cookies_out = await _attempt(fresh)

    if raw is None or b"api/auth/login" in (raw or b""):
        return {"valid": False, "cookies": session_cookies,
                "group_name": None, "error": err or SESSION_EXPIRED}

    strings = _all_strings(raw)
    # ФИО не извлекаем — в UI показываем имя Telegram. Из профиля берём только группу.
    group = next((m.group(0) for s in strings for m in [_GROUP_RE.search(s)] if m), None)
    merged = dict(session_cookies); merged.update(cookies_out)
    merged["__token_refreshed_at"] = str(int(time.time()))
    return {"valid": True, "cookies": merged, "group_name": group, "user_id": _pulse_user_id(raw)}


def _pulse_user_id(raw: bytes) -> str | None:
    """authorized.user.userId из GetMeInfo — под этим id человек известен сервису пропуска."""
    user = _submessage(_submessage(raw, 1) or b"", 1) or b""
    return _string_field(user, 1)


# ------- Расписание -------

@dataclass
class Lesson:
    name: str
    lesson_type: str = ""
    room: str = ""
    building: str = ""
    teacher: str = ""
    start_epoch: float | None = None
    end_epoch: float | None = None
    subgroup: str = ""


def _encode_date_request(y: int, m: int, d: int) -> bytes:
    date_msg = b"\x08" + _varint(y) + b"\x10" + _varint(m) + b"\x18" + _varint(d)
    return b"\x12" + _varint(len(date_msg)) + date_msg


def _parse_timestamp(data: bytes) -> float | None:
    seconds = None; nanos = 0
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        field = key >> 3; wt = key & 7
        if wt == 0:
            val, p = _read_varint(data, p)
            if field == 1:
                seconds = val
            elif field == 2:
                nanos = val
        else:
            p = _skip(data, p, wt)
    if seconds and seconds > 1_000_000_000:
        return float(seconds) + float(nanos) / 1e9
    return None


# Оставляем только буквы, пробел и дефис — убираем случайные служебные байты
# протобуфа, которые иногда прилипают к частям ФИО (напр. «▮Борисовна»).
_NAME_CLEAN_RE = re.compile(r"[^A-Za-zА-Яа-яЁё \-]")


def _clean_name(s: str) -> str:
    return _NAME_CLEAN_RE.sub("", s or "").strip()


def _parse_teacher(data: bytes) -> str:
    first = _clean_name(_string_field(data, 2) or "")
    last = _clean_name(_string_field(data, 3) or "")
    patr = _clean_name(_string_field(data, 4) or "")
    return " ".join(p for p in (last, first, patr) if p)


def _parse_lesson(data: bytes) -> Lesson | None:
    name = ltype = room = building = teacher = subgroup = ""
    start = end = None
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        field = key >> 3; wt = key & 7
        if wt == 2:
            raw, p = _ld(data, p)
            if field == 2:
                start = _parse_timestamp(raw)
            elif field == 3:
                end = _parse_timestamp(raw)
            elif field == 4:
                name = _string_field(raw, 2) or ""
            elif field == 5:
                ltype = _string_field(raw, 2) or ""
            elif field == 6:
                room = _string_field(raw, 2) or ""
                building = _string_field(raw, 3) or ""
            elif field == 7:
                teacher = _parse_teacher(raw)
            elif field == 8 and not end:
                end = _parse_timestamp(raw)
            elif field == 10:
                subgroup = raw.decode("utf-8", "ignore").strip()
        else:
            p = _skip(data, p, wt)
    if not name:
        return None
    return Lesson(name, ltype, room, building, teacher, start, end, subgroup)


def _parse_lessons(data: bytes) -> list[Lesson]:
    lessons: list[Lesson] = []
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        wt = key & 7
        if wt == 2:
            outer, p = _ld(data, p)
            inner = _submessage(outer, 3) or outer
            lesson = _parse_lesson(inner)
            if lesson:
                lessons.append(lesson)
        else:
            p = _skip(data, p, wt)
    return lessons


async def get_schedule(session_cookies: dict, days: int = 7) -> list[dict]:
    """Расписание на N дней вперёд. Возвращает список пар (dict)."""
    from datetime import datetime, timedelta

    cli = PulseClient(session_cookies)
    try:
        ok, _ = await cli.ensure_cookie()
        if not ok:
            return []
        now = datetime.now()
        sem = asyncio.Semaphore(7)   # неделя — одной волной

        async def _day(offset: int) -> list[Lesson]:
            dt = now + timedelta(days=offset)
            async with sem:
                raw, err = await cli.grpc(LESSONS_URL, _encode_date_request(dt.year, dt.month, dt.day))
            return _parse_lessons(raw) if raw else []

        results = await asyncio.gather(*(_day(d) for d in range(days)))
    finally:
        await cli.close()

    out: list[dict] = []
    for day in results:
        for l in day:
            out.append({"name": l.name, "type": l.lesson_type, "room": l.room, "building": l.building,
                        "teacher": l.teacher, "subgroup": l.subgroup,
                        "start_epoch": l.start_epoch, "end_epoch": l.end_epoch})
    out.sort(key=lambda x: x["start_epoch"] or 0)
    return out


# ------- Пропуск (турникеты) -------

HUMANPASS = f"{PULSE}/rtu_tc.rtu_attend.humanpass.HumanPassService"
ACS_TIMELINE_URL = f"{HUMANPASS}/GetHumanAcsTimeline"

ACS_ENTER, ACS_EXIT, ACS_MOVE = 1, 2, 3   # EventKind Пульса


def _fields(data: bytes):
    """Поля protobuf верхнего уровня: (номер, тип, значение). Значение — bytes, int или double."""
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, p = _read_varint(data, p)
        elif wt == 2:
            val, p = _ld(data, p)
        elif wt == 1 and p + 8 <= len(data):
            val = struct.unpack("<d", data[p:p + 8])[0]
            p += 8
        else:
            p = _skip(data, p, wt)
            continue
        yield field, wt, val


def _msg(field: int, payload: bytes) -> bytes:
    return _varint(field << 3 | 2) + _varint(len(payload)) + payload


def _encode_acs_request(human_id: str, start: float, end: float, ascending: bool) -> bytes:
    """humanId, timeRange{start, end}, eventType=ALL и (для ленты) порядок по возрастанию."""
    time_range = _msg(1, b"\x08" + _varint(int(start))) + _msg(2, b"\x08" + _varint(int(end)))
    out = _msg(1, human_id.encode()) + _msg(2, time_range) + b"\x18\x01"
    return out + (b"\x20\x01" if ascending else b"")


def _parse_acs_event(data: bytes) -> dict | None:
    ev = {"time": None, "kind": 0, "enter": "", "exit": ""}
    for field, wt, val in _fields(data):
        if field == 2 and wt == 2:
            ev["time"] = _parse_timestamp(val)
        elif field == 3 and wt == 2:
            ev["enter"] = _string_field(val, 2) or ""
        elif field == 4 and wt == 2:
            ev["exit"] = _string_field(val, 2) or ""
        elif field == 5 and wt == 0:
            ev["kind"] = val
    return ev if ev["time"] else None


def _parse_acs_timeline(data: bytes) -> list[dict]:
    """dwellPeriods: либо пачка перемещений внутри (movementEvents), либо вход/выход (borderEvent)."""
    events: list[dict] = []
    for field, wt, period in _fields(data):
        if field != 1 or wt != 2:
            continue
        for kind, kwt, part in _fields(period):
            if kwt != 2:
                continue
            if kind == 1:
                rows = [v for f, w, v in _fields(part) if f == 1 and w == 2]
            elif kind == 2:
                rows = [part]
            else:
                continue
            events += [e for e in map(_parse_acs_event, rows) if e]
    events.sort(key=lambda e: e["time"])
    return events


async def get_acs_events(session_cookies: dict, human_id: str, start: float, end: float) -> dict:
    """События турникетов за [start, end] по времени."""
    cli = PulseClient(session_cookies)
    try:
        ok, err = await cli.ensure_cookie()
        if not ok:
            return {"error": err or SESSION_EXPIRED}
        raw, err = await cli.grpc(ACS_TIMELINE_URL, _encode_acs_request(human_id, start, end, True), timeout=8.0)
    finally:
        await cli.close()
    if err:
        return {"error": err}
    return {"error": None, "events": _parse_acs_timeline(raw or b"")}


# ------- Отметка посещаемости -------

def extract_qr_token(qr_data: str) -> str | None:
    """Достаёт token (UUID) из QR.

    Поддерживает три формата:
      * URL с ?token=UUID / &token=UUID;
      * сам UUID строкой;
      * UUID где угодно в URL, в т.ч. в пути (напр. .../self-approve/<uuid>) —
        именно так выглядит QR Пульса, поэтому без этого отметка не распознавалась.
    """
    if not qr_data:
        return None
    s = qr_data.strip()
    m = re.search(r"[?&]token=([0-9a-fA-F-]{36})", s)
    if m:
        return m.group(1)
    if _UUID_RE.match(s):
        return s
    m2 = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s
    )
    if m2:
        return m2.group(0)
    return None


def _parse_selfapprove(data: bytes) -> tuple[bool | None, str | None]:
    """Разбор ответа SelfApprove. (approved, reason)."""
    # BoolValue: 08 01  или  0a 02 08 01
    p = 0
    while p < len(data):
        key, p = _read_varint(data, p)
        field = key >> 3; wt = key & 7
        if field == 1 and wt == 0:
            val, _ = _read_varint(data, p)
            return bool(val), None
        if field == 1 and wt == 2:
            raw, p2 = _ld(data, p)
            inner = _string_field(raw, 1)
            if inner:
                return False, inner  # notYet с причиной
            # вложенный BoolValue
            kk = raw[0] if raw else 0
            if kk == 0x08 and len(raw) >= 2:
                return bool(raw[1]), None
            p = p2
            continue
        if field == 2 and wt == 2:
            # approved {lessonId}
            _, p = _ld(data, p)
            return True, None
        p = _skip(data, p, wt)
    return None, None


def _approved_lesson_id(data: bytes) -> str | None:
    """lessonId из approved{lessonId}: по нему видно повторную отметку на той же паре.

    Берём только UUID: что-то другое могло бы совпасть у разных пар, и тогда
    новая отметка считалась бы повтором (без уведомления).
    """
    approved = _submessage(data, 2)
    m = _UUID_ANY_RE.search(approved.decode("utf-8", "ignore")) if approved else None
    return m.group(0).lower() if m else None


async def self_approve(
    session_cookies: dict, qr_data: str,
) -> tuple[bool, str | None, dict | None, str | None]:
    """Отметка по QR. Возвращает (успех, текст_ошибки, обновлённые_cookies или None, lessonId или None).

    Если cookie Пульса протухла, один раз обновляет её по KEYCLOAK_IDENTITY и повторяет.
    """
    token = extract_qr_token(qr_data)
    if not token:
        return False, "Не удалось распознать QR-код", None, None
    payload = b"\x0a" + _varint(len(token.encode())) + token.encode()

    ok, err, cookies, lesson_id = await _approve(session_cookies, payload)
    if is_session_expired(err) and session_cookies.get("KEYCLOAK_IDENTITY"):
        fresh = {k: v for k, v in session_cookies.items() if k not in AUTH_COOKIES}
        ok, err, cookies, lesson_id = await _approve(fresh, payload)

    changed = any(cookies.get(n) and cookies.get(n) != session_cookies.get(n) for n in AUTH_COOKIES)
    return ok, err, cookies if changed else None, lesson_id


async def _approve(session_cookies: dict, payload: bytes) -> tuple[bool, str | None, dict, str | None]:
    cli = PulseClient(session_cookies)
    ok, err, lesson_id = await _approve_once(cli, payload)
    return ok, err, cli.session_cookies, lesson_id


async def _approve_once(cli: PulseClient, payload: bytes) -> tuple[bool, str | None, str | None]:
    try:
        try:
            # Обновление cookie не должно держать всю волну: QR живёт недолго.
            ok, err = await asyncio.wait_for(cli.ensure_cookie(), timeout=8.0)
        except asyncio.TimeoutError:
            return False, "Сервер не отвечает", None
        if not ok:
            return False, err or SESSION_EXPIRED, None
        # Жёсткий тайм-аут: один зависший друг не должен держать волну (QR живёт ~20с).
        raw, err = await cli.grpc(SELF_APPROVE_URL, payload, timeout=9.0)
        if err:
            return False, err, None
        if raw == b"":
            # Пустой ответ, но с подтверждённым grpc-status=0 (это гарантирует grpc()):
            # RPC прошёл успешно — считаем отметку принятой. Ложного «отмечен» нет,
            # т.к. ответы без статуса grpc() уже превращает в ошибку.
            return True, None, None
        # Схема ответа известна не полностью: по этой строке видно, чем первая отметка
        # отличается от повторной и от отказа.
        logger.warning("self_approve ответ: raw=%s", (raw or b"").hex()[:96])
        approved, reason = _parse_selfapprove(raw or b"")
        if approved is True:
            return True, None, _approved_lesson_id(raw or b"")
        if approved is False:
            return False, reason or "Отметка пока недоступна", None
        # Пусто/непонятно: у настоящей отметки есть поле approved{lessonId}, поэтому
        # без явного подтверждения успехом НЕ считаем (лучше повтор, чем ложное «отмечен»).
        return False, "Пульс не подтвердил отметку — попробуйте ещё раз", None
    finally:
        await cli.close()
