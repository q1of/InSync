import { useState, useEffect } from 'preact/hooks';
import { getSessionApi, getPendingApi } from './api/client';
import { useTelegram } from './hooks/useTelegram';
import { Scanner } from './pages/Scanner';
import { Login } from './pages/Login';
import { Profile } from './pages/Profile';
import { Friends } from './pages/Friends';
import { Lessons } from './pages/Lessons';
import { IconScan, IconUsers, IconCalendar, IconUser } from './components/icons';

type Tab = 'scanner' | 'lessons' | 'friends' | 'profile';

const TABS: { id: Tab; label: string; Icon: any }[] = [
  { id: 'scanner', label: 'Сканер', Icon: IconScan },
  { id: 'lessons', label: 'Пары', Icon: IconCalendar },
  { id: 'friends', label: 'Друзья', Icon: IconUsers },
  { id: 'profile', label: 'Профиль', Icon: IconUser },
];

export function App() {
  const [tab, setTab] = useState<Tab>('scanner');
  const [initializing, setInit] = useState(true);
  const [hasSession, setHasSession] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const { webApp, haptic } = useTelegram();

  useEffect(() => {
    getSessionApi(false)
      .then((s) => setHasSession(s.logged_in))
      .catch(() => setHasSession(false))
      .finally(() => setInit(false));
    if (webApp) { webApp.ready(); webApp.expand(); }
  }, [webApp]);

  useEffect(() => {
    if (!hasSession) return;
    getPendingApi().then((p) => setPendingCount(p.length)).catch(() => {});
  }, [hasSession, tab]);

  const go = (t: Tab) => { haptic?.selectionChanged?.(); setTab(t); };

  if (initializing) {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <div class="spinner" />
      </div>
    );
  }

  if (!hasSession) {
    return <Login onLoginSuccess={() => setHasSession(true)} />;
  }

  return (
    <div class="app-shell">
      {/* Сканер держим смонтированным всегда (камера запрашивается один раз),
          просто скрываем его на других вкладках. */}
      <div style={{ flex: 1, display: tab === 'scanner' ? 'block' : 'none' }}>
        <Scanner active={tab === 'scanner'} />
      </div>
      {tab !== 'scanner' && (
        <div key={tab} class="enter" style={{ flex: 1 }}>
          {tab === 'lessons' && <Lessons />}
          {tab === 'friends' && <Friends onPendingChange={setPendingCount} />}
          {tab === 'profile' && <Profile onLogout={() => { setHasSession(false); setTab('scanner'); }} />}
        </div>
      )}

      <nav class="tabbar">
        {TABS.map(({ id, label, Icon }) => (
          <button key={id} class="tab" data-active={tab === id} onClick={() => go(id)}>
            <span class="tab-mark" />
            <span class="tab-ico"><Icon size={23} stroke={2.1} /></span>
            <span class="tab-label">{label}</span>
            {id === 'friends' && pendingCount > 0 && <span class="tab-badge">{pendingCount}</span>}
          </button>
        ))}
      </nav>
    </div>
  );
}
