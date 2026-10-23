import { useState, useEffect, useRef } from 'preact/hooks';
import type { Html5Qrcode as Html5QrcodeType } from 'html5-qrcode';
import { markAttendanceApi, getFriendsApi, prewarmApi, getScheduleApi } from '../api/client';
import type { Friend } from '../api/client';
import { IconCheck, IconX, IconScan, IconCamera, IconUsers, IconQr } from '../components/icons';
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

type Status = 'pending' | 'marking' | 'done' | 'failed' | 'skipped';
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
  const [zoom, setZoom] = useState(1);
  const [zoomCaps, setZoomCaps] = useState<{ min: number; max: number; step: number } | null>(null);
  const [scanOpen, setScanOpen] = useState(false);  // открыт попап сканирования
  const [camOn, setCamOn] = useState(false);        // пользователь нажал «Открыть камеру»
  const [camScanning, setCamScanning] = useState(false);  // камера реально показывает кадр
  const [camStarting, setCamStarting] = useState(false);

  const scannerRef = useRef<Html5QrcodeType | null>(null);
  const activeRef = useRef(active);
  const sessionRef = useRef<SessionState>('idle');
  const targetsRef = useRef<Target[]>([]);
  const scanIdRef = useRef('');
  const lastTokenRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const lastInvalid = useRef(0);
  const hapticRef = useRef(haptic);
  const lessonRef = useRef<string | null>(null);   // текущая пара (по расписанию) — для истории

  useEffect(() => { activeRef.current = active; }, [active]);
  useEffect(() => { sessionRef.current = session; }, [session]);
  useEffect(() => { targetsRef.current = targets; }, [targets]);
  useEffect(() => { hapticRef.current = haptic; }, [haptic]);

  // Обновляем список друзей при каждом входе на вкладку «Сканер» — чтобы
  // только что добавленный/включённый друг сразу попадал в отметку.
  useEffect(() => {
    if (!active) return;
    getFriendsApi()
      .then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled)))
      .catch(() => {});
  }, [active]);

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
      const res = await markAttendanceApi(text, { only: ids, scanId: scanIdRef.current, lessonName: lessonRef.current });
      const byId = new Map(res.results.map((r) => [r.telegram_id, r]));
      setTargets((ts) =>
        ts.map((t) => {
          if (!ids.includes(t.id)) return t;
          const r = byId.get(t.id);
          if (r?.success) return { ...t, status: 'done', error: null };
          // Сам отключил отметку на этой паре — не отмечаем, не повторяем.
          if (r?.opted_out) return { ...t, status: 'skipped', error: r.error };
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

  // Старт камеры по явному жесту пользователя — в Telegram-вебвью это надёжнее
  // авто-старта (меньше «камера включена, а кадр чёрный»).
  const startCamera = async () => {
    if (scannerRef.current?.isScanning) return;
    setCamStarting(true); setHint('');
    try {
      const { Html5Qrcode } = await import('html5-qrcode');
      let scanner = scannerRef.current;
      if (!scanner) { scanner = new Html5Qrcode('qr-reader', { verbose: false } as any); scannerRef.current = scanner; }
      const cfg = { fps: 15, aspectRatio: 1 };   // без qrbox — рамку рисуем свою
      // Один запрос getUserMedia: ideal-environment не падает, если задней нет
      // (берёт любую) — не дёргаем доступ дважды, меньше запросов разрешения.
      await scanner.start({ facingMode: { ideal: 'environment' } } as any, cfg, handleDecode, () => {});
      setCamScanning(true);
      // Оптический зум, если камера его умеет
      try {
        const caps: any = (scanner as any).getRunningTrackCapabilities?.();
        if (caps?.zoom && typeof caps.zoom.max === 'number' && caps.zoom.max > (caps.zoom.min ?? 1)) {
          setZoomCaps({ min: caps.zoom.min ?? 1, max: caps.zoom.max, step: caps.zoom.step || 0.1 });
        }
      } catch {}
    } catch (e) {
      console.error(e);
      setCamScanning(false);
      setHint('Нет доступа к камере. Разреши её для Telegram в настройках телефона.');
    } finally {
      setCamStarting(false);
    }
  };

  const stopCamera = async () => {
    setCamScanning(false); setZoomCaps(null);
    const s = scannerRef.current;
    if (s) { try { if (s.isScanning) await s.stop(); } catch {} }
  };

  // Камера жива, только пока открыт попап сканирования и вкладка активна.
  // Закрыли попап / ушли с вкладки — останавливаем (освобождаем камеру и чиним
  // «чёрный кадр»); открыли снова и нажали «Открыть камеру» — поднимаем заново.
  useEffect(() => {
    if (camOn && scanOpen && active) startCamera(); else stopCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camOn, scanOpen, active]);

  // Полная остановка при размонтировании.
  useEffect(() => () => {
    const s = scannerRef.current;
    if (s) { try { if (s.isScanning) s.stop().catch(() => {}); } catch {} }
  }, []);

  // «Сканировать QR» — открыть попап, прогреть TLS+сессии и определить текущую пару.
  const openScan = () => {
    hapticRef.current?.impactOccurred?.('light');
    prewarmApi().catch(() => {});
    // свежий список друзей на момент открытия (вдруг добавили прямо сейчас)
    getFriendsApi().then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled))).catch(() => {});
    getScheduleApi(1).then((r) => {
      const now = Date.now() / 1000;
      const l = r.lessons.find((x) => (x.start_epoch || 0) - 900 <= now && now <= (x.end_epoch || (x.start_epoch || 0) + 5400));
      lessonRef.current = l?.name || null;
    }).catch(() => { lessonRef.current = null; });
    setScanOpen(true);
  };

  const closeScan = () => {
    setScanOpen(false);
    setCamOn(false);
    reset();
  };

  // «Открыть камеру» внутри попапа — запуск камеры от жеста пользователя.
  const onStartScan = () => {
    hapticRef.current?.impactOccurred?.('light');
    prewarmApi().catch(() => {});
    setCamOn(true);
  };

  // Завершение сессии: когда никого не осталось в процессе — фиксируем итог.
  useEffect(() => {
    if (session !== 'active' || !targets.length) return;
    const openLeft = targets.some((t) => t.status === 'pending' || t.status === 'marking');
    if (!openLeft) {
      // «skipped» (сам отключил пару) — не ошибка: успех, если нет реальных провалов.
      const allOk = targets.every((t) => t.status === 'done' || t.status === 'skipped');
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

  const applyZoom = (z: number) => {
    setZoom(z);
    (scannerRef.current as any)?.applyVideoConstraints?.({ advanced: [{ zoom: z }] }).catch(() => {});
  };

  const crew = enabledFriends.length;
  const doneCount = targets.filter((t) => t.status === 'done').length;
  const crewLabel = crew ? `ты + ${crew} ${crew === 1 ? 'друг' : crew < 5 ? 'друга' : 'друзей'}` : 'только ты';

  return (
    <div class="screen screen-pad-top">
      {/* Hero — «на месте одним касанием» */}
      <div class="panel enter" style={{ padding: 26, overflow: 'hidden' }}>
        <div class="hero-stage">
          <svg class="hero-orbit a" viewBox="0 0 250 150">
            <ellipse class="ring" cx="125" cy="75" rx="112" ry="40" transform="rotate(-22 125 75)" />
            <circle class="bead" cx="237" cy="75" r="3" transform="rotate(-22 125 75)" />
          </svg>
          <svg class="hero-orbit b" viewBox="0 0 250 150">
            <ellipse class="ring" cx="125" cy="75" rx="112" ry="40" transform="rotate(24 125 75)" />
            <circle class="bead" cx="13" cy="75" r="2.5" transform="rotate(24 125 75)" />
          </svg>
          <div class="hero-glow" />
          <div class="hero-tile">
            <svg class="logo-mark" viewBox="0 0 100 100">
              <polyline class="logo-base" points="18,52 38,68 60,34 82,50" pathLength="100" />
              <polyline class="logo-trace" points="18,52 38,68 60,34 82,50" pathLength="100" />
            </svg>
          </div>
        </div>

        <h1 style={{ fontSize: 33, lineHeight: 1.06, textAlign: 'center', marginTop: 8 }}>
          На&nbsp;месте<br /><span class="sig-text">одним касанием</span>
        </h1>
        <p class="muted" style={{ textAlign: 'center', fontSize: 14.5, lineHeight: 1.5, maxWidth: 300, margin: '13px auto 0' }}>
          Наведи камеру на QR-код пары. Остальное сделает InSync.
        </p>

        <button class="btn" onClick={openScan}
          style={{ marginTop: 24, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <IconScan size={20} stroke={2.2} /> Сканировать QR
          </span>
          <ArrowUpRight />
        </button>

        <div class="row" style={{ justifyContent: 'center', gap: 8, marginTop: 16 }}>
          <span class="faint" style={{ fontSize: 12.5 }}>История отметок — во вкладке «Пары»</span>
        </div>
      </div>

      {/* Попап отметки */}
      {scanOpen && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.62)', backdropFilter: 'blur(5px)',
          display: 'grid', placeItems: 'center', padding: 16 }}>
          <div class="panel enter" style={{ padding: 0, maxWidth: 440, width: '100%', maxHeight: '92vh', overflowY: 'auto' }}>
            {/* Хедер */}
            <div class="row" style={{ justifyContent: 'space-between', padding: '18px 20px 14px', borderBottom: '1px solid var(--hairline)' }}>
              <h3 style={{ fontSize: 20 }}>Отметка на паре</h3>
              <button onClick={closeScan} aria-label="Закрыть"
                style={{ width: 34, height: 34, flex: 'none', borderRadius: '50%', border: '1px solid var(--hairline-2)',
                  background: 'none', color: 'var(--ink-dim)', display: 'grid', placeItems: 'center', cursor: 'pointer' }}>
                <IconX size={18} />
              </button>
            </div>

            {!camOn ? (
              /* Hero — приглашение открыть камеру */
              <div style={{ padding: '26px 22px 26px' }}>
                <div style={{ display: 'grid', placeItems: 'center' }}>
                  <div class="hero-tile" style={{ width: 68, height: 68, borderRadius: 20, animation: 'none' }}>
                    <IconQr size={32} stroke={1.9} />
                  </div>
                </div>
                <h2 style={{ fontSize: 24, textAlign: 'center', marginTop: 22 }}>Отметься одним сканом</h2>
                <p class="muted" style={{ textAlign: 'center', fontSize: 14.5, lineHeight: 1.5, margin: '10px auto 0', maxWidth: 280 }}>
                  Наведи камеру на QR-код пары — остальное сделаем мы.
                </p>
                <div class="row" style={{ justifyContent: 'center', gap: 7, marginTop: 16 }}>
                  <IconUsers size={15} color="var(--ink-faint)" />
                  <span class="faint" style={{ fontSize: 13 }}>
                    {crew ? `С тобой отметятся ${crew} ${crew === 1 ? 'друг' : crew < 5 ? 'друга' : 'друзей'}` : 'Отметим только тебя'}
                  </span>
                </div>
                <button class="btn" onClick={onStartScan}
                  style={{ marginTop: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
                  <IconCamera size={19} /> Открыть камеру
                </button>
              </div>
            ) : (
              /* Камера + статусы */
              <div style={{ padding: '16px 16px 18px' }}>
                <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', borderRadius: 'var(--r-md)', overflow: 'hidden', background: '#000', border: '1px solid var(--hairline)' }}>
                  <div id="qr-reader" style={{ width: '100%', height: '100%' }} />
                  {camStarting && (
                    <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', zIndex: 4 }}><div class="spinner" /></div>
                  )}
                  {!camStarting && !camScanning && (
                    <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', zIndex: 4, padding: 20 }}>
                      <button class="btn" onClick={onStartScan} style={{ width: 'auto', padding: '13px 24px' }}>Перезапустить камеру</button>
                    </div>
                  )}
                  {camScanning && (
                    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2 }}>
                      <div class={'lock-frame' + (locked ? ' lock' : '')}>
                        <div class="corner tl" /><div class="corner tr" /><div class="corner bl" /><div class="corner br" />
                      </div>
                      {burst && <div class="lock-burst"><span /><span /></div>}
                    </div>
                  )}
                  {hint && (
                    <div style={{ position: 'absolute', left: 14, right: 14, bottom: 14, padding: '10px 14px', borderRadius: 'var(--r-sm)',
                      background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(6px)', textAlign: 'center', fontSize: 13.5, fontWeight: 600 }}>
                      {hint}
                    </div>
                  )}
                  {zoomCaps && (
                    <div style={{ position: 'absolute', left: 14, right: 14, bottom: 12, display: 'flex', alignItems: 'center', gap: 10,
                      padding: '8px 12px', borderRadius: 'var(--r-md)', background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(6px)', pointerEvents: 'auto', zIndex: 3 }}>
                      <span style={{ fontSize: 13, opacity: 0.85 }}>Зум</span>
                      <input type="range" min={zoomCaps.min} max={zoomCaps.max} step={zoomCaps.step} value={zoom}
                        onInput={(e) => applyZoom(parseFloat((e.target as HTMLInputElement).value))}
                        style={{ flex: 1, accentColor: 'var(--live)' }} />
                      <span style={{ fontSize: 12, opacity: 0.7, minWidth: 30, textAlign: 'right' }}>{zoom.toFixed(1)}×</span>
                    </div>
                  )}
                </div>

                {/* Кого отмечаем — под камерой */}
                <div class="row" style={{ gap: 12, marginTop: 14 }}>
                  <div class="avatar" style={{ width: 38, height: 38, fontSize: 14 }}>{(user?.first_name || 'Я')[0]}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 15 }}>Отмечаем: {crewLabel}</div>
                    <div class="faint" style={{ fontSize: 12.5 }}>
                      {crew ? `${crew + 1} ${crew + 1 < 5 ? 'человека' : 'человек'} одним сканом` : 'Только тебя'}
                    </div>
                  </div>
                </div>

                {/* Статусы: въезжают при поимке QR */}
                {session !== 'idle' && (
                  <div class="enter" style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div class="row" style={{ justifyContent: 'space-between' }}>
                      <span style={{ fontWeight: 700 }}>
                        <span class="display">{doneCount}</span><span class="faint"> / {targets.length}</span> отмечено
                      </span>
                      {session === 'active'
                        ? <span class="pill"><span class="dot dot-live" /> идёт отметка</span>
                        : <span class="pill"><span class="dot dot-off" /> завершено</span>}
                    </div>
                    <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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
                      <button class="btn" style={{ marginTop: 4 }} onClick={reset}>Отметить снова</button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ArrowUpRight() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M7 17 17 7M9 7h8v8" />
    </svg>
  );
}

function subtitle(t: Target): string {
  switch (t.status) {
    case 'done': return 'отмечен';
    case 'marking': return 'отмечаем…';
    case 'pending': return 'ждём следующий QR…';
    case 'failed': return t.error || 'не удалось';
    case 'skipped': return 'не отмечается на этой паре';
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
  if (status === 'skipped') {
    // сам отключил пару — нейтральный «минус», не провал
    return <div style={{ ...box, background: 'var(--bg-2)', color: 'var(--ink-faint)' }}><span style={{ width: 12, height: 2.6, borderRadius: 2, background: 'currentColor' }} /></div>;
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
