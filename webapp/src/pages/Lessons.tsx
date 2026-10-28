import { useState, useEffect } from 'preact/hooks';
import { getScheduleApi, getOptoutsApi, setOptoutApi, Lesson } from '../api/client';
import { HistoryList } from './History';
import { useTelegram } from '../hooks/useTelegram';
import { IconCalendar } from '../components/icons';

function fmtTime(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

interface Day { key: string; label: string; tag: string; dow: string; dnum: number; lessons: Lesson[]; }

function buildDays(lessons: Lesson[]): Day[] {
  const now = new Date();
  const todayKey = now.toDateString();
  const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
  const tomorrowKey = tomorrow.toDateString();

  const map = new Map<string, Lesson[]>();
  for (const l of lessons) {
    if (!l.start_epoch) continue;
    const k = new Date(l.start_epoch * 1000).toDateString();
    (map.get(k) || map.set(k, []).get(k)!).push(l);
  }
  const days: Day[] = [];
  for (const [k, ls] of map) {
    const d = new Date(k);
    const label = d.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
    const tag = k === todayKey ? 'Сегодня' : k === tomorrowKey ? 'Завтра' : '';
    days.push({
      key: k, label, tag,
      dow: d.toLocaleDateString('ru-RU', { weekday: 'short' }),
      dnum: d.getDate(),
      lessons: ls.sort((a, b) => (a.start_epoch || 0) - (b.start_epoch || 0)),
    });
  }
  days.sort((a, b) => +new Date(a.key) - +new Date(b.key));
  return days;
}

export function Lessons() {
  const { haptic } = useTelegram();
  const [tab, setTab] = useState<'schedule' | 'history'>('schedule');
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const [off, setOff] = useState<Set<number>>(new Set());
  const [sel, setSel] = useState<string>('');   // выбранный день

  useEffect(() => {
    getScheduleApi(7).then((r) => setLessons(r.lessons)).catch(() => setLessons([]));
    getOptoutsApi().then((r) => setOff(new Set(r.start_epochs))).catch(() => {});
  }, []);

  const now = Date.now() / 1000;
  const upcoming = (lessons || []).filter((l) => (l.end_epoch || l.start_epoch || 0) >= now);
  const days = buildDays(upcoming);

  useEffect(() => {
    if (days.length && (!sel || !days.some((d) => d.key === sel))) setSel(days[0].key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lessons]);

  const toggleOptout = async (l: Lesson) => {
    const se = l.start_epoch;
    if (!se) return;
    const enable = !off.has(se);
    setOff((prev) => { const n = new Set(prev); enable ? n.add(se) : n.delete(se); return n; });
    haptic?.impactOccurred?.('light');
    try {
      await setOptoutApi({ start_epoch: se, end_epoch: l.end_epoch, lesson_name: l.name, enabled: enable });
    } catch {
      setOff((prev) => { const n = new Set(prev); enable ? n.delete(se) : n.add(se); return n; });
    }
  };

  const selDay = days.find((d) => d.key === sel) || days[0];
  const offToday = selDay ? selDay.lessons.filter((l) => off.has(l.start_epoch || 0)).length : 0;

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 26, marginBottom: 18 }}>Пары</h1>

      <div class="segment" style={{ marginBottom: 20 }}>
        <div class="thumb" style={{ left: tab === 'schedule' ? 4 : '50%', width: 'calc(50% - 4px)' }} />
        <button data-active={tab === 'schedule'} onClick={() => setTab('schedule')}>Расписание</button>
        <button data-active={tab === 'history'} onClick={() => setTab('history')}>История</button>
      </div>

      {tab === 'schedule' && (
        <div class="enter" style={{ fontFamily: "'Onest', system-ui, sans-serif" }}>
          {lessons == null ? (
            <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>
          ) : days.length === 0 ? (
            <div class="empty">
              <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconCalendar size={44} color="var(--ink-faint)" /></div>
              <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Пар не нашлось</h3>
              <p style={{ fontSize: 14 }}>Ближайшие занятия из Пульса появятся здесь</p>
            </div>
          ) : (
            <>
              {/* Лента дней недели */}
              <div style={{ display: 'flex', gap: 8, overflowX: 'auto', margin: '0 -20px 16px', padding: '2px 20px 6px' }}>
                {days.map((d) => {
                  const active = d.key === sel;
                  return (
                    <button key={d.key} onClick={() => { haptic?.selectionChanged?.(); setSel(d.key); }}
                      style={{ flex: 'none', minWidth: 54, padding: '9px 12px', borderRadius: 16, cursor: 'pointer',
                        fontFamily: 'inherit', textAlign: 'center',
                        background: active ? '#FFFFFF' : 'var(--bg-2)',
                        color: active ? '#09090B' : 'var(--ink-dim)',
                        border: '1px solid ' + (active ? '#FFFFFF' : 'var(--hairline)'),
                        boxShadow: active ? '0 8px 20px -8px rgba(255,255,255,0.3)' : 'none',
                        transition: 'background 0.2s, color 0.2s' }}>
                      <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', opacity: active ? 0.65 : 1 }}>{d.dow}</div>
                      <div style={{ fontSize: 19, fontWeight: 700, fontFamily: 'Unbounded, sans-serif', marginTop: 2 }}>{d.dnum}</div>
                    </button>
                  );
                })}
              </div>

              {/* Заголовок выбранного дня */}
              {selDay && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 700, fontSize: 17, textTransform: 'capitalize' }}>
                    {selDay.tag || selDay.label}
                  </div>
                  <div class="faint" style={{ fontSize: 12.5, marginTop: 3 }}>
                    {selDay.lessons.length} {plural(selDay.lessons.length, 'пара', 'пары', 'пар')}
                    {offToday > 0 ? ` · ${offToday} без отметки` : ''}
                  </div>
                </div>
              )}

              {/* Пары выбранного дня */}
              <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {selDay?.lessons.map((l) => (
                  <LessonRow key={l.start_epoch} l={l} off={off} onToggle={toggleOptout} />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'history' && (
        <div class="enter"><HistoryList /></div>
      )}
    </div>
  );
}

function LessonRow({ l, off, onToggle }: { l: Lesson; off: Set<number>; onToggle: (l: Lesson) => void }) {
  const markable = isMarkable(l.type);
  const isOff = markable && off.has(l.start_epoch || 0);
  const meta1 = [l.room && `ауд. ${l.room}`, l.subgroup].filter(Boolean).join(' · ');
  return (
    <div class="panel" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 13px', opacity: isOff ? 0.55 : 1 }}>
      <div style={{ width: 50, flex: 'none', textAlign: 'center', borderRadius: 12, padding: '7px 0',
        background: 'var(--bg-2)', border: '1px solid var(--hairline)' }}>
        <div style={{ fontWeight: 700, fontSize: 13.5, fontFamily: 'Unbounded, monospace' }}>{fmtTime(l.start_epoch)}</div>
        <div class="faint" style={{ fontSize: 10.5, marginTop: 1 }}>{fmtTime(l.end_epoch)}</div>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div class="row" style={{ gap: 6, alignItems: 'center' }}>
          <TypeTag type={l.type} />
          <span style={{ fontWeight: 600, fontSize: 14.5, lineHeight: 1.25, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.name}</span>
        </div>
        {meta1 && <div class="faint" style={{ fontSize: 12, marginTop: 5 }}>{meta1}</div>}
        {l.teacher && <div class="faint" style={{ fontSize: 12, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{l.teacher}</div>}
      </div>
      <div style={{ flex: 'none', display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
        {markable
          ? <OptoutToggle on={isOff} onClick={() => onToggle(l)} />
          : <span class="faint" style={{ fontSize: 10.5, textAlign: 'right', lineHeight: 1.25, whiteSpace: 'nowrap' }}>отмечает<br />преподаватель</span>}
      </div>
    </div>
  );
}

function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

// Классификация пары по типу из Пульса.
const isLecture = (t?: string) => /лекц|^лк$|lect/i.test((t || '').trim());
const isPractice = (t?: string) => /практ|^пр$|^пз$/i.test((t || '').trim());
const isLab = (t?: string) => /лаб|^лр$/i.test((t || '').trim());
const isMarkable = (t?: string) => !isPractice(t) && !isLab(t);

function shortType(t?: string): string {
  if (isLecture(t)) return 'ЛК';
  if (isPractice(t)) return 'ПР';
  if (isLab(t)) return 'ЛАБ';
  return (t || '').trim().slice(0, 6).toUpperCase();
}

function TypeTag({ type }: { type?: string }) {
  const lect = isLecture(type);
  const label = shortType(type);
  if (!label) return null;
  return (
    <span style={{ flex: 'none', fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, padding: '2px 6px',
      borderRadius: 6, lineHeight: 1,
      background: lect ? 'var(--live-dim)' : 'var(--bg-2)',
      color: lect ? 'var(--live)' : 'var(--ink-faint)' }}>
      {label}
    </span>
  );
}

function OptoutToggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <div onClick={onClick} title="Не отмечаться на этой паре"
      style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer' }}>
      <span style={{ fontSize: 10.5, fontWeight: 600, whiteSpace: 'nowrap',
        color: on ? 'var(--warn)' : 'var(--ink-faint)' }}>не&nbsp;отмечать</span>
      <div class="toggle sm" data-on={on} />
    </div>
  );
}
