import asyncio
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.settings import UserSettings
from libs.core.db.models import UserRow
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis

pytestmark = pytest.mark.anyio

MAX_USER_ID = 123456789


@pytest.fixture
def user_service(session_factory: async_sessionmaker[AsyncSession]) -> UserService:
    return UserService(session_factory, "Europe/Moscow", FakeRedis())


async def test_new_user_is_created_with_default_settings(user_service: UserService) -> None:
    resolved = await user_service.resolve(MAX_USER_ID, 987654321, "Степан")

    assert resolved.created is True
    assert resolved.settings == UserSettings(
        tz="Europe/Moscow", voice_replies=False, default_event_min=60, remind_before_min=15
    )


async def test_known_user_is_found_not_created_again(user_service: UserService) -> None:
    first = await user_service.resolve(MAX_USER_ID, 987654321, "Степан")

    second = await user_service.resolve(MAX_USER_ID, 987654321, "Степан")

    assert second.created is False
    assert second.user_id == first.user_id


async def test_different_max_users_get_different_ids(user_service: UserService) -> None:
    first = await user_service.resolve(MAX_USER_ID, None, None)
    second = await user_service.resolve(MAX_USER_ID + 1, None, None)

    assert first.user_id != second.user_id


async def test_parallel_resolve_of_new_user_creates_one_row(user_service: UserService) -> None:
    # Бот повторил апдейт: два запроса за одним и тем же новым пользователем пришли одновременно.
    results = await asyncio.gather(
        *(user_service.resolve(MAX_USER_ID, 987654321, "Степан") for _ in range(5))
    )

    assert len({result.user_id for result in results}) == 1
    assert sorted(result.created for result in results) == [False, False, False, False, True]


async def _stored(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int | None, str | None]:
    async with session_factory() as session:
        user = await session.scalar(select(UserRow).where(UserRow.max_user_id == MAX_USER_ID))
    assert user is not None
    return user.max_chat_id, user.name


async def test_known_user_gets_new_chat_id_and_name(
    user_service: UserService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # Пользователь удалил чат с ботом и начал заново: напоминания должны идти в новый чат.
    await user_service.resolve(MAX_USER_ID, 987654321, "Степан")

    second = await user_service.resolve(MAX_USER_ID, 111222333, "Стёпа")

    assert second.created is False
    assert await _stored(session_factory) == (111222333, "Стёпа")


async def test_missing_chat_id_and_name_do_not_erase_stored(
    user_service: UserService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await user_service.resolve(MAX_USER_ID, 987654321, "Степан")

    await user_service.resolve(MAX_USER_ID, None, None)

    assert await _stored(session_factory) == (987654321, "Степан")


async def test_unchanged_resolve_does_not_write(
    user_service: UserService, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await user_service.resolve(MAX_USER_ID, 987654321, "Степан")
    updates: list[str] = []

    def remember_update(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)

    engine = session_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", remember_update)
    try:
        await user_service.resolve(MAX_USER_ID, 987654321, "Степан")
    finally:
        event.remove(engine, "before_cursor_execute", remember_update)

    assert updates == []
