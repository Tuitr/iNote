// J6: какой экран открыть по start_param. Формат — КиП и libs/contracts/.../start_param.py (ветка C).

export type Route =
  | { screen: 'today' }
  | { screen: 'day'; date: string }
  | { screen: 'week'; date: string }
  | { screen: 'item'; id: string }
  | { screen: 'dialog' };

const DATED = /^(day|week)_(\d{4}-\d{2}-\d{2})$/;
const ITEM = /^item_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;

/** Неизвестный или битый формат открывает «Сегодня» (КиП, J6). */
export function parseStartParam(value: string | null | undefined): Route {
  if (!value || value === 'today') return { screen: 'today' };
  if (value === 'dialog') return { screen: 'dialog' };

  const dated = DATED.exec(value);
  if (dated) {
    const [, kind, date] = dated as unknown as [string, 'day' | 'week', string];
    return isRealDate(date) ? { screen: kind, date } : { screen: 'today' };
  }

  const item = ITEM.exec(value);
  if (item?.[1]) return { screen: 'item', id: item[1] };

  return { screen: 'today' };
}

/** 2026-02-30 проходит регулярку, но такого дня нет. */
function isRealDate(value: string): boolean {
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(value);
}
