import { useState, useEffect, useRef } from 'preact/hooks';
import type { Html5Qrcode as Html5QrcodeType } from 'html5-qrcode';
import { markAttendanceApi, getFriendsApi } from '../api/client';
import type { Friend } from '../api/client';
import { IconCheck, IconX } from '../components/icons';
import { useTelegram } from '../hooks/useTelegram';

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

// Валиден только QR пары МИРЭА: ссылка pulse.mirea.ru с token, параметр token=UUID,
// либо сам UUID. Всё остальное (случайные коды из интернета) игнорируем.
function isAttendanceQr(text: string): boolean {
  if (!text || text.length > 512) return false;
  const t = text.trim();
  if (/pulse\.mirea\.ru/i.test(t) && UUID.test(t)) return true;
  if (/[?&]token=/i.test(t) && UUID.test(t)) return true;
  if (/^[0-9a-f-]{36}$/i.test(t) && UUID.test(t)) return true;
  return false;
}

// Токен (UUID) из QR — чтобы поймать именно СМЕНУ кода (меняется каждые 5с).
function qrToken(text: string): string | null {
  const m = text.match(/[?&]token=([0-9a-fA-F-]{36})/);
  if (m) return m[1].toLowerCase();
  const u = text.match(UUID);
  return u ? u[0].toLowerCase() : null;
}

type Status = 'pending' | 'marking' | 'done' | 'failed';
interface Target { id: number; name: string; is_self: boolean; status: Status; error?: string | null; attempts: number; }
type SessionState = 'idle' | 'active' | 'done';

// Сессию считаем «мёртвой» (нужен перезаход), только если человеку пришло
// больше стольких неудачных запросов. Временные причины (ретраибл) повторяются
// без этого лимита.
const MAX_DEAD_ATTEMPTS = 3;

interface Props { active: boolean }

