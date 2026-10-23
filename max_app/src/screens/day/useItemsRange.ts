import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../../api/client';
import type { ItemsDay } from '../../api/types';

/** Опрос раз в 20 с: ETag/304 делает его дешёвым, а записи, созданные голосом, появляются сами (КиП, J5). */
export const POLL_INTERVAL_MS = 20_000;

export type RangeState =
  | { status: 'loading' }
  | { status: 'ready'; days: ItemsDay[]; from: string }
  | { status: 'error'; error: unknown };

/** Записи за дни [from, to), сгруппированные по дням, с фоновым опросом. */
export function useItemsRange(client: ApiClient, from: string, to: string) {
  const [state, setState] = useState<RangeState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const etag = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // ETag — версия данных пользователя, а не ответа: с другим периодом старый ETag дал бы 304
    // на чужие данные. Поэтому при смене периода начинаем без него.
    etag.current = null;
    setState({ status: 'loading' });

    async function load() {
      try {
        // status=all: выполненные показываем зачёркнутыми, отменённые экран отбрасывает сам.
        const result = await client.getItemsByDay({ from, to, status: 'all' }, etag.current);
        if (cancelled || result.notModified) return;
        etag.current = result.etag;
        setState({ status: 'ready', days: result.data.days, from });
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
  }, [client, from, to, attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  // Сразу после смены периода в состоянии ещё старые дни, пока эффект не сбросил его: не отдаём их.
  const current: RangeState = state.status === 'ready' && state.from !== from ? { status: 'loading' } : state;
  return { state: current, retry };
}
