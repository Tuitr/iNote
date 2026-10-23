/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" — запросы /v1 перехватывает MSW (npm run dev:mock). */
  readonly VITE_MOCKS?: string;
  /** Адрес api, если он на другом домене. По умолчанию тот же origin (прокси Vite или Caddy). */
  readonly VITE_API_URL?: string;
}
