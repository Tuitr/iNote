import { Button, CellList, Spinner, Typography } from '@maxhub/max-ui';
import { useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../../api/client';
import type { Item, Me } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { addDays, dayLabel, relativeDayTitle, startOfWeek, weekdayLong } from '../../lib/time';
import { ItemCell } from './ItemCell';
import { useItemsRange } from './useItemsRange';
import { useToday } from './useToday';
import { WeekStrip } from './WeekStrip';

interface DayScreenProps {
  client: ApiClient;
  me: Me;
  /** День, с которого открыть экран (J6: day_…, week_…); по умолчанию — сегодня. */
  initialDate?: string;
}

export function DayScreen({ client, me, initialDate }: DayScreenProps) {
  const today = useToday(me.settings.tz);
  const [selected, setSelected] = useState(initialDate ?? today);

  // Кто смотрел «сегодня», после полуночи переезжает на новый день; выбранный вручную день остаётся.
  const previousToday = useRef(today);
  useEffect(() => {
    setSelected((current) => (current === previousToday.current ? today : current));
    previousToday.current = today;
  }, [today]);

  // Грузим всю неделю одним запросом: переход между днями недели мгновенный, точки в полоске — бесплатно.
  const weekStart = startOfWeek(selected);
  const { state, retry } = useItemsRange(client, weekStart, addDays(weekStart, 7));
  const days = state.status === 'ready' ? state.days : undefined;
  const day = days?.find((value) => value.date === selected);


  return (
    <main className="screen">
      <header className="screen-header">
        <div className="screen-header__row">
          <Typography.Headline variant="large-strong" asChild>
            <h1>{relativeDayTitle(selected, today) ?? weekdayLong(selected)}</h1>
          </Typography.Headline>
          {selected !== today && (
            <Button size="small" variant="secondary" onClick={() => setSelected(today)}>
              Сегодня
            </Button>
          )}
        </div>
        {/* Заголовок всегда в две строки: полоска недели не прыгает при смене дня. */}
        <Typography.Body variant="medium" className="muted">
          {dayLabel(selected)}
        </Typography.Body>
      </header>

      <WeekStrip
        weekStart={weekStart}
        selected={selected}
        today={today}
        days={days}
        onSelect={setSelected}
        onShiftWeek={(weeks) => setSelected((current) => addDays(current, weeks * 7))}
      />

      {state.status === 'loading' && (
        <div className="center" role="status" aria-label="Загрузка">
          <Spinner size={24} />
        </div>
      )}
      {state.status === 'error' && (
        <ErrorState error={state.error} action={<Button onClick={retry}>Повторить</Button>} />
      )}
      {state.status === 'ready' && <DayItems items={day?.items ?? []} isToday={selected === today} />}
    </main>
  );
}

function DayItems({ items, isToday }: { items: Item[]; isToday: boolean }) {
  // Порядок задаёт api: «весь день» первыми, дальше по времени. Отменённые не показываем.
  const open = items.filter((item) => item.status === 'open');
  const done = items.filter((item) => item.status === 'done');

  if (open.length === 0 && done.length === 0) {
    return (
      <div className="empty">
        <Typography.Title variant="medium-strong">
          {isToday ? 'На сегодня ничего нет' : 'На этот день ничего нет'}
        </Typography.Title>
        <Typography.Body variant="medium" className="muted">
          Скажите или напишите боту, например: «Напомни в 18:00 купить продукты»
        </Typography.Body>
      </div>
    );
  }

  return (
    <>
      {open.length > 0 ? (
        <CellList mode="island" filled>
          {open.map((item) => (
            <ItemCell key={item.id} item={item} />
          ))}
        </CellList>
      ) : (
        <Typography.Body variant="medium" className="muted all-done">
          {isToday ? 'Все дела на сегодня сделаны' : 'Все дела на этот день сделаны'}
        </Typography.Body>
      )}
      {done.length > 0 && (
        <CellList mode="island" filled header={`Выполнено · ${done.length}`}>
          {done.map((item) => (
            <ItemCell key={item.id} item={item} />
          ))}
        </CellList>
      )}
    </>
  );
}
