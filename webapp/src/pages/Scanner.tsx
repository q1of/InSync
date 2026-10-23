import { useState, useEffect, useRef } from 'preact/hooks';
import { markAttendanceApi, getFriendsApi, prewarmApi, getScheduleApi } from '../api/client';
import type { Friend } from '../api/client';
import { IconCheck, IconX, IconScan, IconUsers } from '../components/icons';
import { useTelegram } from '../hooks/useTelegram';

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

type Status = 'pending' | 'marking' | 'done' | 'failed' | 'skipped';
interface Target { id: number; name: string; is_self: boolean; status: Status; error?: string | null; attempts: number; }

const MAX_DEAD_ATTEMPTS = 3;

interface Props { active: boolean }

export function Scanner({ active }: Props) {
  const { user, haptic, webApp } = useTelegram();
  const [enabledFriends, setEnabledFriends] = useState<Friend[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [resultOpen, setResultOpen] = useState(false);   // мобайл: попап итога после нативного скана
  const [scanning, setScanning] = useState(false);       // мобайл: открыт нативный сканер
  const [camOpen, setCamOpen] = useState(false);         // десктоп: попап с нашей камерой
  const [camScanning, setCamScanning] = useState(false);
  const [camStarting, setCamStarting] = useState(false);
  const [camDone, setCamDone] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [zoomCaps, setZoomCaps] = useState<{ min: number; max: number; step: number } | null>(null);

  const targetsRef = useRef<Target[]>([]);
  const scanIdRef = useRef('');
  const lastTokenRef = useRef<string | null>(null);
  const inFlightRef = useRef(false);
  const doneRef = useRef(false);
  const lessonRef = useRef<string | null>(null);
  const hapticRef = useRef(haptic);
  const scannerRef = useRef<any>(null);
  const activeRef = useRef(active);

  useEffect(() => { hapticRef.current = haptic; }, [haptic]);
  useEffect(() => { targetsRef.current = targets; }, [targets]);
  useEffect(() => { activeRef.current = active; }, [active]);

  useEffect(() => {
    if (!active) return;
    getFriendsApi().then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled))).catch(() => {});
  }, [active]);

  const nativeSupported = () => {
    const tg: any = webApp;
    if (!tg?.showScanQrPopup) return false;
    const p = (tg.platform || '').toLowerCase();
    return p === 'ios' || p === 'android';   // на ПК нативный сканер ботов не поддерживается
  };

  const buildTargets = (): Target[] => {
    const self: Target = { id: user?.id ?? 0, name: user?.first_name || 'Ты', is_self: true, status: 'pending', attempts: 0 };
    const fr: Target[] = enabledFriends.map((f) => ({ id: f.telegram_id, name: f.name, is_self: false, status: 'pending', attempts: 0 }));
    return [self, ...fr];
  };

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
          if (r?.opted_out) return { ...t, status: 'skipped', error: r.error };
          if (r?.retriable) return { ...t, status: 'pending', error: r.error };
          const attempts = t.attempts + 1;
          const error = r ? r.error : 'Нужно перезайти';
          if (attempts > MAX_DEAD_ATTEMPTS) return { ...t, status: 'failed', error, attempts };
          return { ...t, status: 'pending', error, attempts };
        }),
      );
    } catch {
      setTargets((ts) => ts.map((t) => (ids.includes(t.id) && t.status === 'marking' ? { ...t, status: 'pending' } : t)));
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
    lastTokenRef.current = token;
    hapticRef.current?.impactOccurred?.('light');
    const ids = targetsRef.current.filter((t) => t.status === 'pending').map((t) => t.id);
    if (ids.length) fireWave(text, ids);
  };

  // Завершение: все отметились/определились.
  useEffect(() => {
    if ((!scanning && !camOpen) || !targets.length) return;
    const openLeft = targets.some((t) => t.status === 'pending' || t.status === 'marking');
    if (openLeft) return;
    doneRef.current = true;
    const allOk = targets.every((t) => t.status === 'done' || t.status === 'skipped');
    hapticRef.current?.notificationOccurred?.(allOk ? 'success' : 'error');
    if (scanning) {   // мобайл: закрываем нативный сканер, показываем итог
      try { (webApp as any)?.closeScanQrPopup?.(); } catch {}
      setScanning(false);
      setResultOpen(true);
    } else {          // десктоп: оставляем попап, показываем «Сканировать снова»
      setCamDone(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targets, scanning, camOpen]);

  // Пользователь сам закрыл нативный сканер.
  useEffect(() => {
    const tg: any = webApp;
    if (!tg?.onEvent) return;
    const onClosed = () => {
      if (doneRef.current) return;
      setScanning(false);
      if (targetsRef.current.length) setResultOpen(true);
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
    getFriendsApi().then((f) => setEnabledFriends(f.filter((x) => x.marking_enabled))).catch(() => {});
    getScheduleApi(1).then((r) => {
      const now = Date.now() / 1000;
      const l = r.lessons.find((x) => (x.start_epoch || 0) - 900 <= now && now <= (x.end_epoch || (x.start_epoch || 0) + 5400));
      lessonRef.current = l?.name || null;
    }).catch(() => { lessonRef.current = null; });
    const list = buildTargets();
    scanIdRef.current = Math.random().toString(36).slice(2) + Date.now().toString(36);
    lastTokenRef.current = null;
    doneRef.current = false;
    targetsRef.current = list;
    setTargets(list);
    setCamDone(false);
    return list;
  };

  const startScan = () => {
    const list = prepare();
    if (!list.length) return;
    if (nativeSupported()) {
      setResultOpen(false);
      setScanning(true);
      try {
        (webApp as any).showScanQrPopup({ text: 'Наведи на QR-код пары' }, (text: string) => { handleQr(text); return false; });
      } catch { setScanning(false); }
    } else {
      setCamOpen(true);   // десктоп: наш попап с камерой
    }
  };

  const rescan = () => {
    setResultOpen(false);
    if (camOpen) { prepare(); } else { startScan(); }
  };

  const closeCamera = () => { setCamOpen(false); doneRef.current = true; };

  const crew = enabledFriends.length;
  const crewLine = crew ? `С тобой — ${crew} ${crew === 1 ? 'друг' : crew < 5 ? 'друга' : 'друзей'}` : 'Отметим только тебя';
  const doneCount = targets.filter((t) => t.status === 'done').length;

  const StatusList = () => (
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
  );

  return (
    <div class="screen screen-pad-top">
      {/* Hero */}
      <div class="panel enter" style={{ padding: 26, overflow: 'hidden' }}>
        <div class="hero-stage" style={{ height: 118 }}>
          <div class="hero-tile" style={{ width: 74, height: 74, borderRadius: 21, animation: 'none' }}>
            <svg class="logo-mark" viewBox="0 0 100 100" style={{ width: 40, height: 40 }}>
              <polyline class="logo-base" points="18,52 38,68 60,34 82,50" style={{ opacity: 1 }} />
            </svg>
          </div>
        </div>

        <h1 style={{ fontSize: 33, lineHeight: 1.06, textAlign: 'center', marginTop: 8 }}>
          На&nbsp;месте<br /><span class="sig-text">одним касанием</span>
        </h1>
        <p class="muted" style={{ textAlign: 'center', fontSize: 14.5, lineHeight: 1.5, maxWidth: 300, margin: '13px auto 0' }}>
          Нажми и наведи камеру на QR-код пары. Остальное сделает InSync.
        </p>

        <button class="btn" onClick={startScan}
          style={{ marginTop: 24, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}>
          <IconScan size={20} stroke={2.2} /> Сканировать QR
        </button>

        <div class="row" style={{ justifyContent: 'center', gap: 7, marginTop: 16 }}>
          <IconUsers size={14} color="var(--ink-faint)" />
          <span class="faint" style={{ fontSize: 13 }}>{crewLine}</span>
        </div>
      </div>

      <div class="row" style={{ justifyContent: 'center', gap: 8, marginTop: 14 }}>
        <span class="faint" style={{ fontSize: 12.5 }}>История отметок — во вкладке «Пары»</span>
      </div>

      {/* Мобайл: попап итога после нативного скана */}
      {resultOpen && (
        <Popup title="Отметка на паре" onClose={() => setResultOpen(false)}>
          <div class="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 700 }}>
              <span class="display">{doneCount}</span><span class="faint"> / {targets.length}</span> отмечено
            </span>
          </div>
          <StatusList />
          <button class="btn" style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }} onClick={rescan}>
            <IconScan size={19} stroke={2.2} /> Сканировать снова
          </button>
        </Popup>
      )}

      {/* Десктоп: попап с нашей камерой */}
      {camOpen && (
        <Popup title="Отметка на паре" onClose={closeCamera}>
          <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1', borderRadius: 'var(--r-md)', overflow: 'hidden', background: '#000', border: '1px solid var(--hairline)' }}>
            <div id="qr-reader" style={{ width: '100%', height: '100%' }} />
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

          <div class="row" style={{ justifyContent: 'space-between', marginTop: 14, marginBottom: 10 }}>
            <span style={{ fontWeight: 700 }}>
              <span class="display">{doneCount}</span><span class="faint"> / {targets.length}</span> отмечено
            </span>
            {camDone
              ? <span class="pill"><span class="dot dot-off" /> завершено</span>
              : <span class="pill"><span class="dot dot-live" /> наведи на QR</span>}
          </div>
          <StatusList />
          {camDone && (
            <button class="btn" style={{ marginTop: 14, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }} onClick={rescan}>
              <IconScan size={19} stroke={2.2} /> Сканировать снова
            </button>
          )}
        </Popup>
      )}
    </div>
  );
}

function Popup({ title, onClose, children }: { title: string; onClose: () => void; children: any }) {
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.62)', backdropFilter: 'blur(5px)',
      display: 'grid', placeItems: 'center', padding: 16 }}>
      <div class="panel enter" style={{ padding: 0, maxWidth: 440, width: '100%', maxHeight: '92vh', overflowY: 'auto' }}>
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

