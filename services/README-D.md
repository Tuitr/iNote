# Бэкенд роли D: данные, MCP, API, Scheduler

Всё, что ассистент сохраняет, находит и о чём напоминает. Один Docker-образ, четыре процесса:

| Контейнер | Команда | Что делает |
| --- | --- | --- |
| `migrate` | `alembic upgrade head` | Накатывает схему и выходит; остальные ждут его |
| `mcp` | `uvicorn services.mcp.app:app` | 8 инструментов MCP для агента C (J3) и служебный REST `/internal/v1` (J8) |
| `api` | `uvicorn services.api.app:app` (127.0.0.1:`API_DEV_PORT`, по умолчанию 8000) | REST `/v1` для мини-приложения E (J5), только чтение |
| `scheduler` | `python -m services.scheduler.main` | Напоминания в бот C (J4), сдвиг повторяющихся записей |

Все четыре — в общем стенде `deploy/docker-compose.yml` рядом с ботом и агентом C (образ —
`Dockerfile` в корне, зависимости по `uv.lock`).

Логика — в `libs/core` (сервисы → репозитории), контейнеры — тонкие адаптеры. Модели контрактов —
`libs/contracts`, снапшоты и примеры — `contracts/`. Планы запросов —
`docs/d-explain.md`.

## Как запустить

```bash
cp .env.example .env    # заполнить секреты; учётка POSTGRES_* и DATABASE_URL должны совпадать
make up                 # стенд: postgres, redis, migrate (схема с нуля), mcp, api, scheduler, bot, agent
make seed MAX_USER_ID=<id в MAX>   # демо-данные D
make e2e                # сценарии ТЗ через фейковый MAX на настоящем D
```

Без `make` (Windows) — те же команды напрямую:
`docker compose -f deploy/docker-compose.yml --env-file .env --profile mock up -d --build --wait` и
`docker compose -f deploy/docker-compose.yml --env-file .env --profile mock run --rm mcp python -m services.seed --max-user-id <id>`.

- База с нуля: `make down`, удалить том (`docker volume rm va_pgdata`), `make up` — `migrate` строит
  схему одной командой.
- Сид — только вручную: 18 записей за месяц назад и неделю вперёд (встречи, задачи, выполненные,
  удалённая, планёрка по понедельникам). Повторный запуск ничего не дублирует.
- Готовность: `make ps` — все сервисы `healthy` (у `scheduler` проверки нет, `migrate` выходит с кодом 0);
  `curl localhost:8000/health/ready` → `{"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}`.
- Тесты (нужен Docker: testcontainers поднимает `postgres:16` и `redis:7-alpine`):

  ```bash
  uv sync --all-packages --frozen
  uv run ruff check . && uv run ruff format --check .
  uv run pytest libs/core libs/contracts services/tests --cov=libs/core --cov-fail-under=80
  ```

  То же делает job `backend-d` в `.github/workflows/ci.yml` на каждый PR.
- Выгрузить контракты после изменения моделей: `python -m services.api.export_openapi`,
  `python -m services.mcp.export_tools` (иначе снапшот-тесты красные).

## Переменные окружения

Полный список с комментариями — `.env.example`.

| Переменная | Кто читает | Зачем |
| --- | --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | контейнер postgres | Учётка базы |
| `DATABASE_URL` | все | PostgreSQL; схема `postgresql://`, драйвер asyncpg подставляется сам |
| `REDIS_URL` | mcp, api, scheduler | Версия данных пользователя и кеш списков |
| `LOG_LEVEL` | все | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DEFAULT_TZ` | mcp, api | Пояс нового пользователя (IANA) |
| `SERVICE_TOKEN` | mcp (проверяет), scheduler (шлёт боту) | `Authorization: Bearer` во внутренних вызовах |
| `API_MAX_BOT_TOKEN` | api | Проверка подписи `initData` |
| `API_JWT_SECRET` | api | Подпись JWT, не короче 32 символов |
| `API_DEV_AUTH` | api | `true` — `initData` без подписи, только на машине разработчика |
| `SCHED_BOT_URL` | scheduler | Адрес бота C, например `http://bot:8000` |
| `SCHED_INTERVAL_SECONDS` | scheduler | Пауза между проходами, по умолчанию 15 |

