# services/agent — Agent Back (зона C)

Ход ассистента для бота: `POST /v1/messages` — фраза пользователя (текст или распознанный голос) → Brain →
инструменты MCP → ответ по шаблонам; `POST /v1/actions` — кнопки и команды без LLM в том же каркасе;
`POST /v1/focus` — запись из уведомления становится фокусом диалога. Агент знает внутреннего пользователя
(J8), держит лок, контекст и `pending` в Redis, сам заполняет `caller` для MCP и строит подтверждения из того,
что реально сохранено. Не ходит в PostgreSQL и не знает формата MAX (отдаёт абстрактный `Reply`).

Brain сейчас — `ScriptedBrain` (правила, `AGENT_BRAIN=scripted`); LLM-Brain участника B подключается после
мержа (`AGENT_BRAIN=llm`). MCP и J8 на стенде — временная заглушка C (`deploy/stub/mcp`). Голосового ответа нет.

## Слои и порты

```
domain/          UserContext, Caller, периоды (сегодня/завтра/неделя с понедельника), белый список поясов
application/     порты: UserResolver, UserLock, DialogContextStore, Clock, ToolExecutorFactory, ReplyRenderer,
                 DialogLog, SettingsWriter; Brain и ToolExecutor — из contracts/agent_side.py (J2)
                 TurnPipeline (лок → контекст → шаг → контекст), HandleMessage, HandleAction, HandleFocus,
                 ReminderActions, SettingsActions, правила хода (llm_no_action), item_time, snooze
infrastructure/  McpConnection и McpToolExecutor (J3), HttpUserResolver, HttpSettingsWriter, HttpDialogLogWriter
                 (J8), Redis: лок и контекст, ScriptedBrain, AgentSettings
presentation/    роуты /v1/*, TemplateReplyRenderer, шаблоны templates/ru.toml (проверка при старте)
main.py          composition root: createApp() и lifespan
```

## Эндпоинты

| Путь | Кто зовёт | Что делает |
|---|---|---|
| `POST /v1/messages` | бот, сервисный токен | `MessageRequest` → ход Brain → `Reply` (200 всегда; сбой — `state=error` с кодом) |
| `POST /v1/actions` | бот | `ActionRequest` → действие из каталога без LLM → `Reply`; каталог — `docs/standards.md` |
| `POST /v1/focus` | бот после уведомления | `FocusRequest` → фокус = запись; 204, лок занят — 503 |
| `GET /health/live`, `/health/ready` | compose | ready — Redis и `/health/live` mcp-контейнера (Brain и LLM не зовём) |

Вход только с `Authorization: Bearer $SERVICE_TOKEN` (нет — 401, неверный — 403). Контракты — `contracts/reply.py`,
`contracts/focus.py`, сторона C в `contracts/agent_side.py`; примеры — `contracts/examples/{agent,reply,actions}`.

## Как работает

- **Ход** (`application/turn_pipeline.py`, шаги — `handle_message.py` и `handle_action.py`): лок на пользователя `va:v1:agent:lock:{user_id}` (60 с), бюджет
  хода 40 с вместе с ожиданием лока, любой сбой → `Reply` с кодом и `trace_id`. Brain получает `TurnInput`
  без `user_id`; «готово» без успешного изменяющего вызова → `llm_no_action` (правило 3). История (10 реплик),
  фокус и `pending` — в Redis с TTL (`docs/redis-keys.md`).
- **Brain**: `AGENT_BRAIN=scripted` — `ScriptedBrain` на правилах («что у меня сегодня/завтра/на неделе»,
  «напомни завтра в 10 …», «напомни через N минут …», «создай задачу …», «встреча …», «найди …», «перенеси его …»);
  `llm` — Brain участника B после мержа.
- **MCP** (`infrastructure/mcp_connection.py`, `mcp_executor.py`): одна сессия Streamable HTTP на процесс,
  переподключение при обрыве. `caller` из ответа модели выбрасывается, агент подставляет свой: `user_id`,
  `tz`, `trace_id`, `idempotency_key = <trace_id>:<n>`. Таймаут 5 с и один повтор с тем же ключом;
  `isError` → `ok=false` и код из конверта. Схемы для LLM — без `caller`.
- **Пользователь** (`infrastructure/http_user_resolver.py`): J8 `POST /internal/v1/users/resolve` в mcp-контейнере
  (5 с, 1 повтор с ключом `user-resolve:{max_user_id}`), кеш `va:v1:agent:user:{max_user_id}` на 1 ч.
  J8 недоступен — пользователь получает ответ с кодом ошибки. Readiness: Redis и `/health/live` mcp-контейнера.
