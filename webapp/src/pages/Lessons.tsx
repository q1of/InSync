import { useState, useEffect } from 'preact/hooks';
import { getScheduleApi, getOptoutsApi, setOptoutApi, Lesson } from '../api/client';
import { HistoryList } from './History';
import { useTelegram } from '../hooks/useTelegram';
import { IconCalendar } from '../components/icons';

function fmtTime(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}
function dayKey(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' });
}

export function Lessons() {
  const { haptic } = useTelegram();
  const [tab, setTab] = useState<'lessons' | 'history'>('lessons');
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const [off, setOff] = useState<Set<number>>(new Set());   // start_epoch отключённых пар

  useEffect(() => {
    getScheduleApi(7).then((r) => setLessons(r.lessons)).catch(() => setLessons([]));
    getOptoutsApi().then((r) => setOff(new Set(r.start_epochs))).catch(() => {});
  }, []);

  const toggle = async (l: Lesson) => {
    const se = l.start_epoch;
    if (!se) return;
    const enable = !off.has(se);   // не отключена → отключаем отметку
    setOff((prev) => { const n = new Set(prev); enable ? n.add(se) : n.delete(se); return n; });
    haptic?.selectionChanged?.();
    try {
      await setOptoutApi({ start_epoch: se, end_epoch: l.end_epoch, lesson_name: l.name, enabled: enable });
    } catch {
      setOff((prev) => { const n = new Set(prev); enable ? n.delete(se) : n.add(se); return n; });  // откат
    }
  };

  const now = Date.now() / 1000;
  const upcoming = (lessons || []).filter((l) => (l.end_epoch || l.start_epoch || 0) >= now);
  const groups: Record<string, Lesson[]> = {};
  for (const l of upcoming) (groups[dayKey(l.start_epoch)] ||= []).push(l);

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 26, marginBottom: 18 }}>Пары</h1>

      <div class="segment" style={{ marginBottom: 20 }}>
        <div class="thumb" style={{ left: tab === 'lessons' ? 4 : '50%', width: 'calc(50% - 4px)' }} />
        <button data-active={tab === 'lessons'} onClick={() => setTab('lessons')}>Расписание</button>
        <button data-active={tab === 'history'} onClick={() => setTab('history')}>История</button>
      </div>

      {tab === 'lessons' && (
        <div class="enter">
          <p class="muted" style={{ fontSize: 13.5, marginBottom: 16, lineHeight: 1.5 }}>
            Отключи пары, на которых не хочешь отмечаться. Тебя не отметят — ни ты сам, ни друзья.
          </p>

          {lessons == null ? (
            <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>
          ) : upcoming.length === 0 ? (
            <div class="empty">
              <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconCalendar size={44} color="var(--ink-faint)" /></div>
              <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Пар не нашлось</h3>
              <p style={{ fontSize: 14 }}>Ближайшие занятия из Пульса появятся здесь</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
              {Object.entries(groups).map(([day, items]) => (
                <div key={day}>
                  <p class="eyebrow" style={{ marginBottom: 10, textTransform: 'capitalize' }}>{day}</p>
                  <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {items.map((l) => {
                      const isOff = off.has(l.start_epoch || 0);
                      return (
                        <div key={l.start_epoch} class="panel" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 12, opacity: isOff ? 0.6 : 1 }}>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l.name}</div>
                            <div class="faint" style={{ fontSize: 13 }}>
                              {fmtTime(l.start_epoch)}{l.end_epoch ? `–${fmtTime(l.end_epoch)}` : ''}
                              {l.room ? ` · ${l.room}` : ''}
                              {isOff ? ' · не отмечаюсь' : ''}
                            </div>
                          </div>
                          <div class="toggle" data-on={isOff} onClick={() => toggle(l)} title="Не отмечаться на этой паре" />
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'history' && (
        <div class="enter"><HistoryList /></div>
      )}
    </div>
  );
}
