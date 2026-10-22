"""Планы основных запросов на реалистичном объёме данных (шаг 31 S4).

Объём — 100 пользователей по 500 записей, уведомления и история диалога. Запросы берутся
не переписанными руками, а ровно те, что шлют репозитории: SQL ловится при вызове метода,
затем по нему строится EXPLAIN (ANALYZE, BUFFERS). Все изменения откатываются.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.db.models import ItemRow, UserRow
from libs.core.repositories.dialog import DialogRepo
from libs.core.repositories.items import ItemRepo, SearchFilter
from libs.core.repositories.notifications import NotificationRepo
from libs.core.repositories.users import UserRepo

USERS = 100
ITEMS_PER_USER = 500
TURNS_PER_USER = 200

_GENERATE = (
    """
    INSERT INTO users (max_user_id, max_chat_id, name, tz)
    SELECT 1000000 + g, 2000000 + g, 'Пользователь ' || g, 'Europe/Moscow'
    FROM generate_series(1, :users) AS g
    """,
    # Записи за ~2 месяца назад и вперёд: задачи, напоминания, встречи, часть выполнена,
    # часть удалена, редкие серии; заголовки из небольшого словаря, чтобы поиск что-то находил.
    """
    INSERT INTO items (id, user_id, kind, title, starts_at, ends_at, all_day, remind_at,
                       status, rrule, participants, idempotency_key, search_hints, deleted_at)
    SELECT gen_random_uuid(), u.id, kind, title, starts_at,
           CASE WHEN kind = 'event' THEN starts_at + interval '1 hour' END,
           false,
           CASE WHEN kind = 'task' THEN NULL ELSE starts_at - interval '15 minutes' END,
           CASE WHEN n % 7 = 0 THEN 'done' ELSE 'open' END,
           CASE WHEN n % 100 = 0 THEN 'FREQ=WEEKLY' END,
           CASE WHEN kind = 'event' THEN ARRAY['Иван Петров'] ELSE '{}'::text[] END,
           'perf:' || n,
           CASE WHEN n % 3 = 0 THEN 'звонок разговор созвон' END,
           CASE WHEN n % 50 = 0 THEN now() END
    FROM users u
    CROSS JOIN generate_series(1, :items) AS n
    CROSS JOIN LATERAL (
        SELECT (ARRAY['task', 'reminder', 'event'])[1 + n % 3] AS kind,
               (ARRAY['Позвонить', 'Встреча с', 'Купить подарок', 'Отчёт для', 'Написать'])
                   [1 + n % 5]
               || ' ' || (ARRAY['Ивану', 'Марине', 'Олегу', 'маме', 'банку', 'врачу'])
                   [1 + (n * 7) % 6]
               || ' ' || n AS title,
               CASE WHEN n % 10 = 0 THEN NULL
                    ELSE date_trunc('hour', now()) + (n - :items / 2) * interval '3 hours'
               END AS starts_at
    ) AS generated
    """,
    # Уведомления в будущем ждут, за последние 12 часов — созрели (их заберёт claim_due),
    # раньше — уже отправлены.
    """
    INSERT INTO notifications (id, user_id, item_id, fire_at, status, attempt, sent_at)
    SELECT gen_random_uuid(), user_id, id, remind_at,
           CASE WHEN remind_at > now() - interval '12 hours' THEN 'pending' ELSE 'sent' END,
           CASE WHEN remind_at > now() - interval '12 hours' THEN 0 ELSE 1 END,
           CASE WHEN remind_at > now() - interval '12 hours' THEN NULL ELSE remind_at END
    FROM items
    WHERE remind_at IS NOT NULL AND status = 'open' AND deleted_at IS NULL
    """,
    """
    INSERT INTO dialog_log (user_id, trace_id, turn_no, role, text, input_type)
    SELECT u.id, md5(u.id || ':' || (t / 2)), 1 + t % 2,
           CASE WHEN t % 2 = 0 THEN 'user' ELSE 'assistant' END,
           'Реплика ' || t, CASE WHEN t % 2 = 0 THEN 'voice' END
    FROM users u CROSS JOIN generate_series(0, :turns - 1) AS t
    """,
    "ANALYZE",
)


@dataclass(frozen=True)
class Plan:
    name: str
    sql: str
    plan: str
    # Время выполнения из EXPLAIN ANALYZE, мс.
    execution_ms: float


async def generate_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    # ANALYZE в конце: без свежей статистики планировщик гадает и планы не те, что в бою.
    async with session_factory() as session, session.begin():
        for statement in _GENERATE:
            await session.execute(
                text(statement), {"users": USERS, "items": ITEMS_PER_USER, "turns": TURNS_PER_USER}
            )


async def collect_plans(session_factory: async_sessionmaker[AsyncSession]) -> list[Plan]:
    """Планы всех основных запросов для «типичного» пользователя (средний id)."""
    async with session_factory() as session:
        user = await session.scalar(select(UserRow).order_by(UserRow.id).offset(USERS // 2))
        assert user is not None
        item = await session.scalar(
            select(ItemRow)
            .where(ItemRow.user_id == user.id, ItemRow.deleted_at.is_(None))
            .order_by(ItemRow.id)
            .offset(ITEMS_PER_USER // 2)
        )
        assert item is not None
    week_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    everything = SearchFilter(kinds=None, statuses=["open", "done"], start=None, end=None)
    uid = user.id

    queries: list[tuple[str, Callable[[AsyncSession], Awaitable[Any]]]] = [
        (
            "list_items / GET /v1/items за неделю",
            lambda s: ItemRepo(s).list_in_range(uid, week_start, week_end, None, None, 31),
        ),
        (
            "search: полнотекст",
            lambda s: ItemRepo(s).search_fulltext(uid, "позвонить Ивану", everything, 20),
        ),
        (
            "search: триграммы",
            lambda s: ItemRepo(s).search_trigram(uid, "Иван", everything, 0.3, 20),
        ),
        (
            "запись по короткому id (8 символов), изменение",
            lambda s: ItemRepo(s).find_by_ref(uid, str(item.id)[:8], False, 5, for_update=True),
        ),
        (
            "запись по короткому id, удалённые тоже (delete_item)",
            lambda s: ItemRepo(s).find_by_ref(uid, str(item.id)[:8], None, 5, for_update=True),
        ),
        (
            "карточка по полному id (GET /v1/items/{id})",
            lambda s: ItemRepo(s).find_by_ref(uid, str(item.id), False, 1, for_update=False),
        ),
        (
            "повтор вызова: idempotency_key",
            lambda s: ItemRepo(s).find_by_idempotency_key(uid, "perf:250"),
        ),
        (
            "конфликты встреч",
            lambda s: ItemRepo(s).find_events_overlapping(
                uid, week_start + timedelta(hours=15), week_start + timedelta(hours=16), None
            ),
        ),
        (
            "users/resolve: по max_user_id",
            lambda s: UserRepo(s).get_by_max_user_id(user.max_user_id),
        ),
        ("история диалога с курсором", lambda s: DialogRepo(s).list_before(uid, 10**9, 21)),
        (
            "уведомления записи: отмена при изменении",
            lambda s: NotificationRepo(s).cancel_pending(uid, item.id),
        ),
        ("Scheduler: claim_due", lambda s: NotificationRepo(s).claim_due(5, timedelta(minutes=5))),
        ("Scheduler: reclaim_expired", lambda s: NotificationRepo(s).reclaim_expired()),
        ("Scheduler: cancel_stale", lambda s: NotificationRepo(s).cancel_stale()),
        (
            "Scheduler: schedule_next (прошедшие серии)",
            lambda s: ItemRepo(s).lock_passed_series(50),
        ),
    ]
    return [await _explain(session_factory, name, call) for name, call in queries]


async def _explain(
    session_factory: async_sessionmaker[AsyncSession],
    name: str,
    call: Callable[[AsyncSession], Awaitable[Any]],
) -> Plan:
    captured: list[tuple[str, Any]] = []

    def remember(conn: Any, cursor: Any, statement: str, parameters: Any, *args: Any) -> None:
        captured.append((statement, parameters))

    engine = session_factory.kw["bind"].sync_engine
    async with session_factory() as session:
        await session.begin()
        event.listen(engine, "before_cursor_execute", remember)
        try:
            await call(session)
        finally:
            event.remove(engine, "before_cursor_execute", remember)
            await session.rollback()
    statement, parameters = captured[-1]

    async with session_factory() as session:
        await session.begin()
        connection = await session.connection()
        result = await connection.exec_driver_sql(
            f"EXPLAIN (ANALYZE, BUFFERS) {statement}", parameters
        )
        lines = [row[0] for row in result]
        await session.rollback()
    execution = next(line for line in lines if line.startswith("Execution Time"))
    return Plan(
        name=name,
        sql=statement,
        plan="\n".join(lines),
        execution_ms=float(execution.split(":")[1].split()[0]),
    )
