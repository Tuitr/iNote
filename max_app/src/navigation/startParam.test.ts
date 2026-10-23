import { describe, expect, it } from 'vitest';
import { parseStartParam } from './startParam';

describe('parseStartParam', () => {
  it.each([
    ['today', { screen: 'today' }],
    ['dialog', { screen: 'dialog' }],
    ['day_2026-10-18', { screen: 'day', date: '2026-10-18' }],
    ['week_2026-10-19', { screen: 'week', date: '2026-10-19' }],
    [
      'item_7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90',
      { screen: 'item', id: '7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90' },
    ],
  ])('%s', (value, route) => {
    expect(parseStartParam(value)).toEqual(route);
  });

  it.each([null, '', 'day_2026-02-30', 'day_tomorrow', 'item_123', 'week', 'TODAY'])(
    'неизвестный формат «%s» открывает «Сегодня»',
    (value) => {
      expect(parseStartParam(value)).toEqual({ screen: 'today' });
    },
  );
});
