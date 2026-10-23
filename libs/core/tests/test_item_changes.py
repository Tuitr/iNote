"""Изменение, выполнение, удаление, восстановление записей и занятость. Время заморожено."""

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import (
    CheckConflictsArgs,
    CompleteItemArgs,
    CreateEventArgs,
    CreateTaskArgs,
    DeleteItemArgs,
    ListItemsArgs,
    MutationResult,
    UpdateItemArgs,
)
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.errors import InvalidArgumentsError, NotFoundError, PastTimeError
from libs.core.services.items import ItemService
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
# «Сейчас» во всех тестах — 21 октября 2026, 12:00 по Москве.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=MOSCOW)


def _at(clock: str, day: int = 22) -> datetime:
    hours, minutes = (int(part) for part in clock.split(":"))
    return datetime(2026, 10, day, hours, minutes, tzinfo=MOSCOW)


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> ItemService:
    return ItemService(session_factory, fake_redis, clock=lambda: NOW)


async def _reminder(service: ItemService, user_id: int, clock: str = "10:00") -> str:
    result = await service.create_task(
        CreateTaskArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": "Позвонить Ивану",
                "kind": "reminder",
                "remind_at": _at(clock).isoformat(),
            }
        )
    )
    assert result.item is not None
    return str(result.item.id)


async def _event(service: ItemService, user_id: int, title: str, clock: str) -> str:
    result = await service.create_event(
        CreateEventArgs.model_validate(
            {"caller": _caller(user_id), "title": title, "starts_at": _at(clock).isoformat()}
        )
    )
    assert result.item is not None
    return str(result.item.id)


async def _update(
    service: ItemService, user_id: int, item_id: str, **fields: Any
) -> MutationResult:
    return await service.update_item(
        UpdateItemArgs.model_validate({"caller": _caller(user_id), "id": item_id, **fields})
    )


async def _notifications(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[datetime, str]]:
    async with session_factory() as session:
        rows = await session.scalars(select(NotificationRow).order_by(NotificationRow.fire_at))
        return [(row.fire_at, row.status) for row in rows]


async def _listed(service: ItemService, user_id: int) -> list[str]:
    result = await service.list_items(
        ListItemsArgs.model_validate(
            {"caller": _caller(user_id), "from": "2026-10-22", "to": "2026-10-23", "status": "all"}
        )
    )
    return [item.title for item in result.items]


async def test_move_call_to_noon_moves_the_reminder(
    service: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> None:
    # «Перенеси звонок Ивану на 12»
    item_id = await _reminder(service, user_id)

    result = await _update(service, user_id, item_id[:8], remind_at=_at("12:00").isoformat())

    assert result.status == "updated"
    assert result.item is not None
    assert result.item.remind_at == _at("12:00")
    assert await _notifications(session_factory) == [
        (_at("10:00"), "cancelled"),
        (_at("12:00"), "pending"),
    ]
    assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 2}


