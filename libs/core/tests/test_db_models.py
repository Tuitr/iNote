from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.db.models import ItemRow, UserRow

pytestmark = pytest.mark.anyio

MOSCOW_OFFSET = timezone(timedelta(hours=3))


async def test_rows_are_written_and_read_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    item_id = uuid4()
    remind_at = datetime(2026, 10, 22, 10, 0, tzinfo=MOSCOW_OFFSET)
    async with session_factory() as session, session.begin():
        user = UserRow(max_user_id=123456789, tz="Europe/Moscow")
        session.add(user)
        await session.flush()
        session.add(
            ItemRow(
                id=item_id,
                user_id=user.id,
                kind="reminder",
                title="Позвонить Ивану",
                remind_at=remind_at,
            )
        )

    async with session_factory() as session:
        item = await session.scalar(select(ItemRow).where(ItemRow.id == item_id))
        user = await session.scalar(select(UserRow).where(UserRow.max_user_id == 123456789))

    assert user is not None
    assert user.id == 1
    assert (user.voice_replies, user.default_event_min, user.remind_before_min) == (False, 60, 15)
    assert item is not None
    assert item.user_id == user.id
    assert (item.status, item.all_day, item.participants) == ("open", False, [])
    # Тот же момент, но база отдаёт его в UTC: перевод в пояс пользователя — дело TimeService.
    assert item.remind_at == remind_at
    assert item.remind_at.utcoffset() == timedelta(0)
    assert item.created_at.tzinfo is not None
    assert item.created_at > datetime(2026, 1, 1, tzinfo=UTC)
