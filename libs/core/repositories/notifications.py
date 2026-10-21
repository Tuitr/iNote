"""Запросы к таблице notifications. В S1 таблица только пополняется, читает её Scheduler (S3)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.db.models import NotificationRow


class NotificationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, user_id: int, item_id: UUID, fire_at: datetime) -> None:
        """Уведомление уходит в базу вместе с коммитом транзакции, в которой создана запись."""
        self._session.add(
            NotificationRow(id=uuid4(), user_id=user_id, item_id=item_id, fire_at=fire_at)
        )
