/**
 * Среда запуска: один и тот же фронтенд работает как Telegram Mini App и как
 * Android-приложение (Capacitor). Отличается только способ авторизации и адрес API.
 *
 *  • Telegram  — опознание по подписи initData, API на том же домене (/api).
 *  • App       — опознание по токену (Authorization: Bearer …), API по абсолютному
 *                адресу бэкенда (в APK адрес зашит на этапе сборки).
 *
 * Открытие webapp в обычном браузере (вне Telegram) тоже работает в режиме App —
 * удобно для отладки и как веб-версия.
 */

function hasTelegram(): boolean {
  try {
    const tg = (window as any).Telegram?.WebApp;
    return !!(tg && (tg.initData || tg.initDataUnsafe?.user));
  } catch {
    return false;
  }
}

function isNative(): boolean {
  try {
    return !!(window as any).Capacitor?.isNativePlatform?.();
  } catch {
    return false;
  }
}

export const isNativeApp = isNative();
export const isTelegram = hasTelegram();
export const authMode: 'tg' | 'app' = isTelegram ? 'tg' : 'app';

// Адрес бэкенда. В Telegram и в вебе фронт на том же домене → относительный /api.
// В нативном приложении origin — localhost, поэтому нужен абсолютный адрес (зашит в сборку).
const API_FALLBACK = 'https://innsync.duckdns.org/api';
export const API_BASE: string = isNativeApp
  ? ((import.meta as any).env?.VITE_API_BASE || API_FALLBACK)
  : '/api';

// ── Токен приложения (в localStorage; в WebView и браузере переживает перезапуск) ──
const TOKEN_KEY = 'insync_app_token';

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {}
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {}
}
