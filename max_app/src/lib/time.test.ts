import { describe, expect, it } from 'vitest';
import type { Item } from '../api/types';
import {
  addDays,
  dayLabel,
  itemTimeLabel,
  relativeDayTitle,
  startOfWeek,
  todayIn,
  weekdayShort,
} from './time';

const base: Item = {
  id: '3c9e1a7b-2d4f-4b6a-8c1e-5f7a9b0d2e4c',
  kind: 'event',
  title: 'Встреча',
  notes: null,
  starts_at: null,
  ends_at: null,
  all_day: false,
  remind_at: null,
  status: 'open',
  rrule: null,
  participants: [],
  source_text: null,
  created_at: '2026-10-20T14:05:00+03:00',
  updated_at: '2026-10-20T14:05:00+03:00',
};

describe('todayIn', () => {
  it('считает дату в поясе пользователя, а не в UTC', () => {
    // 22:30 UTC — в Москве уже следующий день, в Нью-Йорке ещё тот же.
    const now = new Date('2026-10-22T22:30:00Z');
    expect(todayIn('Europe/Moscow', now)).toBe('2026-10-23');
    expect(todayIn('America/New_York', now)).toBe('2026-10-22');
  });
});

describe('addDays', () => {
  it('переходит через конец месяца и года', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });
});

describe('startOfWeek', () => {
  it('неделя начинается с понедельника', () => {
    expect(startOfWeek('2026-10-22')).toBe('2026-10-19'); // четверг
    expect(startOfWeek('2026-10-19')).toBe('2026-10-19'); // понедельник
    expect(startOfWeek('2026-10-25')).toBe('2026-10-19'); // воскресенье
    expect(startOfWeek('2026-11-01')).toBe('2026-10-26'); // через конец месяца
  });
});

describe('подписи дней', () => {
  it('как у api и для полоски', () => {
    expect(dayLabel('2026-10-22')).toBe('Чт, 22 октября');
    expect(weekdayShort('2026-10-25')).toBe('Вс');
  });

  it('сегодня, завтра, вчера', () => {
    expect(relativeDayTitle('2026-10-22', '2026-10-22')).toBe('Сегодня');
    expect(relativeDayTitle('2026-11-01', '2026-10-31')).toBe('Завтра');
    expect(relativeDayTitle('2026-10-21', '2026-10-22')).toBe('Вчера');
    expect(relativeDayTitle('2026-10-24', '2026-10-22')).toBeNull();
  });
});

describe('itemTimeLabel', () => {
  it('весь день', () => {
    const item = { ...base, all_day: true, starts_at: '2026-10-22T00:00:00+03:00' };
    expect(itemTimeLabel(item)).toBe('Весь день');
  });

  it('встреча с концом в тот же день', () => {
    const item = {
      ...base,
      starts_at: '2026-10-22T11:00:00+03:00',
      ends_at: '2026-10-22T12:30:00+03:00',
    };
    expect(itemTimeLabel(item)).toBe('11:00–12:30');
  });

  it('встреча до следующего дня — только начало', () => {
    const item = {
      ...base,
      starts_at: '2026-10-22T23:00:00+03:00',
      ends_at: '2026-10-23T01:00:00+03:00',
    };
    expect(itemTimeLabel(item)).toBe('23:00');
  });

  it('напоминание без срока — по remind_at, без пересчёта в пояс браузера', () => {
    const item = { ...base, kind: 'reminder' as const, remind_at: '2026-10-22T10:00:00+05:00' };
    expect(itemTimeLabel(item)).toBe('10:00');
  });
});
