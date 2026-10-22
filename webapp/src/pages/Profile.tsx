import { useState, useEffect } from 'preact/hooks';
import { getSessionApi, getScheduleApi, logoutApi, SessionStatus, Lesson } from '../api/client';
import { useTelegram } from '../hooks/useTelegram';
import { IconLogout, IconCalendar, IconX, IconCheck } from '../components/icons';

interface Props { onLogout: () => void; }

const DOW = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

function initials(name?: string | null) {
  if (!name) return 'U';
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || 'U';
}

interface Day { key: string; label: string; count: number; ts: number; }

export function Profile({ onLogout }: Props) {
  const { user, haptic, webApp } = useTelegram();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [days, setDays] = useState<Day[] | null>(null);
  const [privacy, setPrivacy] = useState(false);

  useEffect(() => {
    getSessionApi(true).then(setSession).catch(() => {});
    getScheduleApi(7).then((r) => setDays(groupDays(r.lessons))).catch(() => setDays([]));
  }, []);

  const groupDays = (lessons: Lesson[]): Day[] => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const map = new Map<string, Day>();
    for (const l of lessons) {
      if (!l.start_epoch) continue;
      const d = new Date(l.start_epoch * 1000);
      const dayTs = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      const key = String(dayTs);
      if (!map.has(key)) {
        const diff = Math.round((dayTs - today) / 86400000);
        const label = diff === 0 ? 'Сегодня' : diff === 1 ? 'Завтра' : `${DOW[d.getDay()]} ${d.getDate()}`;
        map.set(key, { key, label, count: 0, ts: dayTs });
      }
      map.get(key)!.count++;
    }
    return [...map.values()].filter((d) => d.ts >= today).sort((a, b) => a.ts - b.ts).slice(0, 4);
  };

  const doLogout = async () => {
    haptic?.impactOccurred?.('medium');
    try { await logoutApi(); } catch {}
    onLogout();
  };

  // Подтверждение выхода: нативный поп-ап Telegram, иначе обычный confirm
  const logout = () => {
    const msg = 'Вы уверены, что хотите выйти из аккаунта?';
    if (webApp?.showConfirm) {
      webApp.showConfirm(msg, (ok: boolean) => { if (ok) doLogout(); });
    } else if (confirm(msg)) {
      doLogout();
    }
  };

  const name = user?.first_name || 'Студент';
  const valid = session?.valid;
  const nearest = days?.find((d) => d.count > 0);

  return (
    <div class="screen screen-pad-top">
      <div class="panel enter" style={{ padding: 26, textAlign: 'center', marginBottom: 16 }}>
        <div class="avatar" style={{ width: 84, height: 84, fontSize: 30, margin: '0 auto 16px' }}>{initials(name)}</div>
        <h2 style={{ fontSize: 23 }}>{name}</h2>
        {session?.group_name && <p class="muted" style={{ marginTop: 6 }}>{session.group_name}</p>}
        <div style={{ marginTop: 16 }}>
          <span class="pill">
            <span class={'dot ' + (valid ? 'dot-live' : 'dot-off')} />
            {session == null ? 'Проверяем…' : valid ? 'Сессия активна' : 'Сессия истекла'}
          </span>
        </div>
      </div>

      {/* Ближайшие дни — только количество пар, без самого расписания */}
      <div class="panel enter" style={{ padding: 18, marginBottom: 16 }}>
        <div class="row" style={{ justifyContent: 'space-between', marginBottom: 14 }}>
          <div class="row" style={{ gap: 9 }}>
            <IconCalendar size={18} color="var(--sig-b)" />
            <span style={{ fontWeight: 600, fontSize: 15 }}>Учебные дни</span>
          </div>
          {nearest && <span class="faint" style={{ fontSize: 13 }}>{nearest.label.toLowerCase()} — {nearest.count} {plural(nearest.count)}</span>}
        </div>
        {days == null ? (
          <p class="faint" style={{ fontSize: 13 }}>Загрузка…</p>
        ) : days.length === 0 ? (
          <p class="faint" style={{ fontSize: 13 }}>Пар в ближайшие дни нет</p>
        ) : (
          <div class="row" style={{ gap: 10 }}>
            {days.map((d) => (
              <div key={d.key} style={{
                flex: 1, textAlign: 'center', padding: '12px 6px', borderRadius: 'var(--r-sm)',
                background: d.count ? 'var(--bg-2)' : 'transparent',
                border: '1px solid ' + (d.count ? 'var(--hairline)' : 'transparent'),
                opacity: d.count ? 1 : 0.5,
              }}>
                <div class="display sig-text" style={{ fontSize: 24 }}>{d.count}</div>
                <div class="faint" style={{ fontSize: 11, marginTop: 4 }}>{d.label}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <button class="btn btn-danger" onClick={logout}>
        <IconLogout size={18} /> Выйти
      </button>

      <div style={{ textAlign: 'center', marginTop: 18 }}>
        <button onClick={() => { haptic?.selectionChanged?.(); setPrivacy(true); }}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-faint)',
            fontSize: 12, textDecoration: 'underline', textUnderlineOffset: 3, padding: 6, fontFamily: 'inherit' }}>
          Конфиденциальность
        </button>
      </div>

      {privacy && <PrivacyModal onClose={() => setPrivacy(false)} />}
    </div>
  );
}

function PrivacyModal({ onClose }: { onClose: () => void }) {
  const points = [
    'Логин и пароль вводятся на странице входа и уходят напрямую в МИРЭА (Пульс). InSync их не хранит и не видит.',
    'Мы храним только зашифрованную сессию Пульса — она нужна, чтобы отмечать вас на парах по QR.',
    'Сессия используется только для: проверки входа, загрузки вашего расписания и отметки посещаемости — за вас и за друзей, которых вы сами включили.',
    'Друзья видят только ваше имя из Telegram и учебную группу. Отметить вас может лишь тот, кого вы добавили и включили.',
    'Мы не передаём ваши данные третьим лицам и не используем их ни для чего, кроме отметок.',
    'Кнопка «Выйти» удаляет сессию с сервера.',
  ];
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.6)',
      backdropFilter: 'blur(4px)', display: 'grid', placeItems: 'center', padding: 20 }}>
      <div class="panel enter" onClick={(e) => e.stopPropagation()}
        style={{ padding: 22, maxWidth: 460, width: '100%', maxHeight: '80vh', overflowY: 'auto' }}>
        <div class="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14 }}>
          <h3 style={{ fontSize: 19 }}>Конфиденциальность</h3>
          <button onClick={onClose} aria-label="Закрыть"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-faint)', padding: 2, marginTop: 2 }}>
            <IconX size={20} />
          </button>
        </div>
        <p class="muted" style={{ fontSize: 13.5, marginBottom: 16, lineHeight: 1.5 }}>
          InSync хранит минимум данных и использует их только для отметки на парах.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {points.map((t, i) => (
            <div key={i} class="row" style={{ gap: 10, alignItems: 'flex-start' }}>
              <span style={{ width: 18, height: 18, borderRadius: '50%', flex: 'none', marginTop: 1, display: 'grid',
                placeItems: 'center', background: 'var(--live-dim)', color: 'var(--live)' }}>
                <IconCheck size={11} stroke={3} />
              </span>
              <span style={{ fontSize: 13.5, lineHeight: 1.5 }}>{t}</span>
            </div>
          ))}
        </div>
        <button class="btn" style={{ marginTop: 20 }} onClick={onClose}>Понятно</button>
      </div>
    </div>
  );
}

function plural(n: number) {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return 'пара';
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return 'пары';
  return 'пар';
}
