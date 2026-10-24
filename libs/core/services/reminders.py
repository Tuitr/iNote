"""Напоминания для Scheduler: забрать созревшие уведомления и записать итог доставки (J4).

Каждый метод — своя транзакция: аренду надо закоммитить до вызова бота, иначе вторая
реплика Scheduler заберёт те же уведомления.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.items import Item
from libs.core.db.models import ItemRow, NotificationRow, UserRow
from libs.core.repositories.items import ItemRepo
from libs.core.repositories.notifications import NotificationRepo
from libs.core.repositories.users import UserRepo
from libs.core.services.items import next_in_series, require_user, reschedule, to_item
from libs.core.services.time import to_user_tz
from libs.core.services.versioning import bump_version

logger = logging.getLogger(__name__)

# Пачка отправляется по одному с таймаутом 10 с: аренда должна пережить всю пачку
# (5 × 10 с) с запасом, иначе вторая реплика заберёт уведомления посреди отправки.
CLAIM_LIMIT = 5
SERIES_LIMIT = 50
LEASE = timedelta(minutes=5)
# Задержка перед повтором после неудачной попытки с этим номером; после последней — failed.
RETRY_DELAYS = tuple(timedelta(minutes=minutes) for minutes in (1, 2, 4, 8, 16))


@dataclass(frozen=True)
class DueReminder:
    notification_id: UUID
    user_id: int
    max_user_id: int
    max_chat_id: int
    tz: str
    item: Item
    fire_at: datetime
    attempt: int


@dataclass(frozen=True)
class ClaimedBatch:
    # Сколько взято в аренду. Отправлять бывает нечего (нет чата, запись удалили),
    # но пока пачка не пустая, созревшие ещё могут быть.
    claimed: int
    due: list[DueReminder]


class Delivery(Enum):
    SENT = "sent"
    # Повторять бесполезно: чат недоступен или бот отверг запрос.
    FAILED = "failed"
    # Бот перегружен, лежит или не ответил вовремя.
    RETRY = "retry"


class ReminderService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis: Redis) -> None:
        self._session_factory = session_factory
        self._redis = redis

    async def reclaim_expired(self) -> int:
        async with self._session_factory() as session, session.begin():
            return await NotificationRepo(session).reclaim_expired()

    async def cancel_stale(self) -> int:
        async with self._session_factory() as session, session.begin():
            return await NotificationRepo(session).cancel_stale()

    async def claim_due(self) -> ClaimedBatch:
        """Созревшие напоминания в аренду; без чата сразу failed — бот их не доставит."""
        async with self._session_factory() as session, session.begin():
            notifications = NotificationRepo(session)
            due: list[DueReminder] = []
            claimed = await notifications.claim_due(CLAIM_LIMIT, LEASE)
            for row in claimed:
                user = await UserRepo(session).get(row.user_id)
                items = await ItemRepo(session).find_by_ids(row.user_id, [row.item_id])
                if not items:
                    # Запись удалили между выборкой уведомлений и чтением записи.
                    await notifications.mark_cancelled(row.user_id, row.id)
                elif user is None or user.max_chat_id is None:
                    logger.warning("Уведомление %s: у пользователя нет max_chat_id", row.id)
                    await notifications.mark_failed(row.user_id, row.id, "no max_chat_id")
                else:
                    due.append(_due(row, user, user.max_chat_id, items[0]))
            return ClaimedBatch(claimed=len(claimed), due=due)

    async def finish(self, reminder: DueReminder, delivery: Delivery, error: str) -> None:
        async with self._session_factory() as session, session.begin():
            notifications = NotificationRepo(session)
            if delivery is Delivery.SENT:
                await notifications.mark_sent(reminder.user_id, reminder.notification_id)
            elif delivery is Delivery.RETRY and reminder.attempt <= len(RETRY_DELAYS):
                delay = RETRY_DELAYS[reminder.attempt - 1]
                await notifications.retry_later(
                    reminder.user_id, reminder.notification_id, delay, error
                )
            else:
                await notifications.mark_failed(reminder.user_id, reminder.notification_id, error)

    async def schedule_next(self) -> int:
        """Серии, чьё вхождение прошло без «Выполнено», переезжают на следующее вхождение.

        Без этого «каждый понедельник планёрка» напомнила бы один раз: встречу обычно
        никто не отмечает выполненной. Закончившаяся серия (COUNT, UNTIL) становится done.
        """
        async with self._session_factory() as session, session.begin():
            # «Сейчас» — по часам базы, как и выборка прошедших вхождений.
            now = await session.scalar(select(func.now()))
            items = ItemRepo(session)
            moved: list[ItemRow] = []
            for row in await items.lock_passed_series(SERIES_LIMIT):
                user = await require_user(session, row.user_id)
                values = next_in_series(row, user.tz, now) or {"status": "done"}
                updated = await items.update(row.user_id, row.id, values)
                await reschedule(session, updated, now)
                moved.append(updated)
        for user_id in {row.user_id for row in moved}:
            await bump_version(self._redis, user_id)
        return len(moved)


def _due(row: NotificationRow, user: UserRow, max_chat_id: int, item: ItemRow) -> DueReminder:
    return DueReminder(
        notification_id=row.id,
        user_id=user.id,
        max_user_id=user.max_user_id,
        max_chat_id=max_chat_id,
        tz=user.tz,
        item=to_item(item, user.tz),
        fire_at=to_user_tz(row.fire_at, user.tz),
        # Номер этой попытки для J4: закончившихся попыток было row.attempt.
        attempt=row.attempt + 1,
    )
