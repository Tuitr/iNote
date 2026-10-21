# libs/common — общая библиотека (зона C)

Нужна всем Python-сервисам. Импорт всегда по модулю: `from common.http import ServiceHttpClient`.

| Модуль | Что даёт |
|---|---|
| `config` | `BaseServiceSettings`: общие переменные (`DATABASE_URL`, `REDIS_URL`, `SERVICE_TOKEN`, `LOG_LEVEL`, `LOG_FORMAT`, `DEFAULT_TZ`). Сервис наследует, задаёт `envPrefix = "BOT_"` и поля с явным `validation_alias`; конфликт префиксов и алиасов — `TypeError` при объявлении класса |
| `logging` | `configureLogging(serviceName, level, fmt)`: structlog, `console` или `json`; stdlib (uvicorn, httpx) идёт тем же потоком; `getLogger`, `bindUserId` |
| `trace` | `newTraceId()` (32 hex), `currentTraceId()`, `shortTraceId()` (6 символов), `traceContext()` — contextvar + structlog |
| `middleware` | `installMiddleware(app, serviceToken=..., protectedPrefixes=("/internal/",))`: `X-Trace-Id` (берёт каноничный или создаёт), лог `http_request` с `duration_ms`, 500 `internal` на непойманное, Bearer-токен: нет → 401, неверный → 403 |
| `errors` | `AppError(code, message, details)`, `ErrorCode`, `installErrorHandlers(app)` — конверт `{"error": {code, message, details, trace_id}}` |
| `lifecycle` | `TaskRegistry.spawn(coro, name)`, `shutdown(timeoutS)` → `ShutdownReport`; `managedTasks()` для lifespan; `stopping` — сигнал циклам; `listenForShutdownSignal()` для процессов без uvicorn |
| `health` | `buildHealthRouter({"redis": redisHealthCheck(client)})`: `/health/live`, `/health/ready` (503 `degraded`), у каждой проверки свой таймаут |
| `http` | `ServiceHttpClient`: base URL, таймаут, `X-Trace-Id` и токен сами; повторы только для идемпотентных методов или с `idempotencyKey`; конверт ошибки соседа → `AppError` |
| `wire` | `WireModel`: в коде camelCase, на проводе snake_case, незнакомые поля игнорируются |
| `redis` | `createRedis(url)`, `RedisKey("bot", "upd", id)` → `va:v1:bot:upd:<id>`, `KeyedRedis` — запись только с TTL (без TTL — белый список реестра) |
| `testing` | pytest-плагин (подключается сам): `redisContainerUrl`, `redisClient`, `uniqueId` |

Тесты: `uv run pytest libs/common` (unit), `-m integration` (нужен Docker; образ `redis:7.4-alpine`, как на стенде), `-m lifecycle` (SIGTERM в подпроцессе).
