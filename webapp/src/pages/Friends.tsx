import { useState, useEffect } from 'preact/hooks';
import {
  getFriendsApi, getPendingApi, sendFriendRequestApi, acceptFriendApi,
  rejectFriendApi, removeFriendApi, setMarkingApi, Friend, PendingReq,
} from '../api/client';
import { useTelegram } from '../hooks/useTelegram';
import { IconUsers, IconMail, IconTrash, IconCheck, IconX, IconPlus } from '../components/icons';

interface Props { onPendingChange?: (n: number) => void; }

function initials(name?: string | null) {
  if (!name) return 'U';
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || 'U';
}

export function Friends({ onPendingChange }: Props) {
  const { haptic, webApp } = useTelegram();
  const [tab, setTab] = useState<'friends' | 'pending'>('friends');
  const [friends, setFriends] = useState<Friend[]>([]);
  const [pending, setPending] = useState<PendingReq[]>([]);
  const [loading, setLoading] = useState(true);
  const [addId, setAddId] = useState('');
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);

  const load = async () => {
    try {
      const [f, p] = await Promise.all([getFriendsApi(), getPendingApi()]);
      setFriends(f); setPending(p); onPendingChange?.(p.length);
    } catch {} finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const flash = (text: string, ok = true) => { setToast({ text, ok }); setTimeout(() => setToast(null), 2200); };

  const add = async (e: Event) => {
    e.preventDefault();
    const id = addId.trim();
    if (!id) return;
    try { await sendFriendRequestApi(id); setAddId(''); flash('Заявка отправлена'); haptic?.notificationOccurred?.('success'); }
    catch (err: any) { flash(err.message || 'Не удалось', false); haptic?.notificationOccurred?.('error'); }
  };
  const accept = async (tid: number) => { try { await acceptFriendApi(tid); haptic?.impactOccurred?.('light'); load(); } catch (e: any) { flash(e.message || 'Ошибка', false); } };
  const reject = async (tid: number) => { try { await rejectFriendApi(tid); load(); } catch {} };
  const doRemove = async (tid: number) => { try { await removeFriendApi(tid); haptic?.impactOccurred?.('medium'); load(); } catch {} };
  const remove = (tid: number, name?: string | null) => {
    const msg = `Удалить ${name || 'этого друга'} из друзей?`;
    if (webApp?.showConfirm) webApp.showConfirm(msg, (ok: boolean) => { if (ok) doRemove(tid); });
    else if (confirm(msg)) doRemove(tid);
  };
  const toggle = async (f: Friend) => {
    const next = !f.marking_enabled;
    setFriends((arr) => arr.map((x) => x.telegram_id === f.telegram_id ? { ...x, marking_enabled: next } : x));
    haptic?.selectionChanged?.();
    try { await setMarkingApi(f.telegram_id, next); }
    catch { setFriends((arr) => arr.map((x) => x.telegram_id === f.telegram_id ? { ...x, marking_enabled: !next } : x)); }
  };

  const markedCount = friends.filter((f) => f.marking_enabled).length;

  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 26, marginBottom: 18 }}>Друзья</h1>

      <div class="segment" style={{ marginBottom: 20 }}>
        <div class="thumb" style={{ left: tab === 'friends' ? 4 : '50%', width: 'calc(50% - 4px)' }} />
        <button data-active={tab === 'friends'} onClick={() => setTab('friends')}>Мои друзья</button>
        <button data-active={tab === 'pending'} onClick={() => setTab('pending')}>
          Заявки{pending.length > 0 ? ` · ${pending.length}` : ''}
        </button>
      </div>

      {tab === 'friends' && (
        <div class="enter">
          <form onSubmit={add} class="row" style={{ marginBottom: 18 }}>
            <input class="field" placeholder="@username" value={addId}
              onInput={(e) => setAddId((e.target as HTMLInputElement).value)} />
            <button class="btn" type="submit" style={{ width: 54, flex: 'none', padding: 0, height: 54 }} aria-label="Добавить"><IconPlus size={22} /></button>
          </form>

          {markedCount > 0 && (
            <div class="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
              <span class="muted" style={{ fontSize: 13 }}>Отмечаем на паре</span>
              <span class="pill" style={{ padding: '4px 10px', fontSize: 12 }}>
                <span class="dot dot-live" />{markedCount} из {friends.length}
              </span>
            </div>
          )}

          {loading ? <Loader /> : friends.length === 0 ? (
            <Empty icon={<IconUsers size={44} color="var(--ink-faint)" />} title="Пока никого" sub="Добавь друга по его @username" />
          ) : (
            <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {friends.map((f) => (
                <div key={f.telegram_id} class="panel" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div class="avatar" style={{ width: 44, height: 44, fontSize: 15 }}>{initials(f.name)}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.name}</div>
                    <div class="faint" style={{ fontSize: 13 }}>{f.group || `ID ${f.telegram_id}`}</div>
                  </div>
                  <div class="toggle" data-on={f.marking_enabled} onClick={() => toggle(f)} title="Отмечать на паре" />
                  <button class="tap" onClick={() => remove(f.telegram_id, f.name)} style={{ background: 'none', border: 'none', color: 'var(--ink-faint)', cursor: 'pointer', padding: 4 }} aria-label="Удалить"><IconTrash size={18} /></button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'pending' && (
        <div class="enter">
          {loading ? <Loader /> : pending.length === 0 ? (
            <Empty icon={<IconMail size={44} color="var(--ink-faint)" />} title="Заявок нет" sub="Здесь появятся входящие приглашения" />
          ) : (
            <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {pending.map((p) => (
                <div key={p.telegram_id} class="panel" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div class="avatar" style={{ width: 44, height: 44, fontSize: 15 }}>{initials(p.name)}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.name}</div>
                    <div class="faint" style={{ fontSize: 13 }}>{p.group || `ID ${p.telegram_id}`}</div>
                  </div>
                  <button onClick={() => accept(p.telegram_id)} style={iconBtn('var(--live)', 'var(--live-dim)')} aria-label="Принять"><IconCheck size={18} stroke={2.6} /></button>
                  <button onClick={() => reject(p.telegram_id)} style={iconBtn('var(--miss)', 'var(--miss-dim)')} aria-label="Отклонить"><IconX size={18} stroke={2.6} /></button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {toast && (
        <div style={{ position: 'fixed', bottom: 'calc(var(--tab-h) + 18px)', left: 20, right: 20, maxWidth: 480, margin: '0 auto',
          padding: '13px 16px', borderRadius: 'var(--r-md)', textAlign: 'center', fontWeight: 600, zIndex: 150,
          background: toast.ok ? 'var(--live-dim)' : 'var(--miss-dim)', color: toast.ok ? 'var(--live)' : 'var(--miss)',
          border: '1px solid ' + (toast.ok ? 'rgba(61,232,174,0.4)' : 'rgba(255,91,123,0.4)'), animation: 'enter 0.3s both' }}>
          {toast.text}
        </div>
      )}
    </div>
  );
}

const iconBtn = (color: string, bg: string) => ({
  width: 38, height: 38, borderRadius: 'var(--r-sm)', border: 'none', cursor: 'pointer',
  background: bg, color, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none',
});

function Loader() { return <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>; }
function Empty({ icon, title, sub }: { icon: any; title: string; sub: string }) {
  return (
    <div class="empty">
      <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}>{icon}</div>
      <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>{title}</h3>
      <p style={{ fontSize: 14 }}>{sub}</p>
    </div>
  );
}
