import { useState, useEffect } from 'preact/hooks';
import { getHistoryApi, Scan } from '../api/client';
import { IconHistory, IconCheck, IconX } from '../components/icons';

export function History() {
  const [scans, setScans] = useState<Scan[] | null>(null);

  useEffect(() => { getHistoryApi().then((r) => setScans(r.scans)).catch(() => setScans([])); }, []);

  const groups = (scans || []).reduce((acc, s) => {
    const d = new Date((s.created_at || 0) * 1000);
    const key = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
    (acc[key] ||= []).push(s);
    return acc;
  }, {} as Record<string, Scan[]>);

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 26, marginBottom: 18 }}>История</h1>

      {scans == null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>
      ) : scans.length === 0 ? (
        <div class="empty">
          <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconHistory size={44} color="var(--ink-faint)" /></div>
          <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Пока пусто</h3>
          <p style={{ fontSize: 14 }}>Отсканируй QR на паре — отметки появятся здесь</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
          {Object.entries(groups).map(([day, items]) => (
            <div key={day}>
              <p class="eyebrow" style={{ marginBottom: 10 }}>{day}</p>
              <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {items.map((s, i) => <ScanCard key={i} scan={s} />)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ScanCard({ scan }: { scan: Scan }) {
  const time = new Date((scan.created_at || 0) * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const allOk = scan.successful === scan.total;
  const accent = allOk ? 'var(--live)' : scan.successful === 0 ? 'var(--miss)' : 'var(--warn)';
  return (
    <div class="panel" style={{ padding: 16 }}>
      <div class="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div class="row" style={{ gap: 10 }}>
          <span class="display" style={{ fontSize: 18 }}>{time}</span>
          <span class="faint" style={{ fontSize: 13 }}>Пара</span>
        </div>
        <span class="pill" style={{ padding: '4px 11px', fontSize: 12.5, color: accent }}>
          {scan.successful}/{scan.total} отмечено
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        {scan.targets.map((t, i) => (
          <div key={i} class="row" style={{ gap: 10 }}>
            <span style={{ width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none',
              background: t.success ? 'var(--live-dim)' : 'var(--miss-dim)', color: t.success ? 'var(--live)' : 'var(--miss)' }}>
              {t.success ? <IconCheck size={13} stroke={2.8} /> : <IconX size={13} stroke={2.8} />}
            </span>
            <span style={{ fontSize: 14.5, fontWeight: t.is_self ? 600 : 400 }}>{t.name}</span>
            {!t.success && t.error && <span class="faint" style={{ fontSize: 12.5, marginLeft: 'auto' }}>{t.error}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
