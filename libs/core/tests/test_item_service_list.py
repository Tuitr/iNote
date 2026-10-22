from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from libs.contracts.tools import CreateEventArgs, CreateTaskArgs, ListItemsArgs
from libs.core.errors import InvalidArgumentsError, NotFoundError
from libs.core.services.items import MAX_LIST_ITEMS, ItemService
from libs.core.services.time import now_utc, to_user_tz

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
TOMORROW = to_user_tz(now_utc(), "Europe/Moscow").date() + timedelta(days=1)


def _at(clock: str, day: date = TOMORROW) -> str:
    return datetime.combine(day, time.fromisoformat(clock), tzinfo=MOSCOW).isoformat()


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


def _list(user_id: int, start: date = TOMORROW, days: int = 1, **fields: Any) -> ListItemsArgs:
    return ListItemsArgs.model_validate(
        {
            "caller": _caller(user_id),
            "from": start.isoformat(),
            "to": (start + timedelta(days=days)).isoformat(),
            **fields,
        }
    )


async def _task(service: ItemService, user_id: int, title: str, **fields: Any) -> None:
    await service.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(user_id), "title": title, **fields})
    )


async def _event(service: ItemService, user_id: int, title: str, starts_at: str) -> None:
    await service.create_event(
        CreateEventArgs.model_validate(
            {"caller": _caller(user_id), "title": title, "starts_at": starts_at}
        )
    )


async def test_what_do_i_have_tomorrow(item_service: ItemService, user_id: int) -> None:
    await _event(item_service, user_id, "Встреча с Иваном", _at("15:00"))
    await _task(item_service, user_id, "Позвонить Ивану", kind="reminder", remind_at=_at("10:00"))
    await _task(item_service, user_id, "Купить молоко", starts_at=TOMORROW.isoformat())
    await _task(item_service, user_id, "Без срока")
    await _event(item_service, user_id, "Послезавтра", _at("09:00", TOMORROW + timedelta(days=1)))

    result = await item_service.list_items(_list(user_id))

    assert [item.title for item in result.items] == [
        "Купить молоко",
        "Позвонить Ивану",
        "Встреча с Иваном",
    ]
    assert result.items[2].starts_at is not None
    assert result.items[2].starts_at.isoformat() == _at("15:00")
    assert (result.range.from_, result.range.to) == (TOMORROW, TOMORROW + timedelta(days=1))
    assert result.truncated is False


async def test_filters_by_kind(item_service: ItemService, user_id: int) -> None:
    await _event(item_service, user_id, "Встреча с Иваном", _at("15:00"))
    await _task(item_service, user_id, "Позвонить Ивану", kind="reminder", remind_at=_at("10:00"))

    result = await item_service.list_items(_list(user_id, kind=["event"]))

    assert [item.title for item in result.items] == ["Встреча с Иваном"]


async def test_more_than_thirty_items_are_truncated(
    item_service: ItemService, user_id: int
) -> None:
    for minute in range(MAX_LIST_ITEMS + 1):
        await _task(item_service, user_id, f"Задача {minute}", starts_at=_at(f"12:{minute:02}"))

    result = await item_service.list_items(_list(user_id))

    assert len(result.items) == MAX_LIST_ITEMS
    assert result.truncated is True


@pytest.mark.parametrize("days", [0, -1])
async def test_empty_or_reversed_period_is_invalid(
    item_service: ItemService, user_id: int, days: int
) -> None:
    with pytest.raises(InvalidArgumentsError) as error:
        await item_service.list_items(_list(user_id, days=days))

    assert error.value.details == {"invalid": ["to"]}


async def test_unknown_user_is_not_found(item_service: ItemService, user_id: int) -> None:
    with pytest.raises(NotFoundError):
        await item_service.list_items(_list(user_id + 1))
