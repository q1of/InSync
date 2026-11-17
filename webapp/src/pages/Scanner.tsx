import { useState, useEffect, useRef } from 'preact/hooks';
import { markAttendanceApi, getMarkTargetsApi, prewarmApi, getScheduleApi, getSessionApi, getHistoryApi } from '../api/client';
import type { MarkTarget, ScanTarget } from '../api/client';
import { IconCheck, IconX, IconScan, IconUsers } from '../components/icons';
import { telegramReady, useTelegram } from '../hooks/useTelegram';

const SDK_WAIT_MS = 3000;
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function isAttendanceQr(text: string): boolean {
  if (!text || text.length > 512) return false;
  const t = text.trim();
  if (/pulse\.mirea\.ru/i.test(t) && UUID.test(t)) return true;
  if (/[?&]token=/i.test(t) && UUID.test(t)) return true;
  if (/^[0-9a-f-]{36}$/i.test(t) && UUID.test(t)) return true;
  return false;
}
function qrToken(text: string): string | null {
  const m = text.match(/[?&]token=([0-9a-fA-F-]{36})/);
  if (m) return m[1].toLowerCase();
  const u = text.match(UUID);
  return u ? u[0].toLowerCase() : null;
}

type Status = 'pending' | 'marking' | 'done' | 'already' | 'failed' | 'skipped';
interface Target {
  id: number; name: string; is_self: boolean; status: Status; error?: string | null; attempts: number;
}

// Кто уже отмечен в этом скане по данным сервера (telegram_id → запись истории).
// После обрыва сервер мог ещё дописывать историю — опрашиваем несколько раз.
async function markedInScan(scanId: string, tries = 3): Promise<Map<number, ScanTarget>> {
  for (let i = 0; i < tries; i++) {
    try {
      const { scans } = await getHistoryApi();
      const scan = scans.find((s) => s.scan_id === scanId);
      const m = new Map((scan?.targets || []).filter((t) => t.success).map((t) => [t.telegram_id, t] as const));
      if (m.size || i === tries - 1) return m;
    } catch { /* пробуем ещё раз */ }
    await new Promise((r) => setTimeout(r, 1200));
  }
  return new Map();
}

const RESULT_AFTER_NATIVE_MS = 200;
const MAX_ATTEMPTS = 3;   // максимум 3 QR на человека: дальше сканер не держим

interface Props { active: boolean }

