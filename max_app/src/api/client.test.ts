import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { server, TEST_BASE_URL } from '../test/server';
import { ApiError, createApiClient } from './client';

const client = () => createApiClient({ baseUrl: TEST_BASE_URL, getInitData: () => 'user=dev' });

function unauthorized() {
  return HttpResponse.json(
    {
      error: {
        code: 'unauthorized',
        message: 'Токен истёк',
        details: {},
        trace_id: 'a'.repeat(32),
      },
    },
    { status: 401 },
  );
}

describe('createApiClient', () => {
  it('входит сам перед первым запросом', async () => {
    const me = await client().getMe();
    expect(me.name).toBe('Степан');
  });

  it('на 401 входит заново и повторяет запрос один раз', async () => {
    let logins = 0;
    let meCalls = 0;
    server.use(
      http.post('*/v1/auth/max', () => {
        logins += 1;
        return HttpResponse.json({ token: `t${logins}`, expires_at: '', me: {}, start_param: null });
      }),
      http.get('*/v1/me', ({ request }) => {
        meCalls += 1;
        if (request.headers.get('Authorization') !== 'Bearer t2') return unauthorized();
        return HttpResponse.json({ name: 'Степан', settings: { tz: 'Europe/Moscow' } });
      }),
    );
    await expect(client().getMe()).resolves.toMatchObject({ name: 'Степан' });
    expect(logins).toBe(2);
    expect(meCalls).toBe(2);
  });

  it('параллельные запросы входят один раз', async () => {
    let logins = 0;
    server.use(
      http.post('*/v1/auth/max', () => {
        logins += 1;
        return HttpResponse.json({ token: 'mock-token', expires_at: '', me: {}, start_param: null });
      }),
    );
    const api = client();
    await Promise.all([api.getMe(), api.getMe(), api.getMe()]);
    expect(logins).toBe(1);
  });

  it('с тем же ETag получает 304 без тела', async () => {
    const api = client();
    const range = { from: '2026-10-22', to: '2026-10-23' };
    const first = await api.getItemsByDay(range);
    if (first.notModified) throw new Error('первый ответ должен быть с данными');
    expect(first.etag).toBe('"42"');
    await expect(api.getItemsByDay(range, first.etag)).resolves.toEqual({ notModified: true });
  });

  it('ошибку в конверте превращает в ApiError с коротким trace_id', async () => {
    const error = await client()
      .getItemsByDay({ from: '2026-10-23', to: '2026-10-22' })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 400, code: 'validation_error', shortTraceId: 'ab12f0' });
  });

  it('ответ не в конверте — ApiError по статусу', async () => {
    server.use(http.get('*/v1/me', () => new HttpResponse('Bad Gateway', { status: 502 })));
    await expect(client().getMe()).rejects.toMatchObject({
      status: 502,
      code: 'upstream_unavailable',
    });
  });
});
