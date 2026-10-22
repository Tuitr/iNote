import type { Item } from '../api/types';

/** Сегодняшняя дата YYYY-MM-DD в поясе пользователя, а не браузера (у них может быть разный пояс). */
export function todayIn(tz: string, now: Date = new Date()): string {
  // en-CA форматирует дату как YYYY-MM-DD.
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(now);
}

/** Дата YYYY-MM-DD плюс n дней. */
export function addDays(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

/**
 * «11:00» из ISO-строки API. API отдаёт время уже в поясе пользователя со смещением
 * (2026-10-19T11:00:00+03:00), поэтому берём часы и минуты как есть, без пересчёта в пояс браузера.
 */
export function clockTime(iso: string): string {
  return iso.slice(11, 16);
}

/** Подпись времени записи в списке дня. */
export function itemTimeLabel(item: Item): string {
  if (item.all_day) return 'Весь день';
  if (item.starts_at) {
    const start = clockTime(item.starts_at);
    // Конец показываем, только если встреча кончается в тот же день.
    if (item.ends_at && item.ends_at.slice(0, 10) === item.starts_at.slice(0, 10)) {
      return `${start}–${clockTime(item.ends_at)}`;
    }
    return start;
  }
  // Напоминание без срока попадает в день по remind_at (ItemRepo.list_in_range).
  if (item.remind_at) return clockTime(item.remind_at);
  return '';
}
