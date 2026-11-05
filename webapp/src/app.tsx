import { useState, useEffect } from 'preact/hooks';
import { getSessionApi, getPendingApi } from './api/client';
import { useTelegram } from './hooks/useTelegram';
import { Scanner } from './pages/Scanner';
import { Login } from './pages/Login';
import { Profile } from './pages/Profile';
import { Friends } from './pages/Friends';
import { Lessons } from './pages/Lessons';
import { Pass } from './pages/Pass';
import { Group } from './pages/Group';
import { IconScan, IconUsers, IconCalendar, IconUser, IconPass, IconGroup } from './components/icons';

type Tab = 'scanner' | 'lessons' | 'pass' | 'group' | 'friends' | 'profile';

const BOOT_ATTEMPTS = 3;
const BOOT_TIMEOUT_MS = 6000;
const BOOT_RETRY_DELAY_MS = 1500;

const TABS: { id: Tab; label: string; Icon: any }[] = [
  { id: 'scanner', label: 'Сканер', Icon: IconScan },
  { id: 'lessons', label: 'Пары', Icon: IconCalendar },
  { id: 'pass', label: 'Пропуск', Icon: IconPass },
  { id: 'group', label: 'Группа', Icon: IconGroup },
  { id: 'friends', label: 'Друзья', Icon: IconUsers },
  { id: 'profile', label: 'Профиль', Icon: IconUser },
];

export function App() {
  const [tab, setTab] = useState<Tab>('scanner');
  const [initializing, setInit] = useState(true);
  const [hasSession, setHasSession] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const [bootFailed, setBootFailed] = useState(false);
  const { haptic } = useTelegram();

  // Обрыв связи — не повод выкидывать на экран входа: повторяем и даём кнопку.
  const boot = async () => {
    setInit(true);
    setBootFailed(false);
    for (let i = 0; i < BOOT_ATTEMPTS; i++) {
      try {
        const s = await getSessionApi(false, BOOT_TIMEOUT_MS);
        setHasSession(s.logged_in);
        setInit(false);
        return;
      } catch (e: any) {
        // 5xx — сервер недоступен (их отдаёт и Cloudflare), остальное — его ответ: повтор не поможет.
        if (e?.status && e.status < 500) {
          setHasSession(false);
          setInit(false);
          return;
        }
        if (e?.status && i < BOOT_ATTEMPTS - 1) await new Promise((r) => setTimeout(r, BOOT_RETRY_DELAY_MS));
      }
    }
    setBootFailed(true);
    setInit(false);
  };

  useEffect(() => { boot(); }, []);

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

  if (bootFailed) {
    return (
      <div class="screen" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', justifyContent: 'center', textAlign: 'center', gap: 16 }}>
        <p class="muted" style={{ fontSize: 15 }}>Не удалось подключиться к InSync</p>
        <button class="btn" onClick={boot}>Повторить</button>
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
          {tab === 'pass' && <Pass />}
          {tab === 'group' && <Group />}
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
