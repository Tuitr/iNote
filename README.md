# iNote — голосовой ассистент для календаря в MAX

Бот в MAX: голосом или текстом ставит задачи, напоминания и встречи, отвечает «что у меня завтра»,
ищет старые записи и присылает напоминания; мини-приложение MAX показывает календарь.

## Быстрый старт (стенд на фейках, без ключей)

```bash
make install        # uv sync --all-packages --frozen
make up             # docker compose, профиль mock; ждёт healthy всех сервисов
make smoke          # два сценария через фейковый MAX: «что у меня сегодня» и «напомни завтра в 10 …»
make e2e            # tests/e2e на поднятом стенде
make logs TRACE=ab12f0   # путь запроса по первым 6 символам trace_id (их бот показывает в ошибках)
make down
```

Подложить свой апдейт и посмотреть ответ бота:

```bash
curl -s -X POST localhost:8001/dev/max/updates -H 'content-type: application/json' \
  -d @services/bot/tests/fixtures/max_updates/text.json
curl -s localhost:8001/dev/max/sent
```

## Путь сообщения (S1)

```
фейковый MAX → bot (дедуп, лимит, «печатает…») → agent POST /v1/messages
  → J8 users/resolve (кеш Redis) → лок пользователя → контекст в Redis
  → Brain (сейчас ScriptedBrain; LLM — B) → MCP-инструменты (caller подставляет агент)
  → шаблон ответа (данные инструмента, даты по-русски) → Reply → bot → кнопки MAX
```

| Путь | Что | Зона |
|---|---|---|
| `services/bot` | бот MAX: апдейты, дедуп, лимиты, «печатает…», Reply → сообщение и кнопки MAX | C |
| `services/agent` | ход агента: пользователь, лок, контекст, MCP-клиент, шаблоны ответа | C |
| `libs/common` | конфиг, логи, trace, middleware, lifecycle, health, HTTP-клиент, Redis-ключи | C |
| `libs/contracts`, `contracts/examples` | модели и примеры стыков J1–J8 | владелец стыка |
| `deploy/` | compose, Dockerfile, заглушки чужих сервисов, временная MCP-заглушка | C |

Остальные зоны (ASR — A, Brain — B, MCP/API/Scheduler/core — D, MAX app — E) появятся после мержа.

## Проверки

```bash
make check          # pre-commit: ruff, mypy --strict, import-linter, gitleaks
make test           # unit + контракты
make test-integration test-lifecycle   # нужен Docker (testcontainers)
make cov            # покрытие, порог 60%
```

Правила работы, стандарты и стыки — `docs/standards.md`, `docs/redis-keys.md`, `docs/decisions.md`.
