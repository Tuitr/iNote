// Типы J5 из contracts/openapi/api.json. Сам schema.d.ts не правим: npm run gen:api.
import type { components } from './schema';

type Schemas = components['schemas'];

export type Item = Schemas['Item'];
export type ItemKind = Schemas['ItemKind'];
export type ItemStatus = Schemas['ItemStatus'];
export type ItemsDay = Schemas['ItemsDay'];
export type ItemsByDayResponse = Schemas['ItemsByDayResponse'];
export type Me = Schemas['Me'];
export type AuthMaxResponse = Schemas['AuthMaxResponse'];
export type HttpErrorResponse = Schemas['HttpErrorResponse'];

/** Фильтр status в GET /v1/items: "all" включает и отменённые записи. */
export type ItemsStatusFilter = 'open' | 'done' | 'all';
