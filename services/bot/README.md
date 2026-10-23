# services/bot — бот MAX (зона C)

Единственный, кто разговаривает с MAX. Принимает апдейты (long polling в dev, webhook на стенде), отсекает
дубли и частые сообщения, голос отдаёт в ASR, текст и кнопки — агенту, переводит абстрактный `Reply` агента в
сообщение и клавиатуру MAX и доставляет напоминания Scheduler'а (J4). Не зовёт LLM и не ходит в PostgreSQL.

Сейчас MAX — **фейковый** (`BOT_MAX_MODE=fake`, `/dev/max/*`): токена пока нет, реальный адаптер (`maxapi` или
своя обёртка над `platform-api2.max.ru`) — открытое решение. Голосового ответа ассистента нет (решение команды).

## Слои и порты

```
domain/          IncomingUpdate, UpdateKind, VoiceRef, UpdateBatch, OutgoingMessage (≤ 4000 символов, клавиатура
                 ≤ 30 рядов × 7 кнопок, ≤ 3 open_app/ссылок в ряду), DeliveryFailure, лимиты MAX
application/     порты: MaxGateway, AgentGateway, AsrGateway, AudioDownloader, CallbackStore, UpdateDeduplicator,
                 RateLimiter, NotificationLedger, FocusNotifier, BotTexts
                 use case: HandleUpdate, UpdateDispatcher, DeliverNotification, ReplyToMaxMapper, TypingIndicator
infrastructure/  FakeMaxGateway, ThrottledMaxGateway (≤ 30 запросов в секунду), MAX ↔ модели, HttpAgentGateway,
                 HttpAsrGateway, HttpAudioDownloader, HttpFocusNotifier, Redis-адаптеры, BotSettings
presentation/    PollingLoop, POST /webhook/max, POST /internal/v1/notify, /dev/max/*, тексты (templates.py)
main.py          composition root: createApp() и lifespan (клиенты, реестр задач, порядок остановки)
```

Границы слоёв проверяет `import-linter` (`make imports`).

## Эндпоинты

| Путь | Кто зовёт | Что делает |
|---|---|---|
| `POST /webhook/max` | MAX (через Caddy) | апдейт; секрет в `X-Max-Bot-Api-Secret` (чужой — 401), 200 сразу, обработка в фоне; тело ≤ 1 МБ |
| `POST /internal/v1/notify` | Scheduler D, сервисный токен | J4: ровно одно сообщение; 200 `delivered`/`duplicate`, 404 `chat_not_found`, 429, 503 (+ `Retry-After: 5` при параллельном повторе) |
| `GET /health/live`, `/health/ready` | compose, Caddy | live — 200; ready — Redis и `/health/live` агента (ASR и LLM не зовём) |
| `/dev/max/*` | разработчик, e2e, демо | только `BOT_MAX_MODE=fake`: `updates`, `voice`, `files/{id}`, `failures`, `subscriptions`, `sent` (с `trace_id`) |

## Как работает

- **Апдейты.** `polling` — цикл `GET /updates`, marker фиксируется после обработки страницы. `webhook` — при каждом
  старте `ensureSubscription(BOT_WEBHOOK_URL, BOT_WEBHOOK_SECRET)` (MAX отписывает после 8 ч без 200); битый или
  неподдержанный апдейт — тоже 200. Оба пути — в `UpdateDispatcher`: у пользователя своя очередь (≤ 50, строго по
  порядку), разные — параллельно, не больше `BOT_MAX_CONCURRENT_UPDATES`.
- **`HandleUpdate`.** Дубль (`va:v1:bot:upd`, 24 ч) пропускается; сверх 20 в минуту (`va:v1:bot:rl`) — «Слишком часто».
  Текст → агент `/v1/messages` под «печатает…» (сразу и раз в 4 с). Кнопка: payload — ключ `va:v1:bot:cb:{key}`
  (24 ч, привязан к чату), за ним действие → `/v1/actions`; истёкший или чужой — «Кнопка устарела». Команды
  `/start`, «Начать», `/help`, `/today`, `/tomorrow`, `/week`, `/settings` — действия агента. Ответ — всегда новым
  сообщением. Любой сбой самого бота (например, Redis недоступен) — «Что-то пошло не так… Код», не тишина.
