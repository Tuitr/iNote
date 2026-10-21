# MCP-заглушка (временно, зона C)

> **Временно.** Нужна, чтобы цепочка S1 «бот → агент → MCP» шла на стенде до готовой заглушки D.
> После мержа заменить на заглушку D (ответы из `contracts/examples`), затем на `build: services/mcp`,
> а эту папку удалить. Владелец настоящего сервиса — D.

Что умеет:

- Streamable HTTP на `/mcp` (официальный SDK `mcp` 2.3, low-level `Server`), `/health/live` и `/health/ready`;
- четыре обязательных инструмента — схемы в `fixtures/tools.json`, в каждую добавляется обязательный `caller`
  (`user_id`, `tz`, `trace_id`, `idempotency_key`), как у D;
- ответы по формам плана: изменяющие → `{status, item, conflicts, free_slots}`, `list_items` →
  `{items, range, truncated}`, `search_items` → `{results: [{item, score}], best_match_confident}`,
  ошибка → `isError` + конверт `{"error": {code, message, details}}` в `structuredContent`;
- записи — из `fixtures/items.json`, день считается от `from` (для списка) или от «сегодня» пользователя (для поиска);
- время без смещения → `validation_error`, в прошлом → `past_time`, нет `caller` → `validation_error`;
- повтор с тем же `(user_id, idempotency_key)` возвращает тот же ответ (id записи — от ключа);
- J8 `POST /internal/v1/users/resolve`: `user_id` по `max_user_id` в памяти (с 17, повтор — тот же, `created=false`),
  пояс `Europe/Moscow`; с `SERVICE_TOKEN` в окружении требует `Authorization: Bearer` (иначе 401/403);
- в stdout — JSON-строка `tool_called` с полученным `caller` (без текста пользователя).

Запуск без Docker: `uv run python deploy/stub/mcp/server.py` (порт — `STUB_PORT`, по умолчанию 8000).
Тест агента по HTTP против заглушки: `uv run pytest services/agent/tests/integration/test_mcp_http.py`.
