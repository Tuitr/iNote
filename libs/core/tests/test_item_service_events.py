from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CreateEventArgs, CreateTaskArgs
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.errors import InvalidArgumentsError, PastTimeError
from libs.core.services.items import ItemService, free_slots
from libs.core.services.time import now_utc, to_user_tz
from libs.core.tests.fakes import FakeRedis

MOSCOW = ZoneInfo("Europe/Moscow")
TOMORROW = to_user_tz(now_utc(), "Europe/Moscow").date() + timedelta(days=1)


def _at(clock: str, day: date = TOMORROW) -> datetime:
    return datetime.combine(day, time.fromisoformat(clock), tzinfo=MOSCOW)


def _caller(user_id: int, idempotency_key: str | None = None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        "idempotency_key": idempotency_key,
    }


def _event(user_id: int, starts_at: object, **fields: Any) -> CreateEventArgs:
    if isinstance(starts_at, datetime | date):
        starts_at = starts_at.isoformat()
    return CreateEventArgs.model_validate(
        {"caller": _caller(user_id), "title": "Встреча с Иваном", "starts_at": starts_at, **fields}
    )


async def _count(session_factory: async_sessionmaker[AsyncSession], table: type) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(table)) or 0


class TestFreeSlots:
    """Свободные окна без базы: только арифметика интервалов."""

    window = (_at("08:00"), _at("22:00"))

    def test_slot_right_after_the_busy_meeting(self) -> None:
        busy = [(_at("15:00"), _at("16:00"))]

        assert free_slots(busy, timedelta(hours=1), *self.window) == [(_at("16:00"), _at("17:00"))]

    def test_slot_skips_back_to_back_meetings(self) -> None:
        busy = [(_at("15:00"), _at("16:00")), (_at("16:00"), _at("17:30"))]

        assert free_slots(busy, timedelta(hours=1), *self.window) == [(_at("17:30"), _at("18:30"))]

    def test_slot_must_fit_into_the_window(self) -> None:
        busy = [(_at("21:30"), _at("21:45"))]

        assert free_slots(busy, timedelta(minutes=30), *self.window) == []

    def test_at_most_three_slots_in_time_order(self) -> None:
        busy = [(_at(f"{hour:02}:00"), _at(f"{hour:02}:30")) for hour in (9, 11, 13, 15)]

        assert free_slots(busy, timedelta(minutes=30), *self.window) == [
            (_at("09:30"), _at("10:00")),
            (_at("11:30"), _at("12:00")),
            (_at("13:30"), _at("14:00")),
        ]