export function Scanner({ active }: Props) {
  const { user, haptic, webApp } = useTelegram();
  const [crewList, setCrewList] = useState<MarkTarget[]>([]);   // друзья и группа
  const [targets, setTargets] = useState<Target[]>([]);
  const [resultOpen, setResultOpen] = useState(false);   // мобайл: попап итога после нативного скана
  const [scanning, setScanning] = useState(false);       // мобайл: открыт нативный сканер
  const [camOpen, setCamOpen] = useState(false);         // десктоп: попап с нашей камерой
  const [camScanning, setCamScanning] = useState(false);
  const [camStarting, setCamStarting] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [zoomCaps, setZoomCaps] = useState<{ min: number; max: number; step: number } | null>(null);

  const targetsRef = useRef<Target[]>([]);
  const scanIdRef = useRef('');
  const lastTokenRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const doneRef = useRef(false);
  const lessonRef = useRef<string | null>(null);
  const lessonStartRef = useRef<number | null>(null);
  const lessonPlaceRef = useRef<{ room: string; building: string } | null>(null);
  const hapticRef = useRef(haptic);
  const scannerRef = useRef<any>(null);
  const activeRef = useRef(active);

  useEffect(() => { hapticRef.current = haptic; }, [haptic]);
  useEffect(() => { targetsRef.current = targets; }, [targets]);
  useEffect(() => { activeRef.current = active; }, [active]);
  const scanningRef = useRef(false);
  useEffect(() => { scanningRef.current = scanning; }, [scanning]);
  // Была ли уже хоть одна попытка отметки в этом скане.
  const hadAttempt = () => targetsRef.current.some((t) => t.attempts > 0 || t.status !== 'pending');

  const [sessionBad, setSessionBad] = useState(false);

  useEffect(() => {
    if (!active) return;
    getMarkTargetsApi().then(setCrewList).catch(() => {});
    // Проактивно проверяем свою сессию — чтобы перезайти ЗАРАНЕЕ, а не когда уже висит QR.
    // logged_in=false значит слёт; valid=false при logged_in=true — временный сбой, баннер не нужен.
    getSessionApi(true).then((s) => setSessionBad(!s.logged_in)).catch(() => {});
  }, [active]);

  const nativeSupported = (tg: any) => {
    if (!tg?.showScanQrPopup) return false;
    const p = (tg.platform || '').toLowerCase();
    return p === 'ios' || p === 'android';   // на ПК нативный сканер ботов не поддерживается
  };

  const buildTargets = (): Target[] => {
    const self: Target = { id: user?.id ?? 0, name: user?.first_name || 'Ты', is_self: true, status: 'pending', attempts: 0 };
    const fr: Target[] = crewList.map((f) => ({ id: f.telegram_id, name: f.name, is_self: false, status: 'pending', attempts: 0 }));
    return [self, ...fr];
  };

  const fireWave = async (text: string, ids: number[]) => {
    if (!ids.length || inFlightRef.current) return;
    inFlightRef.current = true;
    setTargets((ts) => ts.map((t) => (ids.includes(t.id) ? { ...t, status: 'marking' } : t)));
    try {
      const res = await markAttendanceApi(text, {
        only: ids, scanId: scanIdRef.current, lessonName: lessonRef.current, lessonStart: lessonStartRef.current,
        lessonRoom: lessonPlaceRef.current?.room, lessonBuilding: lessonPlaceRef.current?.building,
      });
      const byId = new Map(res.results.map((r) => [r.telegram_id, r]));
      setTargets((ts) =>
        ts.map((t) => {
          if (!ids.includes(t.id)) return t;
          const r = byId.get(t.id);
          if (r?.success) return { ...t, status: r.already_marked ? 'already' : 'done', error: null };
          if (r?.opted_out) return { ...t, status: 'skipped', error: r.error };
          const attempts = t.attempts + 1;
          const error = r ? r.error : 'Нужно перезайти';
          if (attempts >= MAX_ATTEMPTS) return { ...t, status: 'failed', error, attempts };
          return { ...t, status: 'pending', error, attempts };
        }),
      );
    } catch {
      // Ответ не дошёл (обрыв связи, долгий ответ), а сервер мог уже всех отметить:
      // спрашиваем историю этого скана и показываем, кто реально отмечен.
      const marked = await markedInScan(scanIdRef.current);
      setTargets((ts) => ts.map((t) => {
        if (!ids.includes(t.id) || t.status !== 'marking') return t;
        const m = marked.get(t.id);
        return m ? { ...t, status: m.already_marked ? 'already' : 'done', error: null } : { ...t, status: 'pending' };
      }));
    } finally {
      inFlightRef.current = false;
    }
  };

  // Общий обработчик кадра QR (и для нативного сканера, и для нашей камеры).
  const handleQr = (text: string) => {
    if (doneRef.current) return;
    if (!isAttendanceQr(text)) return;
    const token = qrToken(text);
    if (!token || token === lastTokenRef.current) return;
    if (inFlightRef.current) return;   // прошлая отметка ещё идёт — ждём её, камеру не закрываем
    lastTokenRef.current = token;
    const ids = targetsRef.current.filter((t) => t.status === 'pending').map((t) => t.id);
    if (!ids.length) return;
    // QR пары пойман: камеру сразу убираем, а ход отметки показываем в нашем окне —
    // так видно, что скан прошёл, и не кажется, что всё зависло.
    doneRef.current = true;
    hapticRef.current?.impactOccurred?.('heavy');
    fireWave(text, ids);
    closeScannerShowResult();
  };

  const closeScannerShowResult = () => {
    const wasNative = scanningRef.current;
    if (wasNative) { try { (webApp as any)?.closeScanQrPopup?.(); } catch {} setScanning(false); }
    setCamOpen(false);
    openResult(wasNative);
  };

  // Пока нативный сканер Telegram закрывается, WebView перерисовывается — попап в этот
  // момент дёргается. Поэтому после нативного сканера открываем его с небольшой паузой.
  const openResult = (afterNative: boolean) => {
    if (afterNative) setTimeout(() => setResultOpen(true), RESULT_AFTER_NATIVE_MS);
    else setResultOpen(true);
  };

  // Отметка дошла до итога (никого «в процессе») — вибрация: успех или «не всех».
  const wasMarkingRef = useRef(false);
  useEffect(() => {
    const marking = targets.some((t) => t.status === 'marking');
    if (wasMarkingRef.current && !marking) {
      const allOk = targets.every((t) => t.status === 'done' || t.status === 'already' || t.status === 'skipped');
      hapticRef.current?.notificationOccurred?.(allOk ? 'success' : 'warning');
    }
    wasMarkingRef.current = marking;
  }, [targets]);

  // Пользователь сам закрыл нативный сканер, не поймав QR.
  useEffect(() => {
    const tg: any = webApp;
    if (!tg?.onEvent) return;
    const onClosed = () => {
      if (doneRef.current) return;
      doneRef.current = true;
      setScanning(false);
      if (hadAttempt()) openResult(true);   // это был «досканировать» — возвращаем окно итога
    };
    tg.onEvent('scanQrPopupClosed', onClosed);
    return () => tg.offEvent?.('scanQrPopupClosed', onClosed);
  }, [webApp]);

  // --- Наша камера (десктоп / нет нативного сканера) ---
  const startCamera = async () => {
    if (scannerRef.current?.isScanning) return;
    setCamStarting(true);
    try {
      const { Html5Qrcode } = await import('html5-qrcode');
      let scanner = scannerRef.current;
      if (!scanner) { scanner = new Html5Qrcode('qr-reader', { verbose: false } as any); scannerRef.current = scanner; }
      const cfg = { fps: 15, aspectRatio: 1 };
      try {
        await scanner.start({ facingMode: 'environment' }, cfg, handleQr, () => {});
      } catch {
        await scanner.start({ facingMode: 'user' }, cfg, handleQr, () => {});
      }
      setCamScanning(true);
      try {
        const caps: any = (scanner as any).getRunningTrackCapabilities?.();
        if (caps?.zoom && typeof caps.zoom.max === 'number' && caps.zoom.max > (caps.zoom.min ?? 1)) {
          setZoomCaps({ min: caps.zoom.min ?? 1, max: caps.zoom.max, step: caps.zoom.step || 0.1 });
        }
      } catch {}
    } catch (e) {
      console.error(e);
      setCamScanning(false);
    } finally {
      setCamStarting(false);
    }
  };
  const stopCamera = async () => {
    setCamScanning(false); setZoomCaps(null);
    const s = scannerRef.current;
    if (s) { try { if (s.isScanning) await s.stop(); } catch {} }
  };
  useEffect(() => {
    if (camOpen && active) startCamera(); else stopCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camOpen, active]);
  useEffect(() => () => { const s = scannerRef.current; if (s) { try { if (s.isScanning) s.stop().catch(() => {}); } catch {} } }, []);

  const applyZoom = (z: number) => {
    setZoom(z);
    (scannerRef.current as any)?.applyVideoConstraints?.({ advanced: [{ zoom: z }] }).catch(() => {});
  };

  const prepare = () => {
    hapticRef.current?.impactOccurred?.('light');
    prewarmApi().catch(() => {});
    getMarkTargetsApi().then(setCrewList).catch(() => {});
    getScheduleApi(1).then((r) => {
      const now = Date.now() / 1000;
      const l = r.lessons.find((x) => (x.start_epoch || 0) - 900 <= now && now <= (x.end_epoch || (x.start_epoch || 0) + 5400));
      lessonRef.current = l?.name || null;
      lessonStartRef.current = l?.start_epoch ? Math.round(l.start_epoch) : null;
      lessonPlaceRef.current = l ? { room: l.room, building: l.building } : null;
    }).catch(() => { lessonRef.current = null; lessonStartRef.current = null; lessonPlaceRef.current = null; });
    const list = buildTargets();
    scanIdRef.current = Math.random().toString(36).slice(2) + Date.now().toString(36);
    lastTokenRef.current = null;
    doneRef.current = false;
    targetsRef.current = list;
    setTargets(list);
    return list;
  };

  // Открыть камеру (нативный сканер Telegram или нашу на ПК) для тех, кто ещё не отмечен.
  const openScanner = async (text: string) => {
    doneRef.current = false;
    lastTokenRef.current = null;
    setResultOpen(false);
    // На быстрый тап сразу после открытия SDK может ещё грузиться.
    const tg = await telegramReady(SDK_WAIT_MS);
    if (nativeSupported(tg)) {
      setScanning(true);
      scanningRef.current = true;
      try {
        tg.showScanQrPopup({ text }, (qr: string) => { handleQr(qr); return false; });
      } catch { setScanning(false); scanningRef.current = false; }
    } else {
      setCamOpen(true);   // десктоп: наш попап с камерой
    }
  };

  const startScan = () => {
    if (!prepare().length) return;
    openScanner('Наведи на QR-код пары');
  };

  // Досканировать только тех, кого не вышло отметить с прошлого QR.
  const retryScan = () => {
    hapticRef.current?.impactOccurred?.('light');
    prewarmApi().catch(() => {});
    openScanner('Наведи на QR — досканируем остальных');
  };

  // На ПК: закрыли камеру, не поймав QR.
  const closeCamera = () => {
    doneRef.current = true;
    setCamOpen(false);
    if (hadAttempt()) setResultOpen(true);
  };

  const crew = crewList.length;
  // Друзья и группа вместе: «человек» склоняем по числу (2 человека, 5 человек, 21 человек).
  // Считаем вместе с собой — как в карточке группы, чтобы числа совпадали.
  const total = crew + 1;
  const few = total % 10 >= 2 && total % 10 <= 4 && (total % 100 < 12 || total % 100 > 14);
  const crewLine = crew ? `Отметим ${total} ${few ? 'человека' : 'человек'}` : 'Отметим только тебя';
  const marked = targets.filter((t) => t.status === 'done' || t.status === 'already');
  const inProgress = targets.some((t) => t.status === 'marking');
  const leftover = targets.filter((t) => t.status === 'pending').length;   // можно досканировать

  // Показываем всех: кто отмечен, кто был отмечен раньше и кого не вышло отметить (с причиной).
  const resultList = () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {targets.map((t) => (
        <div key={t.id} class="row" style={{ padding: '10px 12px', borderRadius: 'var(--r-sm)', background: 'var(--bg-2)', border: '1px solid var(--hairline)' }}>
          <StatusBadge status={t.status === 'pending' ? 'failed' : t.status} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {t.is_self ? 'Ты' : t.name}
            </div>
            <div class="faint" style={{ fontSize: 12.5, lineHeight: 1.35 }}>{statusNote(t)}</div>
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div class="screen screen-pad-top">
      {sessionBad && (
        <div class="panel" style={{ padding: '12px 14px', marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center',
          background: 'var(--miss-dim)', border: '1px solid rgba(255,91,123,0.3)' }}>
          <span style={{ fontSize: 18 }}>⚠️</span>
          <span style={{ fontSize: 13, lineHeight: 1.4 }}>
            Сессия Пульса слетела — зайди заново (Профиль → Выйти), иначе отметка не сработает.
          </span>
        </div>
      )}

      {/* Главная: большой знак «на фоне», без карточки и рамок */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        textAlign: 'center', minHeight: '62vh' }}>
        <div style={{ position: 'relative', marginBottom: 30 }}>
          <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
            width: 300, height: 300, borderRadius: '50%', pointerEvents: 'none',
            background: 'radial-gradient(circle, rgba(255,255,255,0.11), transparent 60%)' }} />
          <svg class="logo-mark" viewBox="0 0 100 100" style={{ width: 132, height: 132, position: 'relative' }}>
            <polyline class="logo-base" points="18,52 38,68 60,34 82,50" pathLength="100" />
            <polyline class="logo-trace" points="18,52 38,68 60,34 82,50" pathLength="100" />
          </svg>
        </div>

        <h1 style={{ fontSize: 30, lineHeight: 1.08 }}>Лучший бот<br /><span class="sig-text">для отметки на паре</span></h1>
        <p class="muted" style={{ fontSize: 14.5, lineHeight: 1.5, maxWidth: 280, margin: '14px auto 0' }}>
          Отметься на паре — за себя и друзей — одним сканом.
        </p>

        <button class="btn" onClick={startScan}
          style={{ marginTop: 28, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, fontSize: 16 }}>
          <IconScan size={20} stroke={2.2} /> Сканировать QR
        </button>

        <div class="row" style={{ justifyContent: 'center', gap: 7, marginTop: 16 }}>
          <IconUsers size={14} color="var(--ink-faint)" />
          <span class="faint" style={{ fontSize: 13 }}>{crewLine}</span>
        </div>
      </div>

      {/* Единый попап итога (телефон и ПК) */}
      {resultOpen && (
        <Popup title="Отметка на паре" onClose={() => setResultOpen(false)}>
          <div class="row" style={{ marginBottom: 12, gap: 10 }}>
            <span style={{ fontWeight: 700 }}>
              <span class="display">{marked.length}</span><span class="faint"> / {targets.length}</span> отмечено
            </span>
            {inProgress && (
              <span class="row faint" style={{ marginLeft: 'auto', gap: 7, fontSize: 13 }}>
                <span class="spinner" style={{ display: 'inline-block', width: 14, height: 14, borderWidth: 2 }} /> QR принят, отмечаем…
              </span>
            )}
          </div>
          {resultList()}
          {!inProgress && (leftover ? (
            <button class="btn" style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }} onClick={retryScan}>
              <IconScan size={19} stroke={2.2} /> Досканировать ({leftover})
            </button>
          ) : (
            <button class="btn" style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }} onClick={startScan}>
              <IconScan size={19} stroke={2.2} /> Сканировать снова
            </button>
          ))}
        </Popup>
      )}

      {/* Десктоп: попап только с камерой (без статусов снизу) + уголки как в TG */}
      {camOpen && (
        <Popup title="Отметка на паре" onClose={closeCamera}>
          <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', borderRadius: 'var(--r-md)', overflow: 'hidden', background: '#000', border: '1px solid var(--hairline)' }}>
            <div id="qr-reader" style={{ width: '100%', height: '100%' }} />
            {camScanning && (
              <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', pointerEvents: 'none', zIndex: 2 }}>
                <div style={{ position: 'relative', width: '64%', aspectRatio: '1 / 1', maxWidth: 240 }}>
                  <span class="scan-corner tl" /><span class="scan-corner tr" /><span class="scan-corner bl" /><span class="scan-corner br" />
                </div>
              </div>
            )}
            {camStarting && <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', zIndex: 4 }}><div class="spinner" /></div>}
            {!camStarting && !camScanning && (
              <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', zIndex: 4, padding: 20 }}>
                <button class="btn" onClick={() => startCamera()} style={{ width: 'auto', padding: '13px 24px' }}>Перезапустить камеру</button>
              </div>
            )}
            {zoomCaps && (
              <div style={{ position: 'absolute', left: 14, right: 14, bottom: 12, display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 12px', borderRadius: 'var(--r-md)', background: 'rgba(0,0,0,0.45)', backdropFilter: 'blur(6px)', zIndex: 3 }}>
                <span style={{ fontSize: 13, opacity: 0.85 }}>Зум</span>
                <input type="range" min={zoomCaps.min} max={zoomCaps.max} step={zoomCaps.step} value={zoom}
                  onInput={(e) => applyZoom(parseFloat((e.target as HTMLInputElement).value))}
                  style={{ flex: 1, accentColor: 'var(--live)' }} />
                <span style={{ fontSize: 12, opacity: 0.7, minWidth: 30, textAlign: 'right' }}>{zoom.toFixed(1)}×</span>
              </div>
            )}
          </div>
        </Popup>
      )}
    </div>
  );
}

function Popup({ title, onClose, children }: { title: string; onClose: () => void; children: any }) {
  return (
    // Без размытия фона: на телефоне blur поверх всего экрана и таб-бара заметно тормозит анимацию.
    <div class="popup-backdrop" style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.74)',
      display: 'grid', placeItems: 'center', padding: 16 }}>
      <div class="panel popup-in" style={{ padding: 0, maxWidth: 440, width: '100%', maxHeight: '92vh', overflowY: 'auto' }}>
        <div class="row" style={{ justifyContent: 'space-between', padding: '18px 20px 14px', borderBottom: '1px solid var(--hairline)' }}>
          <h3 style={{ fontSize: 20 }}>{title}</h3>
          <button onClick={onClose} aria-label="Закрыть"
            style={{ width: 34, height: 34, flex: 'none', borderRadius: '50%', border: '1px solid var(--hairline-2)',
              background: 'none', color: 'var(--ink-dim)', display: 'grid', placeItems: 'center', cursor: 'pointer' }}>
            <IconX size={18} />
          </button>
        </div>
        <div style={{ padding: '16px 16px 18px' }}>{children}</div>
      </div>
    </div>
  );
}