## Схема БД

PostgreSQL 16, расширение `pg_trgm`. Время — `timestamptz` в UTC, наружу — в поясе пользователя.
Миграции Alembic пишутся руками (`migrations/versions`), модели SQLAlchemy сверяются с ними
`alembic check` в тестах. Эмбеддингов нет: поиск по смыслу — через подсказки `search_hints`.

| Таблица | Ключевые поля | Индексы и ограничения |
| --- | --- | --- |
| `users` | `id`, `max_user_id` (unique), `max_chat_id`, `name`, `tz`, `voice_replies`, `default_event_min` (5–480), `remind_before_min` (0–1440) | `uq_users_max_user_id` |
| `items` | `id` uuid, `user_id`, `kind` (task/reminder/event), `title` (1–200), `notes`, `starts_at`, `ends_at` (> starts_at), `all_day`, `remind_at`, `status` (open/done/cancelled), `rrule`, `participants[]`, `source_text`, `search_hints`, `idempotency_key`, `created_at`, `updated_at`, `deleted_at`; вычисляемые `search_tsv`, `search_text` | `ix_items_user_id_starts_at (user_id, starts_at) WHERE deleted_at IS NULL`; `uq_items_user_id_idempotency_key`; GIN `ix_items_search_tsv`; GIN trgm `ix_items_search_text_trgm`; `ix_items_open_series WHERE rrule IS NOT NULL AND deleted_at IS NULL AND status = 'open'` |
| `notifications` | `id`, `user_id`, `item_id`, `fire_at`, `status` (pending/sending/sent/failed/cancelled), `attempt`, `lease_until`, `next_try_at`, `last_error`, `sent_at` | `uq_notifications_item_id_fire_at`; `ix_notifications_fire_at WHERE status = 'pending'`; `ix_notifications_lease_until WHERE status = 'sending'` |
| `dialog_log` | `id`, `user_id`, `trace_id`, `turn_no`, `role`, `text`, `input_type`, `item_ids[]`, `latency_ms`, `created_at` | `uq_dialog_log_trace_id_turn_no`; `ix_dialog_log_user_id_id` |

Правила, которые держит код: каждый метод репозитория берёт `user_id` первым (исключение —
выборки Scheduler по всем пользователям); `deleted_at IS NULL` во всех выборках; удаление мягкое;
версия данных `va:v1:mcp:ver:{user_id}` растёт в Redis только после коммита.

## Инструменты MCP (J3)

`POST http://mcp:8000/mcp`, Streamable HTTP, `Authorization: Bearer <SERVICE_TOKEN>`. Схемы — из
`libs/contracts/tools.py`, снапшот — `contracts/mcp/tools.json`, примеры — `contracts/examples/j3`.
Описания инструментов для модели пишет B.

Во всех аргументах есть `caller` (`user_id`, `tz`, `trace_id`, `idempotency_key`) — его заполняет
агент, модель его не видит. Ответ: `content[0].text` — короткая строка для модели
(`Создано: [3c9e1a7b] чт, 22 октября 10:00 Позвонить Ивану`), `structuredContent` — данные по схеме.

| Инструмент | Аргументы (? — необязательный) | Ответ |
| --- | --- | --- |
| `create_task` | `title`, `kind?` (task/reminder), `starts_at?` (дата = весь день), `remind_at?`, `notes?`, `rrule?`, `source_text?`, `search_hints?` | `status: created`, `item` |
| `create_event` | `title`, `starts_at`, `ends_at?` (по умолчанию из настроек), `participants?`, `remind_before_min?`, `notes?`, `rrule?`, `source_text?`, `search_hints?`, `allow_conflict?` | `created` или `conflict` + `conflicts` + `free_slots` |
| `list_items` | `from`, `to` (даты, `to` не включается), `kind?`, `status?` (open/done/all) | `items` (до 30), `range`, `truncated` |
| `search_items` | `query`, `kind?`, `from?`, `to?`, `include_done?`, `limit?` | `results` (`item`, `score` 0–1), `best_match_confident` |
| `update_item` | `id` + любые поля; `null` очищает, непереданные не меняются; `allow_conflict?` | `updated` или `conflict` |
| `complete_item` | `id`, `done?` (false — снова в работу) | `completed` / `updated` |
| `delete_item` | `id`, `restore?` | `deleted` / `updated` |
| `check_conflicts` | `starts_at`, `ends_at`, `exclude_id?` | `conflicts`, `free_slots` |

