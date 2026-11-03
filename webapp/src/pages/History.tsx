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
  const allOk = scan.successful === scan.total && scan.targets.every((t) => !t.success || !t.error);
  const accent = allOk ? 'var(--live)' : 'var(--fail)';
  const names = (list: Scan['targets']) => list.map((t) => (t.is_self ? 'Вы' : t.name)).join(', ');
  const marked = scan.targets.filter((t) => t.success && !t.already_marked && !t.error);
  const warned = scan.targets.filter((t) => t.success && !t.already_marked && t.error);
  const already = scan.targets.filter((t) => t.already_marked);
  const failed = scan.targets.filter((t) => !t.success);
  return (
    <div class="panel" style={{ padding: '13px 14px' }}>
      {/* Верх: время · статус */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
        <span style={{ fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 14 }}>{time}</span>
        <span style={{ marginLeft: 'auto', flex: 'none', fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 13, color: accent }}>
          {scan.successful}/{scan.total}
        </span>
      </div>

      {/* Название пары — переносится */}
      <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.3 }}>{scan.lesson_name || 'Пара'}</div>

      {/* Кого отметили, кто уже был отмечен и кого не вышло */}
      {marked.length > 0 && <div class="faint" style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.4 }}>Отмечены: {names(marked)}</div>}
      {already.length > 0 && <div class="faint" style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.4 }}>Уже были отмечены: {names(already)}</div>}
      {warned.map((t, i) => (
        <div key={`w${i}`} style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.4, color: 'var(--fail)' }}>
          Отмечен{t.is_self ? 'ы' : ''}: {t.is_self ? 'Вы' : t.name} — {t.error!.charAt(0).toLowerCase()}{t.error!.slice(1)}
        </div>
      ))}
      {failed.map((t, i) => (
        <div key={i} style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.4, color: 'var(--fail)' }}>
          Не отмечен{t.is_self ? 'ы' : ''}: {t.is_self ? 'Вы' : t.name}{t.error ? ` — ${t.error}` : ''}
        </div>
      ))}
    </div>
  );
}
