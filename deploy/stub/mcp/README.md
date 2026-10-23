# MCP-заглушка (временно, зона C)

> **Временно.** Нужна, чтобы цепочка S1 «бот → агент → MCP» шла на стенде до готовой заглушки D.
> После мержа заменить на заглушку D (ответы из `contracts/examples`), затем на `build: services/mcp`,
> а эту папку удалить. Владелец настоящего сервиса — D.

Что умеет:

- Streamable HTTP на `/mcp` (официальный SDK `mcp` 2.3, low-level `Server`), `/health/live` и `/health/ready`;
- восемь инструментов D — схемы в `fixtures/tools.json`: **копия `contracts/mcp/tools.json` D из `main`**
  (`caller` там уже есть через `$ref`): `create_task`, `create_event`, `list_items`, `search_items`, `update_item`,
  `complete_item`, `delete_item`, `check_conflicts`;
- записи — в форме `Item` у D (`notes`, `remind_at`, `rrule`, `participants`, `source_text`, `created_at`, `updated_at`):
  у напоминания время в `remind_at`, `starts_at = null`; у встречи `remind_at` = начало − 15 мин;
- ответы по формам плана: изменяющие → `{status, item, conflicts, free_slots}`, `list_items` →
  `{items, range, truncated}`, `search_items` → `{results: [{item, score}], best_match_confident}`,
  ошибка → `isError` + конверт `{"error": {code, message, details}}` в `structuredContent`;
- записи — из `fixtures/items.json`, день считается от `from` (для списка) или от «сегодня» пользователя (для поиска);
- `list_items` — `from`/`to` **датами** `[from, to)`, отбор по `starts_at` или `remind_at`, `status` open (по умолчанию) | done | all;
- `create_task`: напоминанию нужен `remind_at`, срок задачи — `starts_at` датой (весь день) или моментом;
- `create_event` без `allow_conflict: true` проверяет пересечения с записями из фикстур и созданными: при конфликте —
  `status: conflict`, `item: null`, `conflicts` и до трёх `free_slots` той же длины в пределах 9:00–20:00;
- созданные записи хранятся в памяти по `user_id` и видны в `list_items` и `search_items`;
- `update_item` (`id` целиком или 8 символов, `starts_at`, `ends_at`, `remind_at`, `title`, `allow_conflict`):
  встреча сохраняет длительность и проверяет пересечения (кроме самой себя), время в прошлом — `past_time`;
  `complete_item {id, done}` — `completed`/`reopened`, уже в этом статусе — `unchanged` (встречу — `validation_error`);
  `delete_item {id, restore}` — мягкое удаление и возврат, повтор удаления — `not_found`;
  `check_conflicts {starts_at, ends_at, exclude_id}` — пересечения и свободные окна; правки — в памяти поверх фикстур;
- поиск — по началу слов (первые 4 буквы слова запроса длиной от 4): «звонок» найдёт «Позвонить»;
- время без смещения → `validation_error`, в прошлом → `past_time`, нет `caller` → `validation_error`;
- повтор с тем же `(user_id, idempotency_key)` возвращает тот же ответ (id записи — от ключа);
- J8 `POST /internal/v1/users/resolve`: `user_id` по `max_user_id` в памяти (с 17, повтор — тот же, `created=false`),
  пояс `Europe/Moscow`; с `SERVICE_TOKEN` в окружении требует `Authorization: Bearer` (иначе 401/403);
- J8 `PATCH /internal/v1/users/{user_id}/settings` (S3, черновик C — у D метода ещё нет): `{tz?, voice_replies?}` →
  пользователь как у resolve; пустое тело и пояс не IANA — 400, незнакомый `user_id` — 404; `resolve` дальше
  отдаёт сохранённое (в памяти процесса);
- J8 `POST /internal/v1/dialog-turns` (`input_type` у реплики пользователя — только `voice|text`, как у D): ход хранится в памяти по `trace_id`, повтор — 202 без дубля
  (`dialog_turns_stored` / `dialog_turns_duplicate` в логе, без текстов); под тем же токеном;
- в stdout — JSON-строка `tool_called` с полученным `caller` (без текста пользователя).

Запуск без Docker: `uv run python deploy/stub/mcp/server.py` (порт — `STUB_PORT`, по умолчанию 8000).
Тест агента по HTTP против заглушки: `uv run pytest services/agent/tests/integration/test_mcp_http.py`.
