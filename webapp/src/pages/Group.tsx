import { useState, useEffect } from 'preact/hooks';
import { getGroupApi, setGroupMarkingApi, GroupInfo, GroupMember } from '../api/client';
import { IconGroup } from '../components/icons';
import { useTelegram } from '../hooks/useTelegram';

function initials(name?: string | null) {
  if (!name) return 'U';
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] || '') + (p[1]?.[0] || '')).toUpperCase() || 'U';
}

function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10, h = n % 100;
  if (d === 1 && h !== 11) return one;
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return few;
  return many;
}

function memberNote(m: GroupMember): string {
  if (!m.enabled) return 'не отмечается с группой';
  return m.session_alive ? 'отмечается с группой' : 'нужно перезайти в InSync';
}

export function Group() {
  const { haptic } = useTelegram();
  const [group, setGroup] = useState<GroupInfo | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => { getGroupApi().then(setGroup).catch(() => setError(true)); }, []);

  const toggle = async () => {
    if (!group) return;
    const next = !group.enabled;
    const apply = (on: boolean) => setGroup((g) => g && {
      ...g, enabled: on, members: g.members.map((m) => (m.is_self ? { ...m, enabled: on } : m)),
    });
    haptic?.impactOccurred?.('light');
    apply(next);
    try { await setGroupMarkingApi(next); } catch { apply(!next); }
  };

  if (error) return <Screen><Empty title="Не удалось загрузить группу" sub="Попробуй открыть вкладку ещё раз" /></Screen>;
  if (!group) return <Screen><div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div></Screen>;
  if (!group.group_name) {
    return <Screen><Empty title="Группа не найдена" sub="Мы берём её из профиля Пульса. Выйди и войди заново — она подтянется." /></Screen>;
  }

  const total = group.members.length;
  const together = group.members.filter((m) => m.enabled).length;

  return (
    <Screen>
      {/* Карточка группы: название, сколько нас и главный тумблер */}
      <div class="panel enter" style={{ padding: 18, marginBottom: 22 }}>
        <div class="eyebrow">Учебная группа</div>
        <div style={{ fontFamily: 'Unbounded, sans-serif', fontWeight: 700, fontSize: 24, marginTop: 8 }}>{group.group_name}</div>
        <div class="faint" style={{ fontSize: 13, marginTop: 6 }}>
          {total} {plural(total, 'человек', 'человека', 'человек')} в InSync · {together} {plural(together, 'отмечается', 'отмечаются', 'отмечаются')} вместе
        </div>
        <div style={{ height: 1, background: 'var(--hairline)', margin: '16px 0' }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 15 }}>Отмечаться с группой</div>
            <div class="faint" style={{ fontSize: 12.5, marginTop: 4, lineHeight: 1.4 }}>
              Твой скан отмечает всех, у кого это включено, а их скан — тебя
            </div>
          </div>
          <div class="toggle" data-on={group.enabled} onClick={toggle} title="Отмечаться с группой" />
        </div>
      </div>

      {total <= 1 ? (
        <Empty title="Пока здесь только ты" sub="Позови одногруппников в InSync — они появятся здесь сами, без заявок" />
      ) : (
        <>
          <div class="muted" style={{ fontSize: 13, marginBottom: 12 }}>Участники</div>
          <div class="stagger" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {group.members.map((m) => (
              <div key={m.telegram_id} class="panel" style={{ padding: 14, display: 'flex', alignItems: 'center', gap: 12, opacity: m.enabled ? 1 : 0.55 }}>
                <div class="avatar" style={{ width: 44, height: 44, fontSize: 15, flex: 'none' }}>{initials(m.name)}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {m.name}{m.is_self && <span class="faint" style={{ fontWeight: 500 }}> · ты</span>}
                  </div>
                  <div class="faint" style={{ fontSize: 13, marginTop: 2 }}>{memberNote(m)}</div>
                </div>
                <span style={{ width: 9, height: 9, borderRadius: '50%', flex: 'none',
                  background: m.enabled && m.session_alive ? 'var(--live)' : m.enabled ? 'var(--fail)' : 'var(--hairline-2)',
                  boxShadow: m.enabled && m.session_alive ? '0 0 10px rgba(255,255,255,0.45)' : 'none' }} />
              </div>
            ))}
          </div>
        </>
      )}
    </Screen>
  );
}

function Screen({ children }: { children: any }) {
  return (
    <div class="screen screen-pad-top">
      <h1 style={{ fontSize: 30, marginBottom: 22 }}>Группа</h1>
      {children}
    </div>
  );
}

function Empty({ title, sub }: { title: string; sub: string }) {
  return (
    <div class="empty">
      <div style={{ marginBottom: 14, display: 'flex', justifyContent: 'center' }}><IconGroup size={44} color="var(--ink-faint)" /></div>
      <h3 style={{ color: 'var(--ink)', fontSize: 17, marginBottom: 6 }}>{title}</h3>
      <p style={{ fontSize: 14 }}>{sub}</p>
    </div>
  );
}
