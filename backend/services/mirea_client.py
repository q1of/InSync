"""Собственный клиент Пульса МИРЭА (без pymirea).

Содержит всё, что раньше тянулось из pymirea:
  * SessionCrypto   — шифрование cookies сессии в БД (Fernet + HKDF);
  * gRPC-Web клиент — кадрирование, protobuf varint/поля;
  * get_me          — профиль студента (ФИО, группа) + проверка сессии;
  * get_schedule    — расписание на N дней;
  * self_approve    — отметка посещаемости по токену из QR.

Общение с API Пульса идёт по gRPC-Web поверх `.AspNetCore.Cookies`
(и/или Bearer access_token). Эти cookies выдаёт логин (см. pulse_auth).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import struct
import time
from dataclasses import dataclass

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

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

# Внутренние (не-cookie) поля, которые нельзя слать как HTTP-cookie
_INTERNAL = {"access_token", "token_type", "refresh_token", "expires_in"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


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
        except httpx.HTTPError as e:
            return False, f"МИРЭА не отвечает: {type(e).__name__}"
        host = (httpx.URL(str(resp.url)).host or "").lower()
        if any(self.client.cookies.get(n) for n in AUTH_COOKIES):
            self._sync_cookies()
            return True, None
        if "sso.mirea.ru" in host or "login.mirea.ru" in host or self._bearer():
            # Сессия SSO истекла (или полагаемся на Bearer в gRPC-заголовках)
            return (True, None) if self._bearer() else (False, "Сессия истекла. Войдите заново.")
        return False, "Не удалось получить сессию Пульса"

    async def grpc(self, url: str, payload: bytes) -> tuple[bytes | None, str | None]:
        try:
            resp = await self.client.post(url, content=_grpc_frame(payload), headers=self._grpc_headers())
        except httpx.TimeoutException:
            return None, "Сервер не отвечает"
        except httpx.HTTPError as e:
            return None, f"Ошибка сети: {type(e).__name__}"

        if resp.status_code != 200:
            return None, f"Ошибка сервера: {resp.status_code}"
        self._sync_cookies()

        msgs, trailers = _parse_grpc_frames(resp.content or b"")
        status = trailers.get("grpc-status")
        if status and status != "0":
            return None, trailers.get("grpc-message") or f"gRPC ошибка {status}"
        if not msgs:
            preview = (resp.content[:300] or b"").decode("utf-8", "ignore").lower()
            if any(x in preview for x in ("<html", "login", "signin", "keycloak")):
                return None, "Сессия МИРЭА истекла. Войдите заново."
            return b"", None
        return msgs[0], None


# ------- Высокоуровневые операции -------

_FIO_RE = re.compile(r"^[А-ЯЁ][а-яё-]+ [А-ЯЁ][а-яё-]+( [А-ЯЁ][а-яё-]+)?$")
_GROUP_RE = re.compile(r"\b[А-ЯЁ]{4}-\d{2}-\d{2}\b")


async def get_me(session_cookies: dict) -> dict:
    """Профиль + проверка сессии. Возвращает valid / cookies / full_name / group_name.

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
        return {"valid": False, "cookies": session_cookies, "full_name": None,
                "group_name": None, "error": err or "Сессия истекла"}

    strings = _all_strings(raw)
    full_name = next((s.strip() for s in strings if _FIO_RE.match(s.strip())), None)
    group = next((m.group(0) for s in strings for m in [_GROUP_RE.search(s)] if m), None)
    if not full_name:
        # ФИО не распознано строгим regex — логируем кандидатов, чтобы поправить парсинг.
        cand = [s.strip() for s in strings if 3 <= len(s.strip()) <= 60]
        logger.warning("get_me: ФИО не распознано. Кандидаты (%d): %s", len(cand), cand[:30])
    merged = dict(session_cookies); merged.update(cookies_out)
    merged["__token_refreshed_at"] = str(int(time.time()))
    return {"valid": True, "cookies": merged, "full_name": full_name, "group_name": group}


# ------- Расписание -------

@dataclass
class Lesson:
    name: str
    lesson_type: str = ""
    room: str = ""
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


def _parse_teacher(data: bytes) -> str:
    first = _string_field(data, 2) or ""
    last = _string_field(data, 3) or ""
    patr = _string_field(data, 4) or ""
    return " ".join(p for p in (last, first, patr) if p)


def _parse_lesson(data: bytes) -> Lesson | None:
    name = ltype = room = teacher = subgroup = ""
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
    return Lesson(name, ltype, room, teacher, start, end, subgroup)


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
    import asyncio
    from datetime import datetime, timedelta

    cli = PulseClient(session_cookies)
    try:
        ok, _ = await cli.ensure_cookie()
        if not ok:
            return []
        now = datetime.now()
        sem = asyncio.Semaphore(5)

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
            out.append({"name": l.name, "type": l.lesson_type, "room": l.room,
                        "teacher": l.teacher, "subgroup": l.subgroup,
                        "start_epoch": l.start_epoch, "end_epoch": l.end_epoch})
    out.sort(key=lambda x: x["start_epoch"] or 0)
    return out


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


async def self_approve(session_cookies: dict, qr_data: str) -> tuple[bool, str | None]:
    """Отметка по QR. Возвращает (успех, текст_ошибки)."""
    token = extract_qr_token(qr_data)
    if not token:
        return False, "Не удалось распознать QR-код"

    cli = PulseClient(session_cookies)
    try:
        ok, err = await cli.ensure_cookie()
        if not ok:
            return False, err or "Сессия истекла"
        payload = b"\x0a" + _varint(len(token.encode())) + token.encode()
        raw, err = await cli.grpc(SELF_APPROVE_URL, payload)
        if err:
            return False, err
        if raw == b"":
            return True, None  # пустой ok-ответ
        approved, reason = _parse_selfapprove(raw or b"")
        if approved is True:
            return True, None
        if approved is False:
            return False, reason or "Отметка пока недоступна"
        return False, "Неизвестный ответ сервера"
    finally:
        await cli.close()
