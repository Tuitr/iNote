# services/bot — бот MAX (зона C)

Единственный, кто разговаривает с MAX. С S1 — тонкий клиент агента на **фейковом MAX**: доступа к API MAX
пока нет, а реальный адаптер (`maxapi` или своя обёртка над `platform-api2.max.ru`) — открытое решение.

## Слои

```
domain/          IncomingUpdate, UpdateKind, VoiceRef, UpdateBatch, OutgoingMessage (4000 символов, клавиатура)
application/     порты MaxGateway, AgentGateway, CallbackStore, …; HandleUpdate, ReplyToMaxMapper, TypingIndicator
infrastructure/  FakeMaxGateway, MAX → IncomingUpdate, OutgoingMessage → NewMessageBody, HttpAgentGateway,
                 Redis: дедуп, лимит, кнопки; BotSettings
presentation/    PollingLoop (long polling), /dev/max/*, тексты (templates.py)
main.py          composition root: createApp() и lifespan
```

`HandleUpdate`: дубль пропускается, сверх лимита (20 в минуту) — «Слишком часто». Текст → агент
`/v1/messages` (с именем и языком из апдейта), пока ждём — «печатает…» (`typing_on` сразу и раз в 4 с).
Кнопка: payload — ключ `va:v1:bot:cb:{key}` (24 ч), за ним действие → `/v1/actions`; спиннер MAX гасится сразу,
истёкший или чужой payload — «Кнопка устарела»; ответ агента — **новым** сообщением (старые не правим).
Команды `/start` и «Начать» → действие `start`, `/help` → `help`, `/today`, `/tomorrow`, `/week` → `list_period`
— всё через `/v1/actions`, тексты приветствия и справки у агента; незнакомая команда — подсказка бота.
Повтор того же нажатия отсекает дедуп апдейтов. Голосовое (S2): «печатает…» сразу → скачать по ссылке сразу (лимит `BOT_VOICE_MAX_BYTES`)
→ ASR `/v1/transcribe` (J1, 15 с, 2 повтора) → агенту текст с `source=voice` и флагами ASR; «Распознал: …» рисует
агент. `no_speech`, «слишком длинное», прочие сбои ASR — тексты бота с кодом, агент не вызывается. Агент не ответил (45 с, без повторов) — «Не получилось
связаться с ассистентом… Код: <6 символов trace_id>».
Health: `/health/live` — 200; `/health/ready` — Redis и `/health/live` агента (без агента бот не готов).
`ReplyToMaxMapper`: `Reply` агента → сообщение MAX: `action` → callback-кнопка, `open_app` → кнопка open_app
(`web_app = BOT_USERNAME`, `payload = start_param`) или ссылка `https://max.ru/<бот>?startapp=…`
(`BOT_OPEN_APP_MODE=link`); ряды режутся по лимитам MAX (7 кнопок, 3 ссылки или open_app, 30 рядов).
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
| `BOT_AGENT_URL` / `BOT_AGENT_TIMEOUT_S` | `http://agent:8000` / `45` | агент; повторов нет (стандарт) |
| `BOT_OPEN_APP_MODE` | `open_app` | кнопка мини-приложения: `open_app` или `link` |
| `BOT_USERNAME` | — | имя бота в MAX: `web_app` и адрес deep link |
| `BOT_TYPING_INTERVAL_S` | `4` | как часто повторять «печатает…» |

Плюс общие: `REDIS_URL`, `SERVICE_TOKEN`, `LOG_LEVEL`, `LOG_FORMAT`.

## Запустить локально

Проще всего — весь стенд: `make up`, затем `make smoke`. Руками — нужен запущенный агент (см. его README):

```bash
docker run -d --rm -p 6379:6379 redis:7.4-alpine
export SERVICE_TOKEN=$(openssl rand -hex 16)
REDIS_URL=redis://localhost:6379/0 BOT_AGENT_URL=http://127.0.0.1:8002 \
  uv run uvicorn bot.main:createApp --factory --port 8001

# подложить апдейт (формат MAX) и посмотреть ответ бота
curl -s -X POST localhost:8001/dev/max/updates \
  -H 'content-type: application/json' \
  -d @services/bot/tests/fixtures/max_updates/text.json
curl -s localhost:8001/dev/max/sent
# {"messages":[{"message_id":"fake-mid-1","chat_id":500600700,"text":"Напоминание создано\n…","format":"markdown",
#   "attachments":[{"type":"inline_keyboard","payload":{"buttons":[[…]]}}]}],"callback_answers":[],"typing":[500600700]}
```

## Тесты

```bash
uv run pytest services/bot                    # unit
uv run pytest services/bot -m integration     # Redis в testcontainers: дедуп, лимит, кнопки, TTL
uv run pytest services/bot -m lifecycle       # uvicorn + SIGTERM посреди обработки
```

Фикстуры апдейтов (`tests/fixtures/max_updates`) синтетические, собраны по OpenAPI-схеме MAX.
Заменить живыми, как только будет токен (подробнее — README там же).