async def test_moving_back_revives_the_old_notification(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _reminder(service, user_id)

    await _update(service, user_id, item_id, remind_at=_at("12:00").isoformat())
    await _update(service, user_id, item_id, remind_at=_at("10:00").isoformat())

    assert await _notifications(session_factory) == [
        (_at("10:00"), "pending"),
        (_at("12:00"), "cancelled"),
    ]


async def test_moved_meeting_keeps_duration_and_reminder_offset(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _event(service, user_id, "Встреча с Иваном", "15:00")

    result = await _update(service, user_id, item_id, starts_at=_at("17:00").isoformat())

    assert result.item is not None
    assert (result.item.starts_at, result.item.ends_at, result.item.remind_at) == (
        _at("17:00"),
        _at("18:00"),
        _at("16:45"),
    )
    assert await _notifications(session_factory) == [
        (_at("14:45"), "cancelled"),
        (_at("16:45"), "pending"),
    ]


async def test_moving_onto_a_busy_time_is_a_conflict_and_changes_nothing(
    service: ItemService, user_id: int
) -> None:
    await _event(service, user_id, "Встреча с дизайнером", "15:00")
    item_id = await _event(service, user_id, "Встреча с Иваном", "17:00")

    result = await _update(service, user_id, item_id, starts_at=_at("15:30").isoformat())
    nudge = await _update(service, user_id, item_id, starts_at=_at("17:30").isoformat())

    assert result.status == "conflict"
    assert [brief.title for brief in result.conflicts] == ["Встреча с дизайнером"]
    assert [(slot.starts_at, slot.ends_at) for slot in result.free_slots] == [
        (_at("16:00"), _at("17:00"))
    ]
    # Сдвиг на полчаса пересекается только со старым временем самой встречи — это не конфликт.
    assert nudge.status == "updated"


async def test_update_rules(service: ItemService, user_id: int) -> None:
    item_id = await _reminder(service, user_id)
    event_id = await _event(service, user_id, "Встреча с Иваном", "15:00")

    with pytest.raises(PastTimeError):
        await _update(service, user_id, item_id, remind_at=_at("09:00", day=21).isoformat())
    with pytest.raises(InvalidArgumentsError) as no_time:
        await _update(service, user_id, item_id, remind_at=None)
    with pytest.raises(InvalidArgumentsError) as no_title:
        await _update(service, user_id, item_id, title=None)
    with pytest.raises(InvalidArgumentsError) as event_to_task:
        await _update(service, user_id, event_id, kind="task")
    with pytest.raises(InvalidArgumentsError) as end_before_start:
        await _update(service, user_id, event_id, ends_at=_at("14:00").isoformat())

    assert no_time.value.details == {"missing": ["remind_at"]}
    assert no_title.value.details == {"invalid": ["title"]}
    assert event_to_task.value.details == {"invalid": ["kind"]}
    assert end_before_start.value.details == {"invalid": ["ends_at"]}


async def test_search_hints_can_be_changed(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _reminder(service, user_id)

    await _update(service, user_id, item_id, search_hints="звонок телефон Иван Петров")

    async with session_factory() as session:
        hints = await session.scalar(select(ItemRow.search_hints))
    assert hints == "звонок телефон Иван Петров"


async def test_complete_and_reopen(
    service: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> None:
    # «Отметь выполненным»
    item_id = await _reminder(service, user_id)
    args = CompleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id})

    done = await service.complete_item(args)
    again = await service.complete_item(args)

    assert (done.status, again.status) == ("completed", "completed")
    assert done.item is not None
    assert done.item.status == "done"
    assert await _notifications(session_factory) == [(_at("10:00"), "cancelled")]
    assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 2}

    reopened = await service.complete_item(
        CompleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id, "done": False})
    )

    assert reopened.status == "updated"
    assert reopened.item is not None
    assert reopened.item.status == "open"
    assert await _notifications(session_factory) == [(_at("10:00"), "pending")]


async def test_delete_and_restore(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # «Удали», «верни»
    item_id = await _reminder(service, user_id)
    delete = DeleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id})

    deleted = await service.delete_item(delete)
    deleted_again = await service.delete_item(delete)
    listed_after_delete = await _listed(service, user_id)
    notifications_after_delete = await _notifications(session_factory)
    with pytest.raises(NotFoundError):
        await _update(service, user_id, item_id, title="Нельзя менять удалённое")
    restored = await service.delete_item(
        DeleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id, "restore": True})
    )

    assert (deleted.status, deleted_again.status, restored.status) == (
        "deleted",
        "deleted",
        "updated",
    )
    assert listed_after_delete == []
    assert notifications_after_delete == [(_at("10:00"), "cancelled")]
    assert await _listed(service, user_id) == ["Позвонить Ивану"]
    assert await _notifications(session_factory) == [(_at("10:00"), "pending")]


