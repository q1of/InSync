"""Вход в Пульс МИРЭА — своя реализация (без pymirea).

Схема входа pulse.mirea.ru (актуальна на октябрь 2026):
  1. GET pulse.mirea.ru/api/auth/login?redirectUri=...&rememberMe=True
     — сервер Пульса делает Pushed Authorization Request и редиректит
     на sso.mirea.ru с формой входа (SPA на React).
  2. POST логин/пароль на loginAction из kcContext.
  3. 2FA: SSO рисует форму кода (почта/приложение). Формы рисуются в
     JavaScript, в статическом HTML полей НЕТ — поэтому шаг 2FA мы
     определяем по pageId в kcContext, а не по DOM:
        email-code-form  -> поле emailCode + login=true (код на почту)
        login-otp / *otp -> поле otp       + login=true (приложение)
     Повторная отправка кода — тот же POST с resend=true.
  4. SSO редиректит на pulse.mirea.ru/api/mireaauth?code=..., Пульс
     меняет code на токены, ставит `.AspNetCore.Cookies` и уводит на
     redirectUri. При ошибке обмена уводит на /auth_error.

Весь путь ведём одним httpx-клиентом (нужны cookies сессии SSO).
Каждый шаг пишется в лог; непонятные страницы — в data/ для разбора.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("insync.auth")

PULSE = "https://pulse.mirea.ru"
SSO = "https://sso.mirea.ru"
LOGIN_START = f"{PULSE}/api/auth/login"

# Имя сессионной cookie Пульса после входа (раньше было .AspNetCore.Cookies).
AUTH_COOKIES = ("Pulse.Auth.Cookie", ".AspNetCore.Cookies")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

DEBUG_DIR = os.getenv("PULSE_DEBUG_DIR", "./data")

# pageId -> (имя поля кода, тип). Формы SSO рисуются в JS, поля берём отсюда.
_OTP_PAGES = {
    "email-code-form": ("emailCode", "email_code"),
    "login-otp": ("otp", "otp"),
    "login-otp.ftl": ("otp", "otp"),
}


@dataclass
class Challenge:
    action_url: str
    field_name: str
    hidden_fields: dict = field(default_factory=dict)
    referer: str | None = None
    kind: str = "otp"
    masked_email: str | None = None


@dataclass
class LoginOutcome:
    success: bool = False
    error: str | None = None
    cookies: dict | None = None
    challenge: Challenge | None = None


# ----------------- разбор kcContext / HTML -----------------

def _unescape(s: str) -> str:
    return (s.replace("\\u0026", "&").replace("\\/", "/")
             .replace("\\u003d", "=").replace("&amp;", "&").replace('\\"', '"'))


def _kc_login_action(html: str, base_url: str) -> str | None:
    m = re.search(r'"loginAction"\s*:\s*"((?:https?:|/)[^"]+)"', html)
    if m:
        return urljoin(base_url, _unescape(m.group(1)))
    # запасной путь — <form action=...>
    soup = BeautifulSoup(html, "html.parser")
    form = (soup.find("form", id="kc-otp-login-form") or soup.find("form", id="kc-form-login")
            or soup.find("form"))
    if form and form.get("action"):
        return urljoin(base_url, form["action"])
    return None


def _page_id(html: str) -> str | None:
    m = re.search(r'"pageId"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def _masked_email(html: str) -> str | None:
    m = re.search(r'"maskedEmail"\s*:\s*"([^"]+)"', html)
    return _unescape(m.group(1)) if m else None


def _kc_error(html: str) -> str | None:
    """Человекочитаемая ошибка из kcContext.message.summary."""
    m = re.search(r'"message"\s*:\s*\{[^}]*?"summary"\s*:\s*"([^"]*)"', html)
    summary = None
    if m:
        summary = m.group(1)
    else:
        soup = BeautifulSoup(html, "html.parser")
        el = (soup.find(id="input-error") or soup.find(class_="kc-feedback-text")
              or soup.find(class_="alert-error") or soup.find(class_="pf-c-alert__title"))
        if el and el.get_text(strip=True):
            summary = el.get_text(strip=True)
    if not summary:
        return None
    summary = html_mod.unescape(re.sub(r"<[^>]+>", "", _unescape(summary))).strip()
    low = summary.lower()
    if "invalid" in low or "недейств" in low or "неверн" in low:
        if any(x in low for x in ("otp", "code", "код", "authenticator", "одноразов")):
            return "Неверный код подтверждения"
        if any(x in low for x in ("credential", "password", "парол", "username", "логин")):
            return "Неверный логин или пароль"
    if "expired" in low or "истёк" in low or "истек" in low:
        return "Код истёк. Запросите новый."
    if "account is disabled" in low or "заблокир" in low:
        return "Аккаунт заблокирован в МИРЭА"
    return summary


def _otp_challenge(html: str, base_url: str) -> Challenge | None:
    """Форма ввода кода 2FA — по pageId из kcContext (формы рисуются в JS)."""
    page = (_page_id(html) or "").lower()

    field_name, kind = None, "otp"
    for pid, (fname, k) in _OTP_PAGES.items():
        if page == pid or page.startswith(pid):
            field_name, kind = fname, k
            break
    if not field_name and ("otp" in page or "code" in page):
        # Незнакомая, но явно OTP-страница — пробуем стандартное поле
        field_name, kind = "otp", "otp"
    if not field_name:
        return None

    action = _kc_login_action(html, base_url)
    if not action:
        return None

    return Challenge(
        action_url=action,
        field_name=field_name,
        hidden_fields={"login": "true"},   # submit-кнопка «Подтвердить»
        referer=base_url,
        kind=kind,
        masked_email=_masked_email(html),
    )


def code_hint(challenge: Challenge) -> str:
    if challenge.kind == "email_code":
        if challenge.masked_email:
            return f"Код отправлен на почту {challenge.masked_email}"
        return "Код отправлен на вашу почту @edu.mirea.ru"
    return "Введите код из приложения-аутентификатора"


# ----------------- утилиты -----------------

def _host(url) -> str:
    return (urlparse(str(url)).hostname or "").lower()


def _all_cookies(client: httpx.AsyncClient) -> dict:
    out = {c.name: c.value for c in client.cookies.jar}
    out["__token_refreshed_at"] = str(int(time.time()))
    return out


def _has_auth_cookie(client: httpx.AsyncClient) -> bool:
    return any(c.name in AUTH_COOKIES for c in client.cookies.jar)


def _logged_in(client: httpx.AsyncClient, resp: httpx.Response) -> bool:
    if _host(resp.url) != "pulse.mirea.ru":
        return False
    path = urlparse(str(resp.url)).path.lower()
    if path.startswith("/login") or "auth_error" in path:
        return False
    # Успех: вернулись на страницу Пульса (напр. /services) с сессионной cookie
    return _has_auth_cookie(client)


# Отладочные дампы пишутся только если включён флаг PULSE_DEBUG (в .env).
# В проде по умолчанию выключено — чтобы не засорять data/ страницами входа.
DEBUG_ENABLED = bool(os.getenv("PULSE_DEBUG"))


def _trace(resp: httpx.Response, step: str) -> None:
    """Пишет цепочку редиректов и финальный HTML в data/ (только при PULSE_DEBUG)."""
    if not DEBUG_ENABLED:
        return
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        hops = [{
            "method": r.request.method, "url": str(r.request.url),
            "status": r.status_code, "location": r.headers.get("location"),
            "set_cookie": [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")],
        } for r in list(resp.history) + [resp]]
        info = {"final_url": str(resp.url), "final_status": resp.status_code,
                "page_id": _page_id(resp.text or ""), "hops": hops}
        with open(os.path.join(DEBUG_DIR, f"sso_trace_{step}.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        with open(os.path.join(DEBUG_DIR, f"sso_debug_{step}.html"), "w", encoding="utf-8") as f:
            f.write(f"<!-- {resp.status_code} {resp.url} -->\n{resp.text}")
    except Exception as e:
        logger.debug("trace failed: %s", e)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": UA,
                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                 "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
        proxy=os.getenv("MIREA_PROXY") or None,
    )


# ----------------- основной флоу -----------------

class PulseLogin:
    def __init__(self):
        self.client = _make_client()
        self._last_challenge: Challenge | None = None
        self._skip_count = 0   # сколько раз пропускали необязательную настройку (MAX и т.п.)

    async def close(self):
        try:
            await self.client.aclose()
        except Exception:
            pass

    async def _analyze(self, resp: httpx.Response, step: str) -> LoginOutcome:
        _trace(resp, step)
        host = _host(resp.url)

        if _logged_in(self.client, resp):
            logger.info("Вход успешен (шаг %s), сессия Пульса получена", step)
            return LoginOutcome(success=True, cookies=_all_cookies(self.client))

        html = resp.text or ""

        if host == "pulse.mirea.ru" and "auth_error" in str(resp.url):
            logger.error("Пульс отклонил вход на шаге '%s' (auth_error). Трассировка в %s", step, DEBUG_DIR)
            return LoginOutcome(error="Пульс не принял вход (ошибка обмена токена). "
                                      "Попробуйте войти ещё раз; если повторяется — пришлите data/sso_trace_code.json")

        if host != "sso.mirea.ru":
            logger.error("Неожиданная страница после '%s': %s (%s)", step, resp.url, resp.status_code)
            return LoginOutcome(error=f"МИРЭА вернула неожиданную страницу ({resp.status_code})")

        # Необязательная настройка MAX (max-account-config / login-max-otp) — жмём «Skip»
        # и идём дальше. Иначе вход зацикливается: эта страница не приём кода, а push в MAX.
        url_s = str(resp.url)
        # Никакой особой обработки MAX. Код из MAX приходит и вводится так же, как
        # код с почты — через обычную форму кода (её ловит _otp_challenge ниже).

        err = _kc_error(html)
        ch = _otp_challenge(html, url_s)
        if ch:
            # На странице кода бывает и текст ошибки (неверный код) — покажем его
            if err and step == "code":
                logger.warning("Код отклонён МИРЭА: %s", err)
                self._last_challenge = ch
                return LoginOutcome(error=err, challenge=ch)
            logger.info("Нужен код подтверждения (%s), поле '%s'", ch.kind, ch.field_name)
            self._last_challenge = ch
            return LoginOutcome(challenge=ch)

        if err:
            logger.warning("МИРЭА отклонила вход на шаге '%s': %s", step, err)
            return LoginOutcome(error=err)

        page = _page_id(html)
        logger.error("Неизвестный шаг входа SSO pageId=%s (шаг %s). Дамп в %s", page, step, DEBUG_DIR)
        return LoginOutcome(error=f"МИРЭА показала незнакомый шаг входа ({page or 'неизвестно'}). "
                                  f"Сообщите разработчику — дамп сохранён в data/.")

    async def start(self, username: str, password: str) -> LoginOutcome:
        logger.info("Старт входа для %s", username)
        try:
            page = await self.client.get(
                LOGIN_START,
                params={"redirectUri": f"{PULSE}/services", "rememberMe": "True"},
                headers={"Referer": f"{PULSE}/login"},
            )
        except httpx.HTTPError as e:
            logger.error("Нет связи с МИРЭА на старте: %s", e)
            return LoginOutcome(error=f"Не удалось подключиться к МИРЭА ({type(e).__name__}). "
                                      "Проверьте интернет; из дата-центров Пульс часто закрыт — нужен прокси (MIREA_PROXY).")

        if _host(page.url) != "sso.mirea.ru":
            _trace(page, "start")
            logger.error("Пульс не увёл на SSO: %s (%s)", page.url, page.status_code)
            return LoginOutcome(error=f"Пульс не открыл форму входа ({page.status_code}). Попробуйте позже.")

        action = _kc_login_action(page.text, str(page.url))
        if not action:
            _trace(page, "start")
            logger.error("Не найден loginAction на форме входа")
            return LoginOutcome(error="Не удалось прочитать форму входа МИРЭА (дамп в data/)")

        try:
            resp = await self.client.post(
                action,
                data={"username": username, "password": password, "rememberMe": "on", "credentialId": ""},
                headers={"Referer": str(page.url), "Origin": SSO,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            logger.error("Ошибка отправки логина/пароля: %s", e)
            return LoginOutcome(error=f"МИРЭА не ответила на вход ({type(e).__name__})")
        return await self._analyze(resp, "password")

    async def submit_code(self, challenge: Challenge, code: str) -> LoginOutcome:
        logger.info("Отправка кода подтверждения (поле %s)", challenge.field_name)
        payload = dict(challenge.hidden_fields)
        payload[challenge.field_name] = code.strip()
        try:
            resp = await self.client.post(
                challenge.action_url, data=payload,
                headers={"Referer": challenge.referer or challenge.action_url, "Origin": SSO,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            logger.error("Ошибка отправки кода: %s", e)
            return LoginOutcome(error=f"МИРЭА не ответила ({type(e).__name__})")
        return await self._analyze(resp, "code")

    async def resend_code(self) -> LoginOutcome:
        """Повторная отправка кода (кнопка «Отправить ещё раз»)."""
        ch = self._last_challenge
        if not ch:
            return LoginOutcome(error="Нет активной формы кода")
        logger.info("Повторная отправка кода")
        try:
            resp = await self.client.post(
                ch.action_url, data={"resend": "true"},
                headers={"Referer": ch.referer or ch.action_url, "Origin": SSO,
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as e:
            logger.error("Ошибка повторной отправки кода: %s", e)
            return LoginOutcome(error=f"МИРЭА не ответила ({type(e).__name__})")
        out = await self._analyze(resp, "resend")
        # resend возвращает ту же форму — это ок
        if out.challenge and not out.error:
            return LoginOutcome(challenge=out.challenge)
        return out
