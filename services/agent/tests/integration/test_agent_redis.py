from datetime import UTC, datetime
from typing import Final

import pytest
from redis.asyncio import Redis

from agent.application.ports import DialogContext
from agent.infrastructure.redis_context import (
    CONTEXT_TTL_S,
    FOCUS_TTL_S,
    PENDING_TTL_S,
    RedisDialogContextStore,
)
from agent.infrastructure.redis_lock import LOCK_TTL_S, RedisUserLock
from common.redis import KeyedRedis
from contracts.agent_side import ItemBrief, Turn
from services.agent.tests.fakes import ITEM

pytestmark = pytest.mark.integration

AT: Final = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)


# Примечание: свой user_id на тест — порядок запуска не важен
def userIdFrom(uniqueId: str) -> int:
    return int(uniqueId[:12], 16)


# Примечание: лок на настоящем Redis: ключ, TTL 60 с, чужой токен не снимает
async def test_lockKeyAndTtl(redisClient: Redis, uniqueId: str) -> None:
    userId = userIdFrom(uniqueId)
    lock = RedisUserLock(KeyedRedis(redisClient))
    token = await lock.acquire(userId, waitS=0)
    assert token is not None
    key = f"va:v1:agent:lock:{userId}"
    assert await redisClient.get(key) == token
    assert LOCK_TTL_S - 2 < await redisClient.ttl(key) <= LOCK_TTL_S
    assert await lock.release(userId, "чужой") is False
    assert await redisClient.exists(key) == 1
    assert await lock.release(userId, token) is True
    assert await redisClient.exists(key) == 0


# Примечание: контекст на настоящем Redis: три ключа с TTL по реестру
async def test_contextKeysAndTtl(redisClient: Redis, uniqueId: str) -> None:
    userId = userIdFrom(uniqueId)
    store = RedisDialogContextStore(KeyedRedis(redisClient))
    await store.save(
        userId,
        DialogContext(
            history=(Turn(role="user", text="привет", at=AT),),
            focus=(ItemBrief.fromWire(ITEM),),
            pending={"step": 1},
        ),
    )
    for entity, ttl in (("ctx", CONTEXT_TTL_S), ("focus", FOCUS_TTL_S), ("pending", PENDING_TTL_S)):
        actual = await redisClient.ttl(f"va:v1:agent:{entity}:{userId}")
        assert ttl - 2 < actual <= ttl, entity
    assert (await store.load(userId)).pending == {"step": 1}
