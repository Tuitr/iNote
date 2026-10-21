# deploy — стенд (зона C)

`make up` поднимает стенд (профиль `mock`, всё на фейках, без ключей) и ждёт, пока все сервисы станут healthy;
`make smoke` — два сценария S1 через фейковый MAX («что у меня сегодня», «напомни завтра в 10 …»);
`make e2e` — `tests/e2e` на стенде (то же плюс кнопка и дедуп); `make logs TRACE=<6 символов trace_id>` —
путь запроса через все сервисы; `make down` — остановка (данные Postgres остаются в volume `pgdata`).

| Файл | Что это |
|---|---|
| `docker-compose.yml` | postgres (pgvector:pg16), redis (256 МБ, `volatile-lru`, без персистентности), bot, agent; заглушки asr (A), api, scheduler, migrate (D), max_app (E); mcp — временная MCP-заглушка C (`stub/mcp`, см. её README) |
| `docker/python.Dockerfile` | общий образ Python-сервисов: `uv sync --frozen` без dev, пакеты не editable, пользователь `app`, uvicorn — PID 1 |
| `stub/` | заглушка чужого сервиса на stdlib: `/health/*` → 200, остальное → 404 в конверте ошибки |
| `stub/mcp/` | **временная** MCP-заглушка C: 4 инструмента, J8 `users/resolve`, ответы из фикстур — до заглушки D |
| `smoke.sh` | смоук: два апдейта через `/dev/max/*` → ответы агента |

- Наружу опубликован только бот на `127.0.0.1:${BOT_DEV_PORT:-8001}` — ради `/dev/max/*`; `/internal/*` под сервисным токеном.
- Профиль `full` пока совпадает с `mock`; реальные провайдеры (платно) — только по прямой просьбе.
- Владельцы чужих сервисов после мержа заменяют свою заглушку на `build:` и добавляют `COPY <пакет>/pyproject.toml` в `python.Dockerfile`.
- Стенд с HTTPS (Caddy, webhook MAX на 443) и `make deploy` — когда будет токен MAX (S2).