- **id** — полный UUID или первые 8 символов. Если 8 символов подходят к нескольким записям —
  `validation_error` с `details.candidates` (до 5 `ItemBrief`) для кнопок выбора.
- **Ошибки** — `isError: true`, `structuredContent = {"error": {"code", "message", "details"}}`,
  текст `Ошибка past_time: время уже прошло`. Коды: `validation_error` (`details.missing` /
  `details.invalid` — имена полей), `past_time` (время раньше «сейчас» больше чем на минуту),
  `not_found` (в том числе чужая запись), `internal` (неожиданный сбой, подробности только в логе).
- **Конфликт — не ошибка**: встреча не создаётся, приходят пересечения и до трёх свободных окон той же
  длины в тот же день с 8:00 до 22:00. Встречи на весь день и задачи не конфликтуют.
- **Повторы**: тот же `caller.idempotency_key` возвращает уже созданную запись. Изменения
  идемпотентны по смыслу: повтор с теми же значениями ничего не меняет.
- **Поиск**: полнотекст «любое из слов» по названию, заметкам, участникам, исходной фразе и
  `search_hints` + триграммы, слияние по месту (RRF). `best_match_confident` — можно брать первый
  результат без уточнения.

## Служебный REST (J8)

`/internal/v1` на контейнере mcp, `Authorization: Bearer <SERVICE_TOKEN>` (без токена — 401,
с неверным — 403). Примеры — `contracts/examples/j8`.

| Метод | Тело → ответ |
| --- | --- |
| `POST /internal/v1/users/resolve` | `{max_user_id, max_chat_id?, name?}` → `{user_id, tz, settings, created}`; у известного пользователя обновляет пришедшие `max_chat_id` и `name` |
| `PATCH /internal/v1/users/{user_id}/settings` | любое подмножество `{tz, voice_replies, default_event_min, remind_before_min}` → `settings`; неизвестный пользователь — 404 |
| `POST /internal/v1/dialog-turns` | `{user_id, trace_id, turns[], latency_ms}` → 202; повтор с тем же `trace_id` не создаёт дубль |

## API мини-приложения (J5)

Только чтение (решение 2026-10-22): кроме входа — все методы `GET`, запись даёт 405.
OpenAPI с примерами — `contracts/openapi/api.json`; по нему E собирает моки.

- **Вход**: `POST /v1/auth/max {"init_data": "<WebApp.initData как есть>"}` — проверка подписи
  (HMAC-SHA256 с ключом `WebAppData` от токена бота), `auth_date` не старше часа → JWT HS256 на час,
  в `sub` — `max_user_id`. Дальше `Authorization: Bearer <token>`.
- **Dev-авторизация**: `API_DEV_AUTH=true` — `initData` без проверки подписи и срока, при старте
  предупреждение в логе. Только локально.
- **Методы**: `GET /v1/me`; `GET /v1/items?from&to&kind&status&group=day` (период не длиннее
  62 дней, с `group=day` — все дни периода с подписями «Пн, 19 октября»); `GET /v1/items/search?q&limit`;
  `GET /v1/items/{id}`; `GET /v1/dialog?limit&before` (черновик до G3).
- **ETag**: `GET /v1/items` отдаёт `ETag` = версия данных пользователя; с `If-None-Match` той же
  версии — 304 без тела. Ответы кешируются в Redis на 5 минут под ключом с версией, чистить не нужно.
