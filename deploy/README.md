# deploy — стенд (зона C)

`make up` поднимает стенд (профиль `mock`, всё на фейках, без ключей) и ждёт, пока все сервисы станут healthy;
`make smoke` — четыре сценария ТЗ голосом через фейковый MAX и asr с фейковым провайдером («напомни завтра в 10 …»,
«встреча с Иваном завтра в 15», «что у меня завтра», «найди звонок Ивану») и «что у меня сегодня» текстом;
`make e2e` — `tests/e2e` на стенде (S1 текстом и S2 голосом: кнопки, уточнения, конфликт встреч, `no_speech`,
`/week`; S3 — уведомления, кнопки уведомления, фокус, кнопки мини-приложения, `/settings`); `make logs TRACE=<trace_id>` — путь голосового через bot → asr → agent → mcp одним `trace_id`; `make down` — остановка (данные Postgres остаются в volume `pgdata`).

**S3 — уведомления (J4) вручную**, вместо Scheduler'а D: `SERVICE_TOKEN=<из .env> ./contracts/examples/notify/notify.sh all`
(`once` — одно уведомление, `repeat` — повтор того же `notification_id` → `duplicate`, `parallel` — два сразу →
одно сообщение, `chat-not-found` — 404 без повторов). Скрипт печатает `trace_id`; `make logs TRACE=<первые 6>`
показывает путь: `POST /internal/v1/notify` → `max_message_sent` → `notification_handled` → `focus_sent` (бот) →
`focus_set` (агент) — одной цепочкой. Сквозные сценарии S3 — `tests/e2e/test_s3_*.py` (голосового ответа
ассистента нет — решение команды).

**Финальный прогон S4:** `make e2e` на свежем `make up` — обязательные сценарии ТЗ текстом и голосом
(`tests/e2e/test_s5_required.py`), «плюс» (уточнения, кнопки, конфликт, уведомления, «перенеси его», `/settings`,
`chat_not_found`), ошибки (`no_speech`, отказ агента, ASR, MCP и J8, Redis — `test_s4_degradation.py`), затем аудит
Redis. В конце — таблица «итог → trace_id → сценарий»: путь любого шага — `make logs TRACE=<первые 6>`.

**Демо (3–5 минут):** `make up && make demo` (или `DEMO_PAUSE_S=0 ./deploy/demo.sh` без пауз). Скрипт `deploy/demo.py`
проигрывает через `/dev/max/*` и печатает как чат: `/start` → напоминание голосом → встреча с конфликтом и «Всё
равно создать» → «что у меня завтра» → поиск → уведомление J4 → «Отложить на 1 ч» → «перенеси его на завтра в 15»
голосом → `/settings` → Екатеринбург → «что у меня сегодня». Под каждым ответом — кнопки и `trace_id`. Данные —
фикстуры MCP-заглушки (`deploy/stub/mcp/fixtures`), пользователь — новый на каждый запуск.

### Чек-лист ручного прохода на стенде с настоящим MAX

Нужны токен бота, домен и `make deploy` (только по прямой просьбе). Каждый пункт — в чате с ботом, код ошибки
(6 символов) — в `make logs TRACE=…`.

1. Подписка: в логах бота `max_subscribed` (так пишет фейк; реальный адаптер должен писать то же событие); `POST /webhook/max` без секрета — 401.
2. `/start` — приветствие, кнопки «Сегодня / Завтра / Неделя», «Мой день» и «История» открывают мини-приложение
   на нужном экране (проверить с E, что `payload` кнопки доходит как `start_param`; иначе `BOT_OPEN_APP_MODE=link`).
3. Голосом: «Напомни завтра в 10 позвонить Ивану» — «Распознал: …», подтверждение, «Отменить» → «Отменено».
4. Текстом и голосом: встреча, «что у меня завтра», «найди звонок» — четыре сценария ТЗ.
5. Встреча поверх существующей — свободные окна и «Всё равно создать».
6. Уведомление от Scheduler D приходит один раз; «Отложить на 1 ч», «Выполнено», повтор — «Уже отмечено».
7. «Перенеси его на завтра в 15» после уведомления — меняется та же запись.
8. `/settings` → другой пояс → «что у меня сегодня» в новом поясе.
9. Название с разметкой (`*жирный* [ссылка](x)`) — показано как текст, без форматирования; длинный список — «…и ещё N».
10. Живые апдейты: голосовое приходит вложением `audio` (TODO в `max_mapper.py`), повтор `callback_id` отсекается.