- **Голосовое.** Скачать по ссылке сразу (лимит `BOT_VOICE_MAX_BYTES`, у настоящего MAX — только https) → ASR
  `/v1/transcribe` (J1, 15 с, 2 повтора с ключом от хеша аудио) → агенту текст с `source=voice`; «Распознал: …» рисует
  агент. `no_speech`, «слишком длинное», сбой ASR — тексты бота с кодом.
- **Уведомления J4.** `DeliverNotification`: «доставлено» (`va:v1:bot:notif`, 24 ч) → `duplicate`; «в работе»
  (`va:v1:bot:notif_lock`, `SET NX`, 60 с); «доставлено» пишется только после ответа MAX. Кнопки «Выполнено» (не у
  встречи), «Отложить на 1 ч», «Завтра», «В календаре» (`item_<id>`). После первой доставки — фоном
  `POST /v1/focus` агенту: «перенеси его…» относится к этой записи.
- **`ReplyToMaxMapper`.** `action` → callback-кнопка, `open_app` → кнопка мини-приложения (`web_app = BOT_USERNAME`,
  `payload = start_param`) или ссылка `https://max.ru/<бот>?startapp=…` (`BOT_OPEN_APP_MODE=link`).
- **Остановка.** SIGTERM: новые апдейты не берём, принятые дорабатываем (до `BOT_SHUTDOWN_TIMEOUT_S`), недоработанные —
  в лог `shutdown_tasks_cancelled` (имена задач — без id MAX). Бюджет укладывается в `stop_grace_period` compose.

## Переменные

Таблица генерируется из `BotSettings` (`make env-docs`), тест следит, чтобы она не расходилась с кодом.

<!-- env:begin -->
| Переменная | По умолчанию | Зачем |
|---|---|---|
| `BOT_MAX_MODE` | `fake` | `fake` — MAX в памяти и `/dev/max/*`; `real` — пока ошибка на старте |
| `BOT_POLL_TIMEOUT_S` | `30` | таймаут long polling, с (0–90) |
| `BOT_RATE_LIMIT_PER_MIN` | `20` | сообщений пользователя в минуту, сверх — «Слишком часто» |
| `BOT_SHUTDOWN_TIMEOUT_S` | `10.0` | сколько ждать фоновые задачи на SIGTERM, с |
| `BOT_FAKE_SEND_DELAY_MS` | `0` | задержка «отправки» в фейковом MAX, мс (lifecycle-тесты) |
| `BOT_AGENT_URL` | `http://agent:8000` | адрес агента (API из `contracts/reply.py`) |
| `BOT_AGENT_TIMEOUT_S` | `45.0` | сколько ждать агента, с; повторов нет (стандарт) |
| `BOT_OPEN_APP_MODE` | `open_app` | кнопка мини-приложения: `open_app` или ссылка `link` (max.ru/<бот>?startapp=) |
| `BOT_USERNAME` | — | имя бота в MAX: `web_app` у open_app и адрес deep link |
| `BOT_ASR_URL` | `http://asr:8000` | адрес ASR (J1) |
| `BOT_ASR_TIMEOUT_S` | `15.0` | таймаут ASR, с; 2 повтора с ключом от хеша аудио |
| `BOT_VOICE_MAX_BYTES` | `2097152` | лимит голосового, байт; больше — «слишком длинное» |
| `BOT_VOICE_DOWNLOAD_TIMEOUT_S` | `10.0` | таймаут скачивания голосового по ссылке MAX, с |
| `BOT_FAKE_FILES_URL` | `http://127.0.0.1:8000` | откуда бот скачивает голосовые фейкового MAX (`/dev/max/files`) — сам у себя |
| `BOT_TYPING_INTERVAL_S` | `4.0` | как часто повторять «печатает…», с |
| `BOT_UPDATES_MODE` | `polling` | `polling` (GET /updates) или `webhook` (POST /webhook/max за Caddy) |
| `BOT_WEBHOOK_URL` | — | адрес подписки webhook; у настоящего MAX — только https:// на 443 |
| `BOT_WEBHOOK_SECRET` | — | секрет подписки (5–256 символов a-zA-Z0-9_-), MAX шлёт в X-Max-Bot-Api-Secret |
| `BOT_MAX_CONCURRENT_UPDATES` | `16` | апдейтов одновременно (у одного пользователя — по очереди) |
| `DATABASE_URL` | — | общая: PostgreSQL (зона C не читает — для D) |
| `REDIS_URL` | `redis://localhost:6379/0` | общая: Redis (ключи — `docs/redis-keys.md`) |
| `SERVICE_TOKEN` | обязательно | общая: сервисный токен внутренних вызовов (≥ 16 символов; на стенде с HTTPS ≥ 32) |
| `LOG_LEVEL` | `INFO` | общая: уровень логов: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `console` | общая: `console` или `json` |
| `DEFAULT_TZ` | `Europe/Moscow` | общая: пояс по умолчанию (IANA) |
<!-- env:end -->