- **Формы D** (`origin/main`): у напоминания время в `remind_at` (`starts_at = null`) — показываем и сортируем по
  `starts_at` или `remind_at`; `list_items` — даты `[from, to)`; перенос — `application/item_time.py`
  (напоминание → `remind_at`, иначе `starts_at`); в истории кнопка — `input_type=text` с подписью кнопки.
- **Ответ** (`presentation/reply_renderer.py`, тексты — `presentation/templates/ru.toml`): подтверждения, списки
  и поиск строятся из `actions[].result.data`, а не из текста модели (правило 3); даты по-русски в поясе
  пользователя («Завтра, чт 22 октября, 10:00», год — только у чужого года); названия экранируются под
  markdown; текст ≤ 4000 символов, хвост списка — «…и ещё N». Свободные ответы и вопросы — текстом модели,
  без разметки. Голосовое (`source=voice`) — первой строкой «Распознал: «…»» (экранировано в markdown). У каждого кода ошибки свой текст + «Код: <6 символов trace_id>». Шаблоны проверяются
  при старте: пропущенный текст ошибки или лишний ключ — агент не стартует.
- **Кнопки мини-приложения** (J6, только через `contracts/start_param.py`, функция `openApp` в
  `reply_renderer.py`): подтверждение, «Отложено», «На когда перенести?» и один результат поиска → `item_<uuid>`; несколько похожих
  в поиске — по кнопке с названием на каждую из первых трёх; список «сегодня» → `today`, другой день →
  `day_YYYY-MM-DD`, неделя → `week_<понедельник>` (даты в поясе пользователя); `start`/`help` → `today` и
  `dialog`. start_param не собрался — `warning start_param_invalid` без значения и кнопка `today`.
- **Настройки** (`/settings`, `application/settings_actions.py`): экран — текущий пояс и 12 поясов РФ кнопками
  `set_tz {tz}` (по 4 в ряд, текущий — «✓»; белый список — `domain/timezones.py`). Голосовых ответов в продукте
  нет (решение команды, S3). Запись — `SettingsWriter` (`infrastructure/http_user_settings.py`): J8
  `PATCH /internal/v1/users/{user_id}/settings {tz}` (черновик C; `user_id` — из resolve, не из кнопки; ключ
  `user-settings:<trace_id>`, 1 повтор) → сброс `va:v1:agent:user:{max_user_id}` → «Пояс: Екатеринбург (UTC+5)»
  по ответу D и кнопка «Сегодня». Пояс не из списка — «Не понял кнопку», J8 не зовём.
- **Фокус** (`POST /v1/focus`, `application/handle_focus.py`): фокус — записи последнего ответа; бот после
  доставленного уведомления ставит фокусом его запись (204, под локом; занят — 503). `ScriptedBrain`
  (`infrastructure/scripted_focus.py`): «перенеси его на завтра в 15», «сдвинь её на 2 часа позже»,
  «отметь выполненной», «удали её» — одна запись в фокусе → сразу по её `id`; несколько → «Какую запись …?»
  с вариантами; пусто → подсказка показать запись списком или поиском.
- **Уточнения**: `outcome=clarify` → вопрос, варианты кнопками `pick_option` (`args.choice` — номер, по 3 в ряд)
  и «Отмена» (`cancel`). В `va:v1:agent:pending:{user_id}` (10 мин) — pending Brain как есть и подписи
  вариантов. `pick_option` → подпись варианта становится текстом хода → `Brain.resume` под тем же локом;
  нет вопроса или номер вне списка — «Вопрос устарел»; `cancel` стирает pending. Ответ на вопрос текстом —
  обычный `/v1/messages`, pending уходит в `TurnInput`. `ScriptedBrain` уточняет время у «напомни …» без
  времени, слот при конфликте встреч и «правильно понял?» при `asr_low_confidence`.
- **Действия** (`application/handle_action.py`, каталог — `application/actions.py`):
  `undo` (`item_id`) → `delete_item`, повтор — «Уже отменено» (`not_found` — не ошибка);
  `reschedule` с `starts_at` → `update_item`, без него — «На когда перенести?» с кнопками «Через час»,
  «Завтра в <время записи>», «Завтра в 9:00» (без прошлого и без текущего времени записи) и фокусом на
  записи — тогда «завтра в 15» текстом разбирает Brain; `list_period` (`today|tomorrow|week`) →
  `list_items` с `[from, to)` в поясе пользователя; `start`, `help` — приветствие и справка из шаблонов
  с кнопками периода, «Мой день» (`open_app today`) и «История» (`open_app dialog`); `pick_option`, `cancel` — уточнения. Из `args`
  берутся только поля каталога (`user_id` из кнопки до MCP не доходит); незнакомое действие или
  `args` не по каталогу — `validation_error` в лог и «Не понял кнопку» без лока и вызовов.
  «Отменить» есть только у созданной записи, у удалённой кнопок нет.
