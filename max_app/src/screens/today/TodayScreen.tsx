import { Button, CellList, Spinner, Typography } from '@maxhub/max-ui';
import type { ApiClient } from '../../api/client';
import type { Item, Me } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { ItemCell } from './ItemCell';
import { useTodayItems } from './useTodayItems';

interface TodayScreenProps {
  client: ApiClient;
  me: Me;
}

export function TodayScreen({ client, me }: TodayScreenProps) {
  const { state, retry } = useTodayItems(client, me.settings.tz);

  return (
    <main className="screen">
      <header className="screen-header">
        <Typography.Headline variant="large-strong" asChild>
          <h1>Сегодня</h1>
        </Typography.Headline>
        {state.status === 'ready' && state.day.label && (
          <Typography.Body variant="medium" className="muted">
            {state.day.label}
          </Typography.Body>
        )}
      </header>

      {state.status === 'loading' && (
        <div className="center" role="status" aria-label="Загрузка">
          <Spinner size={24} />
        </div>
      )}
      {state.status === 'error' && (
        <ErrorState error={state.error} action={<Button onClick={retry}>Повторить</Button>} />
      )}
      {state.status === 'ready' && <DayItems items={state.day.items} />}
    </main>
  );
}

function DayItems({ items }: { items: Item[] }) {
  // Порядок задаёт api: «весь день» первыми, дальше по времени. Отменённые не показываем.
  const open = items.filter((item) => item.status === 'open');
  const done = items.filter((item) => item.status === 'done');

  if (open.length === 0 && done.length === 0) {
    return (
      <div className="empty">
        <Typography.Title variant="medium-strong">На сегодня ничего нет</Typography.Title>
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
          Все дела на сегодня сделаны
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