export function Scanner({ active }: Props) {
  const { user, haptic } = useTelegram();
  const [enabledFriends, setEnabledFriends] = useState<Friend[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [session, setSession] = useState<SessionState>('idle');
  const [burst, setBurst] = useState(false);
  const [locked, setLocked] = useState(false);
  const [hint, setHint] = useState('');

  const scannerRef = useRef<Html5QrcodeType | null>(null);
  const activeRef = useRef(active);
  const sessionRef = useRef<SessionState>('idle');
  const targetsRef = useRef<Target[]>([]);
  const scanIdRef = useRef('');
  const lastTokenRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const lastInvalid = useRef(0);
  const hapticRef = useRef(haptic);

  useEffect(() => { activeRef.current = active; }, [active]);
  useEffect(() => { sessionRef.current = session; }, [session]);
  useEffect(() => { targetsRef.current = targets; }, [targets]);
  useEffect(() => { hapticRef.current = haptic; }, [haptic]);

  useEffect(() => {
    getFriendsApi()
      .then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled)))
      .catch(() => setEnabledFriends([]));
  }, []);

  const buildTargets = (): Target[] => {
    const self: Target = { id: user?.id ?? 0, name: user?.first_name || 'Ты', is_self: true, status: 'pending', attempts: 0 };
    const fr: Target[] = enabledFriends.map((f) => ({ id: f.telegram_id, name: f.name, is_self: false, status: 'pending', attempts: 0 }));
    return [self, ...fr];
  };

  // Одна волна отметки по текущему QR для набора id.
  const fireWave = async (text: string, ids: number[]) => {
    if (!ids.length || inFlightRef.current) return;
    inFlightRef.current = true;
    setTargets((ts) => ts.map((t) => (ids.includes(t.id) ? { ...t, status: 'marking' } : t)));
    try {
      const res = await markAttendanceApi(text, { only: ids, scanId: scanIdRef.current });
      const byId = new Map(res.results.map((r) => [r.telegram_id, r]));
      setTargets((ts) =>
        ts.map((t) => {
          if (!ids.includes(t.id)) return t;
          const r = byId.get(t.id);
          if (r?.success) return { ...t, status: 'done', error: null };
          // Временная причина (сеть, «пара не началась») — повторяем без лимита.
          if (r?.retriable) return { ...t, status: 'pending', error: r.error };
          // Мёртвая/неизвестная причина (нет ответа или «нужно перезайти»):
          // считаем попытку и сдаёмся, только когда их стало больше MAX_DEAD_ATTEMPTS.
          const attempts = t.attempts + 1;
          const error = r ? r.error : 'Нужно перезайти';
          if (attempts > MAX_DEAD_ATTEMPTS) return { ...t, status: 'failed', error, attempts };
          return { ...t, status: 'pending', error, attempts };
        }),
      );
    } catch {
      // сеть/сервер — не трогаем, повторим на следующем QR
      setTargets((ts) => ts.map((t) => (ids.includes(t.id) && t.status === 'marking' ? { ...t, status: 'pending' } : t)));
    } finally {
      inFlightRef.current = false;
    }
  };

  const handleDecode = (text: string) => {
    if (!activeRef.current) return;              // отмечаем только на вкладке «Сканер»
    if (sessionRef.current === 'done') return;   // дождались конца — ждём «Отметить снова»

    if (!isAttendanceQr(text)) {
      const now = Date.now();
      if (now - lastInvalid.current > 2500) {
        lastInvalid.current = now;
        setHint('Это не QR-код пары');
        setTimeout(() => setHint(''), 2200);
      }
      return;
    }

    const token = qrToken(text);
    if (!token || token === lastTokenRef.current) return;  // тот же QR — ждём смены (каждые 5с)
    lastTokenRef.current = token;

    // Визуальный «захват» на каждый новый QR
    hapticRef.current?.impactOccurred?.('light');
    setLocked(true); setTimeout(() => setLocked(false), 450);
    setBurst(true); setTimeout(() => setBurst(false), 500);

    if (sessionRef.current === 'idle') {
      const list = buildTargets();
      if (!list.length) return;
      scanIdRef.current = Math.random().toString(36).slice(2) + Date.now().toString(36);
      targetsRef.current = list;
      setTargets(list);
      sessionRef.current = 'active';
      setSession('active');
      fireWave(text, list.map((t) => t.id));
    } else {
      // active — повторяем только тем, кто ещё не отметился
      const ids = targetsRef.current.filter((t) => t.status === 'pending').map((t) => t.id);
      if (ids.length) fireWave(text, ids);
    }
  };

  // Камера стартует один раз и работает всё время (нужно непрерывно читать QR,
  // чтобы ловить смену токена). На переключении вкладок не трогаем — Scanner
  // просто скрывается в app.tsx. Полная остановка только при размонтировании.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { Html5Qrcode } = await import('html5-qrcode');
        if (cancelled) return;
        const scanner = new Html5Qrcode('qr-reader', { verbose: false } as any);
        scannerRef.current = scanner;
        await scanner.start(
          { facingMode: 'environment' },
          { fps: 20, aspectRatio: 1 },   // без qrbox — рамку рисуем свою
          handleDecode,
          () => {},
        );
      } catch (e) {
        console.error(e);
      }
    })();
    return () => {
      cancelled = true;
      const s = scannerRef.current;
      if (s) { try { if (s.isScanning) s.stop().catch(() => {}); } catch {} }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Завершение сессии: когда никого не осталось в процессе — фиксируем итог.
  useEffect(() => {
    if (session !== 'active' || !targets.length) return;
    const openLeft = targets.some((t) => t.status === 'pending' || t.status === 'marking');
    if (!openLeft) {
      const allOk = targets.every((t) => t.status === 'done');
      hapticRef.current?.notificationOccurred?.(allOk ? 'success' : 'error');
      sessionRef.current = 'done';
      setSession('done');
    }
  }, [targets, session]);

  const reset = () => {
    lastTokenRef.current = null;
    scanIdRef.current = '';
    targetsRef.current = [];
    setTargets([]);
    sessionRef.current = 'idle';
    setSession('idle');
    // обновим список друзей на случай, если тумблеры меняли
    getFriendsApi().then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled))).catch(() => {});
  };

  const crew = enabledFriends.length;
  const doneCount = targets.filter((t) => t.status === 'done').length;
  const today = new Date().toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });

  return (
    <div class="screen screen-pad-top">
      <div style={{ marginBottom: 22 }}>
        <p class="eyebrow" style={{ textTransform: 'capitalize' }}>{today}</p>
        <h1 style={{ fontSize: 28, marginTop: 6 }}>Привет, {user?.first_name || 'студент'}</h1>
        <p class="muted" style={{ marginTop: 6 }}>Наведи на QR-код пары — держи, пока все отметятся</p>
      </div>

      {/* Камера как «цель захвата сигнала» */}
      <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', borderRadius: 'var(--r-xl)', overflow: 'hidden', background: '#000', border: '1px solid var(--hairline)' }}>
        <div id="qr-reader" style={{ width: '100%', height: '100%' }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <div class={'lock-frame' + (locked ? ' lock' : '')}>
            <div class="corner tl" /><div class="corner tr" /><div class="corner bl" /><div class="corner br" />
          </div>
          {burst && <div class="lock-burst"><span /><span /></div>}
        </div>
        {hint && (
          <div style={{ position: 'absolute', left: 16, right: 16, bottom: 16, padding: '11px 14px', borderRadius: 'var(--r-sm)',
            background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)', textAlign: 'center', fontSize: 14, fontWeight: 600 }}>
            {hint}
          </div>
        )}
      </div>

      {/* Idle: кого будем отмечать */}
      {session === 'idle' && (
        <div class="panel" style={{ marginTop: 18, padding: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <div class="avatar" style={{ width: 40, height: 40, fontSize: 15 }}>{(user?.first_name || 'Я')[0]}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600 }}>Отмечаем: ты{crew ? ` + ${crew} ${crew === 1 ? 'друг' : crew < 5 ? 'друга' : 'друзей'}` : ''}</div>
            <div class="faint" style={{ fontSize: 13 }}>{crew ? 'Кого отмечать — во вкладке «Друзья»' : 'Отмечаем только тебя'}</div>
          </div>
        </div>
      )}

      {/* Active / Done: живой список статусов */}
      {session !== 'idle' && (
        <div class="panel" style={{ marginTop: 18, padding: 8 }}>
          <div style={{ padding: '8px 12px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 700 }}>
              <span class="display">{doneCount}</span>
              <span class="faint"> / {targets.length}</span> отмечено
            </span>
            {session === 'active'
              ? <span class="pill"><span class="dot dot-live" /> идёт отметка</span>
              : <span class="pill"><span class="dot dot-off" /> завершено</span>}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {targets.map((t) => (
              <div key={t.id} class="row" style={{ padding: '10px 12px', borderRadius: 'var(--r-sm)', background: 'var(--bg-2)', border: '1px solid var(--hairline)' }}>
                <StatusBadge status={t.status} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {t.is_self ? 'Ты' : t.name}
                  </div>
                  <div class="faint" style={{ fontSize: 12.5 }}>{subtitle(t)}</div>
                </div>
              </div>
            ))}
          </div>

          {session === 'done' && (
            <button class="btn" style={{ marginTop: 10 }} onClick={reset}>Отметить снова</button>
          )}
        </div>
      )}
    </div>
  );
}

function subtitle(t: Target): string {
  switch (t.status) {
    case 'done': return 'отмечен';
    case 'marking': return 'отмечаем…';
    case 'pending': return 'ждём следующий QR…';
    case 'failed': return t.error || 'не удалось';
  }
}

function StatusBadge({ status }: { status: Status }) {
  const box = { width: 30, height: 30, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none' } as const;
  if (status === 'done') {
    return <div style={{ ...box, background: 'var(--live-dim)', color: 'var(--live)' }}><IconCheck size={16} stroke={2.6} /></div>;
  }
  if (status === 'failed') {
    return <div style={{ ...box, background: 'var(--miss-dim)', color: 'var(--miss)' }}><IconX size={16} stroke={2.6} /></div>;
  }
  if (status === 'marking') {
    return (
      <div style={box}>
        <span style={{ width: 18, height: 18, borderRadius: '50%', border: '2px solid rgba(255,255,255,0.18)', borderTopColor: 'var(--ink)', animation: 'spin 0.9s linear infinite', display: 'inline-block' }} />
      </div>
    );
  }
  // pending
  return <div style={box}><span style={{ width: 20, height: 20, borderRadius: '50%', border: '1.5px solid var(--hairline-2)' }} /></div>;
}
