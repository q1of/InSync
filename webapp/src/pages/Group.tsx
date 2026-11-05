import { useState, useEffect } from 'preact/hooks';
import {
  getGroupApi, createGroupApi, joinGroupApi, leaveGroupApi, setGroupMarkingApi, GroupInfo, GroupMember,
} from '../api/client';
import { IconCheck, IconGroup } from '../components/icons';
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

interface Props { inviteCode?: string | null }

// Одинаковая ширина кнопок «Вступить» и «Создать» — и поля рядом с ними одной ширины.
const actionBtn = { width: 100, flex: 'none', padding: 0, height: 54 } as const;

// Группа — раздел вкладки «Друзья». inviteCode приходит из ссылки-приглашения.
export function GroupView({ inviteCode }: Props) {
  const { haptic, webApp } = useTelegram();
  const [group, setGroup] = useState<GroupInfo | null | undefined>(undefined);
  const [loadError, setLoadError] = useState(false);
  const [code, setCode] = useState(inviteCode || '');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [toast, setToast] = useState('');
  const [copied, setCopied] = useState(false);   // кнопка с кодом на миг показывает «Скопировано»

  useEffect(() => { getGroupApi().then((r) => setGroup(r.group)).catch(() => setLoadError(true)); }, []);

  const flash = (text: string) => { setToast(text); setTimeout(() => setToast(''), 2200); };

  const run = async (action: () => Promise<{ group: GroupInfo }>) => {
    setError(''); setBusy(true);
    try {
      const res = await action();
      haptic?.notificationOccurred?.('success');
      setGroup(res.group);
    } catch (e: any) {
      haptic?.notificationOccurred?.('error');
      setError(e?.message || 'Не получилось');
    } finally { setBusy(false); }
  };

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

  const invite = () => {
    if (!group) return;
    haptic?.impactOccurred?.('light');
    if (group.invite_link && webApp?.openTelegramLink) {
      const text = `Вступай в группу «${group.name}» в InSync — будем отмечаться на парах вместе`;
      webApp.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(group.invite_link)}&text=${encodeURIComponent(text)}`);
      return;
    }
    copyCode();
  };

  // Нажатие на код копирует его. В WebView Telegram clipboard API бывает недоступен —
  // тогда копируем через скрытое поле, а если и это не вышло, просто показываем код.
  const copyCode = async () => {
    if (!group) return;
    let ok = false;
    try {
      await navigator.clipboard.writeText(group.code);
      ok = true;
    } catch {
      const field = document.createElement('textarea');
      field.value = group.code;
      field.setAttribute('readonly', '');
      field.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
      document.body.appendChild(field);
      field.select();
      ok = document.execCommand('copy');
      field.remove();
    }
    if (!ok) { flash(`Код группы: ${group.code}`); return; }
    haptic?.notificationOccurred?.('success');
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  const leave = async () => {
    if (!group) return;
    const ok = await new Promise<boolean>((resolve) => {
      const text = `Выйти из группы «${group.name}»? Вы перестанете отмечать друг друга.`;
      if (webApp?.showConfirm) webApp.showConfirm(text, resolve); else resolve(window.confirm(text));
    });
    if (!ok) return;
    try { await leaveGroupApi(); setGroup(null); setCode(''); } catch { flash('Не удалось выйти, попробуй ещё раз'); }
  };

  if (loadError) return <Empty title="Не удалось загрузить группу" sub="Попробуй открыть раздел ещё раз" />;
  if (group === undefined) return <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}><div class="spinner" /></div>;

  // Не в группе: вступить по коду (или по приглашению) либо создать свою
  if (!group) {
    return (
      <div class="enter" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ textAlign: 'center', padding: '6px 10px 4px' }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 12 }}><IconGroup size={40} color="var(--ink-faint)" /></div>
          <p class="muted" style={{ fontSize: 14, lineHeight: 1.45 }}>
            Собери свою группу: один тумблер — и вы отмечаете друг друга без заявок в друзья
          </p>
        </div>

        <form class="panel" style={{ padding: 18 }} onSubmit={(e) => { e.preventDefault(); run(() => joinGroupApi(code)); }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>{inviteCode ? 'Тебя пригласили в группу' : 'Вступить по коду'}</div>
          <div class="faint" style={{ fontSize: 12.5, marginTop: 4, marginBottom: 12 }}>Код из 6 символов — его покажет тот, кто создал группу</div>
          <div class="row">
            {/* Разрядка — только у введённого кода: подсказка выглядит как в соседнем поле */}
            <input class="field" placeholder="Код группы" value={code} maxLength={6}
              style={code ? { letterSpacing: '0.12em', fontWeight: 600 } : undefined}
              onInput={(e) => setCode((e.target as HTMLInputElement).value.toUpperCase())} />
            <button class="btn" type="submit" disabled={busy || code.trim().length < 6} style={actionBtn}>Вступить</button>
          </div>
        </form>

        <form class="panel" style={{ padding: 18 }} onSubmit={(e) => { e.preventDefault(); run(() => createGroupApi(name)); }}>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Создать группу</div>
          <div class="faint" style={{ fontSize: 12.5, marginTop: 4, marginBottom: 12 }}>Потом позовёшь остальных по ссылке</div>
          <div class="row">
            <input class="field" placeholder="Название группы" value={name} maxLength={40}
              onInput={(e) => setName((e.target as HTMLInputElement).value)} />
            <button class="btn" type="submit" disabled={busy || !name.trim()} style={actionBtn}>Создать</button>
          </div>
        </form>

        {error && <div style={{ color: 'var(--fail)', fontSize: 13, textAlign: 'center' }}>{error}</div>}
      </div>
    );
  }

  const total = group.members.length;
  const together = group.members.filter((m) => m.enabled).length;

  return (
    <div class="enter">
      {/* Карточка группы: название, код, приглашение и главный тумблер */}
      <div class="panel" style={{ padding: 18, marginBottom: 22 }}>
        <div class="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <div class="eyebrow">Группа</div>
            <div style={{ fontFamily: 'Unbounded, sans-serif', fontWeight: 700, fontSize: 20, lineHeight: 1.2, marginTop: 8, overflowWrap: 'break-word' }}>{group.name}</div>
          </div>
          <button class="tap" onClick={copyCode} title="Скопировать код" style={{ flex: 'none', padding: '8px 12px', borderRadius: 999,
            border: `1px solid ${copied ? 'var(--ink)' : 'var(--hairline-2)'}`, background: copied ? 'var(--ink)' : 'none',
            color: copied ? '#09090B' : 'var(--ink)', fontFamily: 'Unbounded, monospace', fontWeight: 700, fontSize: 13,
            letterSpacing: '0.08em', cursor: 'pointer', position: 'relative',
            transition: 'background 0.2s ease, color 0.2s ease, border-color 0.2s ease' }}>
            {/* Код остаётся в разметке (невидимым), поэтому кнопка не меняет размер: поверх — галочка */}
            <span style={{ visibility: copied ? 'hidden' : 'visible' }}>{group.code}</span>
            {copied && (
              <span style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
                <IconCheck size={16} stroke={3} />
              </span>
            )}
          </button>
        </div>
        <div class="faint" style={{ fontSize: 13, marginTop: 6 }}>
          {total} {plural(total, 'участник', 'участника', 'участников')} · {together} {plural(together, 'отмечается', 'отмечаются', 'отмечаются')} вместе
        </div>
        <button class="btn" onClick={invite} style={{ marginTop: 16 }}>Пригласить в группу</button>
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

      {inviteCode && inviteCode !== group.code && (
        <div class="faint" style={{ fontSize: 13, marginBottom: 14, textAlign: 'center' }}>
          Тебя пригласили в другую группу — чтобы вступить, сначала выйди из этой
        </div>
      )}

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

      {total <= 1 && (
        <p class="faint" style={{ fontSize: 13, textAlign: 'center', marginTop: 14 }}>Пока здесь только ты — позови остальных по ссылке</p>
      )}

      <button onClick={leave} style={{ display: 'block', margin: '22px auto 4px', background: 'none', border: 'none',
        color: 'var(--ink-faint)', fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}>Выйти из группы</button>

      {toast && (
        <div style={{ position: 'fixed', bottom: 'calc(var(--tab-h) + 18px)', left: 20, right: 20, maxWidth: 480, margin: '0 auto',
          padding: '13px 16px', borderRadius: 'var(--r-md)', textAlign: 'center', fontWeight: 600, zIndex: 150,
          background: 'var(--bg-2)', color: 'var(--ink)', border: '1px solid var(--hairline-2)', animation: 'enter 0.3s both' }}>
          {toast}
        </div>
      )}
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
