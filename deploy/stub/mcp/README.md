# MCP-заглушка (временно, зона C)

> **Временно.** Нужна, чтобы цепочка S1 «бот → агент → MCP» шла на стенде до готовой заглушки D.
> После мержа заменить на заглушку D (ответы из `contracts/examples`), затем на `build: services/mcp`,
> а эту папку удалить. Владелец настоящего сервиса — D.

Что умеет:

- Streamable HTTP на `/mcp` (официальный SDK `mcp` 2.3, low-level `Server`), `/health/live` и `/health/ready`;
- четыре обязательных инструмента и `update_item`, `delete_item` — схемы в `fixtures/tools.json`, в каждую добавляется обязательный `caller`
  (`user_id`, `tz`, `trace_id`, `idempotency_key`), как у D;
- ответы по формам плана: изменяющие → `{status, item, conflicts, free_slots}`, `list_items` →
  `{items, range, truncated}`, `search_items` → `{results: [{item, score}], best_match_confident}`,
  ошибка → `isError` + конверт `{"error": {code, message, details}}` в `structuredContent`;
- записи — из `fixtures/items.json`, день считается от `from` (для списка) или от «сегодня» пользователя (для поиска);
- `create_event` без `force: true` проверяет пересечения с записями из фикстур и созданными: при конфликте —
  `status: conflict`, `item: null`, `conflicts` и до трёх `free_slots` той же длины в пределах 9:00–20:00;
- созданные записи хранятся в памяти по `user_id` и видны в `list_items` и `search_items`;
- `update_item` (`id` целиком или 8 символов, `starts_at`, `ends_at`, `title`): встреча сохраняет длительность,
  время в прошлом — `past_time`; `delete_item` — мягкое удаление, повтор по удалённой — `not_found`
  (правки — в памяти поверх фикстур, без проверки пересечений);
- поиск — по началу слов (первые 4 буквы слова запроса длиной от 4): «звонок» найдёт «Позвонить»;
- время без смещения → `validation_error`, в прошлом → `past_time`, нет `caller` → `validation_error`;
- повтор с тем же `(user_id, idempotency_key)` возвращает тот же ответ (id записи — от ключа);
- J8 `POST /internal/v1/users/resolve`: `user_id` по `max_user_id` в памяти (с 17, повтор — тот же, `created=false`),
  пояс `Europe/Moscow`; с `SERVICE_TOKEN` в окружении требует `Authorization: Bearer` (иначе 401/403);
- J8 `POST /internal/v1/dialog-turns`: ход хранится в памяти по `trace_id`, повтор — 202 без дубля
  (`dialog_turns_stored` / `dialog_turns_duplicate` в логе, без текстов); под тем же токеном;
- в stdout — JSON-строка `tool_called` с полученным `caller` (без текста пользователя).

Запуск без Docker: `uv run python deploy/stub/mcp/server.py` (порт — `STUB_PORT`, по умолчанию 8000).
Тест агента по HTTP против заглушки: `uv run pytest services/agent/tests/integration/test_mcp_http.py`.
