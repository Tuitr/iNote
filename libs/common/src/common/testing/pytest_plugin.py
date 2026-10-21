import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Final

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from common.redis import createRedis

# Та же версия, что в deploy/docker-compose.yml
REDIS_IMAGE: Final = "redis:7.4-alpine"
REDIS_PORT: Final = 6379


# Примечание: один Redis в testcontainers на всю сессию; тесты изолируются уникальными ключами
@pytest.fixture(scope="session")
def redisContainerUrl() -> Iterator[str]:
    from testcontainers.community.redis import RedisContainer

    with RedisContainer(REDIS_IMAGE) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(REDIS_PORT)
        yield f"redis://{host}:{port}/0"


# Примечание: клиент к контейнерному Redis на один тест, закрывается после теста
@pytest_asyncio.fixture
async def redisClient(redisContainerUrl: str) -> AsyncIterator[Redis]:
    client = createRedis(redisContainerUrl)
    try:
        yield client
    finally:
        await client.aclose()


# Примечание: уникальный id для ключей и пользователей теста — порядок запуска не важен
@pytest.fixture
def uniqueId() -> str:
    return uuid.uuid4().hex
