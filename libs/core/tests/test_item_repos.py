from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.db.models import ItemRow, NotificationRow, UserRow
from libs.core.repositories.items import ItemRepo, NewItem
from libs.core.repositories.notifications import NotificationRepo

pytestmark = pytest.mark.anyio

DAY_START = datetime(2026, 10, 21, 21, 0, tzinfo=UTC)  # 22 октября 00:00 по Москве
DAY_END = DAY_START + timedelta(days=1)
REMINDER = NewItem(
    kind="reminder",
    title="Позвонить Ивану",
    notes=None,
    starts_at=None,
    ends_at=None,
    all_day=False,
    remind_at=DAY_START + timedelta(hours=10),
    rrule=None,
    participants=[],
    source_text=None,
    idempotency_key=None,
    search_hints=None,
)


@pytest.fixture
async def users(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with session_factory() as session, session.begin():
        alice = UserRow(max_user_id=1, tz="Europe/Moscow")
        bob = UserRow(max_user_id=2, tz="Europe/Moscow")
        session.add_all([alice, bob])
        await session.flush()
        return alice.id, bob.id


async def _insert(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, item: NewItem
) -> ItemRow | None:
    async with session_factory() as session, session.begin():
        return await ItemRepo(session).insert_if_absent(user_id, item)


async def _list(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    kinds: list[str] | None = None,
    statuses: list[str] | None = None,
    limit: int = 30,
) -> list[str]:
    async with session_factory() as session:
        rows = await ItemRepo(session).list_in_range(
            user_id, DAY_START, DAY_END, kinds, statuses, limit
        )
    return [row.title for row in rows]


async def test_user_sees_only_own_items(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, bob = users
    await _insert(session_factory, alice, REMINDER)

    assert await _list(session_factory, alice) == ["Позвонить Ивану"]
    assert await _list(session_factory, bob) == []


async def test_same_idempotency_key_does_not_create_second_item(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users
    keyed = replace(REMINDER, idempotency_key="ab12:1")

    first = await _insert(session_factory, alice, keyed)
    second = await _insert(session_factory, alice, replace(keyed, title="Другое"))

    assert first is not None
    assert second is None
    async with session_factory() as session:
        found = await ItemRepo(session).find_by_idempotency_key(alice, "ab12:1")
    assert found is not None
    assert found.id == first.id
    assert await _list(session_factory, alice) == ["Позвонить Ивану"]


async def test_idempotency_key_is_per_user(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, bob = users
    keyed = replace(REMINDER, idempotency_key="ab12:1")
    await _insert(session_factory, alice, keyed)

    async with session_factory() as session:
        assert await ItemRepo(session).find_by_idempotency_key(bob, "ab12:1") is None
    assert await _insert(session_factory, bob, keyed) is not None


async def test_items_without_key_are_never_deduplicated(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users

    assert await _insert(session_factory, alice, REMINDER) is not None
    assert await _insert(session_factory, alice, REMINDER) is not None
    assert len(await _list(session_factory, alice)) == 2


async def test_range_is_half_open_and_uses_remind_at_without_starts_at(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users
    for title, starts_at in [
        ("Ровно в начале дня", DAY_START),
        ("Ровно в конце дня", DAY_END),
        ("Накануне", DAY_START - timedelta(minutes=1)),
    ]:
        await _insert(
            session_factory,
            alice,
            replace(REMINDER, kind="task", title=title, starts_at=starts_at, remind_at=None),
        )
    await _insert(
        session_factory, alice, replace(REMINDER, kind="task", title="Без времени", remind_at=None)
    )
    await _insert(session_factory, alice, REMINDER)

    assert await _list(session_factory, alice) == ["Ровно в начале дня", "Позвонить Ивану"]


async def test_order_is_all_day_first_then_by_time_and_limit_applies(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users
    meeting = replace(
        REMINDER,
        kind="event",
        title="Встреча в 15",
        starts_at=DAY_START + timedelta(hours=15),
        ends_at=DAY_START + timedelta(hours=16),
        remind_at=None,
    )
    await _insert(session_factory, alice, meeting)
    await _insert(session_factory, alice, REMINDER)
    await _insert(
        session_factory,
        alice,
        replace(REMINDER, kind="task", title="Весь день", starts_at=DAY_START, all_day=True),
    )

    assert await _list(session_factory, alice) == ["Весь день", "Позвонить Ивану", "Встреча в 15"]
    assert await _list(session_factory, alice, limit=2) == ["Весь день", "Позвонить Ивану"]


async def test_filters_by_kind_and_status_and_skips_deleted(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users
    task = replace(REMINDER, kind="task", title="Задача")
    done = await _insert(session_factory, alice, replace(task, title="Сделанная"))
    deleted = await _insert(session_factory, alice, replace(task, title="Удалённая"))
    await _insert(session_factory, alice, task)
    await _insert(session_factory, alice, REMINDER)
    assert done is not None
    assert deleted is not None
    async with session_factory() as session, session.begin():
        await session.execute(update(ItemRow).where(ItemRow.id == done.id).values(status="done"))
        await session.execute(
            update(ItemRow).where(ItemRow.id == deleted.id).values(deleted_at=datetime.now(UTC))
        )

    assert sorted(await _list(session_factory, alice, kinds=["task"])) == ["Задача", "Сделанная"]
    assert await _list(session_factory, alice, kinds=["task"], statuses=["done"]) == ["Сделанная"]


async def test_notification_is_saved_with_the_item_transaction(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    alice, _ = users
    async with session_factory() as session, session.begin():
        item = await ItemRepo(session).insert_if_absent(alice, REMINDER)
        assert item is not None
        await NotificationRepo(session).schedule(alice, item.id, REMINDER.remind_at)

    async with session_factory() as session:
        notification = await session.scalar(select(NotificationRow))
    assert notification is not None
    assert notification.item_id == item.id
    assert notification.fire_at == REMINDER.remind_at
    assert (notification.status, notification.attempt) == ("pending", 0)


async def test_item_found_for_change_is_locked_until_commit(
    session_factory: async_sessionmaker[AsyncSession], users: tuple[int, int]
) -> None:
    # Пока агент меняет запись, Scheduler (SKIP LOCKED) и второй вызов её не трогают.
    alice, _ = users
    row = await _insert(session_factory, alice, REMINDER)
    assert row is not None

    async with session_factory() as changing, changing.begin():
        await ItemRepo(changing).find_by_ref(
            alice, str(row.id), deleted=False, limit=1, for_update=True
        )
        async with session_factory() as other, other.begin():
            locked_elsewhere = await other.scalars(
                select(ItemRow).where(ItemRow.id == row.id).with_for_update(skip_locked=True)
            )
            assert list(locked_elsewhere) == []