function statusNote(t: Target): string {
  if (t.status === 'done') return 'отмечен';
  if (t.status === 'already') return 'уже был отмечен на этой паре';
  if (t.status === 'skipped') return 'отметка на этой паре отключена';
  if (t.status === 'marking') return 'отмечаем…';
  return t.error || 'не отмечен';
}

function StatusBadge({ status }: { status: Status }) {
  const box = { width: 30, height: 30, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none' } as const;
  if (status === 'done') {
    return <div style={{ ...box, background: 'var(--live-dim)', color: 'var(--live)' }}><IconCheck size={16} stroke={2.6} /></div>;
  }
  if (status === 'already') {
    return <div style={{ ...box, background: 'var(--surface-2)', color: 'var(--ink-dim)' }}><IconCheck size={16} stroke={2.6} /></div>;
  }
  if (status === 'marking') {
    return <div style={box}><span style={{ width: 18, height: 18, borderRadius: '50%', border: '2px solid rgba(255,255,255,0.18)', borderTopColor: 'var(--ink)', animation: 'spin 0.9s linear infinite', display: 'inline-block' }} /></div>;
  }
  if (status === 'failed') {
    return <div style={{ ...box, background: 'var(--surface-2)', color: 'var(--ink-dim)' }}><IconX size={16} stroke={2.6} /></div>;
  }
  if (status === 'skipped') {
    return <div style={{ ...box, background: 'var(--bg-2)', color: 'var(--ink-faint)' }}><span style={{ width: 12, height: 2.6, borderRadius: 2, background: 'currentColor' }} /></div>;
  }
  // pending
  return <div style={box}><span style={{ width: 20, height: 20, borderRadius: '50%', border: '1.5px solid var(--hairline-2)' }} /></div>;
}