function subtitle(t: Target): string {
  switch (t.status) {
    case 'done': return 'отмечен';
    case 'marking': return 'отмечаем…';
    case 'pending': return 'ждём QR…';
    case 'failed': return t.error || 'не удалось';
    case 'skipped': return 'не отмечается на этой паре';
  }
}

function StatusBadge({ status }: { status: Status }) {
  const box = { width: 30, height: 30, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none' } as const;
  if (status === 'done') {
    return <div style={{ ...box, background: 'var(--live-dim)', color: 'var(--live)' }}><IconCheck size={16} stroke={2.6} /></div>;
  }
  if (status === 'marking') {
    return <div style={box}><span style={{ width: 18, height: 18, borderRadius: '50%', border: '2px solid rgba(255,255,255,0.18)', borderTopColor: 'var(--ink)', animation: 'spin 0.9s linear infinite', display: 'inline-block' }} /></div>;
  }
  if (status === 'failed') {
    return <div style={{ ...box, background: 'var(--miss-dim)', color: 'var(--miss)' }}><IconX size={16} stroke={2.6} /></div>;
  }
  if (status === 'skipped') {
    return <div style={{ ...box, background: 'var(--bg-2)', color: 'var(--ink-faint)' }}><span style={{ width: 12, height: 2.6, borderRadius: 2, background: 'currentColor' }} /></div>;
  }
  // pending
  return <div style={box}><span style={{ width: 20, height: 20, borderRadius: '50%', border: '1.5px solid var(--hairline-2)' }} /></div>;
}
