# services/agent — Agent Back (зона C)

S1 (в работе): `POST /v1/messages` проходит настоящий ход — пользователь → лок → контекст в Redis →
Brain → инструменты MCP → ответ. `POST /v1/actions` пока отдаёт фиксированный `Reply` из
`contracts/examples/reply/list.json` (кнопки без LLM — S2).

- **Ход** (`application/handle_message.py`): лок на пользователя `va:v1:agent:lock:{user_id}` (60 с), бюджет
  хода 40 с вместе с ожиданием лока, любой сбой → `Reply` с кодом и `trace_id`. Brain получает `TurnInput`
  без `user_id`; «готово» без успешного изменяющего вызова → `llm_no_action` (правило 3). История (10 реплик),
  фокус и `pending` — в Redis с TTL (`docs/redis-keys.md`).
- **Brain**: `AGENT_BRAIN=scripted` — `ScriptedBrain` на правилах («что у меня сегодня/завтра/на неделе»,
  «напомни завтра в 10 …», «создай задачу …», «найди …»); `llm` — Brain участника B после мержа.
- **MCP** (`infrastructure/mcp_connection.py`, `mcp_executor.py`): одна сессия Streamable HTTP на процесс,
  переподключение при обрыве. `caller` из ответа модели выбрасывается, агент подставляет свой: `user_id`,
  `tz`, `trace_id`, `idempotency_key = <trace_id>:<n>`. Таймаут 5 с и один повтор с тем же ключом;
  `isError` → `ok=false` и код из конверта. Схемы для LLM — без `caller`.
- **Пользователь** (`infrastructure/http_user_resolver.py`): J8 `POST /internal/v1/users/resolve` в mcp-контейнере
  (5 с, 1 повтор с ключом `user-resolve:{max_user_id}`), кеш `va:v1:agent:user:{max_user_id}` на 1 ч.
  J8 недоступен — пользователь получает ответ с кодом ошибки. Readiness: Redis и `/health/live` mcp-контейнера.
- **Ответ** (`presentation/reply_renderer.py`, тексты — `presentation/templates/ru.toml`): подтверждения, списки
  и поиск строятся из `actions[].result.data`, а не из текста модели (правило 3); даты по-русски в поясе
  пользователя («Завтра, чт 22 октября, 10:00», год — только у чужого года); названия экранируются под
  markdown; текст ≤ 4000 символов, хвост списка — «…и ещё N». Свободные ответы и вопросы — текстом модели,
  без разметки. Голосовое (`source=voice`) — первой строкой «Распознал: «…»» (экранировано в markdown). У каждого кода ошибки свой текст + «Код: <6 символов trace_id>». Шаблоны проверяются
  при старте: пропущенный текст ошибки или лишний ключ — агент не стартует.
- Кнопки ответа (`undo`, `reschedule`, `list_period`, `clarify`) обрабатывает `/v1/actions` — с S2; сейчас там
  заглушка. На стенде пока MCP-заглушка C из `deploy/stub/mcp` вместо заглушки D.
- Вход только с сервисным токеном: `Authorization: Bearer $SERVICE_TOKEN` (нет — 401, неверный — 403).
- Слои: `domain` ← `application` (порты, ход) ← `infrastructure` (Redis, MCP, ScriptedBrain, настройки),
  `presentation` (роуты, рендер); `main.py` — composition root, всё создаётся в `lifespan`.

| Переменная | По умолчанию |
|---|---|
| `AGENT_BRAIN` | `scripted` |
| `AGENT_TURN_TIMEOUT_S` / `AGENT_LOCK_WAIT_S` | `40` / `10` |
| `AGENT_MCP_URL` / `AGENT_MCP_TIMEOUT_S` | `http://mcp:8000/mcp` / `5` |
| `AGENT_MCP_INTERNAL_URL` | `http://mcp:8000` |
| `AGENT_STUB_ACTION_REPLY` | `contracts/examples/reply/list.json` |

```bash
export SERVICE_TOKEN=$(openssl rand -hex 16)   # токен не пишем в файлы и команды
STUB_PORT=8010 uv run python deploy/stub/mcp/server.py &
AGENT_MCP_URL=http://127.0.0.1:8010/mcp AGENT_MCP_INTERNAL_URL=http://127.0.0.1:8010 \
  uv run uvicorn agent.main:createApp --factory --port 8002 &
curl -s localhost:8002/v1/messages -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'content-type: application/json' -d '{"max_user_id": 1, "max_chat_id": 1, "text": "что у меня сегодня"}'
```

Тесты: `uv run pytest services/agent` (unit), `-m integration` (Redis в testcontainers, MCP по HTTP
против заглушки), `-m lifecycle` (uvicorn + SIGTERM).
