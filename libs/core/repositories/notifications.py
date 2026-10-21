"""Запросы к таблице notifications. Пишет ItemService, читает и отправляет Scheduler (S3)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.db.models import NotificationRow


class NotificationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def schedule(self, user_id: int, item_id: UUID, fire_at: datetime) -> None:
        """Ждущее уведомление на fire_at; если на это время уже было (отменённое) — оживить его.

        Так перенос «на 12 и обратно на 10» не упирается в unique (item_id, fire_at).
        """
        statement = (
            insert(NotificationRow)
            .values(id=uuid4(), user_id=user_id, item_id=item_id, fire_at=fire_at)
            .on_conflict_do_update(
                index_elements=[NotificationRow.item_id, NotificationRow.fire_at],
                set_={
                    "status": "pending",
                    "attempt": 0,
                    "lease_until": None,
                    "next_try_at": None,
                    "last_error": None,
                },
                where=NotificationRow.user_id == user_id,
            )
        )
        await self._session.execute(statement)

    async def cancel_pending(self, user_id: int, item_id: UUID) -> None:
        """Отменить неотправленные уведомления записи: её перенесли, выполнили или удалили.

        Уведомление в статусе sending уже у Scheduler: перед отправкой он сам перепроверит запись.
        """
        await self._session.execute(
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.item_id == item_id,
                NotificationRow.status == "pending",
            )
            .values(status="cancelled")
        )
