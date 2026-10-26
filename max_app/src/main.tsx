import '@maxhub/max-ui/dist/styles.css';
import './styles.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createApiClient } from './api/client';
import { App } from './App';
import { getInitData } from './max/bridge';

async function startMocks(): Promise<void> {
  if (import.meta.env.VITE_MOCKS !== 'true') return;
  const { worker } = await import('./mocks/browser');
  // Запросы не к /v1 (скрипт MAX, ресурсы Vite) идут в сеть как обычно.
  await worker.start({ onUnhandledFrame: 'bypass' });
}

const client = createApiClient({ baseUrl: import.meta.env.VITE_API_URL ?? '', getInitData });

void startMocks().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App client={client} />
    </StrictMode>,
  );
});
