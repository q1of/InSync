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

async function api<T = any>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
  const initData = getInitData();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': initData,
  };

  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : undefined;
  try {
    const response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller?.signal,
    });

    if (!response.ok) {
      let errorMsg = 'Ошибка сервера';
      try {
        const errorData = await response.json();
        errorMsg = errorData.detail || errorMsg;
      } catch {}
      // status отличает ответ сервера от обрыва связи (у него status нет).
      throw Object.assign(new Error(errorMsg), { status: response.status });
    }

    return await response.json();
  } finally {
    clearTimeout(timer);
  }
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
export const getSessionApi = (check = true, timeoutMs?: number) =>
  api<SessionStatus>('GET', `/auth/session?check=${check}`, undefined, timeoutMs);

export interface Lesson {
  name: string; type: string; room: string; building: string; teacher: string;
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

// Настройки уведомлений
export interface NotifPrefs { marked: boolean; login: boolean; session: boolean; }
export const getNotifPrefsApi = () =>
  api<NotifPrefs>('GET', '/settings/notifications');
export const setNotifPrefApi = (key: keyof NotifPrefs, enabled: boolean) =>
  api('POST', '/settings/notifications', { key, enabled });

// Attendance
export interface MarkResult {
  user_name: string;
  telegram_id: number;
  success: boolean;
  error: string | null;
  retriable: boolean;
  opted_out: boolean;   // сам отключил отметку на этой паре — не ошибка
  already_marked: boolean;   // уже был отмечен на этой паре — не новая отметка
}
export interface MarkResponse { results: MarkResult[]; total: number; successful: number; }

// only — подмножество telegram_id (повтор только непрошедшим); scanId — склейка волн в истории
// Прогрев сессий в кеш перед сканом (чтобы первая отметка была быстрой)
export const prewarmApi = () => api<{ ready: number }>('POST', '/attendance/prewarm');

export const markAttendanceApi = (qrData: string, opts?: { only?: number[]; scanId?: string; lessonName?: string | null; lessonStart?: number | null;
  lessonRoom?: string | null; lessonBuilding?: string | null }) =>
  api<MarkResponse>('POST', '/attendance/mark', {
    qr_data: qrData,
    only: opts?.only ?? null,
    scan_id: opts?.scanId ?? null,
    lesson_name: opts?.lessonName ?? null,
    lesson_start: opts?.lessonStart ?? null,
    lesson_room: opts?.lessonRoom ?? null,
    lesson_building: opts?.lessonBuilding ?? null,
  }, MARK_TIMEOUT_MS);

// Отметка 10+ человек на «холодном» сервере занимает секунды, иногда десятки.
// Ставим запас, чтобы не оборвать успешную волну; после обрыва сканер сверит итог по истории.
const MARK_TIMEOUT_MS = 45000;

// Кого скан отметит вместе с пользователем (друзья и группа)
export interface MarkTarget { telegram_id: number; name: string; }
export const getMarkTargetsApi = () => api<MarkTarget[]>('GET', '/attendance/targets');

// Группа: создаёт любой, остальные вступают по коду или ссылке; тумблер «отмечаться с группой»
export interface GroupMember { telegram_id: number; name: string; is_self: boolean; enabled: boolean; session_alive: boolean; }
export interface GroupInfo { name: string; code: string; invite_link: string | null; enabled: boolean; members: GroupMember[]; }
export const getGroupApi = () => api<{ group: GroupInfo | null }>('GET', '/group');
export const createGroupApi = (name: string) => checked(api<{ success: boolean; error?: string; group: GroupInfo }>('POST', '/group/create', { name }));
export const joinGroupApi = (code: string) => checked(api<{ success: boolean; error?: string; group: GroupInfo }>('POST', '/group/join', { code }));
export const leaveGroupApi = () => api('POST', '/group/leave');
export const setGroupMarkingApi = (enabled: boolean) => api('POST', '/group/marking', { enabled });

// Пропуск: события турникетов за день
export interface PassEvent { time: number; kind: 'enter' | 'exit' | 'move'; title: string; place: string; }
export interface PassDay { day: string; events: PassEvent[]; }
export const getPassDayApi = (day: string) => api<PassDay>('GET', `/pass/day?day=${day}`);

export interface ScanTarget { telegram_id: number; name: string; is_self: boolean; success: boolean; already_marked: boolean; error: string | null; }
// kind 'mine' — мой скан; 'incoming' — меня отметил друг/группа (by — кто именно).
export interface Scan {
  kind?: 'mine' | 'incoming';
  created_at: number; lesson_name?: string | null; scan_id?: string | null;
  targets?: ScanTarget[]; successful?: number; total?: number; by?: string[];
}
export const getHistoryApi = () =>
  api<{ scans: Scan[] }>('GET', '/attendance/history');

// Friends. Отказы бэкенд возвращает как 200 с success: false — превращаем их в ошибку.
const checked = async <T extends { success?: boolean; error?: string }>(request: Promise<T>): Promise<T> => {
  const res = await request;
  if (res?.success === false) throw new Error(res.error || 'Не удалось');
  return res;
};

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
  checked(api('POST', '/friends/request', { identifier }));

export const getPendingApi = () =>
  api<PendingReq[]>('GET', '/friends/pending');

export const setMarkingApi = (friendTelegramId: number, enabled: boolean) =>
  api('POST', '/friends/marking', { friend_telegram_id: friendTelegramId, enabled });

export const acceptFriendApi = (requestId: number) =>
  checked(api('POST', '/friends/accept', { request_id: requestId }));

export const rejectFriendApi = (requestId: number) =>
  api('POST', '/friends/reject', { request_id: requestId });

export const removeFriendApi = (friendId: number) =>
  api('DELETE', `/friends/${friendId}`);
