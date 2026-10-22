"""Напоминания для Scheduler: забрать созревшие уведомления и записать итог доставки (J4).

Каждый метод — своя транзакция: аренду надо закоммитить до вызова бота, иначе вторая
реплика Scheduler заберёт те же уведомления.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.items import Item
from libs.core.db.models import ItemRow, NotificationRow, UserRow
from libs.core.repositories.items import ItemRepo
from libs.core.repositories.notifications import NotificationRepo
from libs.core.repositories.users import UserRepo
from libs.core.services.items import to_item
from libs.core.services.time import to_user_tz

logger = logging.getLogger(__name__)

CLAIM_LIMIT = 50
LEASE = timedelta(seconds=60)
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


class Delivery(Enum):
    SENT = "sent"
    # Повторять бесполезно: чат недоступен или бот отверг запрос.
    FAILED = "failed"
    # Бот перегружен, лежит или не ответил вовремя.
    RETRY = "retry"


class ReminderService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def reclaim_expired(self) -> int:
        async with self._session_factory() as session, session.begin():
            return await NotificationRepo(session).reclaim_expired()

    async def claim_due(self) -> list[DueReminder]:
        """Созревшие напоминания в аренду; без чата сразу failed — бот их не доставит."""
        async with self._session_factory() as session, session.begin():
            notifications = NotificationRepo(session)
            due: list[DueReminder] = []
            for row in await notifications.claim_due(CLAIM_LIMIT, LEASE):
                user = await UserRepo(session).get(row.user_id)
                items = await ItemRepo(session).find_by_ids(row.user_id, [row.item_id])
                if not items:
                    # Запись удалили между выборкой уведомлений и чтением записи.
                    await notifications.mark_cancelled(row.user_id, row.id)
                elif user is None or user.max_chat_id is None:
                    logger.warning("Уведомление %s: у пользователя нет max_chat_id", row.id)
                    await notifications.mark_failed(row.user_id, row.id, "no max_chat_id")
                else:
                    due.append(_due(row, user, items[0]))
            return due

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


def _due(row: NotificationRow, user: UserRow, item: ItemRow) -> DueReminder:
    if user.max_chat_id is None:
        raise ValueError("Напоминание без max_chat_id не отправляется")
    return DueReminder(
        notification_id=row.id,
        user_id=user.id,
        max_user_id=user.max_user_id,
        max_chat_id=user.max_chat_id,
        tz=user.tz,
        item=to_item(item, user.tz),
        fire_at=to_user_tz(row.fire_at, user.tz),
        attempt=row.attempt,
    )
