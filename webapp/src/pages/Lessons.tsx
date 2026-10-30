import { useState, useEffect } from 'preact/hooks';
import { getScheduleApi, getOptoutsApi, setOptoutApi, Lesson } from '../api/client';
import { HistoryList } from './History';
import { useTelegram } from '../hooks/useTelegram';
import { IconCalendar } from '../components/icons';

function fmtTime(epoch: number | null): string {
  if (!epoch) return '';
  return new Date(epoch * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

interface Day { key: string; tag: string; dow: string; dnum: number; month: string; lessons: Lesson[]; }

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
    days.push({
      key: k,
      tag: k === todayKey ? 'Сегодня' : k === tomorrowKey ? 'Завтра' : '',
      dow: d.toLocaleDateString('ru-RU', { weekday: 'long' }),
      dnum: d.getDate(),
      month: d.toLocaleDateString('ru-RU', { month: 'long' }),
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

  useEffect(() => {
    getScheduleApi(7).then((r) => setLessons(r.lessons)).catch(() => setLessons([]));
    getOptoutsApi().then((r) => setOff(new Set(r.start_epochs))).catch(() => {});
  }, []);

  const now = Date.now() / 1000;
  const upcoming = (lessons || []).filter((l) => (l.end_epoch || l.start_epoch || 0) >= now);
  const days = buildDays(upcoming);

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

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 30, marginBottom: 22 }}>Пары</h1>

      <div class="segment" style={{ marginBottom: 24 }}>
        <div class="thumb" style={{ left: tab === 'schedule' ? 4 : '50%', width: 'calc(50% - 4px)' }} />
        <button data-active={tab === 'schedule'} onClick={() => setTab('schedule')}>Расписание</button>
        <button data-active={tab === 'history'} onClick={() => setTab('history')}>История</button>
      </div>

      {tab === 'schedule' && (
        <div class="enter">
          {lessons == null ? (
            <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>
          ) : days.length === 0 ? (
            <div class="empty">
              <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconCalendar size={44} color="var(--ink-faint)" /></div>
              <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>Пар не нашлось</h3>
              <p style={{ fontSize: 14 }}>Ближайшие занятия из Пульса появятся здесь</p>
            </div>
          ) : (
            <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 26 }}>
              {days.map((d) => <DaySection key={d.key} day={d} off={off} onToggle={toggleOptout} />)}
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

function DaySection({ day, off, onToggle }: { day: Day; off: Set<number>; onToggle: (l: Lesson) => void }) {
  return (
    <div>
      {/* Заголовок дня — спокойная строка */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 12 }}>
        <span style={{ fontFamily: 'Unbounded, sans-serif', fontWeight: 700, fontSize: 16, textTransform: 'capitalize' }}>
          {day.tag || day.dow}
        </span>
        <span class="eyebrow">{day.dnum} {day.month}</span>
      </div>

      {/* Пары — блоками */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {day.lessons.map((l) => <LessonBlock key={l.start_epoch} l={l} off={off} onToggle={onToggle} />)}
      </div>
    </div>
  );
}

function LessonBlock({ l, off, onToggle }: { l: Lesson; off: Set<number>; onToggle: (l: Lesson) => void }) {
  const markable = isMarkable(l.type);
  const isOff = markable && off.has(l.start_epoch || 0);
  return (
    <div class="panel" style={{ padding: '13px 14px', opacity: isOff ? 0.5 : 1 }}>
      {/* Верх: время · тип · управление */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
        <span style={{ fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 14 }}>{fmtTime(l.start_epoch)}</span>
        <span class="faint" style={{ fontSize: 11.5 }}>– {fmtTime(l.end_epoch)}</span>
        <TypeTag type={l.type} />
        <div style={{ marginLeft: 'auto', flex: 'none' }}>
          {markable
            ? <OptoutToggle on={isOff} onClick={() => onToggle(l)} />
            : <span class="faint" style={{ fontSize: 10.5 }}>отмечает препод.</span>}
        </div>
      </div>

      {/* Название пары — переносится, не обрезается */}
      <div style={{ fontWeight: 600, fontSize: 15, lineHeight: 1.3 }}>{l.name}</div>

      {/* Детали — аудитория и преподаватель, каждая на своей строке */}
      {(l.room || l.subgroup) && (
        <div class="faint" style={{ fontSize: 12.5, marginTop: 6, lineHeight: 1.4 }}>
          {[l.room && `ауд. ${l.room}`, l.subgroup].filter(Boolean).join(' · ')}
        </div>
      )}
      {l.teacher && <div class="faint" style={{ fontSize: 12.5, marginTop: 2, lineHeight: 1.4 }}>{l.teacher}</div>}
    </div>
  );
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
    <span style={{ flex: 'none', fontSize: 10, fontWeight: 700, letterSpacing: 0.5, padding: '2px 6px',
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
