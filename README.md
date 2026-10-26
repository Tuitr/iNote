# iNote — голосовой ассистент для календаря в MAX

Бот в MAX: голосом или текстом ставит задачи, напоминания и встречи, отвечает «что у меня завтра»,
ищет старые записи и присылает напоминания; мини-приложение MAX показывает календарь.

## Архитектура кратко

```
MAX → bot → asr (голос → текст, Yandex SpeechKit)
        → agent + Brain (GigaChat выбирает инструмент) → mcp (8 инструментов) → libs/core → PostgreSQL
scheduler → bot: напоминания вовремя
max_app (мини-приложение) → api (только чтение) → libs/core → PostgreSQL
```

- `user_id` подставляет агент, модель его не видит; подтверждение «записал» строится из ответа
  инструмента, а не из текста модели; даты и обязательные поля проверяет код.
- Redis — дедуп, локи, контекст диалога и кеши; источник правды — PostgreSQL.
- Наружу через Caddy с HTTPS открыты только webhook бота, `/api/v1/*` и статика мини-приложения.

## Документация

- Техническая: [docs/architecture.md](docs/architecture.md) — контракты J1–J8, роли A–E, деплой.
- Продуктовая: [productDock/README.md](productDock/README.md).
- Стандарты, ключи Redis, решения: [docs/standards.md](docs/standards.md),
  [docs/redis-keys.md](docs/redis-keys.md), [docs/decisions.md](docs/decisions.md).

<!-- --- зона C --- -->
## Зона C: бот, агент, Redis, стенд

**Бот** (`services/bot`) — единственный, кто говорит с MAX: апдейты, дедуп, лимиты, голос → ASR, `Reply` агента →
сообщение и кнопки MAX, доставка напоминаний Scheduler'а (J4). **Агент** (`services/agent`) — ход ассистента:
пользователь (J8), лок, контекст в Redis, Brain, инструменты MCP с `caller`, ответ по шаблонам из сохранённых
данных. **`libs/common`** — конфиг, логи, trace, middleware, lifecycle, health, HTTP-клиент, ключи Redis.
**ASR** (`services/asr`, J1) — голосовое OGG/Opus → текст: на `mock` — фейк, на `full` — Yandex SpeechKit
(ключ в `.env`). **`deploy/`** — compose, образ, заглушки чужих сервисов, Caddy, демо и проверки стенда.

### Быстрый старт (стенд на фейках, без ключей)

```bash
make install            # uv sync --all-packages --frozen
make up                 # docker compose, профиль mock; ждёт healthy всех сервисов
make smoke              # четыре сценария ТЗ голосом и один текстом через фейковый MAX
make e2e                # все сценарии tests/e2e + аудит Redis; в конце — таблица «итог → trace_id → сценарий»
make demo               # демо на 3–5 минут: диалог в фейковом MAX, напечатанный как чат
make logs TRACE=ab12f0  # путь запроса по первым 6 символам trace_id (их бот показывает в ошибках)
make redis-check        # ключи Redis стенда: из реестра, с TTL; maxmemory и политика
make up PROFILE=full    # то же, но asr — настоящий SpeechKit (ASR_YANDEX_API_KEY в .env) — платно
make asr-try FILE=voice.ogg PROFILE=full   # распознать свой OGG/Opus — платно
make down
```

Уведомление «как от Scheduler'а»: `SERVICE_TOKEN=<из .env> ./contracts/examples/notify/notify.sh all`.
Свой апдейт — `curl -X POST localhost:8001/dev/max/updates -d @services/bot/tests/fixtures/max_updates/text.json
-H 'content-type: application/json'`, голосовое — `POST /dev/max/voice {"max_user_id", "max_chat_id", "text"}`,
что ответил бот — `GET localhost:8001/dev/max/sent`.

### Путь сообщения

```
MAX (polling или webhook за Caddy) → bot: дедуп, лимит 20/мин, очередь пользователя, «печатает…»
  → голосовое: скачать сразу → ASR POST /v1/transcribe (J1) → текст
  → agent POST /v1/messages (кнопки и команды — /v1/actions, без LLM)
  → J8 users/resolve (кеш Redis) → лок пользователя → история, фокус и pending в Redis
  → Brain (сейчас ScriptedBrain; LLM — B): run, или resume по кнопке уточнения
  → MCP-инструменты (caller подставляет агент, ключ идемпотентности <trace_id>:<n>)
  → шаблон ответа из того, что сохранено → Reply → bot → сообщение и кнопки MAX (≤ 30 запросов/с)
  → история хода — в фоне J8 dialog-turns
Scheduler D → bot POST /internal/v1/notify (J4) → одно сообщение с кнопками → agent POST /v1/focus
```

Один `trace_id` на всю цепочку: его создаёт бот (или Scheduler), передают все в `X-Trace-Id`, пишут все логи.

| Путь | Что | Зона |
|---|---|---|
| `services/bot` | бот MAX | C |
| `services/agent` | ход агента, шаблоны ответа, `ScriptedBrain` | C |
| `libs/common` | общие инструменты сервисов | C |
| `libs/contracts`, `contracts/examples` | модели и примеры стыков J1–J8 | владелец стыка |
| `services/asr` | ASR по J1: фейк и Yandex SpeechKit (написан C по плану A, верх у A) | C → A |
| `deploy/` | стенд: compose, Dockerfile, заглушки, Caddy, демо, проверки | C |

Brain (B) ещё не в репо — вместо него `ScriptedBrain`; MCP на стенде C — заглушка `deploy/stub/mcp` (код D уже в
`main`, подключение к стенду — следующий шаг), Scheduler — `notify.sh`.

### Проверки

```bash
make check              # pre-commit: ruff, mypy --strict, import-linter, gitleaks
make test               # unit + контракты
make test-integration test-lifecycle   # нужен Docker (testcontainers)
make cov                # покрытие, порог 60%
make env-docs           # таблицы переменных в README бота и агента из классов настроек
```

Стандарты и стыки — `docs/standards.md` (каталог действий `/v1/actions` — там же), ключи Redis — `docs/redis-keys.md`,
решения — `docs/decisions.md`, runbook стенда — `deploy/README.md`.
<!-- --- конец зоны C --- -->
