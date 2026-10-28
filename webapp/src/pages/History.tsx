import { useState, useEffect } from 'preact/hooks';
import { getHistoryApi, Scan } from '../api/client';
import { IconHistory, IconCheck, IconX, IconCalendar } from '../components/icons';

// Список истории без собственной обёртки/заголовка — встраивается во вкладку «Пары».
export function HistoryList() {
  const [scans, setScans] = useState<Scan[] | null>(null);

  useEffect(() => { getHistoryApi().then((r) => setScans(r.scans)).catch(() => setScans([])); }, []);

  const groups = (scans || []).reduce((acc, s) => {
    const d = new Date((s.created_at || 0) * 1000);
    const key = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
    (acc[key] ||= []).push(s);
    return acc;
  }, {} as Record<string, Scan[]>);

  return (
    <>
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
    </>
  );
}

function ScanCard({ scan }: { scan: Scan }) {
  const time = new Date((scan.created_at || 0) * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const allOk = scan.successful === scan.total;
  const accent = allOk ? 'var(--live)' : scan.successful === 0 ? 'var(--miss)' : 'var(--warn)';
  return (
    <div class="panel" style={{ padding: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 15px' }}>
        <div style={{ width: 40, height: 40, flex: 'none', borderRadius: 12, display: 'grid', placeItems: 'center',
          background: 'var(--bg-2)', border: '1px solid var(--hairline)', color: 'var(--ink-dim)' }}>
          <IconCalendar size={18} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {scan.lesson_name || 'Пара'}
          </div>
          <div class="faint" style={{ fontSize: 12.5, marginTop: 2 }}>{time}</div>
        </div>
        <span class="pill" style={{ padding: '4px 11px', fontSize: 12.5, color: accent, flex: 'none' }}>
          {scan.successful}/{scan.total}
        </span>
      </div>
      <div style={{ borderTop: '1px solid var(--hairline)', padding: '11px 15px 13px', display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        {scan.targets.map((t, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 10px 5px 7px',
            borderRadius: 999, fontSize: 13, fontWeight: t.is_self ? 600 : 500,
            background: 'var(--bg-2)', border: '1px solid var(--hairline)' }}>
            <span style={{ width: 18, height: 18, borderRadius: '50%', display: 'grid', placeItems: 'center', flex: 'none',
              background: t.success ? 'var(--live-dim)' : 'var(--miss-dim)', color: t.success ? 'var(--live)' : 'var(--miss)' }}>
              {t.success ? <IconCheck size={11} stroke={3} /> : <IconX size={11} stroke={3} />}
            </span>
            {t.name}
          </span>
        ))}
      </div>
    </div>
  );
}