## Ключи Redis

`va:v1:bot:upd:{update_id}` (24 ч), `va:v1:bot:rl:{max_user_id}` (60 с), `va:v1:bot:cb:{key}` (24 ч),
`va:v1:bot:notif:{notification_id}` (24 ч), `va:v1:bot:notif_lock:{notification_id}` (60 с),
`va:v1:bot:marker:poll` (24 ч, только `real`). Реестр, замер памяти и проверки — `docs/redis-keys.md`.

## Запустить локально

Проще всего — весь стенд: `make up`, затем `make smoke`, `make e2e`, `make demo`. Руками — нужен запущенный агент:

```bash
docker run -d --rm -p 6379:6379 redis:7.4-alpine
export SERVICE_TOKEN=$(openssl rand -hex 16)
REDIS_URL=redis://localhost:6379/0 BOT_AGENT_URL=http://127.0.0.1:8002 \
  uv run uvicorn bot.main:createApp --factory --port 8001

curl -s -X POST localhost:8001/dev/max/updates -H 'content-type: application/json' \
  -d @services/bot/tests/fixtures/max_updates/text.json     # апдейт в формате MAX
curl -s -X POST localhost:8001/dev/max/voice -H 'content-type: application/json' \
  -d '{"max_user_id": 1, "max_chat_id": 1, "text": "что у меня завтра"}'   # «голосовое»
curl -s localhost:8001/dev/max/sent                          # что бот отправил (текст, кнопки, trace_id)
```

Голосовое в фейке — «OggS» + текст; ASR-заглушка возвращает этот текст (`""` — no_speech, `~low~ …` — неуверенно,
`~long~` — слишком длинное, `~down~` — ASR недоступен). Отказы MAX — `POST /dev/max/failures {chat_id, reason, times}`.
Уведомления — `contracts/examples/notify/notify.sh once|repeat|parallel|chat-not-found|all`.

## Тесты

```bash
uv run pytest services/bot                    # unit: HandleUpdate, маппер, лимиты клавиатуры, лимит 30 rps, тексты
uv run pytest services/bot -m integration     # Redis в testcontainers: ключи и TTL
uv run pytest services/bot -m lifecycle       # uvicorn + SIGTERM: polling, webhook, голос, J4; старт без соседей
```

Фикстуры апдейтов (`tests/fixtures/max_updates`) синтетические, по OpenAPI-схеме MAX — заменить живыми, когда
будет токен.
