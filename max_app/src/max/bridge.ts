// MAX Bridge (https://dev.max.ru/docs/webapps/bridge): скрипт из index.html создаёт window.WebApp.
// Описаны только поля, которыми пользуемся.

export interface MaxWebApp {
  initData: string;
  initDataUnsafe: {
    start_param?: string;
    user?: { id: number; first_name?: string; last_name?: string };
  };
  platform?: 'ios' | 'android' | 'desktop' | 'web';
}

declare global {
  interface Window {
    WebApp?: MaxWebApp;
  }
}

export function getWebApp(): MaxWebApp | null {
  return window.WebApp ?? null;
}

/** Открыто ли приложение внутри MAX: в обычном браузере initData пустой. */
export function isInsideMax(): boolean {
  return Boolean(getWebApp()?.initData);
}

/**
 * initData для POST /v1/auth/max. Вне MAX в dev-сборке — фейковая строка без подписи:
 * её примут только моки и api с API_DEV_AUTH=true.
 */
export function getInitData(): string {
  const initData = getWebApp()?.initData;
  if (initData) return initData;
  if (import.meta.env.DEV) return devInitData();
  return '';
}

/**
 * start_param до входа: из MAX, а вне MAX — из адреса (?startapp=today), чтобы проверять маршруты
 * в браузере. После входа берём проверенный start_param из ответа /v1/auth/max.
 */
export function getStartParam(): string | null {
  const fromMax = getWebApp()?.initDataUnsafe.start_param;
  if (fromMax) return fromMax;
  return new URLSearchParams(window.location.search).get('startapp');
}

/** Платформа для MaxUI: у него только ios и android, остальное он определит сам. */
export function getPlatform(): 'ios' | 'android' | undefined {
  const platform = getWebApp()?.platform;
  return platform === 'ios' || platform === 'android' ? platform : undefined;
}

function devInitData(): string {
  const params = new URLSearchParams({
    user: JSON.stringify({ id: 123456789, first_name: 'Разработчик' }),
    auth_date: String(Math.floor(Date.now() / 1000)),
    hash: 'dev',
  });
  const startParam = getStartParam();
  if (startParam) params.set('start_param', startParam);
  return params.toString();
}
