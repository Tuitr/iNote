import pytest
from redis.asyncio import Redis

from bot.infrastructure.redis_adapters import (
    DEDUP_TTL_S,
    RATE_WINDOW_S,
    RedisRateLimiter,
    RedisUpdateDeduplicator,
)
from common.redis import KeyedRedis

pytestmark = pytest.mark.integration


# Примечание: дедуп на настоящем Redis: ключ va:v1:bot:upd:{id}, TTL 24 ч
async def test_dedupKeyAndTtl(redisClient: Redis, uniqueId: str) -> None:
    dedup = RedisUpdateDeduplicator(KeyedRedis(redisClient))
    updateId = f"msg-{uniqueId}"
    assert await dedup.isFirstSeen(updateId) is True
    assert await dedup.isFirstSeen(updateId) is False
    ttl = await redisClient.ttl(f"va:v1:bot:upd:{updateId}")
    assert DEDUP_TTL_S - 5 < ttl <= DEDUP_TTL_S


# Примечание: лимит 20 в минуту: 21-е сообщение отклонено, окно — 60 с, у другого пользователя своё
async def test_rateLimitWindow(redisClient: Redis, uniqueId: str) -> None:
    limiter = RedisRateLimiter(KeyedRedis(redisClient), limit=20)
    userId = int(uniqueId[:12], 16)
    results = [await limiter.allow(userId) for _ in range(21)]
    assert results == [True] * 20 + [False]
    ttl = await redisClient.ttl(f"va:v1:bot:rl:{userId}")
    assert 0 < ttl <= RATE_WINDOW_S
    assert await limiter.allow(userId + 1) is True
