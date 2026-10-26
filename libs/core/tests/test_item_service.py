import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CreateTaskArgs
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.errors import InvalidArgumentsError, NotFoundError, PastTimeError
from libs.core.services.items import ItemService
from libs.core.services.time import now_utc
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
TRACE_ID = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


def _caller(user_id: int, idempotency_key: str | None = None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": TRACE_ID,
        "idempotency_key": idempotency_key,
    }


def _moscow_in(delta: timedelta) -> datetime:
    return (now_utc() + delta).astimezone(MOSCOW).replace(microsecond=0)


def _task(user_id: int, idempotency_key: str | None = None, **fields: Any) -> CreateTaskArgs:
    return CreateTaskArgs.model_validate(
        {"caller": _caller(user_id, idempotency_key), "title": "Позвонить Ивану", **fields}
    )


async def _count(session_factory: async_sessionmaker[AsyncSession], table: type) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


async def test_reminder_is_saved_with_notification_and_returned_in_user_tz(
    item_service: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> None:
    remind_at = _moscow_in(timedelta(days=1))

    result = await item_service.create_task(
        _task(user_id, kind="reminder", remind_at=remind_at.isoformat())
    )

    assert result.status == "created"
    item = result.item
    assert item is not None
    assert (item.kind, item.title, item.status, item.all_day) == (
        "reminder",
        "Позвонить Ивану",
        "open",
        False,
    )
    assert item.remind_at == remind_at
    assert item.remind_at.isoformat() == remind_at.isoformat()
    assert item.created_at.utcoffset() == timedelta(hours=3)
    async with session_factory() as session:
        notification = await session.scalar(select(NotificationRow))
    assert notification is not None
    assert (notification.item_id, notification.fire_at) == (item.id, remind_at)
    assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 1}


async def test_reminder_without_time_asks_for_remind_at(
    item_service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(InvalidArgumentsError) as error:
        await item_service.create_task(_task(user_id, kind="reminder"))

    assert error.value.details == {"missing": ["remind_at"]}
    assert await _count(session_factory, ItemRow) == 0


async def test_reminder_in_the_past_is_rejected(
    item_service: ItemService, user_id: int, fake_redis: FakeRedis
) -> None:
    remind_at = _moscow_in(-timedelta(minutes=5))

    with pytest.raises(PastTimeError) as error:
        await item_service.create_task(
            _task(user_id, kind="reminder", remind_at=remind_at.isoformat())
        )

    assert error.value.details == {"remind_at": remind_at.isoformat()}
    assert fake_redis.values == {}


async def test_reminder_a_few_seconds_late_is_still_accepted(
    item_service: ItemService, user_id: int
) -> None:
    remind_at = _moscow_in(-timedelta(seconds=30))

    result = await item_service.create_task(
        _task(user_id, kind="reminder", remind_at=remind_at.isoformat())
    )

    assert result.status == "created"


async def test_task_with_date_is_all_day_and_has_no_notification(
    item_service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    result = await item_service.create_task(_task(user_id, starts_at="2026-10-22"))

    assert result.item is not None
    assert result.item.kind == "task"
    assert result.item.all_day is True
    assert result.item.starts_at is not None
    assert result.item.starts_at.isoformat() == "2026-10-22T00:00:00+03:00"
    assert await _count(session_factory, NotificationRow) == 0


async def test_task_without_any_time_is_created(
    item_service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    result = await item_service.create_task(_task(user_id, notes="Про отпуск"))

    assert result.item is not None
    assert (result.item.starts_at, result.item.remind_at, result.item.notes) == (
        None,
        None,
        "Про отпуск",
    )
    assert await _count(session_factory, NotificationRow) == 0


async def test_repeated_call_with_same_key_returns_first_item(
    item_service: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> None:
    args = _task(
        user_id, "ab12:1", kind="reminder", remind_at=_moscow_in(timedelta(days=1)).isoformat()
    )

    first = await item_service.create_task(args)
    second = await item_service.create_task(args)

    assert first.item is not None
    assert second.item is not None
    assert second.item.id == first.item.id
    assert await _count(session_factory, ItemRow) == 1
    assert await _count(session_factory, NotificationRow) == 1
    assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 1}


async def test_parallel_calls_with_same_key_create_one_item(
    item_service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    args = _task(
        user_id, "ab12:1", kind="reminder", remind_at=_moscow_in(timedelta(days=1)).isoformat()
    )

    results = await asyncio.gather(*(item_service.create_task(args) for _ in range(5)))

    assert len({result.item.id for result in results if result.item is not None}) == 1
    assert await _count(session_factory, ItemRow) == 1
    assert await _count(session_factory, NotificationRow) == 1


async def test_replay_answers_even_when_time_has_passed_since_first_call(
    item_service: ItemService, user_id: int
) -> None:
    # Первый вызов прошёл, ответ потерялся по таймауту, повтор пришёл, когда время уже прошло.
    first = await item_service.create_task(
        _task(user_id, "ab12:1", kind="reminder", remind_at=_moscow_in(timedelta(0)).isoformat())
    )
    late_retry = _task(
        user_id, "ab12:1", kind="reminder", remind_at=_moscow_in(-timedelta(hours=1)).isoformat()
    )

    second = await item_service.create_task(late_retry)

    assert first.item is not None
    assert second.item is not None
    assert second.item.id == first.item.id


async def test_unknown_user_is_not_found(item_service: ItemService, user_id: int) -> None:
    with pytest.raises(NotFoundError):
        await item_service.create_task(_task(user_id + 1))


async def test_item_is_created_even_if_redis_is_down(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenRedis:
        async def incr(self, key: str) -> int:
            raise RedisConnectionError("нет связи")

    service = ItemService(session_factory, BrokenRedis())

    with caplog.at_level(logging.ERROR):
        result = await service.create_task(_task(user_id, starts_at=date(2026, 10, 22).isoformat()))

    assert result.status == "created"
    assert await _count(session_factory, ItemRow) == 1
    assert "не увеличена: ConnectionError" in caplog.text


async def test_version_is_bumped_only_after_commit(
    session_factory: async_sessionmaker[AsyncSession], user_id: int
) -> None:
    seen_at_bump: list[int] = []

    class CommitCheckingRedis:
        async def incr(self, key: str) -> int:
            # Отдельная сессия видит только закоммиченное: запись уже должна быть там.
            seen_at_bump.append(await _count(session_factory, ItemRow))
            return len(seen_at_bump)

    service = ItemService(session_factory, CommitCheckingRedis())

    await service.create_task(_task(user_id))

    assert seen_at_bump == [1]
