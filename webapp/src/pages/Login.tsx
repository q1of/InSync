import { useState, useEffect, useRef } from 'preact/hooks';
import { loginApi, complete2faApi, resend2faApi } from '../api/client';

interface Props { onLoginSuccess: () => void; }

export function Login({ onLoginSuccess }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [needs2fa, setNeeds2fa] = useState(false);
  const [challengeId, setChallengeId] = useState('');
  const [hint, setHint] = useState('');
  const [resendIn, setResendIn] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (resendIn <= 0) return;
    const t = setInterval(() => setResendIn((n) => (n <= 1 ? 0 : n - 1)), 1000);
    return () => clearInterval(t);
  }, [resendIn]);

  useEffect(() => { if (needs2fa) codeRef.current?.focus(); }, [needs2fa]);

  const submitLogin = async (e: Event) => {
    e.preventDefault();
    setError(''); setLoading(true);
    try {
      const res = await loginApi(username, password);
      if (res.needs_2fa) {
        setChallengeId(res.challenge_id || '');
        setHint(res.hint || '');
        setNeeds2fa(true);
        setResendIn(45);
        setPassword('');
      } else {
        onLoginSuccess();
      }
    } catch (err: any) {
      setError(err.message || 'Не удалось войти');
    } finally { setLoading(false); }
  };

  const submitCode = async (e: Event) => {
    e.preventDefault();
    setError(''); setLoading(true);
    try {
      await complete2faApi(challengeId, code);
      onLoginSuccess();
    } catch (err: any) {
      setError(err.message || 'Неверный код');
    } finally { setLoading(false); }
  };

  const resend = async () => {
    setError('');
    try {
      const res = await resend2faApi(challengeId);
      if (res.hint) setHint(res.hint);
      setResendIn(45);
    } catch (err: any) {
      setError(err.message || 'Не удалось отправить код');
    }
  };

  return (
    <div class="app-shell">
      <div class="screen screen-pad-top" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', minHeight: '100vh' }}>
        {/* Герой: знак «сигнала» + название */}
        <div class="enter" style={{ textAlign: 'center', marginBottom: 36 }}>
          <h1 style={{ fontSize: 34 }}>In<span class="sig-text">Sync</span></h1>
          <p class="muted" style={{ marginTop: 8, fontSize: 15 }}>Отметка на парах — за себя и друзей, одним сканом</p>
        </div>

        {!needs2fa ? (
          <form class="panel enter" onSubmit={submitLogin} style={{ padding: 22 }}>
            <div class="row" style={{ justifyContent: 'space-between', marginBottom: 18 }}>
              <h3 style={{ fontSize: 19 }}>Вход через Пульс</h3>
              <span class="pill"><span class="dot" style={{ background: 'var(--sig-b)' }} />pulse.mirea.ru</span>
            </div>
            {error && <Err text={error} />}
            <input class="field" style={{ marginBottom: 12 }} type="text" autoComplete="username"
              placeholder="Логин от Пульса" value={username}
              onInput={(e) => setUsername((e.target as HTMLInputElement).value)} required />
            <input class="field" style={{ marginBottom: 18 }} type="password" autoComplete="current-password"
              placeholder="Пароль" value={password}
              onInput={(e) => setPassword((e.target as HTMLInputElement).value)} required />
            <button class="btn" type="submit" disabled={loading}>{loading ? 'Входим…' : 'Войти'}</button>
            <p class="faint" style={{ fontSize: 12.5, textAlign: 'center', marginTop: 14, lineHeight: 1.5 }}>
              Данные уходят напрямую в МИРЭА. InSync хранит только зашифрованную сессию.
            </p>
          </form>
        ) : (
          <div class="panel enter" style={{ padding: 22 }}>
            <h3 style={{ fontSize: 19, marginBottom: 6 }}>Код подтверждения</h3>
            <p class="muted" style={{ fontSize: 14, marginBottom: 18 }}>{hint || 'Введите код из сообщения'}</p>
            {error && <Err text={error} />}
            <form onSubmit={submitCode}>
              <input ref={codeRef} class="field" inputMode="numeric" autoComplete="one-time-code"
                maxLength={6} placeholder="______" value={code}
                onInput={(e) => setCode((e.target as HTMLInputElement).value.replace(/\D/g, '').slice(0, 6))}
                style={{ textAlign: 'center', letterSpacing: 14, fontSize: 28, fontFamily: 'Unbounded, monospace', marginBottom: 16 }} required />
              <button class="btn" type="submit" disabled={loading || code.length < 4}>{loading ? 'Проверяем…' : 'Подтвердить'}</button>
            </form>
            <button class="btn btn-ghost" style={{ marginTop: 10 }} onClick={resend} disabled={resendIn > 0}>
              {resendIn > 0 ? `Отправить ещё раз · ${resendIn}` : 'Код не пришёл? Отправить ещё раз'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Err({ text }: { text: string }) {
  return (
    <div style={{ background: 'var(--miss-dim)', color: 'var(--miss)', padding: '12px 14px',
      borderRadius: 'var(--r-sm)', marginBottom: 16, fontSize: 14, border: '1px solid rgba(255,91,123,0.3)' }}>
      {text}
    </div>
  );
}
