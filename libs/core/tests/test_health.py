import asyncio

from libs.core.health import check_postgres, check_readiness, check_redis
from libs.core.resources import create_db_engine, create_redis_client

# На порту 1 никто не слушает: подключение получит отказ (Linux) или упрётся
# в таймаут проверки (Windows), в обоих случаях ответ должен быть fail.
UNREACHABLE_DATABASE_URL = "postgresql://user:password@127.0.0.1:1/db"
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"


def test_unreachable_postgres_is_reported_as_fail() -> None:
    async def scenario() -> str:
        engine = create_db_engine(UNREACHABLE_DATABASE_URL)
        try:
            return await check_postgres(engine)
        finally:
            await engine.dispose()

    assert asyncio.run(scenario()) == "fail"


def test_unreachable_redis_is_reported_as_fail() -> None:
    async def scenario() -> str:
        client = create_redis_client(UNREACHABLE_REDIS_URL)
        try:
            return await check_redis(client)
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == "fail"


def test_readiness_is_degraded_when_dependencies_are_down() -> None:
    async def scenario() -> dict[str, object]:
        engine = create_db_engine(UNREACHABLE_DATABASE_URL)
        client = create_redis_client(UNREACHABLE_REDIS_URL)
        try:
            readiness = await check_readiness(engine, client)
        finally:
            await client.aclose()
            await engine.dispose()
        return readiness.model_dump()

    assert asyncio.run(scenario()) == {
        "status": "degraded",
        "checks": {"postgres": "fail", "redis": "fail"},
    }


def test_engine_uses_asyncpg_driver_for_plain_postgres_url() -> None:
    engine = create_db_engine(UNREACHABLE_DATABASE_URL)

    assert engine.url.drivername == "postgresql+asyncpg"
