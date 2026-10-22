import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    // Моки берут тела ответов из ../contracts/examples/j5 — папка вне max_app.
    fs: { allow: ['..'] },
    // Локальный api (docker compose, порт 8000): CORS в api нет, поэтому ходим через прокси.
    proxy: { '/v1': 'http://localhost:8000' },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['src/test/setup.ts'],
  },
});
