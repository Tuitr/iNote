import { useEffect, useState } from 'react';
import { todayIn } from '../../lib/time';

const CHECK_INTERVAL_MS = 60_000;

/** Сегодняшняя дата в поясе пользователя; после полуночи меняется сама. */
export function useToday(tz: string): string {
  const [today, setToday] = useState(() => todayIn(tz));

  useEffect(() => {
    setToday(todayIn(tz));
    const timer = window.setInterval(() => setToday(todayIn(tz)), CHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [tz]);

  return today;
}
