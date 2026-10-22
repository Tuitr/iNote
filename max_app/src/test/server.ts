import { setupServer } from 'msw/node';
import { handlers } from '../mocks/handlers';

/** Те же моки, что в npm run dev:mock. */
export const server = setupServer(...handlers);

export const TEST_BASE_URL = 'http://api.test';