- **Ошибки**: конверт `{"error": {"code", "message", "details", "trace_id"}}`; `validation_error` — 400,
  `unauthorized` — 401, `not_found` — 404, `internal` — 500.

## Scheduler (J4)

Один проход раз в `SCHED_INTERVAL_SECONDS` (15 с), время — `now()` базы, а не часы контейнера:

1. `reclaim_expired` — вернуть в очередь уведомления с истёкшей арендой (реплика упала).
2. `cancel_stale` — отменить созревшие, которые уже не про текущую запись (перенесли, выполнили, удалили).
3. `claim_due` → `deliver`, пачками по 5, пока есть созревшие: `FOR UPDATE SKIP LOCKED`, статус
   `sending`, аренда 5 минут; `POST <SCHED_BOT_URL>/internal/v1/notify` с телом J4 (полный `Item` в поясе
   пользователя, исходный `fire_at`, номер попытки, `kind: "reminder"`), заголовки `Authorization`,
   `X-Trace-Id`, `Idempotency-Key = notification_id`, таймаут 10 с.
4. `schedule_next` — серия, чьё вхождение прошло без «Выполнено», переезжает на следующее.

| Ответ бота | Итог |
| --- | --- |
| 200 `delivered: true` (в том числе `duplicate`) | `sent` |
| 404 `chat_not_found`, прочие 4xx | `failed` без повторов |
| 429, 5xx, таймаут, сеть | повтор через 1, 2, 4, 8, 16 минут, после шестой неудачной попытки — `failed` |
| у пользователя нет `max_chat_id` | `failed` без вызова бота |

Опоздавшее напоминание (Scheduler лежал) всё равно отправляется с исходным `fire_at`.

**Несколько реплик**: `docker compose -f deploy/docker-compose.yml --env-file .env --profile mock up -d --scale scheduler=2`. Уведомление в аренде другие реплики
не видят (`SKIP LOCKED`), упавшая реплика отдаёт пачку по истечении аренды. Тесты
`services/tests/test_scheduler_replicas.py`: две реплики, 200 созревших — каждое ровно один раз;
упавшая реплика; сдвинутые часы. Ручная проверка на стенде с ботом C: два `scheduler`, напоминание
«через 2 минуты» приходит в MAX один раз.

**Кнопки уведомления** («Выполнено», «Отложить на 1 ч») обрабатывает агент C и вызывает
`complete_item` / `update_item(remind_at=…)` с `caller.idempotency_key` = id нажатия; в D своего кода нет.

## Повторы — минимальный вариант

- Серия — одна запись с `rrule` (RFC 5545 без `RRULE:`, например `FREQ=WEEKLY;BYDAY=MO`); начало
  серии — `starts_at`, а без него `remind_at`. Неверное правило, `DTSTART` внутри или повтор без
  даты — `validation_error`.
- Даты считаются в местном времени пользователя: «каждый понедельник в 9» — 9:00 и после перевода
  часов. `UNTIL` в UTC (`…Z`) понимается.
- «Выполнено» сдвигает `starts_at`, `ends_at`, `remind_at` на следующее будущее вхождение (запись
  остаётся `open`, уведомление пересоздаётся, `COUNT` уменьшается); конец серии — `done`.
  Без «Выполнено» серию сдвигает Scheduler, когда вхождение закончилось.
- «Удали» удаляет всю серию. Список и API показывают только текущее вхождение.

## Логи

Строка на каждый запрос к api и mcp: `GET /v1/me 200 4 ms trace_id=…`; тот же `trace_id` — в конверте
ошибки. Scheduler пишет `trace_id` каждой доставки. Временно, до `libs/common` роли C (structlog).

## Что улучшил бы

- Чистка старых строк: `notifications` (отправленные и отменённые) и `dialog_log` растут без предела.
- Ограничения длины входных строк (`query`, `notes`, `source_text`, `init_data`) — это изменение
  контрактов J3 и J5, решается на G4.
- Выборка созревших уведомлений без перебора `items` при большом объёме (см. `docs/d-explain.md`).
- Слабый ETag (`W/"…"`) в `If-None-Match` сейчас не даёт 304.
