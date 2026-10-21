import pytest
from redis.asyncio import Redis

from bot.infrastructure.redis_adapters import (
    DEDUP_TTL_S,
    MARKER_TTL_S,
    RATE_WINDOW_S,
    RedisMarkerStore,
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


# Примечание: лимит 20 в минуту: 21-е отклонено с предупреждением, 22-е — молча; окно 60 с
async def test_rateLimitWindow(redisClient: Redis, uniqueId: str) -> None:
    limiter = RedisRateLimiter(KeyedRedis(redisClient), limit=20)
    userId = int(uniqueId[:12], 16)
    decisions = [await limiter.check(userId) for _ in range(22)]
    assert [d.allowed for d in decisions] == [True] * 20 + [False, False]
    assert [d.notify for d in decisions[20:]] == [True, False]
    ttl = await redisClient.ttl(f"va:v1:bot:rl:{userId}")
    assert 0 < ttl <= RATE_WINDOW_S
    assert (await limiter.check(userId + 1)).allowed is True


# Примечание: marker в Redis: пусто → None, после save — значение и TTL 24 ч
async def test_markerStore(redisClient: Redis) -> None:
    await redisClient.delete("va:v1:bot:marker:poll")
    store = RedisMarkerStore(KeyedRedis(redisClient))
    assert await store.load() is None
    await store.save(12345)
    assert await store.load() == 12345
    ttl = await redisClient.ttl("va:v1:bot:marker:poll")
    assert MARKER_TTL_S - 5 < ttl <= MARKER_TTL_S
