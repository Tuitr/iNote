import type {
  AuthMaxResponse,
  HttpErrorResponse,
  ItemsByDayResponse,
  ItemsStatusFilter,
  Me,
} from './types';

/** Ошибка API в общем конверте {error: {code, message, details, trace_id}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** Пользователю показываем первые 6 символов trace_id. */
  get shortTraceId(): string | null {
    return this.traceId ? this.traceId.slice(0, 6) : null;
  }
}

/** Ответ на запрос с If-None-Match: либо данные с новым ETag, либо «не изменилось» (304). */
export type Conditional<T> = { notModified: true } | { notModified: false; data: T; etag: string | null };

export interface ApiClient {
  login(): Promise<AuthMaxResponse>;
  getMe(): Promise<Me>;
  getItemsByDay(
    range: { from: string; to: string; status?: ItemsStatusFilter },
    etag?: string | null,
  ): Promise<Conditional<ItemsByDayResponse>>;
}

export interface ApiClientOptions {
  baseUrl?: string;
  /** Строка WebApp.initData как есть: пересборка ломает подпись. */
  getInitData: () => string;
}

export function createApiClient({ baseUrl = '', getInitData }: ApiClientOptions): ApiClient {
  let token: string | null = null;
  // Один вход на всех: параллельные запросы с 401 не должны логиниться каждый сам.
  let pendingLogin: Promise<AuthMaxResponse> | null = null;

  function login(): Promise<AuthMaxResponse> {
    pendingLogin ??= (async () => {
      try {
        const response = await fetch(`${baseUrl}/v1/auth/max`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ init_data: getInitData() }),
        });
        if (!response.ok) throw await toApiError(response);
        const body = (await response.json()) as AuthMaxResponse;
        token = body.token;
        return body;
      } finally {
        pendingLogin = null;
      }
    })();
    return pendingLogin;
  }

  async function request(path: string, headers: Record<string, string> = {}): Promise<Response> {
    if (token === null) await login();
    let response = await send(path, headers);
    // Токен живёт час: на 401 молча входим заново с тем же initData и повторяем один раз (J5).
    if (response.status === 401) {
      await login();
      response = await send(path, headers);
    }
    if (!response.ok && response.status !== 304) throw await toApiError(response);
    return response;
  }

  function send(path: string, headers: Record<string, string>): Promise<Response> {
    return fetch(`${baseUrl}${path}`, {
      headers: { ...headers, Authorization: `Bearer ${token}` },
    });
  }

  return {
    login,

    async getMe() {
      const response = await request('/v1/me');
      return (await response.json()) as Me;
    },

    async getItemsByDay({ from, to, status = 'open' }, etag) {
      const query = new URLSearchParams({ from, to, status, group: 'day' });
      const response = await request(
        `/v1/items?${query}`,
        etag ? { 'If-None-Match': etag } : {},
      );
      if (response.status === 304) return { notModified: true };
      const data = (await response.json()) as ItemsByDayResponse;
      return { notModified: false, data, etag: response.headers.get('ETag') };
    },
  };
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const { error } = (await response.json()) as HttpErrorResponse;
    return new ApiError(response.status, error.code, error.message, error.trace_id);
  } catch {
    // Ответ не в конверте (прокси, обрыв): код по статусу, как в стандартах C.
    const code = response.status >= 500 ? 'upstream_unavailable' : 'internal';
    return new ApiError(response.status, code, `Ошибка сервера (${response.status})`, null);
  }
}
