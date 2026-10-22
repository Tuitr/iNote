// MSW-моки J5 по примерам из contracts/examples/j5 (КиП: «OpenAPI с примерами — из них E собирает MSW»).
import { http, HttpResponse } from 'msw';
import authExample from '../../../contracts/examples/j5/auth_max.response.json';
import meExample from '../../../contracts/examples/j5/me.response.json';
import type { ItemsByDayResponse } from '../api/types';
import { addDays } from '../lib/time';
import { dayLabel, MOCK_TZ, MOCK_VERSION, mockItems } from './data';

const MOCK_TOKEN = 'mock-token';

function error(status: number, code: string, message: string) {
  return HttpResponse.json(
    { error: { code, message, details: {}, trace_id: 'ab12f0c4d5e6f708192a3b4c5d6e7f80' } },
    { status },
  );
}

function authorized(request: Request): boolean {
  return request.headers.get('Authorization') === `Bearer ${MOCK_TOKEN}`;
}

export const handlers = [
  http.post('*/v1/auth/max', async ({ request }) => {
    const { init_data: initData } = (await request.json()) as { init_data: string };
    const params = new URLSearchParams(initData);
    return HttpResponse.json({
      ...authExample,
      token: MOCK_TOKEN,
      expires_at: new Date(Date.now() + 3600_000).toISOString(),
      start_param: params.get('start_param'),
    });
  }),

  http.get('*/v1/me', ({ request }) => {
    if (!authorized(request)) return error(401, 'unauthorized', 'Нужна авторизация');
    return HttpResponse.json(meExample);
  }),

  http.get('*/v1/items', ({ request }) => {
    if (!authorized(request)) return error(401, 'unauthorized', 'Нужна авторизация');
    const etag = `"${MOCK_VERSION}"`;
    if (request.headers.get('If-None-Match') === etag) {
      return new HttpResponse(null, { status: 304, headers: { ETag: etag } });
    }

    const url = new URL(request.url);
    const from = url.searchParams.get('from') ?? '';
    const to = url.searchParams.get('to') ?? '';
    const status = url.searchParams.get('status') ?? 'open';
    if (!from || !to || to <= from) {
      return error(400, 'validation_error', 'Конец периода должен быть позже начала');
    }

    const items = mockItems(from, to).filter(
      (item) => status === 'all' || item.status === status,
    );
    const days: ItemsByDayResponse['days'] = [];
    for (let day = from; day < to; day = addDays(day, 1)) {
      days.push({
        date: day,
        label: dayLabel(day),
        items: items.filter((item) => (item.starts_at ?? item.remind_at ?? '').startsWith(day)),
      });
    }
    const body: ItemsByDayResponse = {
      range: { from, to, tz: MOCK_TZ },
      version: MOCK_VERSION,
      days,
    };
    return HttpResponse.json(body, { headers: { ETag: etag } });
  }),
];
