# services/agent — Agent Back (зона C)

В S0 — **заглушка**: на `POST /v1/messages` и `POST /v1/actions` отвечает фиксированным `Reply`
из `contracts/examples/reply/` (по умолчанию `confirm.json` и `list.json`), подставляя текущий `trace_id`.
Brain, MCP, Redis-контекст, лок пользователя и `caller` — S1.

- Вход только с сервисным токеном: `Authorization: Bearer $SERVICE_TOKEN` (нет — 401, неверный — 403).
- `X-Trace-Id` из запроса бота сохраняется и попадает в логи и в `Reply.trace_id`.
- Пример ответа не прошёл проверку моделью `Reply` — агент не стартует (лучше упасть на старте, чем отдать боту кривой ответ).
- Слои: `application` (порт `ReplySource`, use cases `HandleMessage`, `HandleAction`) ← `infrastructure`
  (`ExampleReplySource`, `AgentSettings`), `presentation` (роуты); `main.py` — composition root.

| Переменная | По умолчанию |
|---|---|
| `AGENT_STUB_MESSAGE_REPLY` | `contracts/examples/reply/confirm.json` |
| `AGENT_STUB_ACTION_REPLY` | `contracts/examples/reply/list.json` |

```bash
export SERVICE_TOKEN=$(openssl rand -hex 16)   # токен не пишем в файлы и команды
uv run uvicorn agent.main:createApp --factory --port 8002 &
curl -s localhost:8002/v1/messages -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H 'content-type: application/json' -d @contracts/examples/agent/message_request.json
```

Тесты: `uv run pytest services/agent` (unit), `-m lifecycle` (uvicorn + SIGTERM).
