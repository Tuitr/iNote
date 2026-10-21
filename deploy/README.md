# deploy — стенд (зона C)

`make up` поднимает стенд S0 (профиль `mock`, всё на фейках, без ключей) и ждёт, пока все сервисы станут healthy;
`make smoke` подкладывает апдейт фейковому MAX и ждёт эха; `make logs TRACE=<6 символов trace_id>` — путь запроса;
`make down` — остановка (данные Postgres остаются в volume `pgdata`).

| Файл | Что это |
|---|---|
| `docker-compose.yml` | postgres (pgvector:pg16), redis (256 МБ, `volatile-lru`, без персистентности), bot, agent; заглушки asr (A), mcp, api, scheduler, migrate (D), max_app (E) |
| `docker/python.Dockerfile` | общий образ Python-сервисов: `uv sync --frozen` без dev, пакеты не editable, пользователь `app`, uvicorn — PID 1 |
| `stub/` | заглушка чужого сервиса на stdlib: `/health/*` → 200, остальное → 404 в конверте ошибки |
| `smoke.sh` | смоук: апдейт → эхо через `/dev/max/*` |

- Наружу опубликован только бот на `127.0.0.1:${BOT_DEV_PORT:-8001}` — ради `/dev/max/*`; `/internal/*` под сервисным токеном.
- Профиль `full` пока совпадает с `mock`; реальные провайдеры (платно) — только по прямой просьбе.
- Владельцы чужих сервисов после мержа заменяют свою заглушку на `build:` и добавляют `COPY <пакет>/pyproject.toml` в `python.Dockerfile`.
- Стенд с HTTPS (Caddy, webhook MAX на 443) и `make deploy` — S1.
