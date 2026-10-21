import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.settings import UserSettings
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
