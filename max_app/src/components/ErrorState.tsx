import { Typography } from '@maxhub/max-ui';
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';

/** Текст ошибки для пользователя: тексты по коду пишет фронт, message api — для логов. */
export function errorText(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Нет связи с сервером. Проверьте интернет.';
  switch (error.code) {
    case 'unauthorized':
      return 'Не получилось войти. Откройте приложение заново из чата с ботом.';
    case 'rate_limited':
      return 'Слишком много запросов. Попробуйте через минуту.';
    default:
      return 'Не получилось загрузить данные.';
  }
}

export function ErrorState({ error, action }: { error: unknown; action?: ReactNode }) {
  const code = error instanceof ApiError ? error.shortTraceId : null;
  return (
    <div className="empty" role="alert">
      <Typography.Title variant="medium-strong">{errorText(error)}</Typography.Title>
      {code && (
        <Typography.Body variant="small" className="muted">
          Код: {code}
        </Typography.Body>
      )}
      {action}
    </div>
  );
}
