"""Scheduler в нескольких репликах против одной базы: каждое напоминание доставлено один раз.

Реплика — свой движок с пулом подключений, свой ReminderService и свой клиент бота, как два
контейнера `scheduler`. Бот — заглушка, которая считает вызовы по notification_id.
"""

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.db.models import NotificationRow
from libs.core.resources import create_db_engine, create_session_factory
from libs.core.services.reminders import ReminderService
from libs.core.tests.fakes import FakeRedis
from services.scheduler.main import run_cycle
from services.scheduler.notify import BotNotifier

pytestmark = pytest.mark.anyio

DUE = 200


class CountingBot:
    """Бот, который отвечает не сразу: так реплики действительно работают вперемешку."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls[json.loads(request.content)["notification_id"]] += 1
        await asyncio.sleep(0.001)
        return httpx.Response(200, json={"delivered": True})


@pytest.fixture
async def replicas(clean_database_url: URL) -> AsyncIterator[list[ReminderService]]:
    engines = [
        create_db_engine(clean_database_url.render_as_string(hide_password=False)) for _ in range(2)
    ]
    try:
        yield [ReminderService(create_session_factory(engine), FakeRedis()) for engine in engines]
    finally:
        for engine in engines:
            await engine.dispose()


def _notifier(bot: CountingBot) -> BotNotifier:
    transport = httpx.MockTransport(bot)
    return BotNotifier(httpx.AsyncClient(base_url="http://bot:8000", transport=transport), "t")


async def _due_reminders(session_factory: async_sessionmaker[AsyncSession], count: int) -> None:
    """count напоминаний, созревших минуту назад по часам базы, у пользователя с чатом."""
    async with session_factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO users (max_user_id, max_chat_id, tz) VALUES (1, 2, 'Europe/Moscow')")
        )
        await session.execute(
            text(
                "INSERT INTO items (id, user_id, kind, title, remind_at)"
                " SELECT gen_random_uuid(), u.id, 'reminder', 'Напоминание ' || n,"
                "        now() - interval '1 minute'"
                " FROM users u CROSS JOIN generate_series(1, :count) AS n"
            ),
            {"count": count},
        )
        await session.execute(
            text(
                "INSERT INTO notifications (id, user_id, item_id, fire_at)"
                " SELECT gen_random_uuid(), user_id, id, remind_at FROM items"
            )
        )


async def _statuses(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with session_factory() as session:
        rows = await session.execute(
            select(NotificationRow.status, func.count()).group_by(NotificationRow.status)
        )
        return {status: count for status, count in rows}


async def test_two_replicas_deliver_each_reminder_exactly_once(
    session_factory: async_sessionmaker[AsyncSession], replicas: list[ReminderService]
) -> None:
    await _due_reminders(session_factory, DUE)
    bots = [CountingBot() for _ in replicas]

    await asyncio.gather(
        *(run_cycle(replica, _notifier(bot)) for replica, bot in zip(replicas, bots, strict=True))
    )

    calls = bots[0].calls + bots[1].calls
    assert len(calls) == DUE
    assert set(calls.values()) == {1}
    # Работали обе реплики, а не одна за обеих.
    assert all(bot.calls for bot in bots)
    assert await _statuses(session_factory) == {"sent": DUE}


async def test_crashed_replica_batch_is_delivered_by_the_other_once_in_db(
    session_factory: async_sessionmaker[AsyncSession], replicas: list[ReminderService]
) -> None:
    # Реплика A взяла пачку, успела позвать бота по первому и умерла, не записав итог.
    crashed, survivor = replicas
    await _due_reminders(session_factory, 3)
    bot = CountingBot()
    batch = await crashed.claim_due()
    await _notifier(bot).notify(batch.due[0])
    async with session_factory() as session, session.begin():
        # Аренда истекла: в бою это LEASE после падения.
        await session.execute(
            text("UPDATE notifications SET lease_until = now() - interval '1 second'")
        )

    await run_cycle(survivor, _notifier(bot))

    first = str(batch.due[0].notification_id)
    # Повторный вызов того же notification_id допустим: бот отсекает дубль сам (J4).
    assert bot.calls[first] == 2
    assert sorted(count for key, count in bot.calls.items() if key != first) == [1, 1]
    assert await _statuses(session_factory) == {"sent": 3}


async def test_replica_clock_does_not_matter_only_database_now(
    session_factory: async_sessionmaker[AsyncSession],
    replicas: list[ReminderService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # У реплики часы убежали на сутки вперёд: напоминание через час она не шлёт раньше времени.
    await _due_reminders(session_factory, 1)
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO items (id, user_id, kind, title, remind_at)"
                " SELECT gen_random_uuid(), id, 'reminder', 'Через час', now() + interval '1 hour'"
                " FROM users"
            )
        )
        await session.execute(
            text(
                "INSERT INTO notifications (id, user_id, item_id, fire_at)"
                " SELECT gen_random_uuid(), user_id, id, remind_at FROM items"
                " WHERE title = 'Через час'"
            )
        )
    ahead = datetime.now(UTC) + timedelta(days=1)

    class FastClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return ahead

    monkeypatch.setattr("libs.core.services.time.datetime", FastClock)
    bot = CountingBot()

    await asyncio.gather(*(run_cycle(replica, _notifier(bot)) for replica in replicas))

    assert len(bot.calls) == 1
    assert await _statuses(session_factory) == {"sent": 1, "pending": 1}
