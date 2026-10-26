import { MaxUI } from '@maxhub/max-ui';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import meExample from '../../../../contracts/examples/j5/me.response.json';
import { createApiClient } from '../../api/client';
import type { Me } from '../../api/types';
import {
  addDays,
  dayLabel,
  dayLabelLong,
  monthLabel,
  startOfMonth,
  startOfWeek,
  todayIn,
  weekdayLong,
} from '../../lib/time';
import { MOCK_TZ } from '../../mocks/data';
import { server, TEST_BASE_URL } from '../../test/server';
import { DayScreen } from './DayScreen';

// Моки раскладывают записи относительно настоящего «сегодня» в поясе пользователя из примера.
const today = todayIn(MOCK_TZ);

function renderDay(initialDate?: string) {
  const client = createApiClient({ baseUrl: TEST_BASE_URL, getInitData: () => 'user=dev' });
  return render(
    <MaxUI>
      <DayScreen client={client} me={meExample as Me} initialDate={initialDate} />
    </MaxUI>,
  );
}

async function titles(): Promise<(string | null | undefined)[]> {
  const items = await screen.findAllByTestId('item');
  return items.map((item) => item.querySelector('.item-title')?.textContent);
}

describe('DayScreen', () => {
  it('сегодня: открытые записи по порядку, выполненные отдельно, отменённых нет', async () => {
    renderDay();
    expect(await titles()).toEqual([
      'День рождения Маши',
      'Позвонить Ивану',
      'Встреча с дизайнером',
      'Купить продукты на неделю',
      'Отправить отчёт',
    ]);
    expect(screen.getByRole('heading', { name: 'Сегодня' })).toBeInTheDocument();
    expect(screen.queryByText('Созвон с командой')).not.toBeInTheDocument();
    expect(screen.getByText('Выполнено · 1')).toBeInTheDocument();
    expect(screen.getByText('11:00–12:00 · Встреча · с Дизайнер')).toBeInTheDocument();
    // Кнопка «Сегодня» нужна только на других днях.
    expect(screen.queryByRole('button', { name: 'Сегодня' })).not.toBeInTheDocument();
  });

  it('полоска показывает неделю с понедельника, сегодня выбрано и отмечено точкой', async () => {
    renderDay();
    await screen.findAllByTestId('item');
    const days = screen.getAllByRole('button', { pressed: false }).concat(
      screen.getAllByRole('button', { pressed: true }),
    );
    expect(days).toHaveLength(7);
    const selected = screen.getByRole('button', { pressed: true });
    expect(selected).toHaveAccessibleName(dayLabelLong(today));
    expect(selected.querySelector('.week-strip__dot--on')).not.toBeNull();
    expect(screen.getByRole('button', { name: dayLabelLong(startOfWeek(today)) })).toBeInTheDocument();
  });

  it('другой день этой недели открывается из полоски без нового запроса', async () => {
    let requests = 0;
    server.events.on('request:start', ({ request }) => {
      if (new URL(request.url).pathname === '/v1/items') requests += 1;
    });
    renderDay();
    await screen.findAllByTestId('item');

    // Соседний день в той же неделе: вчера, а если сегодня понедельник — завтра.
    const isMonday = startOfWeek(today) === today;
    const other = addDays(today, isMonday ? 1 : -1);
    fireEvent.click(screen.getByRole('button', { name: dayLabelLong(other) }));

    expect(screen.getByRole('heading', { name: isMonday ? 'Завтра' : 'Вчера' })).toBeInTheDocument();
    expect(await titles()).toEqual(
      isMonday ? ['Тренировка', 'Оплатить интернет'] : ['Подготовить презентацию'],
    );
    expect(requests).toBe(1);
    server.events.removeAllListeners();
  });

  it('стрелка открывает следующую неделю: старый ETag не даёт 304 на другой период', async () => {
    renderDay();
    await screen.findAllByTestId('item');
    fireEvent.click(screen.getByRole('button', { name: 'Следующая неделя' }));

    const nextWeekDay = addDays(today, 7);
    expect(screen.getByRole('heading', { name: weekdayLong(nextWeekDay) })).toBeInTheDocument();
    expect(screen.getByText(dayLabel(nextWeekDay))).toBeInTheDocument();
    expect(await titles()).toEqual(['Планёрка']);
  });

  it('открывается с заданного дня, «Сегодня» возвращает назад', async () => {
    renderDay(addDays(today, 3));
    expect(await titles()).toEqual(['Стоматолог']);

    fireEvent.click(screen.getByRole('button', { name: 'Сегодня' }));
    expect(screen.getByRole('heading', { name: 'Сегодня' })).toBeInTheDocument();
    expect(await titles()).toContain('Позвонить Ивану');
  });

  it('выпадающий календарь: все дни месяца, любой день открывается со своими записями', async () => {
    renderDay();
    await screen.findAllByTestId('item');
    const toggle = screen.getByRole('button', { name: dayLabel(today) });
    fireEvent.click(toggle);

    let dialog = screen.getByRole('dialog', { name: 'Выбор дня' });
    expect(within(dialog).getByText(monthLabel(today))).toBeInTheDocument();
    const lastDay = Number(addDays(startOfMonth(addDays(startOfMonth(today), 31)), -1).slice(8, 10));
    expect(within(dialog).getAllByRole('button', { name: /,/ })).toHaveLength(lastDay);
    expect(within(dialog).getByRole('button', { pressed: true })).toHaveAccessibleName(dayLabelLong(today));

    // Через три дня — «Стоматолог»; если это уже следующий месяц, листаем.
    const target = addDays(today, 3);
    if (startOfMonth(target) !== startOfMonth(today)) {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Следующий месяц' }));
      dialog = screen.getByRole('dialog', { name: 'Выбор дня' });
    }
    fireEvent.click(within(dialog).getByRole('button', { name: dayLabelLong(target) }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: weekdayLong(target) })).toBeInTheDocument();
    expect(await titles()).toEqual(['Стоматолог']);
  });

  it('выпадающий календарь: переход к другому году и месяцу через сетки годов и месяцев', async () => {
    renderDay();
    await screen.findAllByTestId('item');
    fireEvent.click(screen.getByRole('button', { name: dayLabel(today) }));

    // Дни → месяцы текущего года.
    fireEvent.click(screen.getByRole('button', { name: `${monthLabel(today)}. Выбрать месяц и год` }));
    let dialog = screen.getByRole('dialog', { name: 'Выбор месяца' });
    const year = Number(today.slice(0, 4));
    expect(within(dialog).getAllByRole('button', { name: new RegExp(` ${year}$`) })).toHaveLength(12);
    expect(within(dialog).getByRole('button', { pressed: true })).toHaveAccessibleName(monthLabel(today));

    // Стрелка листает год, заголовок открывает сетку годов.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Следующий год' }));
    fireEvent.click(within(dialog).getByRole('button', { name: `${year + 1}. Выбрать год` }));
    dialog = screen.getByRole('dialog', { name: 'Выбор года' });
    expect(within(dialog).getByRole('button', { pressed: true })).toHaveAccessibleName(String(year));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Следующие годы' }));
    fireEvent.click(within(dialog).getByRole('button', { name: String(year - (year % 12) + 15) }));

    // Год → месяцы этого года → март → дни марта.
    const target = year - (year % 12) + 15;
    dialog = screen.getByRole('dialog', { name: 'Выбор месяца' });
    fireEvent.click(within(dialog).getByRole('button', { name: `Март ${target}` }));
    dialog = screen.getByRole('dialog', { name: 'Выбор дня' });
    expect(within(dialog).getByText(`Март ${target}`)).toBeInTheDocument();

    const date = `${target}-03-08`;
    fireEvent.click(within(dialog).getByRole('button', { name: dayLabelLong(date) }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: dayLabel(date) })).toBeInTheDocument();
  });

  it('выпадающий календарь закрывается по Escape', async () => {
    renderDay();
    await screen.findAllByTestId('item');
    fireEvent.click(screen.getByRole('button', { name: dayLabel(today) }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('пустой день', async () => {
    renderDay(addDays(today, 2));
    expect(await screen.findByText('На этот день ничего нет')).toBeInTheDocument();
  });

  it('пустое сегодня', async () => {
    server.use(
      http.get('*/v1/items', ({ request }) => {
        const url = new URL(request.url);
        const from = url.searchParams.get('from') ?? '';
        const days = Array.from({ length: 7 }, (_, index) => {
          const date = addDays(from, index);
          return { date, label: dayLabel(date), items: [] };
        });
        return HttpResponse.json({
          range: { from, to: addDays(from, 7), tz: MOCK_TZ },
          version: 1,
          days,
        });
      }),
    );
    renderDay();
    expect(await screen.findByText('На сегодня ничего нет')).toBeInTheDocument();
  });

  it('ошибка сервера — текст, код и кнопка «Повторить»', async () => {
    server.use(
      http.get('*/v1/items', () =>
        HttpResponse.json(
          {
            error: {
              code: 'internal',
              message: 'boom',
              details: {},
              trace_id: 'abcdef0123456789abcdef0123456789',
            },
          },
          { status: 500 },
        ),
      ),
    );
    renderDay();
    expect(await screen.findByRole('alert')).toHaveTextContent('Не получилось загрузить данные.');
    expect(screen.getByText('Код: abcdef')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});
