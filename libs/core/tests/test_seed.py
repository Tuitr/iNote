"""Демо-данные: «Что у меня на неделе?» и «Найди про Ивана» дают осмысленный ответ."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import ListItemsArgs, SearchItemsArgs
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.seed import DEMO_ITEMS, seed_demo
from libs.core.services.items import ItemService
from libs.core.services.search import SearchService
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
# Среда, 21 октября 2026, 12:00 по Москве.
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=MOSCOW)


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


async def _count(session_factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_second_run_creates_nothing(
    session_factory: async_sessionmaker[AsyncSession], fake_redis: FakeRedis, user_id: int
) -> None:
    first = await seed_demo(session_factory, fake_redis, user_id, NOW)
    second = await seed_demo(session_factory, fake_redis, user_id, NOW)

    assert (first.created, first.already_there) == (len(DEMO_ITEMS), 0)
    assert (second.created, second.already_there) == (0, len(DEMO_ITEMS))
    assert 15 <= await _count(session_factory, ItemRow) == len(DEMO_ITEMS) <= 20
    assert fake_redis.values == {f"va:v1:mcp:ver:{user_id}": 1}


async def test_what_do_i_have_this_week(
    session_factory: async_sessionmaker[AsyncSession], fake_redis: FakeRedis, user_id: int
) -> None:
    await seed_demo(session_factory, fake_redis, user_id, NOW)

    week = await ItemService(session_factory, fake_redis, clock=lambda: NOW).list_items(
        ListItemsArgs.model_validate(
            {"caller": _caller(user_id), "from": "2026-10-19", "to": "2026-10-26"}
        )
    )

    assert [item.title for item in week.items] == [
        "Забрать посылку",
        "Встреча с Иваном: итоги квартала",
        "Купить продукты на неделю",
    ]


async def test_find_about_ivan_and_by_meaning(
    session_factory: async_sessionmaker[AsyncSession], fake_redis: FakeRedis, user_id: int
) -> None:
    await seed_demo(session_factory, fake_redis, user_id, NOW)
    search = SearchService(session_factory)

    async def titles(query: str) -> list[str]:
        result = await search.search(
            SearchItemsArgs.model_validate({"caller": _caller(user_id), "query": query})
        )
        return [hit.item.title for hit in result.results]

    about_ivan = await titles("Иван")
    dentist = await titles("зубной врач")
    deleted = await titles("черновик")

    assert {"Встреча с Иваном по бюджету", "Подготовить отчёт для Ивана"} <= set(about_ivan)
    assert "Ретроспектива спринта" in about_ivan
    assert dentist[0] == "Записаться к стоматологу"
    assert deleted == []


async def test_only_upcoming_open_records_get_reminders(
    session_factory: async_sessionmaker[AsyncSession], fake_redis: FakeRedis, user_id: int
) -> None:
    await seed_demo(session_factory, fake_redis, user_id, NOW)

    async with session_factory() as session:
        rows = await session.execute(
            select(ItemRow.title, NotificationRow.fire_at)
            .join(ItemRow, ItemRow.id == NotificationRow.item_id)
            .where(NotificationRow.status == "pending")
            .order_by(NotificationRow.fire_at)
        )
        reminders = [
            (title, fire_at.astimezone(MOSCOW).strftime("%d.%m %H:%M")) for title, fire_at in rows
        ]

    assert reminders == [
        ("Забрать посылку", "22.10 18:00"),
        ("Встреча с Иваном: итоги квартала", "23.10 14:45"),
        ("Планёрка", "26.10 08:45"),
        ("Приём у терапевта", "26.10 10:45"),
    ]
