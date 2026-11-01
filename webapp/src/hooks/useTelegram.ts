import { useEffect, useState } from 'preact/hooks';

/** SDK Telegram грузится асинхронно: ждём его не дольше timeoutMs. Без SDK вернёт null. */
export function telegramReady(timeoutMs: number): Promise<any> {
  const w = window as any;
  if (w.Telegram?.WebApp) return Promise.resolve(w.Telegram.WebApp);
  return new Promise((resolve) => {
    const script = document.getElementById('tg-sdk');
    const done = () => {
      clearTimeout(timer);
      script?.removeEventListener('load', done);
      script?.removeEventListener('error', done);
      resolve(w.Telegram?.WebApp ?? null);
    };
    const timer = setTimeout(done, timeoutMs);
    script?.addEventListener('load', done);
    script?.addEventListener('error', done);
  });
}

// Пользователь из адреса Mini App: доступен сразу, ещё до загрузки SDK.
function userFromUrl() {
  try {
    const data = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('tgWebAppData');
    const user = data && new URLSearchParams(data).get('user');
    return user ? JSON.parse(user) : null;
  } catch {
    return null;
  }
}

export function useTelegram() {
  const [tg, setTg] = useState<any>(null);

  useEffect(() => {
    let active = true;
    telegramReady(30000).then((webApp) => {
      if (!active || !webApp) return;
      webApp.ready();
      webApp.expand();
      webApp.setHeaderColor('#090A12');
      webApp.setBackgroundColor('#090A12');
      setTg(webApp);
    });
    return () => { active = false; };
  }, []);

  return {
    webApp: tg,
    user: tg?.initDataUnsafe?.user ?? userFromUrl(),
    colorScheme: tg?.colorScheme ?? 'dark',
    themeParams: tg?.themeParams ?? {},
    haptic: tg?.HapticFeedback ?? null,
  };
}
