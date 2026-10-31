import type { CapacitorConfig } from '@capacitor/cli';

// Android-оболочка InSync. UI тот же, что в Mini App (собранный Vite → dist),
// запросы идут на бэкенд по абсолютному адресу (VITE_API_BASE на сборке).
const config: CapacitorConfig = {
  appId: 'ru.insync.app',
  appName: 'InSync',
  webDir: 'dist',
  android: {
    backgroundColor: '#090A12',
  },
};

export default config;
