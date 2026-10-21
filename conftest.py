"""Общие фикстуры тестов D."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from libs.core.resources import create_db_engine, create_session_factory
from libs.core.services.items import ItemService
from libs.core.services.search import SearchService
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis
from services.mcp.tools.context import ToolServices

ALEMBIC_INI = Path(__file__).resolve().parent / "alembic.ini"
POSTGRES_IMAGE = "pgvector/pgvector:pg16"
_TRUNCATE_ALL = text("TRUNCATE dialog_log, notifications, items, users RESTART IDENTITY")


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[URL]:
    """PostgreSQL 16 с pgvector в Docker на время прогона — тот же образ, что в compose.

    URL, а не строка: pytest печатает аргументы упавшего теста, а URL показывает пароль как ***.
    """
    container = PostgresContainer(POSTGRES_IMAGE, driver=None)
    try:
        container.start()
    except DockerException as error:
        pytest.skip(f"Docker недоступен, тесты с базой пропущены: {type(error).__name__}")
    try:
        yield make_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_database_url(test_database_url: URL) -> URL:
    """Тестовая база со схемой, построенной с нуля по текущим миграциям, один раз на прогон."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", test_database_url.render_as_string(hide_password=False))
        config = Config(str(ALEMBIC_INI))
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    return test_database_url


@pytest.fixture
def clean_database_url(migrated_database_url: URL) -> URL:
    """Тестовая база с пустыми таблицами: каждый тест начинает с чистого листа."""

    async def truncate_all() -> None:
        engine = create_db_engine(migrated_database_url.render_as_string(hide_password=False))
        try:
            async with engine.begin() as connection:
                await connection.execute(_TRUNCATE_ALL)
        finally:
            await engine.dispose()

    asyncio.run(truncate_all())
    return migrated_database_url


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def anyio_backend() -> str:
    # Асинхронные тесты (@pytest.mark.anyio) идут на asyncio, как сервисы; trio не нужен.
    return "asyncio"


@pytest.fixture
async def session_factory(
    clean_database_url: URL,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_db_engine(clean_database_url.render_as_string(hide_password=False))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def item_service(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
) -> ItemService:
    return ItemService(session_factory, fake_redis)


@pytest.fixture
async def user_id(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Пользователь из Москвы, от имени которого тесты создают записи."""
    resolved = await UserService(session_factory, "Europe/Moscow", FakeRedis()).resolve(
        123456789, None, None
    )
    return resolved.user_id


@pytest.fixture
def search_service(session_factory: async_sessionmaker[AsyncSession]) -> SearchService:
    return SearchService(session_factory)


@pytest.fixture
def tool_services(item_service: ItemService, search_service: SearchService) -> ToolServices:
    return ToolServices(items=item_service, search=search_service)
