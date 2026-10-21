"""Создание подключений к PostgreSQL и Redis."""

from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_ASYNC_DRIVER = "postgresql+asyncpg"


def create_db_engine(database_url: str) -> AsyncEngine:
    # DATABASE_URL общий для всех ролей, поэтому в нём обычная схема postgresql://,
    # а асинхронный драйвер выбирается здесь.
    url = make_url(database_url).set(drivername=_ASYNC_DRIVER)
    # pre_ping отбрасывает соединения из пула, порванные перезапуском PostgreSQL,
    # иначе первый запрос после перезапуска падает.
    return create_async_engine(url, pool_pre_ping=True)


def create_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url)
