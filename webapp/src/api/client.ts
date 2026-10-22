const getInitData = (): string => {
  if (typeof window === 'undefined') return '';
  // 1) штатно — из SDK Telegram
  const tg = (window as any).Telegram?.WebApp;
  if (tg?.initData) return tg.initData;
  // 2) фолбэк — из хэша URL (tgWebAppData). Работает, даже если telegram-web-app.js
  //    не загрузился (напр. telegram.org недоступен/медленный в сети пользователя).
  try {
    const h = (window.location.hash || '').replace(/^#/, '');
    const d = new URLSearchParams(h).get('tgWebAppData');
    if (d) return d;
  } catch {}
  // 3) иногда данные лежат в query (?tgWebAppData=...)
  try {
    const d = new URLSearchParams(window.location.search || '').get('tgWebAppData');
    if (d) return d;
  } catch {}
  return '';
};

const BASE_URL = '/api';

async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const initData = getInitData();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': initData,
  };

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!response.ok) {
    let errorMsg = 'Ошибка сервера';
    try {
      const errorData = await response.json();
      errorMsg = errorData.detail || errorMsg;
    } catch {}
    throw new Error(errorMsg);
  }

  return response.json();
}

// Auth
export const loginApi = (username: string, password: string) =>
  api('POST', '/auth/login', { username, password });

export const complete2faApi = (challengeId: string, code: string) =>
  api('POST', '/auth/2fa', { challenge_id: challengeId, code });

export const resend2faApi = (challengeId: string) =>
  api<{ success: boolean; hint?: string }>('POST', '/auth/2fa/resend', { challenge_id: challengeId });

export interface SessionStatus {
  logged_in: boolean;
  valid: boolean;
  updated_at?: number | null;
  expires_in_seconds?: number | null;
  group_name?: string | null;
  mirea_login?: string | null;
}

// check=false — быстрый ответ без запроса к pulse.mirea.ru
export const getSessionApi = (check = true) =>
  api<SessionStatus>('GET', `/auth/session?check=${check}`);

export interface Lesson {
  name: string; type: string; room: string; teacher: string;
  subgroup: string; start_epoch: number | null; end_epoch: number | null;
}
export const getScheduleApi = (days = 7) =>
  api<{ lessons: Lesson[] }>('GET', `/auth/schedule?days=${days}`);

// Отключение отметки на конкретных парах (по start_epoch занятия)
export const getOptoutsApi = () =>
  api<{ start_epochs: number[] }>('GET', '/optouts');

export const setOptoutApi = (body: { start_epoch: number; end_epoch: number | null; lesson_name: string; enabled: boolean }) =>
  api<{ success: boolean }>('POST', '/optouts', body);

export const logoutApi = () =>
  api('POST', '/auth/logout');

// Attendance
export interface MarkResult {
  user_name: string;
  telegram_id: number;
  success: boolean;
  error: string | null;
  retriable: boolean;
  opted_out: boolean;   // сам отключил отметку на этой паре — не ошибка
}
export interface MarkResponse { results: MarkResult[]; total: number; successful: number; }

// only — подмножество telegram_id (повтор только непрошедшим); scanId — склейка волн в истории
// Прогрев сессий в кеш перед сканом (чтобы первая отметка была быстрой)
export const prewarmApi = () => api<{ ready: number }>('POST', '/attendance/prewarm');

export const markAttendanceApi = (qrData: string, opts?: { only?: number[]; scanId?: string; lessonName?: string | null }) =>
  api<MarkResponse>('POST', '/attendance/mark', {
    qr_data: qrData,
    only: opts?.only ?? null,
    scan_id: opts?.scanId ?? null,
    lesson_name: opts?.lessonName ?? null,
  });

export interface ScanTarget { name: string; is_self: boolean; success: boolean; error: string | null; }
export interface Scan { created_at: number; lesson_name?: string | null; targets: ScanTarget[]; successful: number; total: number; }
export const getHistoryApi = () =>
  api<{ scans: Scan[] }>('GET', '/attendance/history');

// Friends
export interface Friend {
  telegram_id: number;
  name: string;
  group: string | null;
  session_alive: boolean;
  marking_enabled: boolean;
}
export interface PendingReq {
  telegram_id: number;
  name: string;
  group: string | null;
  created_at: number;
}
export const getFriendsApi = () =>
  api<Friend[]>('GET', '/friends/');

export const sendFriendRequestApi = (identifier: string) =>
  api('POST', '/friends/request', { identifier });

export const getPendingApi = () =>
  api<PendingReq[]>('GET', '/friends/pending');

export const setMarkingApi = (friendTelegramId: number, enabled: boolean) =>
  api('POST', '/friends/marking', { friend_telegram_id: friendTelegramId, enabled });

export const acceptFriendApi = (requestId: number) =>
  api('POST', '/friends/accept', { request_id: requestId });

export const rejectFriendApi = (requestId: number) =>
  api('POST', '/friends/reject', { request_id: requestId });

export const removeFriendApi = (friendId: number) =>
  api('DELETE', `/friends/${friendId}`);
