# Планы основных запросов зоны D (шаг 31 S4)

Как снято: тестовый PostgreSQL 16 (testcontainers), 100 пользователей × 500 записей (50 000),
≈25 700 уведомлений (будущие ждут, за последние 12 часов созрели, старые отправлены), 20 000 реплик
истории, затем `ANALYZE`. Генератор и сбор планов — `libs/core/tests/query_plans.py`. SQL не
переписан руками: ловится ровно тот, что шлют репозитории, и прогоняется через
`EXPLAIN (ANALYZE, BUFFERS)` в откатываемой транзакции. Пользователь — средний по id.
Тест `libs/core/tests/test_query_plans.py` держит итог: запросы одного пользователя без
`Seq Scan on items`, у Scheduler и истории — нужные индексы.

Дата: 2026-10-22. «До» — схема на миграции `0004`, «после» — с миграцией `0005_query_indexes`.

## Итог

| Запрос | До | После | Время, мс (до → после) |
| --- | --- | --- | --- |
| list_items / GET /v1/items за неделю | Bitmap Index Scan on ix_items_user_id_starts_at | без изменений | 0.52 → 0.56 |
| search: полнотекст | Bitmap Index Scan on ix_items_user_id_starts_at | без изменений | 0.84 → 0.84 |
| search: триграммы | Bitmap Index Scan on ix_items_user_id_starts_at | без изменений | 2.93 → 2.99 |
| запись по короткому id (8 символов), изменение | Bitmap Index Scan on ix_items_user_id_starts_at | без изменений | 0.57 → 0.55 |
| запись по короткому id, удалённые тоже (delete_item) | Bitmap Index Scan on uq_items_user_id_idempotency_key | без изменений | 0.52 → 0.44 |
| карточка по полному id (GET /v1/items/{id}) | Index Scan using pk_items | без изменений | 0.03 → 0.03 |
| повтор вызова: idempotency_key | Index Scan using uq_items_user_id_idempotency_key | без изменений | 0.03 → 0.02 |
| конфликты встреч | Bitmap Index Scan on ix_items_user_id_starts_at | без изменений | 0.27 → 0.26 |
| users/resolve: по max_user_id | Seq Scan on users (100 строк — планировщику так дешевле) | без изменений | 0.02 → 0.02 |
| **история диалога с курсором** | Index Scan Backward using pk_dialog_log, фильтр по user_id отбросил 2 029 чужих реплик | **Index Scan Backward using ix_dialog_log_user_id_id** | 0.14 → 0.03 |
| уведомления записи: отмена при изменении | Index Scan using uq_notifications_item_id_fire_at | без изменений | 0.02 → 0.02 |
| Scheduler: claim_due | Index Scan using ix_notifications_fire_at + Seq Scan on items (hash join) | без изменений, см. ниже | 8.8 → 9.4 |
| **Scheduler: reclaim_expired** | Seq Scan on notifications (25 700 строк ради 0) | **Index Scan using ix_notifications_lease_until** | 1.41 → 0.02 |
| Scheduler: cancel_stale | Index Scan using ix_notifications_fire_at + Seq Scan on items (hash join) | без изменений, см. ниже | 8.4 → 10.7 |
| **Scheduler: schedule_next (прошедшие серии)** | Seq Scan on items (50 000 строк ради открытых серий) | **Bitmap Index Scan on ix_items_open_series** | 5.53 → 0.03 |

Ни один запрос одного пользователя не перебирает таблицу `items`: все идут через индекс с `user_id`
впереди. Время колеблется на десятые доли миллисекунды между прогонами — это шум.

## Миграция 0005_query_indexes (только добавляет)

| Индекс | Зачем |
| --- | --- |
| `ix_dialog_log_user_id_id` на `dialog_log (user_id, id)` | Страница истории от новых к старым: раньше шли по всему pk и отбрасывали чужие реплики, чем старше история пользователя — тем дольше |
| `ix_notifications_lease_until` на `notifications (lease_until) WHERE status = 'sending'` | `reclaim_expired` каждые 15 с: в аренде единицы уведомлений, а таблица — вся история отправок |
| `ix_items_open_series` на `items (id) WHERE rrule IS NOT NULL AND deleted_at IS NULL AND status = 'open'` | `schedule_next` каждые 15 с обходит открытые серии всех пользователей; их единицы на тысячи записей |

Накатывается на существующую базу (проверено на локальном стенде) и с нуля (тест миграций `base → head → base → head` с `alembic check`).

## Что оставлено и почему

- **claim_due и cancel_stale: Seq Scan on items в hash join (~9 мс на 50 000 записей).** Уведомления
  выбираются по частичному индексу `ix_notifications_fire_at` (как и требуется), но планировщик
  ждёт ~6 500 созревших вместо реальных 200: он считает «pending» и «fire_at ≤ now()» независимыми,
  а на деле ждущие почти все в будущем, отправленные — в прошлом. При такой оценке хэш по всей
  `items` ему дешевле, чем 6 500 поисков по pk. Время растёт линейно с таблицей: ~90 мс на 500 000
  записей раз в 15 с — для хакатона приемлемо. Если записей станет много, переписать выборку так,
  чтобы сначала брались id созревших уведомлений (их мало), а записи проверялись поиском по pk.
- **Триграммы — 3 мс.** `word_similarity(...) >= 0.3` — функция, GIN-индекс `ix_items_search_text_trgm`
  ею не используется; записи пользователя (≈500) перебираются после индекса по `user_id`. Оператор
  `<%` взял бы индекс, но порог у него задаётся настройкой сеанса (`pg_trgm.word_similarity_threshold`,
  по умолчанию 0,6), а не 0,3, как в поиске сейчас. Пока у пользователя сотни записей — не нужно.
- **list_items за неделю** читает все ≈500 записей пользователя через `ix_items_user_id_starts_at`
  и фильтрует по `coalesce(starts_at, remind_at)`. Индекс по выражению `(user_id, coalesce(...))`
  сократил бы чтение до недели, но 0,5 мс этого не требуют.
- **Короткий id** ищется `id::text LIKE 'xxxxxxxx%'` по записям пользователя — так же ≈500 строк, 0,5 мс.
- **users по max_user_id** — Seq Scan по 100 строкам: на такой таблице это дешевле индекса,
  unique-индекс `uq_users_max_user_id` есть и будет взят, когда пользователей станет много.
