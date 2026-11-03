import { useState, useEffect } from 'preact/hooks';
import { getPassDayApi, PassDay } from '../api/client';
import { IconPass, IconChevron } from '../components/icons';
import { useTelegram } from '../hooks/useTelegram';

// Дни считаем по Москве: так их отдаёт Пульс.
const MSK_MS = 3 * 3600 * 1000;
const isoDay = (ms: number) => new Date(ms + MSK_MS).toISOString().slice(0, 10);
const shiftDay = (day: string, by: number) => isoDay(Date.parse(day) - MSK_MS + by * 86400000 + 12 * 3600 * 1000);

function fmtTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
}

function fmtHours(h: number | null): string {
  if (h == null) return '';
  const total = Math.round(h * 60);
  const hh = Math.floor(total / 60), mm = total % 60;
  return hh ? `${hh} ч ${mm} м` : `${mm} м`;
}

function dayTitle(day: string, today: string): { tag: string; date: string } {
  const d = new Date(`${day}T12:00:00Z`);
  const date = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', timeZone: 'UTC' });
  if (day === today) return { tag: 'Сегодня', date };
  if (day === shiftDay(today, -1)) return { tag: 'Вчера', date };
  return { tag: d.toLocaleDateString('ru-RU', { weekday: 'long', timeZone: 'UTC' }), date };
}

export function Pass() {
  const { haptic } = useTelegram();
  const today = isoDay(Date.now());
  const [day, setDay] = useState(today);
  const [data, setData] = useState<PassDay | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setData(null); setError(null);
    getPassDayApi(day)
      .then((r) => { if (alive) setData(r); })
      .catch((e) => { if (alive) setError(e?.message || 'Не удалось загрузить события'); });
    return () => { alive = false; };
  }, [day]);

  const go = (by: number) => { haptic?.selectionChanged?.(); setDay((d) => shiftDay(d, by)); };
  const title = dayTitle(day, today);
  const arrow = { width: 38, height: 38, borderRadius: '50%', border: '1px solid var(--hairline-2)', background: 'none',
    color: 'var(--ink-dim)', display: 'grid', placeItems: 'center', cursor: 'pointer', flex: 'none' } as const;

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 30, marginBottom: 22 }}>Пропуск</h1>

      {/* Переключение дней */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 18 }}>
        <button style={arrow} onClick={() => go(-1)} aria-label="Предыдущий день"><IconChevron size={18} left /></button>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div style={{ fontFamily: 'Unbounded, sans-serif', fontWeight: 700, fontSize: 16, textTransform: 'capitalize' }}>{title.tag}</div>
          <div class="eyebrow" style={{ marginTop: 4 }}>{title.date}</div>
        </div>
        <button style={{ ...arrow, opacity: day >= today ? 0.3 : 1 }} disabled={day >= today}
          onClick={() => go(1)} aria-label="Следующий день"><IconChevron size={18} /></button>
      </div>

      {error ? (
        <div class="empty"><p style={{ fontSize: 14 }}>{error}</p></div>
      ) : data == null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>
      ) : data.events.length === 0 ? (
        <div class="empty">
          <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconPass size={44} color="var(--ink-faint)" /></div>
          <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Нет событий</h3>
          <p style={{ fontSize: 14 }}>Проходы через турникеты за этот день появятся здесь</p>
        </div>
      ) : (
        <div class="enter">
          {data.hours != null && (
            <div class="panel" style={{ padding: '13px 14px', marginBottom: 14, display: 'flex', alignItems: 'baseline', gap: 8 }}>
              <span class="faint" style={{ fontSize: 13 }}>В вузе</span>
              <span style={{ marginLeft: 'auto', fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 15 }}>{fmtHours(data.hours)}</span>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.events.map((e, i) => (
              <div key={i} class="panel" style={{ padding: '13px 14px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <span style={{ fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 14, minWidth: 44 }}>{fmtTime(e.time)}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.3, color: e.kind === 'exit' ? 'var(--ink-dim)' : 'var(--ink)' }}>{e.title}</div>
                  {e.place && <div class="faint" style={{ fontSize: 12.5, marginTop: 4, lineHeight: 1.4 }}>{e.place}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
