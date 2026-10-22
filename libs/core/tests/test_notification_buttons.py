"""Кнопки уведомления глазами D: агент C переводит нажатие в update_item или complete_item.

caller.idempotency_key = id нажатия, «Завтра» = то же время завтра в поясе пользователя
(решения S3). Время заморожено: «сейчас» — 22 октября 2026, 10:05, напоминание на 10:00
уже пришло.
"""

from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CompleteItemArgs, CreateTaskArgs, UpdateItemArgs
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.services.items import ItemService
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
YEKATERINBURG = ZoneInfo("Asia/Yekaterinburg")
CREATED_AT = datetime(2026, 10, 21, 12, 0, tzinfo=MOSCOW)
PRESSED_AT = datetime(2026, 10, 22, 10, 5, tzinfo=MOSCOW)


class Clock:
    def __init__(self) -> None:
        self.now = CREATED_AT

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


def _caller(user_id: int, tz: tzinfo | None, press_id: str | None = None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": str(tz),
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        "idempotency_key": press_id,
    }


async def _fired_reminder(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    user_id: int,
    remind_at: datetime,
) -> str:
    """Напоминание создано вчера, Scheduler его уже отправил, пользователь жмёт кнопку."""
    result = await service.create_task(
        CreateTaskArgs.model_validate(
            {
                "caller": _caller(user_id, remind_at.tzinfo),
                "title": "Позвонить Ивану",
                "kind": "reminder",
                "remind_at": remind_at.isoformat(),
            }
        )
    )
    assert result.item is not None
    async with session_factory() as session, session.begin():
        await session.execute(update(NotificationRow).values(status="sent"))
    clock.now = max(PRESSED_AT, remind_at + timedelta(minutes=5))
    return str(result.item.id)


async def _snooze(
    service: ItemService, user_id: int, item_id: str, remind_at: datetime, press_id: str
) -> None:
    await service.update_item(
        UpdateItemArgs.model_validate(
            {
                "caller": _caller(user_id, remind_at.tzinfo, press_id),
                "id": item_id,
                "remind_at": remind_at.isoformat(),
            }
        )
    )


async def _complete(service: ItemService, user_id: int, item_id: str, press_id: str) -> None:
    await service.complete_item(
        CompleteItemArgs.model_validate(
            {"caller": _caller(user_id, MOSCOW, press_id), "id": item_id}
        )
    )


async def _notifications(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[datetime, str]]:
    async with session_factory() as session:
        rows = await session.scalars(select(NotificationRow).order_by(NotificationRow.fire_at))
        return [(row.fire_at, row.status) for row in rows]


async def _item(session_factory: async_sessionmaker[AsyncSession]) -> ItemRow:
    async with session_factory() as session:
        row = await session.scalar(select(ItemRow))
    assert row is not None
    return row


async def test_snooze_for_an_hour_schedules_a_new_reminder(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    user_id: int,
) -> None:
    fired_at = datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW)
    item_id = await _fired_reminder(service, session_factory, clock, user_id, fired_at)

    # «Отложить на 1 ч»: агент считает от момента нажатия.
    await _snooze(service, user_id, item_id, clock.now + timedelta(hours=1), "press-1")

    assert await _notifications(session_factory) == [
        (fired_at, "sent"),
        (datetime(2026, 10, 22, 11, 5, tzinfo=MOSCOW), "pending"),
    ]


async def test_snooze_cancels_the_reminder_that_has_not_fired_yet(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    user_id: int,
) -> None:
    item_id = await _fired_reminder(
        service, session_factory, clock, user_id, datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW)
    )
    await _snooze(service, user_id, item_id, datetime(2026, 10, 22, 11, 5, tzinfo=MOSCOW), "p1")

    await _snooze(service, user_id, item_id, datetime(2026, 10, 22, 12, 5, tzinfo=MOSCOW), "p2")

    assert [status for _, status in await _notifications(session_factory)] == [
        "sent",
        "cancelled",
        "pending",
    ]