| Файл | Что это |
|---|---|
| `docker-compose.yml` | postgres (pgvector:pg16), redis (256 МБ, `volatile-lru`, без персистентности), bot, agent, asr (`services/asr`: `mock` — фейк, `full` — SpeechKit); заглушки api, scheduler, migrate (D), max_app (E); mcp — временная MCP-заглушка C (`stub/mcp`, см. её README) |
| `docker/python.Dockerfile` | общий образ Python-сервисов: `uv sync --frozen` без dev, пакеты не editable, пользователь `app`, uvicorn — PID 1 |
| `stub/` | заглушка чужого сервиса на stdlib: `/health/*` → 200, остальное → 404 в конверте ошибки |
| `stub/mcp/` | **временная** MCP-заглушка C: инструменты J3 с конфликтами, J8 `users/resolve` и `dialog-turns` — до заглушки D |
| `stub/asr/` | прежняя ASR-заглушка C (S2); на стенде её заменил `services/asr` с `ASR_PROVIDER=fake` (то же поведение); осталась для integration-теста бота `test_asr_stub.py` |
| `asr_try.sh` | `make asr-try FILE=voice.ogg` — распознать свой OGG/Opus сервисом asr стенда (с `PROFILE=full` — SpeechKit, платно) |
| `docker-compose.stand.yml` | стенд с HTTPS: Caddy на 80/443, бот — webhook и настоящий MAX (override) |
| `Caddyfile` | что видно снаружи: webhook MAX, `/api/v1/*`, статика мини-приложения; служебное — 404 |
| `smoke.sh` | смоук: четыре сценария ТЗ голосом и один текстом через `/dev/max/*` |
| `demo.sh`, `demo.py` | демо на 3–5 минут: диалог как чат (`make demo`) |
| `redis_check.py` | аудит Redis стенда: ключи из реестра, TTL, `maxmemory` (`make redis-check`) |
| `redis_load.py` | нагрузка на фейках и замер памяти Redis (таблица — `docs/redis-keys.md`) |
| `env_docs.py` | таблицы переменных в README бота и агента из классов настроек (`make env-docs`) |
| `stand-check.sh` | проверка `.env` перед `make deploy` |

- Наружу опубликован только бот на `127.0.0.1:${BOT_DEV_PORT:-8001}` — ради `/dev/max/*`; `/internal/*` под сервисным токеном.
- Профиль `full` пока совпадает с `mock`; реальные провайдеры (платно) — только по прямой просьбе.
- Владельцы чужих сервисов после мержа заменяют свою заглушку на `build:` и добавляют `COPY <пакет>/pyproject.toml` в `python.Dockerfile`.
- **Стенд с HTTPS** — `docker-compose.stand.yml` поверх основного файла: Caddy (`caddy:2.10.0-alpine`, Let's Encrypt
  для `STAND_DOMAIN`) на 80/443 — единственный опубликованный сервис; бот в `BOT_MAX_MODE=real` и
  `BOT_UPDATES_MODE=webhook`. `Caddyfile` пропускает наружу только `POST /webhook/max` → бот, `/api/v1/*` → api
  (префикс `/api` срезается) и остальное → max_app; `/internal`, `/dev`, `/health`, `/v1`, `/mcp`, `/metrics` — 404
  (проверяет `tests/deploy`). Нужны `STAND_DOMAIN`, `ACME_EMAIL`, `BOT_WEBHOOK_SECRET` и токен MAX в `.env`;
  `make stand-config` — проверить `.env` (`deploy/stand-check.sh`: токен не заглушка, секрет, домен) и конфиг,
  `make deploy` — поднять (только по прямой просьбе; пока бот в `real`
  не стартует — адаптер MAX не выбран).

