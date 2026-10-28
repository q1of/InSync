import { useState, useEffect } from 'preact/hooks';
import { getScheduleApi, getOptoutsApi, setOptoutApi, Lesson } from '../api/client';
import { HistoryList } from './History';
import { useTelegram } from '../hooks/useTelegram';
import { IconCalendar, IconChevron } from '../components/icons';

function fmtTime(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

interface Day { key: string; label: string; tag: string; lessons: Lesson[]; }

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
    days.push({ key: k, label, tag, lessons: ls.sort((a, b) => (a.start_epoch || 0) - (b.start_epoch || 0)) });
  }
  days.sort((a, b) => +new Date(a.key) - +new Date(b.key));
  return days;
}

export function Lessons() {
  const { haptic } = useTelegram();
  const [tab, setTab] = useState<'schedule' | 'history'>('schedule');
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const [off, setOff] = useState<Set<number>>(new Set());   // start_epoch отключённых пар
  const [open, setOpen] = useState<Set<string>>(new Set()); // раскрытые дни

  useEffect(() => {
    getScheduleApi(7).then((r) => setLessons(r.lessons)).catch(() => setLessons([]));
    getOptoutsApi().then((r) => setOff(new Set(r.start_epochs))).catch(() => {});
  }, []);

  const now = Date.now() / 1000;
  const upcoming = (lessons || []).filter((l) => (l.end_epoch || l.start_epoch || 0) >= now);
  const days = buildDays(upcoming);

  // По умолчанию раскрыт первый (ближайший) день.
  useEffect(() => {
    if (days.length && open.size === 0) setOpen(new Set([days[0].key]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lessons]);

  const toggleDay = (k: string) => {
    haptic?.selectionChanged?.();
    setOpen((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; });
  };

  const toggleOptout = async (l: Lesson) => {
    const se = l.start_epoch;
    if (!se) return;
    const enable = !off.has(se);   // не отключена → отключаем отметку
    setOff((prev) => { const n = new Set(prev); enable ? n.add(se) : n.delete(se); return n; });
    haptic?.impactOccurred?.('light');
    try {
      await setOptoutApi({ start_epoch: se, end_epoch: l.end_epoch, lesson_name: l.name, enabled: enable });
    } catch {
      setOff((prev) => { const n = new Set(prev); enable ? n.delete(se) : n.add(se); return n; });  // откат
    }
  };

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
              <p class="muted" style={{ fontSize: 13, marginBottom: 14, lineHeight: 1.5 }}>
                Твоё расписание на неделю. Открой день и отключи пары, на которых не хочешь отмечаться.
              </p>
              <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {days.map((d) => (
                  <DayCard key={d.key} day={d} open={open.has(d.key)} offSet={off}
                           onToggleDay={() => toggleDay(d.key)} onToggleOptout={toggleOptout} />
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

function DayCard({ day, open, offSet, onToggleDay, onToggleOptout }: {
  day: Day; open: boolean; offSet: Set<number>;
  onToggleDay: () => void; onToggleOptout: (l: Lesson) => void;
}) {
  const offCount = day.lessons.filter((l) => offSet.has(l.start_epoch || 0)).length;
  return (
    <div class="panel" style={{ padding: 0, overflow: 'hidden' }}>
      <button onClick={onToggleDay} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12,
        padding: '15px 16px', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink)', textAlign: 'left' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 15.5, textTransform: 'capitalize', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {day.tag && <span style={{ color: 'var(--live)' }}>{day.tag} · </span>}{day.label}
          </div>
          <div class="faint" style={{ fontSize: 13, marginTop: 2 }}>
            {day.lessons.length} {plural(day.lessons.length, 'пара', 'пары', 'пар')}
            {offCount > 0 ? ` · ${offCount} без отметки` : ''}
          </div>
        </div>
        <span style={{ display: 'flex', transition: 'transform 0.22s', transform: open ? 'rotate(180deg)' : 'none', color: 'var(--ink-faint)' }}>
          <IconChevron size={20} />
        </span>
      </button>

      {open && (
        <div style={{ padding: '0 14px 8px' }}>
          {day.lessons.map((l) => {
            const markable = isMarkable(l.type);          // отмечаемся только на лекциях
            const isOff = markable && offSet.has(l.start_epoch || 0);
            return (
              <div key={l.start_epoch} class="row" style={{ alignItems: 'center', gap: 10, padding: '9px 0',
                borderTop: '1px solid var(--hairline)', opacity: isOff ? 0.5 : 1 }}>
                <div style={{ width: 44, flex: 'none', textAlign: 'right' }}>
                  <div style={{ fontWeight: 600, fontSize: 13.5, fontFamily: 'Unbounded, monospace' }}>{fmtTime(l.start_epoch)}</div>
                  <div class="faint" style={{ fontSize: 11.5 }}>{fmtTime(l.end_epoch)}</div>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div class="row" style={{ gap: 6, alignItems: 'center' }}>
                    <TypeTag type={l.type} />
                    <span style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.25, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.name}</span>
                  </div>
                  <div class="faint" style={{ fontSize: 12, marginTop: 2, lineHeight: 1.35 }}>
                    {[l.room && `ауд. ${l.room}`, l.teacher, l.subgroup].filter(Boolean).join(' · ')}
                  </div>
                </div>
                <div style={{ flex: 'none', display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
                  {markable
                    ? <OptoutToggle on={isOff} onClick={() => onToggleOptout(l)} />
                    : <span class="faint" style={{ fontSize: 10.5, textAlign: 'right', lineHeight: 1.25, whiteSpace: 'nowrap' }}>отмечает<br />преподаватель</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
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
// Самоотметка по QR только на лекциях; практику/лаб отмечает преподаватель.
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

// Маленький тумблер с подписью «не отмечать» — чтобы было ясно: он не про отметку,
// а про то, чтобы НЕ отмечаться на этой паре. Неброский, не отвлекает от расписания.
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
