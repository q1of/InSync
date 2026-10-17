import { useEffect, useState } from 'preact/hooks';

export function useTelegram() {
  const [tg, setTg] = useState<any>(null);

  useEffect(() => {
    const w = window as any;
    if (w?.Telegram?.WebApp) {
      const webApp = w.Telegram.WebApp;
      webApp.ready();
      webApp.expand();
      webApp.setHeaderColor('#090A12');
      webApp.setBackgroundColor('#090A12');
      setTg(webApp);
    }
  }, []);

  return {
    webApp: tg,
    user: tg?.initDataUnsafe?.user ?? null,
    colorScheme: tg?.colorScheme ?? 'dark',
    themeParams: tg?.themeParams ?? {},
    haptic: tg?.HapticFeedback ?? null,
  };
}