Webhook локально, без HTTPS: `BOT_UPDATES_MODE=webhook BOT_WEBHOOK_URL=http://127.0.0.1:8000/webhook/max
BOT_WEBHOOK_SECRET=$(openssl rand -hex 16)` в `.env` и `make up` — фейковый MAX подпишет бота и будет слать
апдейты из `/dev/max/updates` на его webhook; `smoke` и e2e работают так же.


## Runbook стенда

**Поднять с HTTPS** (только по прямой просьбе — это настоящий MAX и сертификат):

1. `.env`: `SERVICE_TOKEN` (≥ 32 символов, не заглушка), `BOT_WEBHOOK_SECRET` (`openssl rand -hex 24`),
   `STAND_DOMAIN`, `ACME_EMAIL`, токен MAX; `BOT_USERNAME` — имя бота после модерации.
2. `make stand-config` — `deploy/stand-check.sh` проверяет `.env`, затем `docker compose config` обоих файлов.
3. `make deploy` — Caddy на 80/443 получает сертификат Let's Encrypt для `STAND_DOMAIN`, бот стартует в
   `BOT_MAX_MODE=real`, `BOT_UPDATES_MODE=webhook` (пока адаптер MAX не выбран, бот в `real` не стартует).

**Проверить подписку webhook.** При каждом старте бот вызывает `ensureSubscription` — в логах бота `max_subscribed`
(`make logs SERVICE=bot`). Снаружи: `curl -i -X POST https://$STAND_DOMAIN/webhook/max` без секрета — 401
(значит, Caddy пропускает только webhook, а бот проверяет секрет); `https://$STAND_DOMAIN/internal/v1/notify` — 404.

**Найти запрос.** Пользователь видит «Код: ab12f0» — это первые 6 символов `trace_id`.
`make logs TRACE=ab12f0` покажет путь по всем сервисам: бот (`update_handled`, `max_message_sent`), ASR,
агент (`user_resolved`, `turn_finished` с `outcome` и `error_code`), MCP (`tool_called`). Для уведомлений —
`notification_handled` и `focus_set`. Тексты пользователя в логах не пишутся — только события и коды.

**Типовые проблемы**

| Симптом | Причина | Что делать |
|---|---|---|
| Бот перестал получать сообщения | MAX отписал бота: 8 ч без ответа 200 на webhook (бот лежал или Caddy без сертификата) | перезапустить бот (`docker compose … restart bot`) — подписка восстановится при старте; проверить `max_subscribed` в логах |
| «Кнопка устарела» у свежих кнопок, повторы ответов | Redis упёрся в `maxmemory` и вытесняет ключи с TTL (`volatile-lru`) | `make redis-check`; `INFO memory` в Redis; оценка — ≈ 20 КБ на активного пользователя в сутки (`docs/redis-keys.md`); поднять `--maxmemory` в compose или сократить TTL кнопок |
| «Что-то пошло не так… Код» на всё | Redis недоступен | `make ps`, `make logs SERVICE=redis`; после возврата Redis бот и агент работают без перезапуска |
| «Сервис записей сейчас недоступен… Код» | mcp-контейнер (MCP и J8) лежит или не отвечает за 5 с | `make logs SERVICE=mcp`; агент переподключится сам |
| Caddy не получает сертификат | DNS `STAND_DOMAIN` не указывает на сервер, закрыт 80/443 или превышен лимит Let's Encrypt | `docker compose … logs caddy`; проверить DNS и порты; при лимите — подождать или временно staging-CA |
| Сервис убит при остановке (`exit 137`) | не уложился в `stop_grace_period` (30 с) | долгие ходы при остановке обрываются по таймауту uvicorn (15 с) — так задумано; бюджет — `tests/deploy/test_stop_budget.py` |

**После синхронизации папки с другого компьютера** mypy иногда падает с `INTERNAL ERROR` или видит старые
модули — это кеш, а не код: `rm -rf .mypy_cache` (скрипты коммитов `.transfer/commit-*.sh` делают это сами).
