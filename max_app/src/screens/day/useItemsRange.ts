import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../../api/client';
import type { ItemsDay } from '../../api/types';
import { addDays, todayIn } from '../../lib/time';

/** Опрос раз в 20 с: ETag/304 делает его дешёвым, а записи, созданные голосом, появляются сами (КиП, J5). */
export const POLL_INTERVAL_MS = 20_000;

export type TodayState =
  | { status: 'loading' }
  | { status: 'ready'; day: ItemsDay }
  | { status: 'error'; error: unknown };

export function useTodayItems(client: ApiClient, tz: string) {
  const [today, setToday] = useState(() => todayIn(tz));
  const [state, setState] = useState<TodayState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const etag = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    etag.current = null;
    setState({ status: 'loading' });

    async function load() {
      // После полуночи «сегодня» — уже другой день: перезапускаем эффект с новой датой.
      const current = todayIn(tz);
      if (current !== today) {
        setToday(current);
        return;
      }
      try {
        // status=all: выполненные показываем зачёркнутыми, отменённые экран отбрасывает сам.
        const result = await client.getItemsByDay(
          { from: today, to: addDays(today, 1), status: 'all' },
          etag.current,
        );
        if (cancelled || result.notModified) return;
        etag.current = result.etag;
        const day = result.data.days[0] ?? { date: today, label: '', items: [] };
        setState({ status: 'ready', day });
      } catch (error) {
        if (cancelled) return;
        // Сбой фонового опроса не прячет уже показанные записи.
        setState((previous) => (previous.status === 'ready' ? previous : { status: 'error', error }));
      }
    }

    void load();
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, POLL_INTERVAL_MS);
    // Вернулись в приложение — обновляем сразу, не дожидаясь таймера.
    const onVisibilityChange = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [client, tz, today, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { today, state, retry };
}
