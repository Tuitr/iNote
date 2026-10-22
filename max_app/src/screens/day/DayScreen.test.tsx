import { MaxUI } from '@maxhub/max-ui';
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import meExample from '../../../../contracts/examples/j5/me.response.json';
import { createApiClient } from '../../api/client';
import type { Me } from '../../api/types';
import { server, TEST_BASE_URL } from '../../test/server';
import { TodayScreen } from './TodayScreen';

function renderToday() {
  const client = createApiClient({ baseUrl: TEST_BASE_URL, getInitData: () => 'user=dev' });
  return render(
    <MaxUI>
      <TodayScreen client={client} me={meExample as Me} />
    </MaxUI>,
  );
}

describe('TodayScreen', () => {
  it('открытые записи по порядку, выполненные отдельно, отменённых нет', async () => {
    renderToday();
    const items = await screen.findAllByTestId('item');
    const titles = items.map((item) => item.querySelector('.item-title')?.textContent);
    expect(titles).toEqual([
      'День рождения Маши',
      'Позвонить Ивану',
      'Встреча с дизайнером',
      'Купить продукты на неделю',
      'Отправить отчёт',
    ]);
    expect(screen.queryByText('Созвон с командой')).not.toBeInTheDocument();
    expect(screen.getByText('Выполнено · 1')).toBeInTheDocument();
    expect(screen.getByText('11:00–12:00 · Встреча · с Дизайнер')).toBeInTheDocument();
    expect(screen.getByText('Весь день · Встреча')).toBeInTheDocument();
  });

  it('пустой день', async () => {
    server.use(
      http.get('*/v1/items', () =>
        HttpResponse.json({
          range: { from: '2026-10-22', to: '2026-10-23', tz: 'Europe/Moscow' },
          version: 1,
          days: [{ date: '2026-10-22', label: 'Чт, 22 октября', items: [] }],
        }),
      ),
    );
    renderToday();
    expect(await screen.findByText('На сегодня ничего нет')).toBeInTheDocument();
    expect(screen.getByText('Чт, 22 октября')).toBeInTheDocument();
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
    renderToday();
    expect(await screen.findByRole('alert')).toHaveTextContent('Не получилось загрузить данные.');
    expect(screen.getByText('Код: abcdef')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});