async def test_foreign_id_is_not_found_everywhere(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    other = await UserService(session_factory, "Europe/Moscow", FakeRedis()).resolve(2, None, None)
    foreign_id = await _reminder(service, other.user_id)
    caller = _caller(user_id)

    calls = [
        _update(service, user_id, foreign_id, title="Чужое"),
        service.complete_item(
            CompleteItemArgs.model_validate({"caller": caller, "id": foreign_id})
        ),
        service.delete_item(DeleteItemArgs.model_validate({"caller": caller, "id": foreign_id})),
        service.check_conflicts(
            CheckConflictsArgs.model_validate(
                {
                    "caller": caller,
                    "starts_at": _at("15:00").isoformat(),
                    "ends_at": _at("16:00").isoformat(),
                    "exclude_id": foreign_id,
                }
            )
        ),
    ]
    for call in calls:
        with pytest.raises(NotFoundError) as error:
            await call
        assert error.value.details == {"id": foreign_id}


async def test_ambiguous_short_id_offers_candidates(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        for suffix in ("1", "2"):
            session.add(
                ItemRow(
                    id=UUID(f"aaaaaaaa-0000-4000-8000-00000000000{suffix}"),
                    user_id=user_id,
                    kind="task",
                    title=f"Задача {suffix}",
                )
            )

    with pytest.raises(InvalidArgumentsError) as error:
        await _update(service, user_id, "aaaaaaaa", title="Какая?")

    assert error.value.details["invalid"] == ["id"]
    assert [candidate["title"] for candidate in error.value.details["candidates"]] == [
        "Задача 1",
        "Задача 2",
    ]


async def test_check_conflicts(service: ItemService, user_id: int) -> None:
    busy_id = await _event(service, user_id, "Встреча с дизайнером", "15:00")

    def check(start: str, end: str, exclude: str | None = None) -> CheckConflictsArgs:
        return CheckConflictsArgs.model_validate(
            {
                "caller": _caller(user_id),
                "starts_at": _at(start).isoformat(),
                "ends_at": _at(end).isoformat(),
                "exclude_id": exclude,
            }
        )

    busy = await service.check_conflicts(check("15:30", "16:30"))
    free = await service.check_conflicts(check("16:00", "17:00"))
    itself = await service.check_conflicts(check("15:30", "16:30", exclude=busy_id[:8]))

    assert [brief.title for brief in busy.conflicts] == ["Встреча с дизайнером"]
    assert [(slot.starts_at, slot.ends_at) for slot in busy.free_slots] == [
        (_at("16:00"), _at("17:00"))
    ]
    assert (free.conflicts, free.free_slots) == ([], [])
    assert itself.conflicts == []
    with pytest.raises(InvalidArgumentsError):
        await service.check_conflicts(check("16:00", "15:00"))


async def test_rename_add_participants_and_turn_task_into_reminder(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # «Переименуй в „Позвонить Ивану Петровичу“», «добавь Олега», «и напомни в 11»
    task = await service.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(user_id), "title": "Позвонить Ивану"})
    )
    assert task.item is not None

    result = await _update(
        service,
        user_id,
        str(task.item.id),
        title="Позвонить Ивану Петровичу",
        participants=["Олег"],
        kind="reminder",
        remind_at=_at("11:00").isoformat(),
    )

    assert result.item is not None
    assert (result.item.title, result.item.participants, result.item.kind) == (
        "Позвонить Ивану Петровичу",
        ["Олег"],
        "reminder",
    )
    assert await _notifications(session_factory) == [(_at("11:00"), "pending")]


async def test_meeting_time_rules_on_update(service: ItemService, user_id: int) -> None:
    event_id = await _event(service, user_id, "Встреча с Иваном", "15:00")
    task = await service.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(user_id), "title": "Купить молоко"})
    )
    assert task.item is not None

    # Перенос встречи на весь день: конца у неё больше нет.
    all_day = await _update(service, user_id, event_id, starts_at="2026-10-23")
    with pytest.raises(InvalidArgumentsError) as end_of_all_day:
        await _update(service, user_id, event_id, ends_at=_at("16:00", day=23).isoformat())
    with pytest.raises(PastTimeError):
        await _update(service, user_id, event_id, starts_at="2026-10-20")
    with pytest.raises(InvalidArgumentsError) as no_start_to_count_from:
        await _update(service, user_id, str(task.item.id), remind_before_min=15)
    with pytest.raises(InvalidArgumentsError) as meeting_without_start:
        await _update(service, user_id, event_id, starts_at=None)

    assert all_day.item is not None
    assert (all_day.item.all_day, all_day.item.ends_at) == (True, None)
    assert end_of_all_day.value.details == {"invalid": ["ends_at"]}
    assert no_start_to_count_from.value.details == {"missing": ["starts_at"]}
    assert meeting_without_start.value.details == {"missing": ["starts_at"]}
