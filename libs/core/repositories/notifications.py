"""Запросы к таблице notifications. Пишет ItemService, читает и отправляет Scheduler (S3)."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, and_, func, not_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.db.models import ItemRow, NotificationRow


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

    # Scheduler работает за всех пользователей сразу, поэтому у reclaim_expired и claim_due
    # нет user_id. Время везде — now() базы: часы реплик Scheduler могут расходиться.

    async def reclaim_expired(self) -> int:
        """Вернуть в pending уведомления, чья реплика упала, не дождавшись ответа бота."""
        result = await self._session.execute(
            update(NotificationRow)
            .where(NotificationRow.status == "sending", NotificationRow.lease_until < func.now())
            .values(status="pending", lease_until=None)
        )
        return result.rowcount

    async def cancel_stale(self) -> int:
        """Отменить созревшие уведомления, которые уже не про текущее состояние записи.

        Так бывает, когда запись перенесли, выполнили или удалили, пока её уведомление было
        в отправке: после неудачной попытки оно вернулось в pending, а отправлять его нельзя.
        """
        stale = (
            select(NotificationRow.id)
            .join(
                ItemRow,
                (ItemRow.id == NotificationRow.item_id)
                & (ItemRow.user_id == NotificationRow.user_id),
            )
            .where(
                NotificationRow.status == "pending",
                NotificationRow.fire_at <= func.now(),
                or_(
                    ItemRow.deleted_at.is_not(None),
                    ItemRow.status != "open",
                    not_(_is_current()),
                ),
            )
        )
        result = await self._session.execute(
            update(NotificationRow)
            .where(NotificationRow.id.in_(stale.scalar_subquery()))
            .values(status="cancelled")
        )
        return result.rowcount

    async def claim_due(self, limit: int, lease: timedelta) -> list[NotificationRow]:
        """Забрать созревшие уведомления открытых неудалённых записей и взять их в аренду.

        SKIP LOCKED: строки, которые прямо сейчас забирает другая реплика, пропускаются.
        """
        due = (
            select(NotificationRow.id)
            .join(
                ItemRow,
                (ItemRow.id == NotificationRow.item_id)
                & (ItemRow.user_id == NotificationRow.user_id),
            )
            .where(
                NotificationRow.status == "pending",
                NotificationRow.fire_at <= func.now(),
                or_(
                    NotificationRow.next_try_at.is_(None), NotificationRow.next_try_at <= func.now()
                ),
                ItemRow.deleted_at.is_(None),
                ItemRow.status == "open",
                # Напоминание запись перенесли, пока оно было в отправке: старое не воскрешаем.
                _is_current(),
            )
            .order_by(NotificationRow.fire_at)
            .limit(limit)
            .with_for_update(of=NotificationRow, skip_locked=True)
        )
        statement = (
            update(NotificationRow)
            .where(NotificationRow.id.in_(due.scalar_subquery()))
            .values(
                status="sending",
                lease_until=func.now() + lease,
            )
            .returning(NotificationRow)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return list((await self._session.scalars(statement)).all())

    # attempt — сколько попыток доставки закончилось. Растёт только при записи итога:
    # аренда, истёкшая после падения реплики, попыткой не считается, и номер в J4 не уходит за 6.

    async def mark_sent(self, user_id: int, notification_id: UUID) -> None:
        await self._finish(
            user_id,
            notification_id,
            status="sent",
            sent_at=func.now(),
            attempt=NotificationRow.attempt + 1,
        )

    async def mark_failed(self, user_id: int, notification_id: UUID, error: str) -> None:
        await self._finish(
            user_id,
            notification_id,
            status="failed",
            last_error=error,
            attempt=NotificationRow.attempt + 1,
        )

    async def mark_cancelled(self, user_id: int, notification_id: UUID) -> None:
        await self._finish(user_id, notification_id, status="cancelled")

    async def retry_later(
        self, user_id: int, notification_id: UUID, delay: timedelta, error: str
    ) -> None:
        await self._finish(
            user_id,
            notification_id,
            status="pending",
            next_try_at=func.now() + delay,
            last_error=error,
            attempt=NotificationRow.attempt + 1,
        )

    async def _finish(self, user_id: int, notification_id: UUID, **values: object) -> None:
        # Только из sending: если аренда истекла и строку уже вернули в pending, результат
        # опоздавшей попытки её не перетирает.
        await self._session.execute(
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.id == notification_id,
                NotificationRow.status == "sending",
            )
            .values(lease_until=None, **values)
        )


def _is_current() -> ColumnElement[bool]:
    """Уведомление про нынешнее время записи: её remind_at или начало встречи («началась»)."""
    return or_(
        NotificationRow.fire_at == ItemRow.remind_at,
        and_(ItemRow.kind == "event", NotificationRow.fire_at == ItemRow.starts_at),
    )
