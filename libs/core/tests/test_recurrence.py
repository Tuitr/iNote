"""Повторы, минимальный вариант: серия — одна запись, «Выполнено» двигает её на следующую дату.

Время заморожено: «сейчас» — среда, 21 октября 2026, 12:00 по Москве.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import (
    CompleteItemArgs,
    CreateEventArgs,
    CreateTaskArgs,
    DeleteItemArgs,
    ListItemsArgs,
    MutationResult,
    UpdateItemArgs,
)
from libs.core.db.models import NotificationRow
from libs.core.errors import InvalidArgumentsError
from libs.core.services.items import ItemService
from libs.core.services.recurrence import check_rrule, next_occurrence
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")
WEEKLY_MONDAY = "FREQ=WEEKLY;BYDAY=MO"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=MOSCOW)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(
    session_factory: async_sessionmaker[AsyncSession], fake_redis: FakeRedis, clock: Clock
) -> ItemService:
    return ItemService(session_factory, fake_redis, clock=clock)


def _caller(user_id: int, tz: ZoneInfo = MOSCOW) -> dict[str, Any]:
    return {"user_id": user_id, "tz": str(tz), "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80"}


def _monday(month: int, day: int, hour: int = 9, tz: ZoneInfo = MOSCOW) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=tz)


def _reminder_of(starts_at: datetime) -> datetime:
    return starts_at - timedelta(minutes=60)


async def _standup(service: ItemService, user_id: int, rrule: str = WEEKLY_MONDAY) -> str:
    # «Каждый понедельник в 9 планёрка»
    result = await service.create_event(
        CreateEventArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": "Планёрка",
                "starts_at": _monday(10, 26).isoformat(),
                "rrule": rrule,
            }
        )
    )
    assert result.item is not None
    return str(result.item.id)


async def _done(service: ItemService, user_id: int, item_id: str) -> MutationResult:
    return await service.complete_item(
        CompleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id})
    )


async def _notifications(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[datetime, str]]:
    async with session_factory() as session:
        rows = await session.scalars(select(NotificationRow).order_by(NotificationRow.fire_at))
        return [(row.fire_at, row.status) for row in rows]


class TestNextOccurrence:
    def test_weekly_monday_moves_a_week_ahead(self) -> None:
        following = next_occurrence(
            WEEKLY_MONDAY, _monday(10, 26), "Europe/Moscow", now=_monday(10, 21, 12)
        )

        assert following is not None
        assert following.starts_at == _monday(11, 2)
        assert following.rrule == WEEKLY_MONDAY

    def test_local_time_survives_daylight_saving_change(self) -> None:
        # В Берлине 25 октября 2026 часы переводят назад: 9:00 остаётся 9:00, сдвиг 7 дней + 1 ч.
        following = next_occurrence(
            WEEKLY_MONDAY,
            _monday(10, 19, tz=BERLIN),
            "Europe/Berlin",
            now=_monday(10, 19, 10, BERLIN),
        )

        assert following is not None
        assert following.starts_at == _monday(10, 26, tz=BERLIN)
        assert (
            following.starts_at.astimezone(BERLIN).utcoffset()
            != _monday(10, 19, tz=BERLIN).utcoffset()
        )

    def test_late_completion_skips_past_occurrences_and_spends_count(self) -> None:
        following = next_occurrence(
            "FREQ=WEEKLY;BYDAY=MO;COUNT=5",
            _monday(10, 26),
            "Europe/Moscow",
            now=_monday(11, 10, 12),
        )

        assert following is not None
        assert following.starts_at == _monday(11, 16)
        assert following.rrule == "FREQ=WEEKLY;BYDAY=MO;COUNT=2"

    def test_utc_until_is_understood_and_ends_the_series(self) -> None:
        rule = "FREQ=DAILY;UNTIL=20261027T235959Z"
        start = datetime(2026, 10, 26, 9, 0, tzinfo=MOSCOW)

        first = next_occurrence(rule, start, "Europe/Moscow", now=start)
        assert first is not None
        assert first.starts_at == datetime(2026, 10, 27, 9, 0, tzinfo=MOSCOW)
        assert next_occurrence(rule, first.starts_at, "Europe/Moscow", now=first.starts_at) is None

    @pytest.mark.parametrize(
        "rule",
        [
            "ерунда",
            "",
            "FREQ=WEEKLY;BYDAY=XX",
            "FREQ=DAILY\nFREQ=WEEKLY",
            # Начало серии берётся из записи, не из строки.
            "DTSTART:20200101T090000\nRRULE:FREQ=WEEKLY",
        ],
    )
    def test_invalid_rule_is_a_validation_error(self, rule: str) -> None:
        with pytest.raises(InvalidArgumentsError) as error:
            check_rrule(rule, _monday(10, 26), "Europe/Moscow")

        assert error.value.details == {"invalid": ["rrule"]}
        assert "FREQ=WEEKLY;BYDAY=MO" in error.value.message


async def test_weekly_standup_is_created_with_one_reminder(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _standup(service, user_id)

    # За час по настройкам пользователя и в момент начала.
    assert await _notifications(session_factory) == [
        (_reminder_of(_monday(10, 26)), "pending"),
        (_monday(10, 26), "pending"),
    ]


async def test_nonsense_rrule_is_rejected_everywhere(service: ItemService, user_id: int) -> None:
    item_id = await _standup(service, user_id)
    attempts = [
        service.create_event(
            CreateEventArgs.model_validate(
                {
                    "caller": _caller(user_id),
                    "title": "Планёрка",
                    "starts_at": _monday(10, 26).isoformat(),
                    "rrule": "ерунда",
                }
            )
        ),
        service.create_task(
            CreateTaskArgs.model_validate(
                {
                    "caller": _caller(user_id),
                    "title": "Полить цветы",
                    "starts_at": "2026-10-26",
                    "rrule": "ерунда",
                }
            )
        ),
        service.update_item(
            UpdateItemArgs.model_validate(
                {"caller": _caller(user_id), "id": item_id, "rrule": "ерунда"}
            )
        ),
    ]

    for attempt in attempts:
        with pytest.raises(InvalidArgumentsError) as error:
            await attempt
        assert error.value.details == {"invalid": ["rrule"]}


async def test_repeating_task_needs_a_date(service: ItemService, user_id: int) -> None:
    with pytest.raises(InvalidArgumentsError) as error:
        await service.create_task(
            CreateTaskArgs.model_validate(
                {"caller": _caller(user_id), "title": "Полить цветы", "rrule": "FREQ=DAILY"}
            )
        )

    assert error.value.details == {"missing": ["starts_at"]}


async def test_done_moves_standup_and_its_reminder_to_next_monday(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _standup(service, user_id)

    result = await _done(service, user_id, item_id)

    assert result.status == "completed"
    assert result.item is not None
    assert result.item.status == "open"
    assert (result.item.starts_at, result.item.ends_at) == (_monday(11, 2), _monday(11, 2, 10))
    assert result.item.remind_at == _reminder_of(_monday(11, 2))
    assert await _notifications(session_factory) == [
        (_reminder_of(_monday(10, 26)), "cancelled"),
        (_monday(10, 26), "cancelled"),
        (_reminder_of(_monday(11, 2)), "pending"),
        (_monday(11, 2), "pending"),
    ]


async def test_series_with_count_2_is_done_after_second_completion(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _standup(service, user_id, rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=2")

    first = await _done(service, user_id, item_id)
    second = await _done(service, user_id, item_id)

    assert first.item is not None
    assert (first.item.status, first.item.rrule) == ("open", "FREQ=WEEKLY;BYDAY=MO;COUNT=1")
    assert second.item is not None
    assert (second.item.status, second.item.starts_at) == ("done", _monday(11, 2))
    assert {status for _, status in await _notifications(session_factory)} == {"cancelled"}


async def test_deleted_series_does_not_remind(
    service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    item_id = await _standup(service, user_id)

    result = await service.delete_item(
        DeleteItemArgs.model_validate({"caller": _caller(user_id), "id": item_id})
    )

    assert result.status == "deleted"
    assert await _notifications(session_factory) == [
        (_reminder_of(_monday(10, 26)), "cancelled"),
        (_monday(10, 26), "cancelled"),
    ]


async def test_list_shows_only_the_current_occurrence(service: ItemService, user_id: int) -> None:
    await _standup(service, user_id)

    result = await service.list_items(
        ListItemsArgs.model_validate(
            {"caller": _caller(user_id), "from": "2026-10-21", "to": "2026-11-21"}
        )
    )

    assert [(item.title, item.starts_at) for item in result.items] == [
        ("Планёрка", _monday(10, 26))
    ]
