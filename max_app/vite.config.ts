import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    // Моки берут тела ответов из ../contracts/examples/j5 — папка вне max_app.
    fs: { allow: ['..'] },
    // Локальный api (docker compose, порт 8000): CORS в api нет, поэтому ходим через прокси.
    // 127.0.0.1, а не localhost: Node может выбрать ::1, а порт Docker на Windows слушает IPv4.
    proxy: { '/v1': 'http://127.0.0.1:8000' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
  },
});
