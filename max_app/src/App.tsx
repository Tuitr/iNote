import { Button, MaxUI, Spinner, Typography } from '@maxhub/max-ui';
import { useCallback, useEffect, useState } from 'react';
import type { ApiClient } from './api/client';
import type { Me } from './api/types';
import { ErrorState } from './components/ErrorState';
import { getPlatform, getStartParam, isInsideMax } from './max/bridge';
import { parseStartParam, type Route } from './navigation/startParam';
import { TodayScreen } from './screens/today/TodayScreen';

type Session =
  | { status: 'loading' }
  | { status: 'ready'; me: Me; route: Route }
  | { status: 'error'; error: unknown };

export function App({ client }: { client: ApiClient }) {
  return (
    <MaxUI platform={getPlatform()} className="app">
      {!isInsideMax() && import.meta.env.PROD ? <OutsideMax /> : <Authorized client={client} />}
    </MaxUI>
  );
}

function Authorized({ client }: { client: ApiClient }) {
  const [session, setSession] = useState<Session>({ status: 'loading' });

  const login = useCallback(() => {
    setSession({ status: 'loading' });
    client
      .login()
      .then(({ me, start_param: startParam }) =>
        // start_param из ответа api проверен подписью; из initDataUnsafe — запасной вариант.
        setSession({ status: 'ready', me, route: parseStartParam(startParam ?? getStartParam()) }),
      )
      .catch((error: unknown) => setSession({ status: 'error', error }));
  }, [client]);

  useEffect(login, [login]);

  if (session.status === 'loading') {
    return (
      <div className="center center--screen" role="status" aria-label="Загрузка">
        <Spinner size={24} />
      </div>
    );
  }
  if (session.status === 'error') {
    return (
      <main className="screen">
        <ErrorState error={session.error} action={<Button onClick={login}>Повторить</Button>} />
      </main>
    );
  }
  // Пока готов только экран «Сегодня»: остальные маршруты J6 открывают его же (как неизвестный формат).
  return <TodayScreen client={client} me={session.me} />;
}

function OutsideMax() {
  return (
    <main className="screen">
      <div className="empty">
        <Typography.Title variant="medium-strong">Откройте iNote в MAX</Typography.Title>
        <Typography.Body variant="medium" className="muted">
          Мини-приложение работает внутри мессенджера: найдите бота iNote и нажмите «Открыть».
        </Typography.Body>
      </div>
    </main>
  );
}
