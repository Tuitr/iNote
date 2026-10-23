// Данные моков. Форма записей — из примеров контракта (contracts/examples/j5), даты сдвинуты
// на «сегодня», чтобы экран «Сегодня» в моках не был пустым.
import itemExample from '../../../contracts/examples/j5/item.response.json';
import meExample from '../../../contracts/examples/j5/me.response.json';
import type { Item, ItemKind, ItemStatus } from '../api/types';
import { addDays, todayIn } from '../lib/time';

export const MOCK_TZ = meExample.settings.tz;
// Смещение пояса из примера: Europe/Moscow круглый год +03:00.
const OFFSET = '+03:00';

/** Версия данных моков: ETag ответа /v1/items. */
export const MOCK_VERSION = 42;

interface Draft {
  id: string;
  kind: ItemKind;
  title: string;
  dayOffset: number;
  start?: string;
  end?: string;
  remind?: string;
  allDay?: boolean;
  status?: ItemStatus;
  participants?: string[];
  sourceText?: string;
}

const DRAFTS: Draft[] = [
  {
    id: '0b6f3a1e-7c2d-4e5f-8a9b-1c2d3e4f5a6b',
    kind: 'event',
    title: 'День рождения Маши',
    dayOffset: 0,
    allDay: true,
    sourceText: 'Сегодня весь день — день рождения Маши',
  },
  {
    id: '5d8e2f4a-1b3c-4d6e-9f0a-2b4c6d8e0f1a',
    kind: 'task',
    title: 'Отправить отчёт',
    dayOffset: 0,
    start: '09:00',
    status: 'done',
  },
  {
    id: '3c9e1a7b-2d4f-4b6a-8c1e-5f7a9b0d2e4c',
    kind: 'reminder',
    title: 'Позвонить Ивану',
    dayOffset: 0,
    remind: '10:00',
    sourceText: 'Напомни сегодня в 10 позвонить Ивану',
  },
  {
    id: itemExample.id,
    kind: 'event',
    title: itemExample.title,
    dayOffset: 0,
    start: '11:00',
    end: '12:00',
    remind: '10:45',
    participants: itemExample.participants,
    sourceText: itemExample.source_text,
  },
  {
    id: '9a1b2c3d-4e5f-4a6b-8c7d-0e1f2a3b4c5d',
    kind: 'task',
    title: 'Купить продукты на неделю',
    dayOffset: 0,
    start: '18:30',
  },
  {
    id: 'e2f3a4b5-c6d7-4e8f-9a0b-1c2d3e4f5a6b',
    kind: 'event',
    title: 'Созвон с командой',
    dayOffset: 0,
    start: '15:00',
    status: 'cancelled',
  },
  {
    id: '7e8f9a0b-1c2d-4e3f-8a4b-5c6d7e8f9a0b',
    kind: 'event',
    title: 'Тренировка',
    dayOffset: 1,
    start: '08:00',
    end: '09:30',
  },
  {
    id: '1f2e3d4c-5b6a-4978-8a6b-5c4d3e2f1a0b',
    kind: 'reminder',
    title: 'Оплатить интернет',
    dayOffset: 1,
    remind: '12:00',
  },
  {
    id: '2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d',
    kind: 'event',
    title: 'Стоматолог',
    dayOffset: 3,
    start: '16:00',
    end: '17:00',
    participants: ['Доктор Петрова'],
  },
  {
    id: '4b5c6d7e-8f9a-4b0c-8d1e-2f3a4b5c6d7e',
    kind: 'task',
    title: 'Подготовить презентацию',
    dayOffset: -1,
    start: '14:00',
    status: 'done',
  },
  {
    id: '6c7d8e9f-0a1b-4c2d-9e3f-4a5b6c7d8e9f',
    kind: 'event',
    title: 'Планёрка',
    dayOffset: 7,
    start: '10:00',
    end: '10:30',
  },
];

/** Записи за дни [from, to) — так же, как их отдаёт api. */
export function mockItems(from: string, to: string, now: Date = new Date()): Item[] {
  const today = todayIn(MOCK_TZ, now);
  return DRAFTS.map((draft) => toItem(draft, addDays(today, draft.dayOffset), now)).filter(
    (item) => {
      const day = (item.starts_at ?? item.remind_at ?? '').slice(0, 10);
      return day >= from && day < to;
    },
  );
}

function toItem(draft: Draft, date: string, now: Date): Item {
  const at = (time: string | undefined) => (time ? `${date}T${time}:00${OFFSET}` : null);
  const createdAt = now.toISOString().replace(/\.\d+Z$/, 'Z');
  return {
    ...itemExample,
    id: draft.id,
    kind: draft.kind,
    title: draft.title,
    all_day: draft.allDay ?? false,
    // «Весь день» хранится как полночь (ItemRepo.list_in_range).
    starts_at: draft.allDay ? at('00:00') : at(draft.start),
    ends_at: at(draft.end),
    remind_at: at(draft.remind),
    status: draft.status ?? 'open',
    participants: draft.participants ?? [],
    source_text: draft.sourceText ?? null,
    created_at: createdAt,
    updated_at: createdAt,
  };
}
