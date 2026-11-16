import { getScheduleApi, getPassDayApi, Lesson, PassDay } from './client';

// Подгрузка заранее: при открытии приложения сразу просим расписание и пропуск,
// а вкладка потом берёт уже готовый (или летящий) ответ. Только в памяти и ненадолго —
// при следующем открытии всё грузится заново, устаревшее не показываем.
const FRESH_MS = 30_000;
const store = new Map<string, { at: number; p: Promise<any> }>();

function fresh<T>(key: string, load: () => Promise<T>): Promise<T> {
  const hit = store.get(key);
  if (hit && Date.now() - hit.at < FRESH_MS) return hit.p;
  const p = load();
  store.set(key, { at: Date.now(), p });
  p.catch(() => { if (store.get(key)?.p === p) store.delete(key); });
  return p;
}

const MSK_MS = 3 * 3600 * 1000;
export const todayMsk = () => new Date(Date.now() + MSK_MS).toISOString().slice(0, 10);

export const schedule = (): Promise<{ lessons: Lesson[] }> => fresh('schedule', () => getScheduleApi(7));
export const passDay = (day: string): Promise<PassDay> => fresh(`pass:${day}`, () => getPassDayApi(day));

export function prefetchAll() {
  schedule().catch(() => {});
  passDay(todayMsk()).catch(() => {});
}