@pytest.mark.parametrize("tz", [MOSCOW, YEKATERINBURG])
async def test_tomorrow_is_the_same_local_time_next_day(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    tz: ZoneInfo,
) -> None:
    resolved = await UserService(session_factory, str(tz), FakeRedis()).resolve(42, 1, None)
    fired_at = datetime(2026, 10, 22, 10, 0, tzinfo=tz)
    item_id = await _fired_reminder(service, session_factory, clock, resolved.user_id, fired_at)

    await _snooze(service, resolved.user_id, item_id, fired_at + timedelta(days=1), "press-1")

    item = await _item(session_factory)
    assert item.remind_at is not None
    assert item.remind_at.astimezone(tz) == datetime(2026, 10, 23, 10, 0, tzinfo=tz)
    assert (await _notifications(session_factory))[-1] == (item.remind_at, "pending")


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        (timedelta(hours=1), datetime(2026, 10, 23, 0, 35, tzinfo=MOSCOW)),
        (timedelta(days=1), datetime(2026, 10, 23, 23, 30, tzinfo=MOSCOW)),
    ],
    ids=["snooze_hour", "tomorrow"],
)
async def test_buttons_across_midnight(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    user_id: int,
    button: timedelta,
    expected: datetime,
) -> None:
    fired_at = datetime(2026, 10, 22, 23, 30, tzinfo=MOSCOW)
    item_id = await _fired_reminder(service, session_factory, clock, user_id, fired_at)
    # «Отложить на 1 ч» считают от нажатия (23:35), «Завтра» — от исходного времени.
    base = clock.now if button == timedelta(hours=1) else fired_at

    await _snooze(service, user_id, item_id, base + button, "press-1")

    assert (await _notifications(session_factory))[-1] == (expected, "pending")


async def test_done_stops_reminders(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    user_id: int,
) -> None:
    item_id = await _fired_reminder(
        service, session_factory, clock, user_id, datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW)
    )
    await _snooze(service, user_id, item_id, datetime(2026, 10, 22, 11, 5, tzinfo=MOSCOW), "p1")

    await _complete(service, user_id, item_id, "p2")

    assert [status for _, status in await _notifications(session_factory)] == [
        "sent",
        "cancelled",
    ]
    assert (await _item(session_factory)).status == "done"


async def test_double_click_snooze_changes_nothing_second_time(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    fake_redis: FakeRedis,
    user_id: int,
) -> None:
    item_id = await _fired_reminder(
        service, session_factory, clock, user_id, datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW)
    )
    snoozed_to = datetime(2026, 10, 22, 11, 5, tzinfo=MOSCOW)
    await _snooze(service, user_id, item_id, snoozed_to, "press-1")
    after_first = (await _item(session_factory)).updated_at
    version_after_first = dict(fake_redis.values)

    # Бот повторил тот же callback: тот же id нажатия, то же абсолютное время.
    await _snooze(service, user_id, item_id, snoozed_to, "press-1")

    assert await _notifications(session_factory) == [
        (datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW), "sent"),
        (snoozed_to, "pending"),
    ]
    assert (await _item(session_factory)).updated_at == after_first
    assert fake_redis.values == version_after_first


async def test_double_click_done_changes_nothing_second_time(
    service: ItemService,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    fake_redis: FakeRedis,
    user_id: int,
) -> None:
    item_id = await _fired_reminder(
        service, session_factory, clock, user_id, datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW)
    )
    await _complete(service, user_id, item_id, "press-1")
    after_first = (await _item(session_factory)).updated_at
    version_after_first = dict(fake_redis.values)

    await _complete(service, user_id, item_id, "press-1")

    assert (await _item(session_factory)).updated_at == after_first
    assert fake_redis.values == version_after_first
    assert [status for _, status in await _notifications(session_factory)] == ["sent"]
