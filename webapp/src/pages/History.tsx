import { useState, useEffect } from 'preact/hooks';
import { getHistoryApi, Scan } from '../api/client';
import { IconHistory } from '../components/icons';

interface DayGroup { key: string; tag: string; dnum: number; dow: string; month: string; items: Scan[]; }

// Список истории без заголовка — встраивается во вкладку «Пары».
export function HistoryList() {
  const [scans, setScans] = useState<Scan[] | null>(null);

  useEffect(() => { getHistoryApi().then((r) => setScans(r.scans)).catch(() => setScans([])); }, []);

  const now = new Date();
  const todayKey = now.toDateString();
  const y = new Date(now); y.setDate(now.getDate() - 1);
  const yKey = y.toDateString();

  const groups: DayGroup[] = [];
  const idx = new Map<string, DayGroup>();
  for (const s of scans || []) {
    const d = new Date((s.created_at || 0) * 1000);
    const key = d.toDateString();
    let g = idx.get(key);
    if (!g) {
      g = {
        key, tag: key === todayKey ? 'Сегодня' : key === yKey ? 'Вчера' : '',
        dnum: d.getDate(), dow: d.toLocaleDateString('ru-RU', { weekday: 'long' }),
        month: d.toLocaleDateString('ru-RU', { month: 'long' }), items: [],
      };
      idx.set(key, g); groups.push(g);
    }
    g.items.push(s);
  }

  if (scans == null) return <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>;
  if (scans.length === 0) return (
    <div class="empty">
      <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconHistory size={44} color="var(--ink-faint)" /></div>
      <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Пока пусто</h3>
      <p style={{ fontSize: 14 }}>Отсканируй QR на паре — отметки появятся здесь</p>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 26 }}>
      {groups.map((g) => (
        <div key={g.key}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
            <span style={{ fontFamily: 'Unbounded, sans-serif', fontWeight: 700, fontSize: 16, textTransform: 'capitalize' }}>{g.tag || g.dow}</span>
            <span class="eyebrow">{g.dnum} {g.month}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {g.items.map((s, i) => <ScanBlock key={i} scan={s} />)}
          </div>
        </div>
      ))}
    </div>
  );
}

function ScanBlock({ scan }: { scan: Scan }) {
  const time = new Date((scan.created_at || 0) * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const allOk = scan.successful === scan.total;
  const accent = allOk ? 'var(--live)' : scan.successful === 0 ? 'var(--miss)' : 'var(--warn)';
  const who = scan.targets.map((t) => (t.is_self ? 'Вы' : t.name)).join(', ');
  return (
    <div class="panel" style={{ display: 'flex', gap: 12, padding: '13px 13px', alignItems: 'center' }}>
      <div style={{ width: 50, flex: 'none', textAlign: 'center', borderRadius: 12, padding: '9px 0',
        background: 'var(--bg-2)', border: '1px solid var(--hairline)', fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 13.5 }}>
        {time}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 14.5, lineHeight: 1.3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {scan.lesson_name || 'Пара'}
        </div>
        <div class="faint" style={{ fontSize: 12.5, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{who}</div>
      </div>
      <span style={{ flex: 'none', fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 13, color: accent }}>
        {scan.successful}/{scan.total}
      </span>
    </div>
  );
}
