# services/bot — бот MAX (зона C)

Единственный, кто разговаривает с MAX. В S0 это эхо-бот на **фейковом MAX**: доступа к API MAX пока нет,
а реальный адаптер (`maxapi` или своя обёртка над `platform-api.max.ru`) — открытое решение.

## Слои

```
domain/          IncomingUpdate, UpdateKind, VoiceRef, UpdateBatch, OutgoingMessage (лимит 4000)
application/     порты MaxGateway, UpdateDeduplicator, RateLimiter, BotTexts; use case HandleUpdate
infrastructure/  FakeMaxGateway, маппер MAX → IncomingUpdate, Redis-дедуп и лимит, BotSettings
presentation/    PollingLoop (long polling), /dev/max/*, тексты (templates.py)
main.py          composition root: createApp() и lifespan
```

`HandleUpdate`: дубль пропускается, сверх лимита (20 в минуту) — «Слишком часто», текст — эхо,
команда — эхо имени, голосовое — заглушка до J1, кнопка — ответ на callback, «Начать» — приветствие.
`PollingLoop` обрабатывает апдейты одного пользователя по порядку, marker фиксирует после обработки,
по SIGTERM дорабатывает начатую страницу (задача зарегистрирована в `TaskRegistry`).

## Переменные

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `BOT_MAX_MODE` | `fake` | `fake` — MAX в памяти и `/dev/max/*`; `real` — пока падает на старте |
| `BOT_POLL_TIMEOUT_S` | `30` | таймаут long polling (0–90) |
| `BOT_RATE_LIMIT_PER_MIN` | `20` | сообщений пользователя в минуту |
| `BOT_SHUTDOWN_TIMEOUT_S` | `10` | сколько ждать фоновые задачи на SIGTERM |
| `BOT_FAKE_SEND_DELAY_MS` | `0` | задержка «отправки» в фейковом MAX (lifecycle-тесты) |

Плюс общие: `REDIS_URL`, `SERVICE_TOKEN`, `LOG_LEVEL`, `LOG_FORMAT`.

## Запустить локально и увидеть эхо

```bash
docker run -d --rm -p 6379:6379 redis:7.4-alpine
export SERVICE_TOKEN=$(openssl rand -hex 16)
REDIS_URL=redis://localhost:6379/0 uv run uvicorn bot.main:createApp --factory --port 8001

# подложить апдейт (формат MAX) и посмотреть ответ бота
curl -s -X POST localhost:8001/dev/max/updates \
  -H 'content-type: application/json' \
  -d @services/bot/tests/fixtures/max_updates/text.json
curl -s localhost:8001/dev/max/sent
# {"messages":[{"message_id":"fake-mid-1","chat_id":500600700,"text":"Вы написали: ...","format":"plain"}],"callback_answers":[]}
```

## Тесты

```bash
uv run pytest services/bot                    # unit
uv run pytest services/bot -m integration     # Redis в testcontainers: дедуп, лимит, TTL
uv run pytest services/bot -m lifecycle       # uvicorn + SIGTERM посреди обработки
```

Фикстуры апдейтов (`tests/fixtures/max_updates`) синтетические, собраны по OpenAPI-схеме MAX.
Заменить живыми, как только будет токен (подробнее — README там же).