@pytest.mark.anyio
class TestCreateEvent:
    async def test_defaults_come_from_user_settings(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
        fake_redis: FakeRedis,
    ) -> None:
        result = await item_service.create_event(
            _event(user_id, _at("15:00"), participants=["Иван"])
        )

        assert result.status == "created"
        item = result.item
        assert item is not None
        assert (item.kind, item.all_day, item.participants) == ("event", False, ["Иван"])
        assert (item.starts_at, item.ends_at, item.remind_at) == (
            _at("15:00"),
            _at("16:00"),
            _at("14:45"),
        )
        async with session_factory() as session:
            notification = await session.scalar(select(NotificationRow))
        assert notification is not None
        assert notification.fire_at == _at("14:45")
        assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 1}

    async def test_explicit_end_and_no_reminder(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        result = await item_service.create_event(
            _event(user_id, _at("15:00"), ends_at=_at("15:30").isoformat(), remind_before_min=0)
        )

        assert result.item is not None
        assert (result.item.ends_at, result.item.remind_at) == (_at("15:30"), None)
        assert await _count(session_factory, NotificationRow) == 0

    @pytest.mark.parametrize("ends_at", ["15:00", "14:00"])
    async def test_end_not_after_start_is_invalid(
        self, item_service: ItemService, user_id: int, ends_at: str
    ) -> None:
        with pytest.raises(InvalidArgumentsError) as error:
            await item_service.create_event(
                _event(user_id, _at("15:00"), ends_at=_at(ends_at).isoformat())
            )

        assert error.value.details == {"invalid": ["ends_at"]}

    async def test_all_day_event_has_no_end_and_no_auto_reminder(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        result = await item_service.create_event(_event(user_id, TOMORROW))

        item = result.item
        assert item is not None
        assert (item.all_day, item.starts_at, item.ends_at, item.remind_at) == (
            True,
            _at("00:00"),
            None,
            None,
        )
        assert await _count(session_factory, NotificationRow) == 0

    async def test_all_day_event_reminds_only_when_asked(
        self, item_service: ItemService, user_id: int
    ) -> None:
        result = await item_service.create_event(_event(user_id, TOMORROW, remind_before_min=60))

        assert result.item is not None
        assert result.item.remind_at == _at("23:00", TOMORROW - timedelta(days=1))

    async def test_all_day_event_with_end_time_is_invalid(
        self, item_service: ItemService, user_id: int
    ) -> None:
        with pytest.raises(InvalidArgumentsError) as error:
            await item_service.create_event(
                _event(user_id, TOMORROW, ends_at=_at("18:00").isoformat())
            )

        assert error.value.details == {"invalid": ["ends_at"]}

    async def test_all_day_event_today_is_not_in_the_past(
        self, item_service: ItemService, user_id: int
    ) -> None:
        result = await item_service.create_event(
            _event(user_id, to_user_tz(now_utc(), "Europe/Moscow").date())
        )

        assert result.status == "created"

    async def test_past_events_are_rejected(self, item_service: ItemService, user_id: int) -> None:
        yesterday = to_user_tz(now_utc(), "Europe/Moscow").date() - timedelta(days=1)
        an_hour_ago = (now_utc() - timedelta(hours=1)).astimezone(MOSCOW).replace(microsecond=0)

        with pytest.raises(PastTimeError) as all_day_error:
            await item_service.create_event(_event(user_id, yesterday))
        with pytest.raises(PastTimeError) as timed_error:
            await item_service.create_event(_event(user_id, an_hour_ago))

        assert all_day_error.value.details == {"starts_at": yesterday.isoformat()}
        assert timed_error.value.details == {"starts_at": an_hour_ago.isoformat()}

    async def test_meeting_too_soon_for_reminder_is_created_without_it(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        soon = (now_utc() + timedelta(minutes=10)).astimezone(MOSCOW).replace(microsecond=0)

        result = await item_service.create_event(_event(user_id, soon))

        assert result.item is not None
        assert result.item.remind_at is None
        assert await _count(session_factory, NotificationRow) == 0

    async def test_overlap_returns_conflict_with_free_slot_and_creates_nothing(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
        fake_redis: FakeRedis,
    ) -> None:
        designer = await item_service.create_event(
            _event(user_id, _at("15:00"), title="Встреча с дизайнером")
        )
        assert designer.item is not None

        result = await item_service.create_event(_event(user_id, _at("15:30")))

        assert result.status == "conflict"
        assert result.item is None
        assert [(brief.id, brief.title, brief.starts_at) for brief in result.conflicts] == [
            (designer.item.id, "Встреча с дизайнером", _at("15:00"))
        ]
        assert [(slot.starts_at, slot.ends_at) for slot in result.free_slots] == [
            (_at("16:00"), _at("17:00"))
        ]
        assert result.free_slots[0].starts_at.utcoffset() == timedelta(hours=3)
        assert await _count(session_factory, ItemRow) == 1
        assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 1}

    async def test_allow_conflict_creates_anyway(
        self, item_service: ItemService, user_id: int
    ) -> None:
        await item_service.create_event(_event(user_id, _at("15:00")))

        result = await item_service.create_event(_event(user_id, _at("15:30"), allow_conflict=True))

        assert result.status == "created"

    async def test_back_to_back_meetings_do_not_conflict(
        self, item_service: ItemService, user_id: int
    ) -> None:
        await item_service.create_event(_event(user_id, _at("15:00")))

        result = await item_service.create_event(_event(user_id, _at("16:00")))

        assert result.status == "created"

    async def test_tasks_and_all_day_events_do_not_block_time(
        self, item_service: ItemService, user_id: int
    ) -> None:
        await item_service.create_event(_event(user_id, TOMORROW, title="Конференция"))
        await item_service.create_task(
            CreateTaskArgs.model_validate(
                {
                    "caller": _caller(user_id),
                    "title": "Позвонить Ивану",
                    "kind": "reminder",
                    "remind_at": _at("15:00").isoformat(),
                }
            )
        )

        result = await item_service.create_event(_event(user_id, _at("15:00")))

        assert result.status == "created"

    async def test_repeated_call_with_same_key_returns_first_event(
        self,
        item_service: ItemService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        args = CreateEventArgs.model_validate(
            {
                "caller": _caller(user_id, "ab12:1"),
                "title": "Встреча с Иваном",
                "starts_at": _at("15:00").isoformat(),
            }
        )

        first = await item_service.create_event(args)
        # Повтор не должен увидеть конфликт с самим собой.
        second = await item_service.create_event(args)

        assert second.status == "created"
        assert first.item is not None
        assert second.item is not None
        assert second.item.id == first.item.id
        assert await _count(session_factory, ItemRow) == 1


def test_tomorrow_helper_is_in_the_future() -> None:
    assert _at("00:00") > datetime.now(UTC)
