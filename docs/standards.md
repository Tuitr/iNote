# Стандарты взаимодействия

Утверждаются на G0. Короткая версия — в CLAUDE.md («Стандарты»); при расхождении правим оба файла в одном PR.
Разделы зоны C — между маркерами; разделы других зон добавляют их владельцы.

<!-- --- зона C --- -->
## Транспорт

- Внутри compose — HTTP/JSON на `http://<сервис>:8000`: `bot`, `agent`, `asr`, `mcp`, `api`, `scheduler`.
  MCP — Streamable HTTP на `http://mcp:8000/mcp`, служебный REST того же контейнера — `/internal/v1/*`.
- Наружу (на стенде — через Caddy с HTTPS) только `/webhook/max` (бот), `/api/v1/*` (API) и статика MAX app.
  `/internal/*` наружу не публикуется никогда. Локально (`make up`) опубликован только бот на `127.0.0.1:8001` — для `/dev/max/*`.
- JSON в UTF-8, поля `snake_case`, перечисления — строки в нижнем регистре, id — UUID строкой,
  время — ISO 8601 со смещением (без смещения — `validation_error`), длительности — целые минуты в `*_min`.
- Читатель игнорирует незнакомые поля (`common.wire.WireModel`: `extra="ignore"`).
- В Python-коде зоны C — camelCase, на проводах — snake_case; мост — `WireModel` (предложение C, `docs/decisions.md`).

## Заголовки

| Заголовок | Где | Правило |
|---|---|---|
| `Authorization: Bearer <SERVICE_TOKEN>` | все внутренние вызовы | проверяет `ServiceTokenMiddleware`: нет или не Bearer → 401 `unauthorized`, неверный → 403 `forbidden`; сравнение за постоянное время. Защищены `/internal/*`, у агента ещё `/v1/*` |
| `X-Trace-Id` | каждый внутренний вызов и ответ | 32 hex в нижнем регистре; создаёт начало цепочки — бот (апдейт), Scheduler (уведомление), API (запрос фронта); неканоничный входящий заменяется новым; пользователю — первые 6 символов (`shortTraceId`) |
| `Idempotency-Key` | изменяющие HTTP-вызовы между сервисами | `<trace_id>:<номер вызова>`; для MCP — поле `caller.idempotency_key`; с ключом вызов можно повторять |

`ServiceHttpClient` из `libs/common` ставит `X-Trace-Id` и токен сам; `installMiddleware` подключает проверку и trace одной строкой.

## Ошибки

Один конверт на все HTTP-ответы с ошибкой (`common.errors`):

```json
{"error": {"code": "not_found", "message": "Запись не найдена", "details": {"id": "7f3a9c2e-…"}, "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80"}}
```

| Код | HTTP | Когда |
|---|---|---|
| `validation_error` | 400 | неверные поля, время без смещения; в `details.fields` — только пути и типы ошибок, **без введённых значений** |
| `unauthorized` | 401 | нет сервисного токена (или JWT истёк — API) |
| `forbidden` | 403 | неверный сервисный токен |
| `not_found` | 404 | записи нет **или она чужая** — ответ одинаковый; также неизвестный путь |
| `stale` | 409 | запись изменилась после чтения |
| `past_time` | 422 | время в прошлом (решение по напоминаниям — G1) |
| `rate_limited` | 429 | лимит запросов |
| `internal` | 500 | непойманное исключение: стектрейс только в лог, наружу — код и `trace_id` |
| `upstream_unavailable` | 503 | сосед или внешний сервис недоступен (в т.ч. 5xx без конверта) |
| `upstream_timeout` | 504 | сосед не ответил вовремя |

Коды стыков — в своих модулях `libs/contracts` (J1: `no_speech` 422, `audio_too_long` 413, `unsupported_format` 400;
J4: `chat_not_found` 404). `ServiceHttpClient` превращает конверт соседа в `AppError` с тем же кодом и статусом.

## Таймауты и повторы

| Вызов | Таймаут | Повторов | Когда повторяем |
|---|---|---|---|
| бот → ASR | 15 с | 2 | сеть, 429, 5xx |
| агент → LLM | 20 с | 2 | 429 и 5xx |
| агент → MCP | 5 с | 1 | только с `idempotency_key` |
| бот → агент | 45 с | 0 | — (агент сам укладывается в 40 с) |
| бот → MAX API | 10 с | 3 | сеть, 429, 5xx |
| Scheduler → бот (J4) | 10 с | до 5 попыток | 429, 5xx, таймаут; 404 `chat_not_found` — без повторов |

- Повторяем только идемпотентные методы (`GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`) или вызовы с ключом идемпотентности.
  POST без ключа не повторяется никогда (`common.http`).
- Задержка — экспонента с джиттером (tenacity), потолок 2 с между попытками для внутренних вызовов.
- Бюджет голосового: ASR ≤ 15 с, агент ≤ 40 с, бот ждёт агента ≤ 45 с.

## Логи

- structlog через `common.logging.configureLogging(serviceName, level, fmt)`; `LOG_FORMAT=console` локально, `json` на стенде.
  Логи uvicorn и библиотек идут тем же потоком и форматом.
- Поля: `service`, `event`, `trace_id`, `user_id`, `duration_ms`, плюс `level`, `timestamp` (UTC ISO). В JSON — snake_case.
- `trace_id` привязывается через contextvars (`traceContext`), `user_id` — `bindUserId` (только **внутренний** id).
- Запрещено на INFO: токены, аудио, полный текст сообщений, MAX user id, query-строки URL. Лог запроса — путь без query.
- Путь запроса через все сервисы — `make logs TRACE=<первые 6 символов trace_id>`.

## Health

- `GET /health/live` → 200 `{"status": "ok"}` — процесс жив, зависимости не трогает.
- `GET /health/ready` → 200 `{"status": "ok", "checks": {...}}` или 503 `{"status": "degraded", "checks": {"redis": "fail", "agent": "timeout"}}`.
  Проверки параллельно, у каждой свой таймаут (по умолчанию 1 с): Redis — PING, соседи — их `/health/live`.
- Readiness **не вызывает LLM и ASR** — это платно (правило 9). Healthcheck compose смотрит `/health/ready`.

## Конфиг

- pydantic-settings: `common.config.BaseServiceSettings` с общими переменными
  (`DATABASE_URL`, `REDIS_URL`, `SERVICE_TOKEN` ≥ 16 символов, `LOG_LEVEL`, `LOG_FORMAT`, `DEFAULT_TZ`).
- Сервис наследует, задаёт `envPrefix` (`BOT_`, `AGENT_`, `ASR_`, `LLM_`, `MCP_`, `API_`, `SCHED_`) и поля с явным
  `validation_alias`; префикс, формат и уникальность имён проверяются при объявлении класса.
- Имена переменных чувствительны к регистру; `.env` читает compose, в коде источник один — окружение процесса.
- Новая переменная — в `.env.example` в том же PR. `.env` не коммитим; секреты в README и командах — только как `$SERVICE_TOKEN`
  (gitleaks в pre-commit и CI).
- Клиенты и пулы создаются один раз в `lifespan`; фоновые задачи — только через `TaskRegistry` (правило 9).
<!-- --- конец зоны C --- -->
