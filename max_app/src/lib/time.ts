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

/** Понедельник недели, в которую входит дата. */
export function startOfWeek(date: string): string {
  // getUTCDay: 0 — воскресенье; сдвигаем, чтобы неделя начиналась с понедельника.
  const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
  return addDays(date, -((weekday + 6) % 7));
}

/** Разница в днях между датами YYYY-MM-DD: b − a. */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
}

function formatDate(date: string, options: Intl.DateTimeFormatOptions): string {
  // Дата без времени: форматируем в UTC, чтобы пояс браузера не сдвинул день.
  return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone: 'UTC' }).format(
    new Date(`${date}T00:00:00Z`),
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** «Чт, 22 октября» — как подпись дня у api. */
export function dayLabel(date: string): string {
  return capitalize(formatDate(date, { weekday: 'short', day: 'numeric', month: 'long' }));
}

/** «Пн» для полоски недели. */
export function weekdayShort(date: string): string {
  return capitalize(formatDate(date, { weekday: 'short' }));
}

/** «Четверг» — заголовок дня, у которого нет «Сегодня/Завтра/Вчера». */
export function weekdayLong(date: string): string {
  return capitalize(formatDate(date, { weekday: 'long' }));
}

/** «Четверг, 22 октября» — полное название дня для экранного диктора. */
export function dayLabelLong(date: string): string {
  return capitalize(formatDate(date, { weekday: 'long', day: 'numeric', month: 'long' }));
}

/** «Сегодня», «Завтра», «Вчера» или null для остальных дней. */
export function relativeDayTitle(date: string, today: string): string | null {
  switch (daysBetween(today, date)) {
    case 0:
      return 'Сегодня';
    case 1:
      return 'Завтра';
    case -1:
      return 'Вчера';
    default:
      return null;
  }
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

/** Первое число месяца, в который входит дата. */
export function startOfMonth(date: string): string {
  return `${date.slice(0, 7)}-01`;
}

/** Первое число месяца через n месяцев от месяца даты. */
export function addMonths(date: string, months: number): string {
  const value = new Date(`${startOfMonth(date)}T00:00:00Z`);
  value.setUTCMonth(value.getUTCMonth() + months);
  return value.toISOString().slice(0, 10);
}

/** «Октябрь» — название месяца в именительном падеже, для сетки месяцев. */
export function monthName(date: string): string {
  return capitalize(formatDate(date, { month: 'long' }));
}

/** «Октябрь 2026» — заголовок календаря месяца. */
export function monthLabel(date: string): string {
  // В ru-RU отдельный месяц приходит в именительном падеже: «октябрь 2026 г.».
  const text = formatDate(date, { month: 'long', year: 'numeric' }).replace(/\s*г\.$/, '');
  return capitalize(text);
}
