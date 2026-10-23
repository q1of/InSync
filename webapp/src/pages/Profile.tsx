import { useState, useEffect } from 'preact/hooks';
import { getSessionApi, logoutApi, SessionStatus, getNotifPrefsApi, setNotifPrefApi, NotifPrefs } from '../api/client';
import { useTelegram } from '../hooks/useTelegram';
import { IconLogout, IconBell, IconX, IconCheck } from '../components/icons';

interface Props { onLogout: () => void; }

function initials(name?: string | null) {
  if (!name) return 'U';
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || 'U';
}

const NOTIF_ITEMS: { key: keyof NotifPrefs; title: string; desc: string }[] = [
  { key: 'marked', title: 'Отметки на парах', desc: 'Когда вас отметили или пропустили' },
  { key: 'login', title: 'Вход в аккаунт', desc: 'Уведомлять при входе через Пульс' },
  { key: 'session', title: 'Слёт сессии', desc: 'Когда сессия Пульса истекла' },
];

export function Profile({ onLogout }: Props) {
  const { user, haptic, webApp } = useTelegram();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [prefs, setPrefs] = useState<NotifPrefs | null>(null);
  const [privacy, setPrivacy] = useState(false);

  useEffect(() => {
    // SWR: сначала мгновенно из БД (без запроса к Пульсу), потом уточняем живость в фоне.
    getSessionApi(false).then(setSession).catch(() => {});
    getSessionApi(true).then(setSession).catch(() => {});
    getNotifPrefsApi().then(setPrefs).catch(() => {});
  }, []);

  const toggleNotif = async (key: keyof NotifPrefs) => {
    const cur = prefs;
    if (!cur) return;
    const next = !cur[key];
    setPrefs({ ...cur, [key]: next });
    haptic?.selectionChanged?.();
    try { await setNotifPrefApi(key, next); }
    catch { setPrefs({ ...cur, [key]: !next }); }
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

  return (
    <div class="screen screen-pad-top" style={{ display: 'flex', flexDirection: 'column', minHeight: '82vh' }}>
      <div class="panel enter" style={{ padding: 20, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 16 }}>
        <div class="avatar" style={{ width: 60, height: 60, fontSize: 22, flex: 'none' }}>{initials(name)}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 style={{ fontSize: 20, lineHeight: 1.15, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</h2>
          <div class="faint" style={{ fontSize: 13, marginTop: 3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {[user?.username ? `@${user.username}` : null, session?.group_name].filter(Boolean).join('  ·  ') || 'Студент МИРЭА'}
          </div>
          <div class="row" style={{ gap: 7, marginTop: 9 }}>
            <span class={'dot ' + (valid ? 'dot-live' : 'dot-off')} />
            <span class="faint" style={{ fontSize: 12.5 }}>
              {session == null ? 'Проверяем…' : valid ? 'Сессия активна' : 'Сессия истекла'}
            </span>
          </div>
        </div>
      </div>

      {/* Уведомления — что присылать в Telegram */}
      <div class="panel enter" style={{ padding: 18, marginBottom: 16 }}>
        <div class="row" style={{ gap: 9, marginBottom: 4 }}>
          <IconBell size={18} color="var(--sig-b)" />
          <span style={{ fontWeight: 600, fontSize: 15 }}>Уведомления</span>
        </div>
        {prefs == null ? (
          <p class="faint" style={{ fontSize: 13, marginTop: 10 }}>Загрузка…</p>
        ) : (
          NOTIF_ITEMS.map((it, i) => (
            <div key={it.key} class="row" style={{ justifyContent: 'space-between', gap: 12,
              padding: '12px 0', borderTop: i ? '1px solid var(--hairline)' : 'none' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600, fontSize: 14.5 }}>{it.title}</div>
                <div class="faint" style={{ fontSize: 12.5, marginTop: 2 }}>{it.desc}</div>
              </div>
              <div class="toggle" data-on={prefs[it.key]} onClick={() => toggleNotif(it.key)} />
            </div>
          ))
        )}
      </div>

      <button class="btn btn-danger" onClick={logout}>
        <IconLogout size={18} /> Выйти
      </button>

      <div style={{ textAlign: 'center', marginTop: 'auto', paddingTop: 20, paddingBottom: 4 }}>
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