- **Кнопки напоминаний и конфликта** (`application/reminder_actions.py`, S3): `complete` (`item_id`) →
  `complete_item`, повтор (`unchanged`) — «Уже отмечено выполненным»; `snooze` (`item_id`, `minutes` 1…1440 или
  `until=tomorrow`, `kind?`) → `update_item` с новым `remind_at` («на N минут» — от «сейчас», «Завтра» — время
  напоминания из фокуса или «сейчас», завтра; `application/snooze.py`) → «Напомню: …»; `confirm_conflict`
  (`title`, `starts_at`, `ends_at?`) → `create_event` с `allow_conflict: true`, только пока жив вопрос (иначе
  «Вопрос устарел»). Кнопку «Всё равно создать» рисует рендер рядом со свободными окнами — по аргументам того
  `create_event`, что вернул `conflict`. На стенде пока MCP-заглушка C из `deploy/stub/mcp` вместо заглушки D.
- **История диалога** (`infrastructure/http_dialog_log.py`, J8): после каждого хода и действия — фоновая задача
  `POST /internal/v1/dialog-turns` в mcp-контейнере (реплика пользователя с `input_type` text/voice — кнопка как text с подписью, ответ
  и `item_ids`, `latency_ms`). Ответ её не ждёт; ключ `dialog-turns:<trace_id>`, 2 повтора; сбой — только
  `warning` с `trace_id`. При SIGTERM агент дожидается начатых записей до закрытия HTTP-клиента.

## Переменные

Таблица генерируется из `AgentSettings` (`make env-docs`), тест следит, чтобы она не расходилась с кодом.

<!-- env:begin -->
| Переменная | По умолчанию | Зачем |
|---|---|---|
| `AGENT_BRAIN` | `scripted` | `scripted` — правила вместо LLM (mock, e2e); `llm` — Brain B (после мержа) |
| `AGENT_TURN_TIMEOUT_S` | `40.0` | бюджет хода вместе с ожиданием лока, с (меньше ожидания бота — 45 с) |
| `AGENT_LOCK_WAIT_S` | `10.0` | сколько ждать лок пользователя, с; не дождались — «Ещё обрабатываю…» |
| `AGENT_MCP_URL` | `http://mcp:8000/mcp` | MCP (J3), одна сессия Streamable HTTP на процесс |
| `AGENT_MCP_INTERNAL_URL` | `http://mcp:8000` | служебный REST mcp-контейнера (J8) и его /health/live |
| `AGENT_MCP_TIMEOUT_S` | `5.0` | таймаут вызова MCP и J8, с; 1 повтор с тем же ключом |
| `DATABASE_URL` | — | общая: PostgreSQL (зона C не читает — для D) |
| `REDIS_URL` | `redis://localhost:6379/0` | общая: Redis (ключи — `docs/redis-keys.md`) |
| `SERVICE_TOKEN` | обязательно | общая: сервисный токен внутренних вызовов (≥ 16 символов; на стенде с HTTPS ≥ 32) |
| `LOG_LEVEL` | `INFO` | общая: уровень логов: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `console` | общая: `console` или `json` |
| `DEFAULT_TZ` | `Europe/Moscow` | общая: пояс по умолчанию (IANA) |
<!-- env:end -->

## Ключи Redis

`va:v1:agent:lock:{user_id}` (60 с), `va:v1:agent:ctx:{user_id}` (2 ч), `va:v1:agent:focus:{user_id}` (2 ч),
`va:v1:agent:pending:{user_id}` (10 мин), `va:v1:agent:user:{max_user_id}` (1 ч). Реестр и замер памяти —
`docs/redis-keys.md`.

## Запустить локально

```bash
export SERVICE_TOKEN=$(openssl rand -hex 16)   # токен не пишем в файлы и команды
STUB_PORT=8010 uv run python deploy/stub/mcp/server.py &
AGENT_MCP_URL=http://127.0.0.1:8010/mcp AGENT_MCP_INTERNAL_URL=http://127.0.0.1:8010 \
  uv run uvicorn agent.main:createApp --factory --port 8002 &
curl -s localhost:8002/v1/messages -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'content-type: application/json' -d '{"max_user_id": 1, "max_chat_id": 1, "text": "что у меня сегодня"}'
```

## Тесты

```bash
uv run pytest services/agent                    # unit: ход, действия, рендер, шаблоны, пределы, ScriptedBrain
uv run pytest services/agent -m integration     # Redis в testcontainers; MCP и J8 по HTTP против заглушки
uv run pytest services/agent -m lifecycle       # uvicorn + SIGTERM посреди хода и вызова MCP; старт без соседей
```
