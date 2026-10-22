"""Scheduler на настоящей базе, бот — заглушка httpx.MockTransport.

Время «пора ли отправлять» берётся из now() базы, его не заморозить: вместо ожидания
тесты сдвигают fire_at и next_try_at в прошлое.
"""

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CompleteItemArgs, CreateTaskArgs
from libs.core.db.models import ItemRow, NotificationRow
from libs.core.services.items import ItemService
from libs.core.services.reminders import ReminderService
from libs.core.services.time import now_utc
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis
from services.scheduler.main import run_cycle
from services.scheduler.notify import BotNotifier

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")
MAX_USER_ID = 123456789
MAX_CHAT_ID = 987654321
SERVICE_TOKEN = "test-service-token"  # noqa: S105 — токен только для тестов

Handler = Callable[[httpx.Request], httpx.Response]


class FakeBot:
    """Записывает вызовы /notify и отвечает тем, что вернёт answer."""

    def __init__(self, answer: Handler | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._answer = answer or (lambda _: httpx.Response(200, json={"delivered": True}))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def _notifier(bot: FakeBot) -> BotNotifier:
    client = httpx.AsyncClient(base_url="http://bot:8000", transport=httpx.MockTransport(bot))
    return BotNotifier(client, SERVICE_TOKEN)


@pytest.fixture
def reminders(session_factory: async_sessionmaker[AsyncSession]) -> ReminderService:
    return ReminderService(session_factory)


async def _user(session_factory: async_sessionmaker[AsyncSession], chat_id: int | None) -> int:
    resolved = await UserService(session_factory, "Europe/Moscow", FakeRedis()).resolve(
        MAX_USER_ID, chat_id, "Степан"
    )
    return resolved.user_id


async def _reminder(
    item_service: ItemService, user_id: int, title: str = "Позвонить Ивану"
) -> UUID:
    """Напоминание через 2 минуты: «Напомни через 2 минуты позвонить Ивану»."""
    remind_at = (now_utc() + timedelta(minutes=2)).astimezone(MOSCOW).replace(microsecond=0)
    result = await item_service.create_task(
        CreateTaskArgs.model_validate(
            {
                "caller": {
                    "user_id": user_id,
                    "tz": "Europe/Moscow",
                    "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
                },
                "title": title,
                "kind": "reminder",
                "remind_at": remind_at.isoformat(),
            }
        )
    )
    assert result.item is not None
    return result.item.id


async def _execute(
    session_factory: async_sessionmaker[AsyncSession], sql: str, **params: Any
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(text(sql), params)


async def _time_passes(
    session_factory: async_sessionmaker[AsyncSession], late: timedelta = timedelta(seconds=1)
) -> None:
    """Напоминание созрело: fire_at и время повтора — уже в прошлом по часам базы."""
    await _execute(
        session_factory,
        "UPDATE notifications SET fire_at = now() - CAST(:late AS interval)"
        " WHERE status = 'pending' AND fire_at > now() - CAST(:late AS interval)",
        late=late,
    )
    await _execute(
        session_factory,
        "UPDATE notifications SET next_try_at = now() - interval '1 second'"
        " WHERE next_try_at IS NOT NULL",
    )


async def _notification(session_factory: async_sessionmaker[AsyncSession]) -> NotificationRow:
    async with session_factory() as session:
        row = await session.scalar(select(NotificationRow))
    assert row is not None
    return row


async def _seconds_until_retry(session_factory: async_sessionmaker[AsyncSession]) -> float:
    async with session_factory() as session:
        seconds = await session.scalar(
            text("SELECT extract(epoch FROM next_try_at - now()) FROM notifications")
        )
    return float(seconds)


async def test_reminder_in_two_minutes_is_sent_once_when_due(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    user_id = await _user(session_factory, MAX_CHAT_ID)
    item_id = await _reminder(item_service, user_id)
    bot = FakeBot()
    notifier = _notifier(bot)

    await run_cycle(reminders, notifier)
    calls_before_due = len(bot.requests)
    await _time_passes(session_factory)
    await run_cycle(reminders, notifier)
    await run_cycle(reminders, notifier)

    assert calls_before_due == 0
    assert len(bot.requests) == 1
    request = bot.requests[0]
    stored = await _notification(session_factory)
    assert request.url.path == "/internal/v1/notify"
    assert request.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert request.headers["Idempotency-Key"] == str(stored.id)
    assert len(request.headers["X-Trace-Id"]) == 32
    body = bot.bodies()[0]
    assert body["notification_id"] == str(stored.id)
    assert body["kind"] == "reminder"
    assert body["user"] == {
        "max_user_id": MAX_USER_ID,
        "max_chat_id": MAX_CHAT_ID,
        "tz": "Europe/Moscow",
    }
    assert body["item"]["id"] == str(item_id)
    assert body["item"]["kind"] == "reminder"
    assert body["item"]["remind_at"].endswith("+03:00")
    assert body["attempt"] == 1
    assert (stored.status, stored.attempt, stored.lease_until) == ("sent", 1, None)
    assert stored.sent_at is not None


async def test_late_reminder_is_still_sent_with_original_fire_at(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    # Scheduler лежал 2 часа: напоминание всё равно уходит, бот видит исходное время.
    user_id = await _user(session_factory, MAX_CHAT_ID)
    await _reminder(item_service, user_id)
    await _time_passes(session_factory, late=timedelta(hours=2))
    planned = (await _notification(session_factory)).fire_at
    bot = FakeBot()

    await run_cycle(reminders, _notifier(bot))

    assert [body["fire_at"] for body in bot.bodies()] == [planned.astimezone(MOSCOW).isoformat()]
    assert (await _notification(session_factory)).status == "sent"


@pytest.mark.parametrize(
    "change", [{"deleted_at": text("now()")}, {"status": "done"}, {"status": "cancelled"}]
)
async def test_deleted_done_or_cancelled_item_does_not_remind(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
    change: dict[str, Any],
) -> None:
    user_id = await _user(session_factory, MAX_CHAT_ID)
    item_id = await _reminder(item_service, user_id)
    # Меняем запись в обход ItemService: уведомление осталось pending, фильтр — у Scheduler.
    async with session_factory() as session, session.begin():
        await session.execute(update(ItemRow).where(ItemRow.id == item_id).values(**change))
    await _time_passes(session_factory)
    bot = FakeBot()

    await run_cycle(reminders, _notifier(bot))

    assert bot.requests == []
    assert (await _notification(session_factory)).status == "pending"


async def test_bot_errors_are_retried_after_1_2_4_8_16_minutes_then_failed(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    user_id = await _user(session_factory, MAX_CHAT_ID)
    await _reminder(item_service, user_id)
    bot = FakeBot(lambda _: httpx.Response(503, json={"error": {"code": "upstream_unavailable"}}))
    notifier = _notifier(bot)
    delays_min: list[int] = []

    for _ in range(6):
        await _time_passes(session_factory)
        await run_cycle(reminders, notifier)
        if (await _notification(session_factory)).status == "pending":
            delays_min.append(round(await _seconds_until_retry(session_factory) / 60))

    stored = await _notification(session_factory)
    assert delays_min == [1, 2, 4, 8, 16]
    assert [body["attempt"] for body in bot.bodies()] == [1, 2, 3, 4, 5, 6]
    assert (stored.status, stored.last_error) == ("failed", "HTTP 503")


async def test_rate_limit_and_timeout_are_retried(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    user_id = await _user(session_factory, MAX_CHAT_ID)
    await _reminder(item_service, user_id)

    def answer(request: httpx.Request) -> httpx.Response:
        call = len(bot.requests)
        if call == 1:
            return httpx.Response(429)
        if call == 2:
            raise httpx.ReadTimeout("бот не ответил за 10 секунд", request=request)
        return httpx.Response(200, json={"delivered": True, "duplicate": True})

    bot = FakeBot(answer)
    notifier = _notifier(bot)
    errors: list[str | None] = []

    for _ in range(3):
        await _time_passes(session_factory)
        await run_cycle(reminders, notifier)
        errors.append((await _notification(session_factory)).last_error)

    assert errors == ["HTTP 429", "timeout", "timeout"]
    assert (await _notification(session_factory)).status == "sent"


async def test_chat_not_found_fails_without_retries(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    # Пользователь заблокировал бота.
    user_id = await _user(session_factory, MAX_CHAT_ID)
    await _reminder(item_service, user_id)
    bot = FakeBot(
        lambda _: httpx.Response(
            404, json={"error": {"code": "chat_not_found", "message": "Чат недоступен"}}
        )
    )
    notifier = _notifier(bot)

    await _time_passes(session_factory)
    await run_cycle(reminders, notifier)
    await _time_passes(session_factory)
    await run_cycle(reminders, notifier)

    stored = await _notification(session_factory)
    assert len(bot.requests) == 1
    assert (stored.status, stored.last_error) == ("failed", "HTTP 404 chat_not_found")


async def test_user_without_chat_fails_without_calling_bot(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    user_id = await _user(session_factory, chat_id=None)
    await _reminder(item_service, user_id)
    await _time_passes(session_factory)
    bot = FakeBot()

    await run_cycle(reminders, _notifier(bot))

    stored = await _notification(session_factory)
    assert bot.requests == []
    assert (stored.status, stored.last_error) == ("failed", "no max_chat_id")


async def test_expired_lease_is_reclaimed_and_sent(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    # Реплика забрала уведомление и упала, не дождавшись бота.
    user_id = await _user(session_factory, MAX_CHAT_ID)
    await _reminder(item_service, user_id)
    await _time_passes(session_factory)
    await reminders.claim_due()
    await _execute(session_factory, "UPDATE notifications SET lease_until = now() - interval '1 s'")
    bot = FakeBot()

    await run_cycle(reminders, _notifier(bot))

    stored = await _notification(session_factory)
    assert [body["attempt"] for body in bot.bodies()] == [2]
    assert stored.status == "sent"


async def test_parallel_claims_do_not_take_the_same_notification(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    user_id = await _user(session_factory, MAX_CHAT_ID)
    for number in range(6):
        await _reminder(item_service, user_id, title=f"Напоминание {number}")
    await _time_passes(session_factory)

    first, second = await asyncio.gather(reminders.claim_due(), reminders.claim_due())

    first_ids = {reminder.notification_id for reminder in first}
    second_ids = {reminder.notification_id for reminder in second}
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 6


async def test_done_button_before_due_means_no_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    item_service: ItemService,
    reminders: ReminderService,
) -> None:
    # «Выполнено» из уведомления: агент вызывает complete_item с id нажатия как ключом.
    user_id = await _user(session_factory, MAX_CHAT_ID)
    item_id = await _reminder(item_service, user_id)
    await item_service.complete_item(
        CompleteItemArgs.model_validate(
            {
                "caller": {
                    "user_id": user_id,
                    "tz": "Europe/Moscow",
                    "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
                    "idempotency_key": "callback-1",
                },
                "id": str(item_id),
            }
        )
    )
    await _execute(session_factory, "UPDATE notifications SET fire_at = now() - interval '1 s'")
    bot = FakeBot()

    await run_cycle(reminders, _notifier(bot))

    assert bot.requests == []
    assert (await _notification(session_factory)).status == "cancelled"
